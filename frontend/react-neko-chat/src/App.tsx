import {
  useState,
  useEffect,
  useMemo,
  useRef,
  useCallback,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react';
import { createPortal } from 'react-dom';
import CompactExportHistoryPanel, {
  COMPACT_EXPORT_SELECTION_LIMIT,
  isCompactExportMessageSelectable,
  type CompactExportActionRequest,
  type CompactExportPreviewResult,
  type CompactHistoryDropRequest,
} from './CompactExportHistoryPanel';
import { i18n } from './i18n';
import {
  type ChatMessage,
  type MessageAction,
  type ChatWindowSchemaProps,
  type ComposerSubmitPayload,
  type ComposerAttachment,
  type CompactHistoryDropPayload,
  type CompactHistoryDragStatePayload,
  type AvatarInteractionPayload,
  type AvatarToolStatePayload,
  type CompactChatState,
  type MessageBlock,
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
  onCompactHistoryDrop?: (payload: CompactHistoryDropPayload) => unknown;
  onCompactHistoryDragStateChange?: (payload: CompactHistoryDragStatePayload) => void;
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

const COMPACT_PREVIEW_MAX_LENGTH = 84;
const COMPACT_SPEECH_REVEAL_MAX_CHARS_PER_SECOND = 8;
const COMPACT_SPEECH_TURN_MERGE_WINDOW_MS = 12000;
const COMPACT_SPEECH_FALLBACK_REVEAL_DELAY_MS = 700;
const SPEECH_PLAYBACK_STATE_STORAGE_KEY = 'neko_speech_playback_state';
const SPEECH_PLAYBACK_CHANNEL_NAME = 'neko_speech_playback_channel';
const COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY = 'neko.reactChatWindow.compactExportHistoryOpen';
const COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT = 7;
const COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD = 22;
const COMPACT_INPUT_TOOL_WHEEL_SCROLL_THRESHOLD = 64;
const COMPACT_INPUT_TOOL_WHEEL_DRAG_GUARD_MS = 4000;
const COMPACT_INPUT_TOOL_WHEEL_FAST_GESTURE_MS = 140;
const COMPACT_INPUT_TOOL_WHEEL_FAST_ANIMATION_MS = 180;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_STEPS = COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT * 4;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_LAP_STEPS = COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT * 2;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS = COMPACT_INPUT_TOOL_WHEEL_CHARGE_LAP_STEPS * 2;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_STEP_MS = 36;
const COMPACT_INPUT_TOOL_WHEEL_CENTER_X = 116;
const COMPACT_INPUT_TOOL_WHEEL_CENTER_Y = 116;
const COMPACT_INPUT_TOOL_WHEEL_ORBIT_RADIUS = 91.92;
const COMPACT_INPUT_TOOL_WHEEL_HOVER_RADIUS = 116;
const COMPACT_INPUT_TOOL_WHEEL_ANGLE_MIN_RADIUS = 16;
const COMPACT_INPUT_TOOL_TOGGLE_HOVER_OUTSET = 14;
const COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE = 48;
const COMPACT_INPUT_TOOL_FAN_INTERACTIVE_DELAY_MS = 220;
const COMPACT_INPUT_TOOL_FAN_TRANSIENT_CLOSE_DELAY_MS = 360;
const COMPACT_INPUT_TOOL_FAN_OUTSIDE_CLOSE_DELAY_MS = 650;
const COMPACT_SURFACE_RESIZE_MIN_WIDTH = 430;
const COMPACT_SURFACE_RESIZE_MAX_WIDTH = 720;
const COMPACT_SURFACE_RESIZE_VIEWPORT_GUTTER = 32;
const COMPACT_CHOICE_PLACEMENT_HYSTERESIS = 24;

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

function createCompactHistoryDropRequestId() {
  return `compact-history-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeCompactHistoryTextFragment(value: string | undefined) {
  return typeof value === 'string' ? value.trim() : '';
}

function getCompactHistoryTextFromBlock(block: MessageBlock) {
  if (block.type === 'text' || block.type === 'status') {
    return normalizeCompactHistoryTextFragment(block.text);
  }
  if (block.type === 'link') {
    return [
      normalizeCompactHistoryTextFragment(block.title),
      normalizeCompactHistoryTextFragment(block.description),
      normalizeCompactHistoryTextFragment(block.url),
    ].filter(Boolean).join('\n');
  }
  if (block.type === 'buttons') {
    return block.buttons
      .map(button => normalizeCompactHistoryTextFragment(button.label))
      .filter(Boolean)
      .join(' / ');
  }
  return '';
}

function buildCompactHistoryDropPayload(request: CompactHistoryDropRequest): CompactHistoryDropPayload {
  const textParts: string[] = [];
  const images: NonNullable<CompactHistoryDropPayload['images']> = [];

  if (request.payload.type === 'image') {
    images.push({
      url: request.payload.url,
      alt: request.payload.alt,
      width: request.payload.width,
      height: request.payload.height,
    });
  } else {
    for (const block of request.payload.blocks) {
      if (block.type === 'image') {
        images.push({
          url: block.url,
          alt: block.alt,
          width: block.width,
          height: block.height,
        });
        continue;
      }
      const text = getCompactHistoryTextFromBlock(block);
      if (text) {
        textParts.push(text);
      }
    }
  }

  return {
    text: textParts.join('\n').trim(),
    images,
    requestId: createCompactHistoryDropRequestId(),
    sourceMessageId: request.messageId,
    dragType: request.type,
    compactHistoryDragSessionId: request.sessionId,
  };
}

function normalizeCompactHistoryDropResult(result: unknown): Promise<boolean | void> | boolean | void {
  if (result && typeof (result as PromiseLike<unknown>).then === 'function') {
    return Promise.resolve(result).then(value => (value === false ? false : undefined));
  }
  return result === false ? false : undefined;
}

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
  author: string;
  text: string;
  fullText: string;
  isStreaming: boolean;
  isAssistant: boolean;
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

function clampCompactSurfaceResizeWidth(width: number, maxAvailableWidth: number): number {
  const maxWidth = Math.max(
    0,
    Math.min(COMPACT_SURFACE_RESIZE_MAX_WIDTH, maxAvailableWidth - COMPACT_SURFACE_RESIZE_VIEWPORT_GUTTER),
  );
  const minWidth = Math.min(COMPACT_SURFACE_RESIZE_MIN_WIDTH, maxWidth || COMPACT_SURFACE_RESIZE_MIN_WIDTH);
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
    return window.localStorage?.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY) === 'true';
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

type SpeechPlaybackState = {
  active: boolean;
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

function truncateCompactPreview(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, Math.max(0, maxLength - 3)).trimEnd()}...`;
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

function getCompactMessagePreview(messages: ChatMessage[]): CompactMessagePreview | null {
  let latestStreamingAssistantIndex = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === 'assistant' && message.status === 'streaming' && getMessageBlockPreviewText(message)) {
      latestStreamingAssistantIndex = index;
      break;
    }
  }

  if (latestStreamingAssistantIndex >= 0) {
    const turnTexts: string[] = [];
    let turnAuthor = '';
    const latestStreamingMessage = messages[latestStreamingAssistantIndex];
    const turnMessageId = String(latestStreamingMessage?.id || 'assistant-streaming');
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
      if (index !== latestStreamingAssistantIndex && message.status !== 'streaming') {
        const createdAt = typeof message.createdAt === 'number' && Number.isFinite(message.createdAt)
          ? message.createdAt
          : null;
        if (
          previousIncludedCreatedAt === null
          || createdAt === null
          || Math.abs(previousIncludedCreatedAt - createdAt) > COMPACT_SPEECH_TURN_MERGE_WINDOW_MS
        ) {
          break;
        }
        previousIncludedCreatedAt = createdAt;
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
        author: turnAuthor,
        text: turnText,
        fullText: turnText,
        isStreaming: true,
        isAssistant: true,
      };
    }
  }

  let fallbackPreview: CompactMessagePreview | null = null;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message) continue;
    const text = getMessageBlockPreviewText(message);
    if (!text) continue;

    const isStreamingAssistantMessage = message.role === 'assistant' && message.status === 'streaming';
    const preview = {
      messageId: message.id,
      author: message.author,
      text: isStreamingAssistantMessage ? text : truncateCompactPreview(text, COMPACT_PREVIEW_MAX_LENGTH),
      fullText: text,
      isStreaming: isStreamingAssistantMessage,
      isAssistant: message.role === 'assistant',
    };
    if (message.role === 'assistant') {
      return preview;
    }
    if (!fallbackPreview) {
      fallbackPreview = preview;
    }
  }
  return fallbackPreview;
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

type CompactHistoryDesktopDropTargetDetail = {
  active?: boolean;
  sessionId?: string;
  desktopOverAvatar?: boolean | null;
  timestamp?: number;
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

export default function App({
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
  onCompactHistoryDrop,
  onCompactHistoryDragStateChange,
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
  const compactInputToolWheelScrollDeltaRef = useRef(0);
  const compactInputToolTogglePointerHandledRef = useRef(false);
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
  const compactHistoryDesktopDropTargetRef = useRef<{ sessionId?: string; overTarget: boolean; timestamp: number } | null>(null);
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
  const [compactChoiceLayerPlacement, setCompactChoiceLayerPlacement] = useState<'above' | 'below'>('above');
  const [compactInputToolFanOpen, setCompactInputToolFanOpen] = useState(false);
  const [compactInputToolFanInteractive, setCompactInputToolFanInteractive] = useState(false);
  const [compactInputToolWheelIndex, setCompactInputToolWheelIndex] = useState(0);
  const [compactInputToolWheelFastAnimation, setCompactInputToolWheelFastAnimation] = useState(false);
  const [compactInputToolWheelChargeRatio, setCompactInputToolWheelChargeRatio] = useState(0);
  const [compactInputToolWheelChargeDirection, setCompactInputToolWheelChargeDirection] = useState<1 | -1 | null>(null);
  const [compactInputToolWheelChargeReleaseActive, setCompactInputToolWheelChargeReleaseActive] = useState(false);
  const [compactSurfaceResizeWidth, setCompactSurfaceResizeWidth] = useState<number | null>(null);
  const [compactExportHistoryOpen, setCompactExportHistoryOpen] = useState(readPersistedCompactExportHistoryOpen);
  const [compactExportPreviewOpen, setCompactExportPreviewOpen] = useState(false);
  const [compactExportSelectedIds, setCompactExportSelectedIds] = useState<Set<string>>(() => new Set());
  const [compactExportAutoScrollToBottom, setCompactExportAutoScrollToBottom] = useState(true);
  const compactSurfaceResizeStateRef = useRef<CompactSurfaceResizeState | null>(null);
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
  const compactExportHistoryButtonLabel = i18n('chat.compactExportHistory', 'History');
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
  const getClampedCompactSurfaceResizeWidth = useCallback((width: number) => (
    clampCompactSurfaceResizeWidth(width, getCompactSurfaceResizeMaxAvailableWidth())
  ), [getCompactSurfaceResizeMaxAvailableWidth]);
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
        return clampCompactSurfaceResizeWidth(width, maxWidth + COMPACT_SURFACE_RESIZE_VIEWPORT_GUTTER);
      }
    }
    return getClampedCompactSurfaceResizeWidth(width);
  }, [getClampedCompactSurfaceResizeWidth]);
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
    return COMPACT_SURFACE_RESIZE_MIN_WIDTH;
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
  const handleCompactExportConversationClick = useCallback(() => {
    setCompactExportHistoryOpen((open) => {
      const nextOpen = !open;
      persistCompactExportHistoryOpen(nextOpen);
      if (nextOpen) {
        setCompactExportAutoScrollToBottom(true);
      } else {
        setCompactExportPreviewOpen(false);
      }
      return nextOpen;
    });
  }, []);
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
  const compactMessagePreview = useMemo(() => getCompactMessagePreview(messages), [messages]);
  const compactSpeechModeActive = !!compactMessagePreview?.isAssistant
    && !!compactMessagePreview?.messageId
    && (
      compactMessagePreview.isStreaming
      || compactSpeechPreviewIdRef.current === compactMessagePreview.messageId
    );
  const compactSpeechPreservedText = compactSpeechModeActive && !compactMessagePreview?.isStreaming
    ? compactSpeechPreviewTextRef.current
    : '';
  const compactPreviewText = compactSpeechModeActive
    ? (
      compactMessagePreview?.isStreaming
        ? compactMessagePreview?.fullText || ''
        : compactSpeechPreservedText || compactMessagePreview?.fullText || ''
    )
    : compactMessagePreview?.text
    || i18n('chat.emptyState', 'Chat content will appear here.');
  const compactPreviewIsStreaming = compactSpeechModeActive;
  const compactPreviewSpeechDuration = useMemo(() => {
    if (!compactPreviewIsStreaming || !speechPlaybackState) {
      return null;
    }
    const audioDuration = speechPlaybackState.playbackEndAudioTime - speechPlaybackState.playbackStartAudioTime;
    if (!Number.isFinite(audioDuration) || audioDuration <= 0.05) {
      return null;
    }
    return getCompactSpeechRevealDuration(compactPreviewText.length, audioDuration);
  }, [compactPreviewIsStreaming, compactPreviewText.length, speechPlaybackState]);
  const compactPreviewDisplayText = useMemo(() => {
    if (!compactPreviewIsStreaming) {
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
    compactPreviewTextVisibleRef.current = compactPreviewTextVisible;
  }, [compactPreviewTextVisible]);

  useEffect(() => {
    speechPlaybackStateRef.current = speechPlaybackState;
  }, [speechPlaybackState]);

  useEffect(() => {
    isCompactSurfaceRef.current = isCompactSurface;
  }, [isCompactSurface]);

  useEffect(() => {
    compactSpeechVisibleLengthRef.current = compactSpeechVisibleLength;
  }, [compactSpeechVisibleLength]);

  useEffect(() => {
    if (compactMessagePreview?.isStreaming && compactMessagePreview.isAssistant) {
      compactSpeechPreviewIdRef.current = compactMessagePreview.messageId;
      compactSpeechPreviewTextRef.current = compactMessagePreview.fullText || compactMessagePreview.text || '';
    } else if (compactSpeechPreviewIdRef.current && (
      !compactMessagePreview?.messageId
      || compactSpeechPreviewIdRef.current !== compactMessagePreview.messageId
    )) {
      compactSpeechPreviewIdRef.current = '';
      compactSpeechPreviewTextRef.current = '';
    }
  }, [
    compactMessagePreview?.fullText,
    compactMessagePreview?.isAssistant,
    compactMessagePreview?.isStreaming,
    compactMessagePreview?.messageId,
    compactMessagePreview?.text,
  ]);

  useEffect(() => {
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
  }, [compactMessagePreview?.messageId]);

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

    if (!speechPlaybackState?.active) {
      return;
    }
    const estimatedAudioTime = getEstimatedSpeechAudioTime(speechPlaybackState);
    if (estimatedAudioTime >= speechPlaybackState.playbackStartAudioTime) {
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
  }, [compactPreviewIsStreaming, speechPlaybackState]);

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
      const playbackHasStarted = !!playbackState?.active
        && getEstimatedSpeechAudioTime(playbackState) >= playbackState.playbackStartAudioTime;
      if (
        !isCompactSurfaceRef.current
        || compactSpeechPlaybackStartedRef.current
        || playbackHasStarted
        || compactSpeechVisibleLengthRef.current > 0
      ) {
        return;
      }
      compactSpeechFallbackRevealRef.current = true;
      compactSpeechRevealCarryRef.current = 0;
      compactSpeechLastFrameTimeRef.current = 0;
      compactSpeechVisibleLengthRef.current = Math.min(1, compactPreviewText.length);
      setCompactSpeechVisibleLength(compactSpeechVisibleLengthRef.current);
      setCompactSpeechFallbackRevealActive(true);
    }, COMPACT_SPEECH_FALLBACK_REVEAL_DELAY_MS);

    return () => {
      if (compactSpeechFallbackTimerRef.current !== null) {
        window.clearTimeout(compactSpeechFallbackTimerRef.current);
        compactSpeechFallbackTimerRef.current = null;
      }
    };
  }, [compactPreviewIsStreaming, compactPreviewText.length, compactMessagePreview?.messageId]);

  useEffect(() => {
    function handleAssistantSpeechUnavailable() {
      if (!isCompactSurfaceRef.current || !compactPreviewIsStreaming || !compactMessagePreview?.isAssistant) {
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
  }, [compactMessagePreview?.isAssistant, compactPreviewIsStreaming]);

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
      const playbackState = speechPlaybackStateRef.current;
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
  }, [compactPreviewIsStreaming, compactPreviewText.length, compactPreviewSpeechDuration, compactSpeechFallbackRevealActive]);

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
    if (!isCompactSurface || !compactPreviewIsStreaming) {
      textNode.scrollLeft = 0;
      return;
    }
    textNode.scrollLeft = textNode.scrollWidth;
  }, [compactPreviewDisplayText, compactPreviewIsStreaming, isCompactSurface]);

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
    setCompactInputToolFanInteractiveState(false);
    compactInputToolFanPositionSyncRef.current?.();
    compactInputToolFanOpenRef.current = false;
    setCompactInputToolFanOpen(false);
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
    compactInputToolFanInteractive,
    compactInputToolFanOpen,
    compactInputToolWheelIndex,
    effectiveCompactChatState,
    isCompactSurface,
  ]);

  const openCompactInputToolFan = useCallback((intent: 'click' | 'hover') => {
    if (composerDisabled || compactInputHasPayload) return;
    clearCompactInputToolFanCloseTimer();
    clearCompactInputToolFanInteractiveTimer();
    compactInputToolFanOpenIntentRef.current = intent;
    setCompactInputToolFanInteractiveState(false);
    updateCompactInputToolFanPosition();
    compactInputToolFanOpenRef.current = true;
    setCompactInputToolFanOpen(true);
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
      return rawDelta * Math.max(window.innerHeight || 0, COMPACT_INPUT_TOOL_WHEEL_SCROLL_THRESHOLD);
    }
    return rawDelta;
  }, []);

  const rotateCompactInputToolWheelByScroll = useCallback((event: ReactWheelEvent<HTMLDivElement>) => {
    const normalizedDelta = getCompactInputToolWheelNormalizedDelta(event);
    if (normalizedDelta === 0) return;

    event.preventDefault();
    event.stopPropagation();

    const nextDelta = compactInputToolWheelScrollDeltaRef.current + normalizedDelta;
    const stepCount = Math.floor(Math.abs(nextDelta) / COMPACT_INPUT_TOOL_WHEEL_SCROLL_THRESHOLD);
    if (stepCount <= 0) {
      compactInputToolWheelScrollDeltaRef.current = nextDelta;
      return;
    }

    const direction: 1 | -1 = nextDelta > 0 ? 1 : -1;
    rotateCompactInputToolWheelSteps(direction, stepCount);
    compactInputToolWheelScrollDeltaRef.current = nextDelta - (
      direction * stepCount * COMPACT_INPUT_TOOL_WHEEL_SCROLL_THRESHOLD
    );
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
    if (!options?.ignoreFocusedShell && compactExportHistoryOpen) return;
    const activeElement = document.activeElement;
    if (
      !options?.ignoreFocusedShell
      && activeElement instanceof Node
      && (
        !!compactInputShellRef.current?.contains(activeElement)
        || (
          activeElement instanceof Element
          && !!activeElement.closest('.compact-export-history-anchor')
        )
      )
    ) {
      return;
    }
    requestCompactChatState('default');
  }, [
    compactInputToolFanOpen,
    compactExportHistoryOpen,
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
          && !!target.closest('.compact-export-history-anchor')
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
    compactInputToolWheelScrollDeltaRef.current = 0;
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
  }, [compactPreviewText, isCompactSurface]);

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

  function submitDraft() {
    if (composerDisabled) return;
    if (submittingRef.current) return;
    const text = draft.trim();
    if (!text && composerAttachments.length === 0) return;
    closeCompactInputToolFan();
    submittingRef.current = true;
    try {
      onComposerSubmit?.({ text });
      setDraft('');
      restoreCompactExportHistoryToBottomForOutgoingMessage();
      requestCompactChatState('default');
    } finally {
      requestAnimationFrame(() => { submittingRef.current = false; });
    }
  }

  useEffect(() => {
    function handleDesktopDropTargetChange(event: Event) {
      const detail = (event as CustomEvent<CompactHistoryDesktopDropTargetDetail>).detail;
      if (detail?.active === false) {
        compactHistoryDesktopDropTargetRef.current = null;
        return;
      }
      if (!detail?.sessionId || typeof detail.desktopOverAvatar !== 'boolean') return;
      compactHistoryDesktopDropTargetRef.current = {
        sessionId: detail.sessionId,
        overTarget: detail.desktopOverAvatar,
        timestamp: Number.isFinite(Number(detail.timestamp)) ? Number(detail.timestamp) : Date.now(),
      };
    }

    window.addEventListener('neko:compact-history-drag-desktop-target-change', handleDesktopDropTargetChange);
    return () => {
      window.removeEventListener('neko:compact-history-drag-desktop-target-change', handleDesktopDropTargetChange);
    };
  }, []);

  const getCompactHistoryDesktopDropTarget = useCallback((sessionId?: string) => {
    const desktopDropTarget = compactHistoryDesktopDropTargetRef.current;
    if (!desktopDropTarget) return null;
    if (sessionId && desktopDropTarget.sessionId && desktopDropTarget.sessionId !== sessionId) return null;
    if (Date.now() - desktopDropTarget.timestamp >= 800) return null;
    return desktopDropTarget.overTarget;
  }, []);

  const isCompactHistoryDropTargetAt = useCallback((point: { clientX: number; clientY: number; sessionId?: string }) => (
    getCompactHistoryDesktopDropTarget(point.sessionId) ?? isPointerWithinAvatarRange(point.clientX, point.clientY, avatarToolCacheState)
  ), [avatarToolCacheState, getCompactHistoryDesktopDropTarget]);

  const handleCompactHistoryDropToAvatar = useCallback((request: CompactHistoryDropRequest) => {
    const desktopDropTarget = getCompactHistoryDesktopDropTarget(request.sessionId);
    if (
      desktopDropTarget !== true
      && (desktopDropTarget !== null || !isPointerWithinAvatarRange(request.point.clientX, request.point.clientY, avatarToolCacheState))
    ) {
      return false;
    }

    const payload = buildCompactHistoryDropPayload(request);
    const hasText = !!payload.text?.trim();
    const hasImages = (payload.images?.length ?? 0) > 0;
    if (!hasText && !hasImages) {
      return false;
    }

    restoreCompactExportHistoryToBottomForOutgoingMessage();
    if (onCompactHistoryDrop) {
      return normalizeCompactHistoryDropResult(onCompactHistoryDrop(payload));
    }
    if (hasImages) {
      return false;
    }
    onComposerSubmit?.({
      text: payload.text ?? '',
      requestId: payload.requestId,
    });
    return true;
  }, [avatarToolCacheState, compactExportHistoryOpen, getCompactHistoryDesktopDropTarget, onCompactHistoryDrop, onComposerSubmit]);

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
      aria-label={compactToolToggleActsAsSubmit ? sendButtonLabel : overflowMenuAriaLabel}
      aria-haspopup={compactToolToggleActsAsSubmit ? undefined : 'true'}
      aria-expanded={compactToolToggleActsAsSubmit ? undefined : compactInputToolFanOpen}
      disabled={compactToolToggleActsAsSubmit ? !canSubmit : composerDisabled}
      onPointerDown={compactToolToggleActsAsSubmit ? undefined : (event) => {
        event.preventDefault();
        compactInputToolTogglePointerHandledRef.current = true;
        toggleCompactInputToolFanByClick();
      }}
      onPointerEnter={compactToolToggleActsAsSubmit ? undefined : handleCompactInputToolHoverEnter}
      onPointerLeave={compactToolToggleActsAsSubmit ? undefined : handleCompactInputToolHoverLeave}
      onFocus={compactToolToggleActsAsSubmit ? undefined : clearCompactInputToolFanCloseTimer}
      onBlur={compactToolToggleActsAsSubmit ? scheduleCompactInputCollapse : () => {
        scheduleCompactInputToolFanTransientClose();
        scheduleCompactInputCollapse();
      }}
      onClick={compactToolToggleActsAsSubmit ? undefined : () => {
        if (compactInputToolTogglePointerHandledRef.current) {
          compactInputToolTogglePointerHandledRef.current = false;
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

  const compactInputToolFanNode = compactToolToggleVisible ? (
    <div
      ref={compactInputToolFanRef}
      className="compact-input-tool-fan"
      role="group"
      aria-label={overflowMenuAriaLabel}
      data-compact-geometry-item="toolFan"
      data-compact-geometry-owner="surface"
      data-compact-input-tool-fan-open={compactInputToolFanOpen ? 'true' : 'false'}
      data-compact-input-tool-fan-interactive={compactInputToolFanInteractive ? 'true' : 'false'}
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
        event.preventDefault();
        event.stopPropagation();
        markCompactToolFanOriginClickSuppressed();
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
        compactInputToolWheelScrollDeltaRef.current = 0;
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
        finishCompactToolWheelPointer(event);
      }}
      onPointerCancel={(event) => {
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
        tabIndex={getCompactToolWheelTabIndex(0)}
        aria-hidden={getCompactToolWheelAriaHidden(0)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(0)}
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
        tabIndex={getCompactToolWheelTabIndex(1)}
        aria-hidden={getCompactToolWheelAriaHidden(1)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(1)}
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
        tabIndex={getCompactToolWheelTabIndex(2)}
        aria-hidden={getCompactToolWheelAriaHidden(2)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(2)}
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
        tabIndex={getCompactToolWheelTabIndex(3)}
        aria-hidden={getCompactToolWheelAriaHidden(3)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(3)}
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
        tabIndex={getCompactToolWheelTabIndex(4)}
        aria-hidden={getCompactToolWheelAriaHidden(4)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(4)}
        onClick={compactFanRunAction(onJukeboxClick)}
      >
        <img src="/static/icons/jukebox_icon.png" alt="" aria-hidden="true" />
      </button>
      <button
        className={`composer-tool-btn compact-input-tool-item compact-input-tool-item-export${compactExportHistoryOpen ? ' is-active' : ''}`}
        type="button"
        aria-label={compactExportHistoryButtonLabel}
        aria-pressed={compactExportHistoryOpen}
        title={compactExportHistoryButtonLabel}
        disabled={compactInputToolFanActionsDisabled}
        tabIndex={getCompactToolWheelTabIndex(5)}
        aria-hidden={getCompactToolWheelAriaHidden(5)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(5)}
        data-compact-tool-active={compactExportHistoryOpen ? 'true' : 'false'}
        onClick={compactFanRunAction(handleCompactExportConversationClick)}
      >
        <svg viewBox="0 0 1024 1024" width="24" height="24" fill="currentColor" aria-hidden="true">
          <path d="M855.467 501.333c-17.067 0-32 14.934-32 32v198.4c0 70.4-59.734 130.134-130.134 130.134H356.267c-83.2 0-151.467-66.134-151.467-149.334V358.4c0-64 53.333-117.333 117.333-117.333h168.534c17.066 0 32-14.934 32-32s-14.934-32-32-32H322.133c-100.266 0-181.333 81.066-181.333 181.333v352c0 117.333 96 213.333 215.467 213.333h337.066c106.667 0 194.134-87.466 194.134-194.133V533.333c0-17.066-14.934-32-32-32zM680.533 256H761.6L458.667 569.6A30.933 30.933 0 0 0 480 622.933c8.533 0 17.067-4.266 23.467-10.666l305.066-313.6v89.6c0 17.066 14.934 32 32 32s32-14.934 32-32v-147.2c0-27.734-23.466-51.2-51.2-51.2h-140.8c-17.066 0-32 14.933-32 32s14.934 34.133 32 34.133z" />
        </svg>
      </button>
      <div
        className="composer-tool-menu compact-input-tool-item compact-input-tool-item-avatar"
        ref={toolMenuRef}
        aria-hidden={getCompactToolWheelAriaHidden(6)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(6)}
        data-compact-tool-active={activeToolItem ? 'true' : 'false'}
      >
        <button
          className={`composer-tool-btn composer-emoji-btn${toolMenuOpen || activeToolItem ? ' is-active' : ''}`}
          type="button"
          aria-label={selectedEmojiButtonAriaLabel}
          title={selectedEmojiButtonAriaLabel}
          aria-controls={toolMenuOpen ? 'composer-tool-popover-compact' : undefined}
          aria-expanded={toolMenuOpen}
          disabled={compactInputToolFanActionsDisabled}
          tabIndex={getCompactToolWheelTabIndex(6)}
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
            <span aria-hidden="true">脳</span>
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

  const choiceLayerNode = (
    <div
      className={`composer-choice-layer${isCompactSurface ? ' compact-chat-choice-anchor' : ''}`}
      ref={isCompactSurface ? compactChoiceLayerRef : undefined}
      data-compact-geometry-item={isCompactSurface ? 'choice' : undefined}
      data-compact-geometry-owner={isCompactSurface ? 'surface' : undefined}
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
                    title={option.text}
                    disabled={composerDisabled || galgameOptionsLoading}
                    tabIndex={compactChoiceLayerOpen && galgameOptionsVisible ? 0 : -1}
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
                    <span className="composer-galgame-option-text">{option.text}</span>
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
                      <span className="composer-galgame-option-text">{galgameLoadingLabel}</span>
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
                title={option.label}
                disabled={composerDisabled}
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
                  {option.label}
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

  const compactExportHistoryElement = isCompactSurface && compactExportHistoryOpen ? (
    <CompactExportHistoryPanel
      messages={messages}
      selectedIds={compactExportSelectedIds}
      selectedCount={compactExportSelectedCount}
      selectableCount={compactExportSelectableCount}
      autoScrollToBottom={compactExportAutoScrollToBottom}
      previewOpen={compactExportPreviewOpen}
      choiceLayerAbove={compactChoiceLayerOpen && compactChoiceLayerPlacement === 'above'}
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
      isDropTargetAt={isCompactHistoryDropTargetAt}
      onDropToTarget={handleCompactHistoryDropToAvatar}
      onDragStateChange={onCompactHistoryDragStateChange}
    />
  ) : null;
  const compactExportHistoryNode = compactExportHistoryElement;
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
      data-compact-export-preview-open={isCompactSurface && compactExportPreviewOpen ? 'true' : 'false'}
      data-compact-export-selected-count={isCompactSurface ? compactExportSelectedCount : 0}
      data-compact-export-auto-scroll={isCompactSurface && compactExportAutoScrollToBottom ? 'true' : 'false'}
    >
      {compactExportHistoryNode}
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
          <div id="music-player-mount" />
          {composerAttachments.length > 0 ? (
            <div className="composer-attachments" aria-label={composerAttachmentsAriaLabel}>
              {composerAttachments.map((attachment) => (
                <figure key={attachment.id} className="composer-attachment-card">
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
                    脳
                  </button>
                </figure>
              ))}
            </div>
          ) : null}
          <form className="composer" onSubmit={(event) => {
            event.preventDefault();
            submitDraft();
          }}>
            {isCompactSurface ? (
              <div
                className="compact-chat-surface-shell"
                ref={compactInputShellRef}
                data-compact-chat-state={effectiveCompactChatState}
                style={compactSurfaceShellStyle}
                onBlurCapture={effectiveCompactChatState === 'input' ? scheduleCompactInputCollapse : undefined}
              >
                <div
                  className="compact-chat-drag-handle"
                  data-compact-drag-handle="true"
                  data-compact-geometry-item="dragHandle"
                  data-compact-geometry-owner="surface"
                  aria-hidden="true"
                />
                <div
                  className="compact-chat-resize-handle compact-chat-resize-handle-left"
                  data-compact-resize-side="left"
                  data-compact-geometry-item="resizeHandle"
                  data-compact-geometry-owner="surface"
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
                  data-compact-chat-state={effectiveCompactChatState}
                  data-compact-geometry-part={effectiveCompactChatState === 'input' ? 'inputBody' : 'capsuleBody'}
                  data-compact-tool-toggle-visible={compactToolToggleVisible ? 'true' : 'false'}
                >
                  {effectiveCompactChatState === 'input' ? (
                    <>
                      <textarea
                        className="composer-input"
                        ref={compactInputRef}
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
                      <button
                        className="compact-chat-capsule-button"
                        type="button"
                        disabled={composerDisabled}
                        onClick={() => {
                          if (composerHidden) return;
                          requestCompactChatState('input');
                        }}
                      >
                        <span
                          ref={compactPreviewTextRef}
                          className="compact-chat-capsule-text"
                          data-compact-preview-streaming={compactPreviewIsStreaming ? 'true' : 'false'}
                          onWheel={handleCompactPreviewWheel}
                        >
                          {compactPreviewDisplayText}
                        </span>
                      </button>
                      {compactInputToolToggleButton}
                    </>
                  )}
                </div>
                {compactInputToolFanNode}
              </div>
            ) : null}
          </form>
        </footer>
      </section>
    </main>
  );
}
