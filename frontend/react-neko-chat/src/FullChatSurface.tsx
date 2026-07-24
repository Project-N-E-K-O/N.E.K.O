/**
 * Full-window chat surface. Its history, composer and responsive layout remain
 * independent from Compact Chat, while avatar-tool selection delegates to the
 * shared catalog, runtime and visual layer.
 */
import {
  useState,
  useEffect,
  useMemo,
  useRef,
  useCallback,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { createPortal } from 'react-dom';
import MessageList from './MessageList';
import CompactExportHistoryPanel, {
  COMPACT_EXPORT_SELECTION_LIMIT,
  isCompactExportMessageSelectable,
  type CompactExportActionRequest,
  type CompactExportPreviewResult,
} from './CompactExportHistoryPanel';
import { getChatCompanionEmptyStateFallback, getChatEmptyStateFallback } from './chat-copy';
import { i18n } from './i18n';
import { useFocusGlow } from './useFocusGlow';
import AvatarToolVisuals from './avatar-tools/presentation';
import { useAvatarToolRuntime } from './avatar-tools/runtime';
import {
  AVAILABLE_FULL_AVATAR_TOOLS,
  resolveAvatarToolMenuIconVisual,
  type AvatarToolItem,
} from './avatarTools';
import { useGuideChatButtonLock } from './useGuideChatButtonLock';
import {
  playCompactToolWheelDetentSound,
  useCompactToolWheelAudioPreload,
} from './compactToolWheelAudio';
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

type ChatWindowProps = ChatWindowSchemaProps & {
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
};

type CompactInlineExportBridge = {
  buildCompactInlinePreview?: (request: CompactExportActionRequest) => Promise<CompactExportPreviewResult> | CompactExportPreviewResult;
  copyCompactInlineSelection?: (request: CompactExportActionRequest) => Promise<void> | void;
  downloadCompactInlineSelection?: (request: CompactExportActionRequest) => Promise<void> | void;
};

type CompactHistoryDesktopDropTargetDetail = {
  active?: boolean;
  sessionId?: string;
  desktopOverAvatar?: boolean | null;
  timestamp?: number;
};

const defaultMessages: ChatMessage[] = [];

function getEffectiveCompactChatState(
  requestedState: CompactChatState,
  hasVisibleChoices: boolean,
): CompactChatState {
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
const COMPACT_INPUT_TOOL_WHEEL_TOOL_ORDER = [
  'import',
  'screenshot',
  'galgame',
  'translate',
  'jukebox',
  'export',
  'avatar',
] as const;
const COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT = COMPACT_INPUT_TOOL_WHEEL_TOOL_ORDER.length;
const COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD = 28;
const COMPACT_INPUT_TOOL_WHEEL_CENTER_X = 116;
const COMPACT_INPUT_TOOL_WHEEL_CENTER_Y = 116;
const COMPACT_INPUT_TOOL_WHEEL_ANGLE_MIN_RADIUS = 16;
const COMPACT_INPUT_TOOL_WHEEL_DETENT_RESISTANCE_START_RATIO = 0.68;
const COMPACT_INPUT_TOOL_WHEEL_DETENT_HOLD_RATIO = 0.86;
const COMPACT_INPUT_TOOL_WHEEL_DETENT_BREAK_RATIO = 1.16;
const COMPACT_TOOL_WHEEL_DRAG_ANGLE_STEP_DEG = 30.82;
const COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE = 48;
const COMPACT_INPUT_TOOL_FAN_INTERACTIVE_DELAY_MS = 220;
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

type ToolIconItem = AvatarToolItem;

const toolIconItems = AVAILABLE_FULL_AVATAR_TOOLS;

function getToolItemLabel(item: ToolIconItem): string {
  return i18n(item.labelKey, item.labelFallback);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

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

export default function FullChatSurface({
  title = i18n('chat.title', 'N.E.K.O Chat'),
  iconSrc = '/static/icons/chat_icon.png',
  messages = defaultMessages,
  inputPlaceholder = i18n('chat.textInputPlaceholder', 'Type a message...'),
  sendButtonLabel = i18n('chat.send', 'Send'),
  chatWindowAriaLabel = i18n('chat.reactWindowAriaLabel', 'Neko chat window'),
  messageListAriaLabel = i18n('chat.messageListAriaLabel', 'Chat messages'),
  composerToolsAriaLabel = i18n('chat.composerToolsAriaLabel', 'Composer tools'),
  composerHidden = false,
  composerDisabled = false,
  catLocalTextOnly = false,
  chatSurfaceMode = 'full',
  compactChatState = 'default',
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
  onMessageAction,
  onComposerImportImage,
  onComposerScreenshot,
  onComposerRemoveAttachment,
  onComposerSubmit,
  onAvatarInteraction,
  onAvatarToolStateChange,
  onJukeboxClick,
  onExportConversationClick,
  onTranslateToggle,
  onGalgameModeToggle,
  onGalgameOptionSelect,
  choicePrompt = null,
  onChoiceSelect,
  onCompactChatStateChange,
  rollbackDraft,
  _rollbackKey,
  _avatarToolDeactivationKey,
}: ChatWindowProps) {
  useCompactToolWheelAudioPreload();

  const [draft, setDraft] = useState('');
  const [catDraft, setCatDraft] = useState('');
  const visibleDraft = catLocalTextOnly ? catDraft : draft;
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  // Collapse the right-side tools into an overflow menu when the composer gets
  // narrow, while preserving the exit and re-entry animations for the tool row.
  type ComposerLayout = 'expanded' | 'collapsing' | 'compact' | 'expanding';
  const [composerLayout, setComposerLayout] = useState<ComposerLayout>('expanded');
  const showRightTools = composerLayout === 'expanded' || composerLayout === 'collapsing';
  const [collapseFromWidth, setCollapseFromWidth] = useState<number | null>(null);
  const [overflowMenuOpen, setOverflowMenuOpen] = useState(false);
  const [composerBottomBarNode, setComposerBottomBarNode] = useState<HTMLDivElement | null>(null);
  const appShellRef = useRef<HTMLElement | null>(null);
  const toolMenuRef = useRef<HTMLDivElement | null>(null);
  const composerBottomBarRef = useRef<HTMLDivElement | null>(null);
  const composerToolsRightRef = useRef<HTMLDivElement | null>(null);
  const compactInputShellRef = useRef<HTMLDivElement | null>(null);
  const compactInputToolToggleRef = useRef<HTMLButtonElement | null>(null);
  const compactInputToolFanRef = useRef<HTMLDivElement | null>(null);
  const compactInputToolWheelPointerRef = useRef<CompactToolWheelPointerState | null>(null);
  const compactInputToolWheelSuppressClickRef = useRef(false);
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
  const composerLayoutRef = useRef<ComposerLayout>('expanded');
  const overflowMenuRef = useRef<HTMLDivElement | null>(null);
  const compactHistoryDesktopDropTargetRef = useRef<{ sessionId?: string; overTarget: boolean; timestamp: number } | null>(null);
  const draftRef = useRef(visibleDraft);
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
  const [compactPreviewTextVisible, setCompactPreviewTextVisible] = useState('');
  const [compactSpeechVisibleLength, setCompactSpeechVisibleLength] = useState(0);
  const [compactSpeechFallbackRevealActive, setCompactSpeechFallbackRevealActive] = useState(false);
  const [speechPlaybackState, setSpeechPlaybackState] = useState<SpeechPlaybackState | null>(null);
  // Full Chat owns its focus-state subscription because it is mounted as a
  // separate surface and does not receive Compact Chat's local focus state.
  const [focusActive, setFocusActive] = useState(false);
  // 凝神 thinking-dots pulse — mirrors the compact surface (App.tsx).
  const [focusThinking, setFocusThinking] = useState(false);
  const [compactChoiceLayerPlacement, setCompactChoiceLayerPlacement] = useState<'above' | 'below'>('above');
  const [compactInputToolFanOpen, setCompactInputToolFanOpen] = useState(false);
  const [compactInputToolFanInteractive, setCompactInputToolFanInteractive] = useState(false);
  const [compactInputToolWheelIndex, setCompactInputToolWheelIndex] = useState(0);
  const [compactInputToolWheelDragActive, setCompactInputToolWheelDragActive] = useState(false);
  const [compactInputToolWheelDragOffsetRatio, setCompactInputToolWheelDragOffsetRatio] = useState(0);
  const [compactSurfaceResizeWidth, setCompactSurfaceResizeWidth] = useState<number | null>(null);
  const [compactExportHistoryOpen, setCompactExportHistoryOpen] = useState(readPersistedCompactExportHistoryOpen);
  const [compactExportPreviewOpen, setCompactExportPreviewOpen] = useState(false);
  const [compactExportSelectedIds, setCompactExportSelectedIds] = useState<Set<string>>(() => new Set());
  const [compactExportAutoScrollToBottom, setCompactExportAutoScrollToBottom] = useState(true);
  const compactSurfaceResizeStateRef = useRef<CompactSurfaceResizeState | null>(null);
  const submittingRef = useRef(false);
  const lastRollbackKeyRef = useRef('');
  const compactInputHasPayload = visibleDraft.trim().length > 0
    || (!catLocalTextOnly && composerAttachments.length > 0);
  const composerInteractionsDisabled = composerDisabled || composerHidden;
  const canSubmit = !composerInteractionsDisabled && compactInputHasPayload;
  const guideChatButtonsLocked = useGuideChatButtonLock();
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
  const effectiveToolVariant = avatarToolRuntime.effectiveVariant;
  const clearAvatarTool = avatarToolRuntime.clearTool;
  const selectAvatarTool = avatarToolRuntime.selectTool;

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
  const compactExportHistoryButtonLabel = i18n('chat.compactExportHistory', 'History');
  // ChoicePrompt and galgame options share the same composer-anchored slot.
  // The transient invite should win when both are present so we do not stack
  // two button groups in the same compact surface.
  const compactChoiceInteractionsAllowed = !composerHidden && !catLocalTextOnly;
  const choicePromptHasOptions = compactChoiceInteractionsAllowed
    && !!(choicePrompt && choicePrompt.options.length > 0);
  const galgameOptionsVisible =
    compactChoiceInteractionsAllowed && galgameModeEnabled && !choicePromptHasOptions
    && (galgameOptionsLoading || galgameOptions.length > 0);
  const compactSurfaceChoicesVisible = choicePromptHasOptions || galgameOptionsVisible;
  const isCompactSurface = chatSurfaceMode === 'compact';
  const requestedCompactChatState = isCompactSurface && composerHidden && compactChatState === 'input'
    ? 'default'
    : compactChatState;
  const effectiveCompactChatState = isCompactSurface
    ? getEffectiveCompactChatState(requestedCompactChatState, compactSurfaceChoicesVisible)
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
  const compactChoiceLayerOpen = !isCompactSurface
    ? compactSurfaceChoicesVisible
    : effectiveCompactChatState === 'options';
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
    if (!isCompactSurface) {
      onExportConversationClick?.();
      return;
    }
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
  }, [isCompactSurface, onExportConversationClick]);
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
  const compactEmptyStateText = composerHidden
    ? i18n('chat.companionEmptyState', getChatCompanionEmptyStateFallback())
    : i18n('chat.emptyState', getChatEmptyStateFallback());
  const compactPreviewText = compactSpeechModeActive
    ? (
      compactMessagePreview?.isStreaming
        ? compactMessagePreview?.fullText || ''
        : compactSpeechPreservedText || compactMessagePreview?.fullText || ''
    )
    : compactMessagePreview?.text
    || compactEmptyStateText;
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
  const clearAvatarToolAriaLabel = i18n('chat.clearAvatarToolAriaLabel', '取消道具');
  const overflowMenuAriaLabel = i18n('chat.composerOverflowMenu', '更多工具');
  const activeToolMenuVisual = activeToolItem
    ? resolveAvatarToolMenuIconVisual(activeToolItem, effectiveToolVariant)
    : null;
  const activeToolLabel = activeToolItem ? getToolItemLabel(activeToolItem) : '';
  const selectedEmojiButtonAriaLabel = activeToolItem
    ? `${emojiButtonAriaLabel}: ${activeToolLabel}`
    : emojiButtonAriaLabel;

  useEffect(() => {
    draftRef.current = visibleDraft;
  }, [visibleDraft]);

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

  // Focus 凝神 indicator: reflect backend enter/exit. Mirrors the compact
  // surface's subscription (App.tsx) — app-websocket.js translates the
  // `focus_state` ws message into this `neko-focus-state` event.
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

  // 凝神 thinking-dots: show a "…" bubble at the tail of the history while a
  // Focus turn is thinking-on but hasn't emitted visible content yet.
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
    const textNode = compactPreviewTextRef.current;
    if (!textNode) return;
    if (!isCompactSurface || !compactPreviewIsStreaming) {
      textNode.scrollLeft = 0;
      return;
    }
    textNode.scrollLeft = textNode.scrollWidth;
  }, [compactPreviewDisplayText, compactPreviewIsStreaming, isCompactSurface]);

  useEffect(() => {
    if (!isCompactSurface) return;
    if (effectiveCompactChatState !== 'input') return;
    if (composerInteractionsDisabled) return;
    const inputNode = compactInputRef.current;
    if (!inputNode) return;
    if (document.activeElement === inputNode) return;
    inputNode.focus();
    const selectionEnd = inputNode.value.length;
    inputNode.setSelectionRange(selectionEnd, selectionEnd);
  }, [composerInteractionsDisabled, effectiveCompactChatState, isCompactSurface]);

  useEffect(() => {
    if (!isCompactSurface) return;
    if (!compactChoiceLayerOpen) return;

    const shellNode = appShellRef.current;
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

      const surfaceScreenTop = windowY + shellRect.top;
      const surfaceScreenBottom = windowY + shellRect.bottom;
      const workAreaBottom = workAreaY + workAreaHeight;
      return {
        availableAbove: Math.max(0, surfaceScreenTop - workAreaY),
        availableBelow: Math.max(0, workAreaBottom - surfaceScreenBottom),
      };
    };

    const updatePlacement = () => {
      const nextShellNode = appShellRef.current;
      const nextLayerNode = compactChoiceLayerRef.current;
      if (!nextShellNode || !nextLayerNode) return;

      const shellRect = nextShellNode.getBoundingClientRect();
      const layerRect = nextLayerNode.getBoundingClientRect();
      const layerHeight = Math.max(layerRect.height, nextLayerNode.scrollHeight);
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      const desktopSpace = getDesktopPlacementSpace(shellRect);
      const desktopForcedPlacement = ((window as typeof window & {
        __nekoDesktopCompactLayout?: DesktopCompactChoicePlacementLayout | null;
      }).__nekoDesktopCompactLayout?.compactChoicePlacement);
      if (desktopForcedPlacement === 'above' || desktopForcedPlacement === 'below') {
        setCompactChoiceLayerPlacement(current => (current === desktopForcedPlacement ? current : desktopForcedPlacement));
        return;
      }
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
    // Surface/desktop layout moves (avatar drag, window move, work-area change)
    // have no element-level signal a ResizeObserver could catch, so listen for
    // the host's layout-change events instead of polling every frame. Mirrors the
    // event-driven compact placement effect in App.tsx (CompactChatApp).
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
    onCompactChatStateChange?.(nextState);
  }, [isCompactSurface, onCompactChatStateChange]);

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
    compactInputToolFanOpenIntentRef.current = null;
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
  }, [clearCompactInputToolFanCloseTimer, clearCompactInputToolFanInteractiveTimer, setCompactInputToolFanInteractiveState]);

  const updateCompactInputToolFanPosition = useCallback(() => {}, []);

  const scheduleCompactInputToolFanTransientClose = useCallback(() => {
    if (compactInputToolFanOpenIntentRef.current !== 'hover') return;
    clearCompactInputToolFanCloseTimer();
    compactInputToolFanCloseTimerRef.current = window.setTimeout(() => {
      compactInputToolFanCloseTimerRef.current = null;
      closeCompactInputToolFan();
    }, 160);
  }, [clearCompactInputToolFanCloseTimer, closeCompactInputToolFan]);

  const openCompactInputToolFan = useCallback((intent: 'click' | 'hover') => {
    if (catLocalTextOnly || composerInteractionsDisabled || compactInputHasPayload) return;
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
    catLocalTextOnly,
    compactInputHasPayload,
    composerInteractionsDisabled,
    setCompactInputToolFanInteractiveState,
    updateCompactInputToolFanPosition,
  ]);

  const shouldOpenCompactToolFanOnHover = useCallback((event: ReactPointerEvent) => {
    return event.pointerType === 'mouse';
  }, []);

  const isCompactInputToolPointerInHoverRegion = useCallback((clientX: number, clientY: number, relatedTarget?: EventTarget | null) => {
    if (relatedTarget instanceof Node) {
      if (compactInputToolToggleRef.current?.contains(relatedTarget)) return true;
      if (compactInputToolFanRef.current?.contains(relatedTarget)) return true;
    }
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return false;
    const rects = [
      compactInputToolToggleRef.current?.getBoundingClientRect(),
      compactInputToolFanRef.current?.getBoundingClientRect(),
    ];
    return rects.some(rect => (
      !!rect
      && rect.width > 0
      && rect.height > 0
      && clientX >= rect.left
      && clientX <= rect.right
      && clientY >= rect.top
      && clientY <= rect.bottom
    ));
  }, []);

  const handleCompactInputToolHoverEnter = useCallback((event: ReactPointerEvent) => {
    if (!shouldOpenCompactToolFanOnHover(event)) return;
    if (compactInputToolFanSuppressHoverUntilLeaveRef.current) return;
    if (compactInputToolFanHoverInsideRef.current) return;
    compactInputToolFanHoverInsideRef.current = true;
    openCompactInputToolFan('hover');
  }, [openCompactInputToolFan, shouldOpenCompactToolFanOnHover]);

  const handleCompactInputToolHoverLeave = useCallback((event: ReactPointerEvent) => {
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
    playCompactToolWheelDetentSound();
  }, []);

  const getCompactToolWheelDragAngle = useCallback((clientX: number, clientY: number): number | null => {
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
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
    const deltaX = clientX - centerX;
    const deltaY = clientY - centerY;
    if (Math.hypot(deltaX, deltaY) < COMPACT_INPUT_TOOL_WHEEL_ANGLE_MIN_RADIUS) return null;
    return Math.atan2(deltaY, deltaX);
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
  }, [clearCompactInputToolFanCloseTimer, clearCompactInputToolFanInteractiveTimer]);

  useEffect(() => {
    if (!isCompactSurface || effectiveCompactChatState !== 'input') {
      resetCompactInputToolFanHoverBlock();
      return;
    }

    const handlePointerMove = (event: PointerEvent) => {
      if (!compactInputToolFanSuppressHoverUntilLeaveRef.current) return;
      if (isCompactInputToolPointerInHoverRegion(event.clientX, event.clientY, event.target)) return;
      resetCompactInputToolFanHoverBlock();
    };

    window.addEventListener('pointermove', handlePointerMove, true);
    return () => {
      window.removeEventListener('pointermove', handlePointerMove, true);
    };
  }, [
    effectiveCompactChatState,
    isCompactInputToolPointerInHoverRegion,
    isCompactSurface,
    resetCompactInputToolFanHoverBlock,
  ]);

  const finishCompactToolWheelPointer = useCallback((event?: ReactPointerEvent<HTMLDivElement>) => {
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
    compactInputToolWheelPointerRef.current = null;
    setCompactInputToolWheelDragActive(false);
    setCompactInputToolWheelDragOffsetRatio(0);
  }, []);

  useEffect(() => {
    compactInputToolFanPositionSyncRef.current = () => updateCompactInputToolFanPosition();
    return () => {
      compactInputToolFanPositionSyncRef.current = null;
    };
  }, [updateCompactInputToolFanPosition]);

  useEffect(() => () => {
    clearCompactInputToolFanCloseTimer();
  }, [clearCompactInputToolFanCloseTimer]);

  const handleComposerBottomBarRef = useCallback((node: HTMLDivElement | null) => {
    composerBottomBarRef.current = node;
    setComposerBottomBarNode(prev => (prev === node ? prev : node));
  }, []);

  const collapseCompactInputIfEmpty = useCallback((options?: { ignoreFocusedShell?: boolean }) => {
    if (!isCompactSurface) return;
    if (effectiveCompactChatState !== 'input') return;
    if (compactInputToolFanOpen) return;
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
    if (!isCompactSurface || effectiveCompactChatState !== 'input' || composerInteractionsDisabled || compactInputHasPayload) {
      closeCompactInputToolFan();
    }
  }, [
    closeCompactInputToolFan,
    compactInputHasPayload,
    compactInputToolFanOpen,
    composerInteractionsDisabled,
    effectiveCompactChatState,
    isCompactSurface,
  ]);

  useEffect(() => {
    if (!catLocalTextOnly) return;
    closeCompactInputToolFan();
    setToolMenuOpen(false);
    setOverflowMenuOpen(false);
    setCompactExportPreviewOpen(false);
    clearAvatarTool();
  }, [catLocalTextOnly, clearAvatarTool, closeCompactInputToolFan]);

  useEffect(() => {
    if (!catLocalTextOnly) setCatDraft('');
  }, [catLocalTextOnly]);

  useEffect(() => {
    if (compactInputToolFanOpen) return;
    clearCompactInputToolFanCloseTimer();
    compactInputToolFanOpenIntentRef.current = null;
    compactInputToolWheelPointerRef.current = null;
    compactInputToolWheelSuppressClickRef.current = false;
    setCompactInputToolWheelDragActive(false);
    setCompactInputToolWheelDragOffsetRatio(0);
  }, [clearCompactInputToolFanCloseTimer, compactInputToolFanOpen]);

  useEffect(() => {
    if (!isCompactSurface) return;

    const handleDesktopCompactPointerOutside = () => {
      resetCompactInputToolFanHoverBlock();
      closeCompactInputToolFan();
      if (effectiveCompactChatState !== 'input') return;
      if (draftRef.current.trim().length > 0) return;
      if (composerAttachments.length > 0) return;
      requestCompactChatState('default');
    };

    window.addEventListener('neko:desktop-compact-pointer-outside', handleDesktopCompactPointerOutside);
    return () => {
      window.removeEventListener('neko:desktop-compact-pointer-outside', handleDesktopCompactPointerOutside);
    };
  }, [
    closeCompactInputToolFan,
    composerAttachments.length,
    effectiveCompactChatState,
    isCompactSurface,
    requestCompactChatState,
    resetCompactInputToolFanHoverBlock,
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
    composerLayoutRef.current = composerLayout;
  }, [composerLayout]);

  useEffect(() => {
    const target = composerBottomBarNode;
    if (!target || typeof ResizeObserver === 'undefined') return;
    const COMPACT_THRESHOLD = 300;
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const wantCompact = entry.contentRect.width < COMPACT_THRESHOLD;
        // 鍦?expanded 鈫?collapsing 杩欎竴鍒绘姄涓€涓嬪彸 4 鎸夐挳缁勭殑褰撳墠鍍忕礌瀹藉害锛?        // 鍚屼竴鎵?setState 浼氬拰 layout 鍒囨崲涓€璧?commit锛宺ender 鍑烘潵鏃?        // .is-leaving 绫诲拰 --collapse-from-width 鍙橀噺鍚屾椂鐢熸晥锛?        // CSS keyframe 灏辫兘浠庤繖涓浐瀹氬搴︽彃鍊煎埌 0銆?        // 鐢?offsetWidth 鑰岄潪 getBoundingClientRect().width锛氬墠鑰呭熀浜庡竷灞€鐩掞紝
        // 涓嶅彈鍏ュ満 scaleX 鍔ㄧ敾褰卞搷锛涘鏋?expand 鍔ㄧ敾杩樻病璺戝畬灏卞張琚帇绐勶紝
        if (wantCompact && composerLayoutRef.current === 'expanded' && composerToolsRightRef.current) {
          const node = composerToolsRightRef.current;
          const w = Math.max(node.offsetWidth, node.scrollWidth);
          if (w > 0) setCollapseFromWidth(w);
        }
        setComposerLayout(prev => {
          if (wantCompact) {
            if (prev === 'expanded') return 'collapsing';
            if (prev === 'expanding') return 'compact';
            return prev;
          } else {
            if (prev === 'compact') return 'expanding';
            if (prev === 'collapsing') return 'expanded';
            return prev;
          }
        });
      }
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, [composerBottomBarNode]);

  useEffect(() => {
    if (isCompactSurface) {
      setOverflowMenuOpen(false);
      return;
    }
    if (!composerBottomBarNode) return;
    if (composerBottomBarNode.getBoundingClientRect().width >= 300) {
      setComposerLayout(prev => (
        prev === 'compact' || prev === 'collapsing' ? 'expanded' : prev
      ));
    }
  }, [composerBottomBarNode, isCompactSurface]);

  // 鏀惰捣/灞曞紑鍔ㄧ敾璺戝畬鍚庡垏鍒扮ǔ鎬併€傛椂闀块渶涓?styles.css 涓殑 keyframes 瀵归綈銆?  // prefers-reduced-motion 涓?styles.css 鎶婂姩鐢昏鎴?none锛岃繖鏃惰繕绛?270/220ms
  // 浼氳宸ュ叿鍖烘粸鐣欏湪杩囨浮鎬侊紙鎺т欢瑙嗚涓婃彁鍓嶅埌浣嶄絾 layout state 娌″垏锛夛紝
  useEffect(() => {
    const prefersReducedMotion =
      typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (composerLayout === 'collapsing') {
      const timerId = window.setTimeout(() => {
        setComposerLayout(prev => (prev === 'collapsing' ? 'compact' : prev));
      }, prefersReducedMotion ? 0 : 270);
      return () => window.clearTimeout(timerId);
    }
    if (composerLayout === 'expanding') {
      const timerId = window.setTimeout(() => {
        setComposerLayout(prev => (prev === 'expanding' ? 'expanded' : prev));
      }, prefersReducedMotion ? 0 : 220);
      return () => window.clearTimeout(timerId);
    }
    return undefined;
  }, [composerLayout]);

  useEffect(() => {
    if (composerLayout !== 'compact') setOverflowMenuOpen(false);
  }, [composerLayout]);

  // 路路路 鑿滃崟鐨勫閮ㄧ偣鍑?/ Esc 鍏抽棴
  useEffect(() => {
    if (!overflowMenuOpen) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      const node = overflowMenuRef.current;
      if (!node) return;
      if (node.contains(event.target as Node)) return;
      setOverflowMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOverflowMenuOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [overflowMenuOpen]);

  function restoreCompactExportHistoryToBottomForOutgoingMessage() {
    if (compactExportHistoryOpen) {
      setCompactExportAutoScrollToBottom(true);
    }
  }

  function submitDraft() {
    if (composerInteractionsDisabled) return;
    if (submittingRef.current) return;
    const text = visibleDraft.trim();
    if (!text && (catLocalTextOnly || composerAttachments.length === 0)) return;
    closeCompactInputToolFan();
    submittingRef.current = true;
    try {
      onComposerSubmit?.({ text });
      if (catLocalTextOnly) {
        setCatDraft('');
      } else {
        setDraft('');
      }
      restoreCompactExportHistoryToBottomForOutgoingMessage();
      if (!catLocalTextOnly) {
        requestCompactChatState('default');
      }
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

  const translateButtonNode = (
    <button
      className={`composer-tool-btn composer-translate-btn${translateEnabled ? ' is-active' : ''}`}
      type="button"
      aria-label={resolvedTranslateAriaLabel}
      aria-pressed={translateEnabled}
      data-neko-tooltip={translateButtonLabel}
      disabled={composerInteractionsDisabled}
      onClick={() => onTranslateToggle?.()}
    >
      <img src="/static/icons/translate_icon.png" alt="" aria-hidden="true" />
    </button>
  );

  const jukeboxButtonNode = (
    <button
      className="composer-tool-btn"
      type="button"
      aria-label={jukeboxButtonAriaLabel}
      data-neko-tooltip={jukeboxButtonLabel}
      disabled={composerInteractionsDisabled}
      onClick={() => onJukeboxClick?.()}
    >
      <img src="/static/icons/jukebox_icon.png" alt="" aria-hidden="true" />
    </button>
  );

  const galgameToggleButtonNode = (
    <button
      className={`composer-tool-btn composer-galgame-btn${galgameModeEnabled ? ' is-active' : ''}`}
      type="button"
      aria-label={resolvedGalgameAriaLabel}
      aria-pressed={galgameModeEnabled}
      data-neko-tooltip={galgameToggleButtonLabel}
      disabled={composerInteractionsDisabled}
      onClick={() => onGalgameModeToggle?.()}
    >
      <span className="composer-galgame-btn-glyph" aria-hidden="true">G</span>
    </button>
  );

  const emojiToolMenuNode = (
    <div className="composer-tool-menu" ref={toolMenuRef}>
      <button
        className={`composer-tool-btn composer-emoji-btn${toolMenuOpen || activeToolItem ? ' is-active' : ''}`}
        type="button"
        aria-label={selectedEmojiButtonAriaLabel}
        data-neko-tooltip={selectedEmojiButtonAriaLabel}
        aria-controls={toolMenuOpen ? 'composer-tool-popover' : undefined}
        aria-expanded={toolMenuOpen}
        disabled={composerInteractionsDisabled}
        onClick={() => {
          if (activeToolItem) {
            clearAvatarTool();
            return;
          }
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
          aria-label={clearAvatarToolAriaLabel}
          data-neko-tooltip={clearAvatarToolAriaLabel}
          disabled={composerInteractionsDisabled}
          onClick={(event) => {
            event.stopPropagation();
            clearAvatarTool();
          }}
        >
          <span className="composer-tool-clear-icon" aria-hidden="true" />
        </button>
      ) : null}
      {toolMenuOpen ? (
        <div
          id="composer-tool-popover"
          className="composer-icon-popover"
          role="group"
          aria-label={toolIconsAriaLabel}
        >
          {toolIconItems.map(item => {
            const itemLabel = getToolItemLabel(item);
            const menuVariant = activeAvatarToolId === item.id
              ? effectiveToolVariant
              : 'primary';
            const menuVisual = resolveAvatarToolMenuIconVisual(item, menuVariant);
            return (
            <button
              key={item.id}
              className={`composer-icon-button${activeAvatarToolId === item.id ? ' is-active' : ''}`}
              type="button"
              aria-pressed={activeAvatarToolId === item.id}
              aria-label={itemLabel}
              data-neko-tooltip={itemLabel}
              disabled={composerInteractionsDisabled}
              onClick={(event) => {
                selectAvatarTool(item, event);
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
  );

  const compactFanCloseOnAction = (
    action: (() => void) | undefined,
    options?: { deferDesktopAction?: boolean },
  ) => (event: ReactMouseEvent) => {
    if (shouldSuppressCompactToolClick(event)) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    closeCompactInputToolFan({
      afterClose: action,
      deferDesktopAction: options?.deferDesktopAction,
    });
  };

  const compactFanToggleOnAction = (action: (() => void) | undefined) => (event: ReactMouseEvent) => {
    if (shouldSuppressCompactToolClick(event)) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
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
    const slot = getCompactToolWheelSlot(toolIndex);
    return slot === null ? 'hidden' : String(slot);
  };

  const compactInputToolFanActionsDisabled = composerInteractionsDisabled
    || !compactInputToolFanOpen
    || !compactInputToolFanInteractive;
  const isCompactToolWheelActionDisabled = (toolIndex: number): boolean => (
    compactInputToolFanActionsDisabled || !isCompactToolWheelActionable(toolIndex)
  );
  const compactInputToolWheelDragAngle = compactInputToolWheelDragOffsetRatio * COMPACT_TOOL_WHEEL_DRAG_ANGLE_STEP_DEG;
  const compactInputToolWheelDragStyle = {
    '--compact-tool-wheel-drag-angle': `${compactInputToolWheelDragAngle}deg`,
    '--compact-tool-wheel-drag-counter-angle': `${-compactInputToolWheelDragAngle}deg`,
  } as CSSProperties;

  const compactInputToolFanNode = !catLocalTextOnly
    && isCompactSurface
    && effectiveCompactChatState === 'input' ? (
    <div
      ref={compactInputToolFanRef}
      className="compact-input-tool-fan"
      style={compactInputToolWheelDragStyle}
      role="group"
      aria-label={overflowMenuAriaLabel}
      data-compact-geometry-item="toolFan"
      data-compact-geometry-owner="surface"
      data-compact-input-tool-fan-open={compactInputToolFanOpen ? 'true' : 'false'}
      data-compact-input-tool-fan-interactive={compactInputToolFanInteractive ? 'true' : 'false'}
      data-compact-tool-wheel-drag-active={compactInputToolWheelDragActive ? 'true' : 'false'}
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
        compactInputToolWheelSuppressClickRef.current = false;
        setCompactInputToolWheelDragActive(true);
        setCompactInputToolWheelDragOffsetRatio(0);
        compactInputToolWheelPointerRef.current = {
          id: event.pointerId,
          x: event.clientX,
          y: event.clientY,
          angle: getCompactToolWheelDragAngle(event.clientX, event.clientY),
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
        const pointerState = compactInputToolWheelPointerRef.current;
        if (!pointerState || pointerState.id !== event.pointerId) return;
        if (event.pointerType === 'mouse' && event.buttons === 0) {
          finishCompactToolWheelPointer(event);
          return;
        }
        const nextAngle = getCompactToolWheelDragAngle(event.clientX, event.clientY);
        if (pointerState.angle !== null && nextAngle !== null) {
          const angleStepRad = COMPACT_TOOL_WHEEL_DRAG_ANGLE_STEP_DEG * (Math.PI / 180);
          const angleDelta = normalizeCompactToolWheelAngleDelta(nextAngle - pointerState.angle);
          const totalDelta = pointerState.angleRemainder + angleDelta;
          const totalOffsetRatio = totalDelta / angleStepRad;
          const stepCount = getCompactToolWheelDetentStepCount(totalOffsetRatio);
          pointerState.x = event.clientX;
          pointerState.y = event.clientY;
          pointerState.angle = nextAngle;
          if (stepCount <= 0) {
            pointerState.angleRemainder = totalDelta;
            const dragOffsetRatio = clamp(
              getCompactToolWheelDetentDisplayRatio(totalOffsetRatio),
              -0.98,
              0.98,
            );
            pointerState.dragOffsetRatio = dragOffsetRatio;
            setCompactInputToolWheelDragOffsetRatio(dragOffsetRatio);
            return;
          }
          event.preventDefault();
          const direction: 1 | -1 = totalDelta > 0 ? 1 : -1;
          for (let step = 0; step < stepCount; step += 1) {
            rotateCompactInputToolWheel(direction);
          }
          pointerState.angleRemainder = totalDelta - (direction * stepCount * angleStepRad);
          const remainingOffsetRatio = pointerState.angleRemainder / angleStepRad;
          const dragOffsetRatio = clamp(
            getCompactToolWheelDetentDisplayRatio(remainingOffsetRatio),
            -0.98,
            0.98,
          );
          pointerState.dragOffsetRatio = dragOffsetRatio;
          setCompactInputToolWheelDragOffsetRatio(dragOffsetRatio);
          pointerState.didRotate = true;
          return;
        }
        const deltaX = event.clientX - pointerState.x;
        const linearOffsetRatio = -deltaX / COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD;
        const stepCount = getCompactToolWheelDetentStepCount(linearOffsetRatio);
        if (stepCount <= 0) {
          pointerState.angle = nextAngle;
          const dragOffsetRatio = clamp(
            getCompactToolWheelDetentDisplayRatio(linearOffsetRatio),
            -0.98,
            0.98,
          );
          pointerState.dragOffsetRatio = dragOffsetRatio;
          setCompactInputToolWheelDragOffsetRatio(dragOffsetRatio);
          return;
        }
        event.preventDefault();
        const direction = deltaX < 0 ? 1 : -1;
        for (let step = 0; step < stepCount; step += 1) {
          rotateCompactInputToolWheel(direction);
        }
        const consumedDelta = direction === 1
          ? -(stepCount * COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD)
          : stepCount * COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD;
        pointerState.x += consumedDelta;
        pointerState.y = event.clientY;
        pointerState.angle = nextAngle;
        pointerState.angleRemainder = 0;
        const remainingDelta = deltaX - consumedDelta;
        const remainingOffsetRatio = -remainingDelta / COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD;
        const dragOffsetRatio = clamp(
          getCompactToolWheelDetentDisplayRatio(remainingOffsetRatio),
          -0.98,
          0.98,
        );
        pointerState.dragOffsetRatio = dragOffsetRatio;
        setCompactInputToolWheelDragOffsetRatio(dragOffsetRatio);
        pointerState.didRotate = true;
      }}
      onPointerUp={(event) => {
        finishCompactToolWheelPointer(event);
      }}
      onPointerCancel={(event) => {
        finishCompactToolWheelPointer(event);
      }}
      onLostPointerCapture={(event) => {
        finishCompactToolWheelPointer(event);
      }}
    >
      <div className="compact-input-tool-wheel-selection-pointer" aria-hidden="true" />
      <button
        className="composer-tool-btn compact-input-tool-item compact-input-tool-item-import"
        type="button"
        aria-label={resolvedImportImageAriaLabel}
        data-neko-tooltip={importImageButtonLabel}
        disabled={isCompactToolWheelActionDisabled(0)}
        tabIndex={getCompactToolWheelTabIndex(0)}
        aria-hidden={getCompactToolWheelAriaHidden(0)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(0)}
        onClick={compactFanCloseOnAction(onComposerImportImage)}
      >
        <img src="/static/icons/import_image_icon.png" alt="" aria-hidden="true" />
      </button>
      <button
        className="composer-tool-btn compact-input-tool-item compact-input-tool-item-screenshot"
        type="button"
        aria-label={resolvedScreenshotAriaLabel}
        data-neko-tooltip={screenshotButtonLabel}
        disabled={isCompactToolWheelActionDisabled(1)}
        tabIndex={getCompactToolWheelTabIndex(1)}
        aria-hidden={getCompactToolWheelAriaHidden(1)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(1)}
        onClick={compactFanCloseOnAction(onComposerScreenshot)}
      >
        <img src="/static/icons/screenshot_new_icon.png" alt="" aria-hidden="true" />
      </button>
      <button
        className={`composer-tool-btn composer-galgame-btn compact-input-tool-item compact-input-tool-item-galgame${galgameModeEnabled ? ' is-active' : ''}`}
        type="button"
        aria-label={resolvedGalgameAriaLabel}
        aria-pressed={galgameModeEnabled}
        data-neko-tooltip={galgameToggleButtonLabel}
        disabled={isCompactToolWheelActionDisabled(2)}
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
        data-neko-tooltip={translateButtonLabel}
        disabled={isCompactToolWheelActionDisabled(3)}
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
        data-neko-tooltip={jukeboxButtonLabel}
        disabled={isCompactToolWheelActionDisabled(4)}
        tabIndex={getCompactToolWheelTabIndex(4)}
        aria-hidden={getCompactToolWheelAriaHidden(4)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(4)}
        onClick={compactFanCloseOnAction(onJukeboxClick)}
      >
        <img src="/static/icons/jukebox_icon.png" alt="" aria-hidden="true" />
      </button>
      <button
        className={`composer-tool-btn compact-input-tool-item compact-input-tool-item-export${compactExportHistoryOpen ? ' is-active' : ''}`}
        type="button"
        aria-label={compactExportHistoryButtonLabel}
        aria-pressed={compactExportHistoryOpen}
        data-neko-tooltip={compactExportHistoryButtonLabel}
        disabled={isCompactToolWheelActionDisabled(5)}
        tabIndex={getCompactToolWheelTabIndex(5)}
        aria-hidden={getCompactToolWheelAriaHidden(5)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(5)}
        data-compact-tool-active={compactExportHistoryOpen ? 'true' : 'false'}
        onClick={compactFanCloseOnAction(handleCompactExportConversationClick, { deferDesktopAction: true })}
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
      >
        <button
          className={`composer-tool-btn composer-emoji-btn${toolMenuOpen || activeToolItem ? ' is-active' : ''}`}
          type="button"
          aria-label={selectedEmojiButtonAriaLabel}
          data-neko-tooltip={selectedEmojiButtonAriaLabel}
          aria-controls={toolMenuOpen ? 'composer-tool-popover-compact' : undefined}
          aria-expanded={toolMenuOpen}
          disabled={isCompactToolWheelActionDisabled(6)}
          tabIndex={getCompactToolWheelTabIndex(6)}
          onClick={(event) => {
            if (shouldSuppressCompactToolClick(event)) {
              event.preventDefault();
              event.stopPropagation();
              return;
            }
            if (activeToolItem) {
              clearAvatarTool();
              closeCompactInputToolFanFromUserClick();
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
            aria-label={clearAvatarToolAriaLabel}
            data-neko-tooltip={clearAvatarToolAriaLabel}
            disabled={compactInputToolFanActionsDisabled}
            tabIndex={compactInputToolFanOpen ? 0 : -1}
            onClick={(event) => {
              if (shouldSuppressCompactToolClick(event)) {
                event.preventDefault();
                event.stopPropagation();
                return;
              }
              event.stopPropagation();
              clearAvatarTool();
              closeCompactInputToolFanFromUserClick();
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
            const menuVariant = activeAvatarToolId === item.id
              ? effectiveToolVariant
              : 'primary';
            const menuVisual = resolveAvatarToolMenuIconVisual(item, menuVariant);
            return (
            <button
              key={item.id}
              className={`composer-icon-button${activeAvatarToolId === item.id ? ' is-active' : ''}`}
              type="button"
              aria-pressed={activeAvatarToolId === item.id}
              aria-label={itemLabel}
              data-neko-tooltip={itemLabel}
              disabled={compactInputToolFanActionsDisabled}
              onClick={(event) => {
                if (shouldSuppressCompactToolClick(event)) {
                  event.preventDefault();
                  event.stopPropagation();
                  return;
                }
                selectAvatarTool(item, event);
                setToolMenuOpen(false);
                closeCompactInputToolFanFromUserClick();
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
                    data-neko-tooltip={option.text}
                    disabled={composerInteractionsDisabled || galgameOptionsLoading}
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
              : choicePrompt.source === 'new_user_icebreaker'
                ? i18n('chat.newUserIcebreakerOptionsAriaLabel', 'New user icebreaker options')
              : galgameToggleButtonLabel}
          >
            {choicePrompt.options.slice(0, 3).map((option, index) => (
              <button
                key={`${index}-${option.choice}`}
                type="button"
                className="composer-galgame-option composer-choice-option"
                data-neko-tooltip={option.label}
                disabled={composerInteractionsDisabled}
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
  const compactChoiceLayerNode = isCompactSurface && !catLocalTextOnly
    ? (typeof document !== 'undefined' ? createPortal(choiceLayerNode, document.body) : choiceLayerNode)
    : null;

  const messageListNode = (
    <MessageList
      messages={messages}
      ariaLabel={messageListAriaLabel}
      failedStatusLabel={failedStatusLabel}
      thinking={focusThinking}
      onAction={onMessageAction}
    />
  );
  const compactExportHistoryElement = isCompactSurface && compactExportHistoryOpen ? (
    <CompactExportHistoryPanel
      messages={messages}
      selectedIds={compactExportSelectedIds}
      selectedCount={compactExportSelectedCount}
      selectableCount={compactExportSelectableCount}
      autoScrollToBottom={compactExportAutoScrollToBottom}
      previewOpen={compactExportPreviewOpen}
      controlsOpen={false}
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
  ) : (
    <section className="chat-body">
      {messageListNode}
    </section>
  );
  const shouldRenderComposerPanel = isCompactSurface || !composerHidden;

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
      data-focus-active={focusActive ? 'true' : 'false'}
    >
      {focusActive ? (
        <div
          className="chat-surface-focus-indicator"
          role="status"
          aria-live="polite"
          data-neko-tooltip={i18n('chat.focusIndicator', '凝神中')}
        >
          <span className="chat-surface-focus-indicator-label">
            {i18n('chat.focusIndicator', '凝神中')}
          </span>
        </div>
      ) : null}
      <div className="chat-focus-overlay" aria-hidden="true" />
      {compactExportHistoryNode}
      {compactChoiceLayerNode}
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
          <div id="music-player-mount" />
          {!catLocalTextOnly && composerAttachments.length > 0 ? (
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
                    aria-disabled={composerInteractionsDisabled}
                    disabled={composerInteractionsDisabled}
                    onClick={() => {
                      if (!composerInteractionsDisabled) {
                        onComposerRemoveAttachment?.(attachment.id);
                      }
                    }}
                  />
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
                  data-compact-geometry-hit-scope={effectiveCompactChatState === 'input' ? 'children' : undefined}
                >
                  {effectiveCompactChatState === 'input' ? (
                    <>
                      <textarea
                        className="composer-input"
                        ref={compactInputRef}
                        data-compact-hit-region="true"
                        data-compact-hit-region-id="input:text"
                        data-compact-hit-region-kind="input-text"
                        placeholder={inputPlaceholder}
                        aria-label={inputPlaceholder}
                        rows={1}
                        value={visibleDraft}
                        readOnly={composerInteractionsDisabled}
                        disabled={composerInteractionsDisabled}
                        onChange={(event) => {
                          if (catLocalTextOnly) {
                            setCatDraft(event.target.value);
                          } else {
                            setDraft(event.target.value);
                          }
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
                      {(!catLocalTextOnly || compactInputHasPayload) ? <button
                        className={`send-button-circle compact-input-tool-toggle${compactInputToolFanOpen ? ' is-open' : ''}`}
                        ref={compactInputToolToggleRef}
                        type={compactInputHasPayload ? 'submit' : 'button'}
                        data-compact-hit-region="true"
                        data-compact-hit-region-id="input:tool-toggle"
                        data-compact-hit-region-kind="input-tool-toggle"
                        aria-label={compactInputHasPayload ? sendButtonLabel : overflowMenuAriaLabel}
                        aria-haspopup={compactInputHasPayload ? undefined : 'true'}
                        aria-expanded={compactInputHasPayload ? undefined : compactInputToolFanOpen}
                        disabled={compactInputHasPayload ? !canSubmit : composerInteractionsDisabled}
                        onPointerDown={compactInputHasPayload ? undefined : (event) => {
                          event.preventDefault();
                          compactInputToolTogglePointerHandledRef.current = true;
                          toggleCompactInputToolFanByClick();
                        }}
                        onPointerEnter={compactInputHasPayload ? undefined : handleCompactInputToolHoverEnter}
                        onPointerLeave={compactInputHasPayload ? undefined : handleCompactInputToolHoverLeave}
                        onFocus={compactInputHasPayload ? undefined : clearCompactInputToolFanCloseTimer}
                        onBlur={compactInputHasPayload ? scheduleCompactInputCollapse : () => {
                          scheduleCompactInputToolFanTransientClose();
                          scheduleCompactInputCollapse();
                        }}
                        onClick={compactInputHasPayload ? undefined : () => {
                          if (compactInputToolTogglePointerHandledRef.current) {
                            compactInputToolTogglePointerHandledRef.current = false;
                            return;
                          }
                          toggleCompactInputToolFanByClick();
                        }}
                      >
                        <img
                          className={compactInputHasPayload ? undefined : 'compact-input-tool-toggle-icon'}
                          src={compactInputHasPayload ? '/static/icons/send_new_icon.png' : '/static/icons/dropdown_arrow.png'}
                          alt=""
                          aria-hidden="true"
                        />
                      </button> : null}
                    </>
                  ) : (
                    <button
                      className="compact-chat-capsule-button"
                      type="button"
                      disabled={composerInteractionsDisabled}
                      onClick={() => {
                        if (composerHidden) return;
                        requestCompactChatState('input');
                      }}
                    >
                      <span
                        ref={compactPreviewTextRef}
                        className="compact-chat-capsule-text"
                        data-compact-preview-streaming={compactPreviewIsStreaming ? 'true' : 'false'}
                      >
                        {compactPreviewDisplayText}
                      </span>
                    </button>
	                  )}
		                </div>
		                {compactInputToolFanNode}
		              </div>
            ) : (
              <div
                className="composer-input-shell"
                data-compact-chat-state={effectiveCompactChatState}
              >
              <textarea
                className="composer-input"
                placeholder={inputPlaceholder}
                aria-label={inputPlaceholder}
                rows={1}
                value={visibleDraft}
                readOnly={composerInteractionsDisabled}
                disabled={composerInteractionsDisabled}
                onChange={(event) => {
                  if (catLocalTextOnly) {
                    setCatDraft(event.target.value);
                  } else {
                    setDraft(event.target.value);
                  }
                }}
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing) return;
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    submitDraft();
                  }
                }}
              />
              {!isCompactSurface && !catLocalTextOnly ? choiceLayerNode : null}
              <div
                className="composer-bottom-bar"
                ref={handleComposerBottomBarRef}
              >
                {!catLocalTextOnly ? <div className="composer-bottom-tools" aria-label={composerToolsAriaLabel}>
                  <button
                    className="composer-tool-btn"
                    type="button"
                    aria-label={resolvedImportImageAriaLabel}
                    data-neko-tooltip={importImageButtonLabel}
                    disabled={composerInteractionsDisabled}
                    onClick={() => onComposerImportImage?.()}
                  >
                    <img src="/static/icons/import_image_icon.png" alt="" aria-hidden="true" />
                  </button>
                  <span className="composer-tool-divider" aria-hidden="true">|</span>
                  <button
                    className="composer-tool-btn"
                    type="button"
                    aria-label={resolvedScreenshotAriaLabel}
                    data-neko-tooltip={screenshotButtonLabel}
                    disabled={composerInteractionsDisabled}
                    onClick={() => onComposerScreenshot?.()}
                  >
                    <img src="/static/icons/screenshot_new_icon.png" alt="" aria-hidden="true" />
                  </button>
                  {/* 杩欐潯鍒嗛殧绗﹀湪 expanded / compact 涓ゆ€佷笅閮藉父椹诲悓涓€浣嶇疆锛?                      閬垮厤鍒囨崲鏃跺垎闅旂闂儊锛岃鍔ㄧ敾杩囨浮鏇撮『婊?*/}
                  <span className="composer-tool-divider" aria-hidden="true">|</span>
                  {showRightTools ? (
                    <div
                      ref={composerToolsRightRef}
                      className={`composer-tools-right${composerLayout === 'collapsing' ? ' is-leaving' : ''}`}
                      key="composer-tools-expanded"
                      style={
                        composerLayout === 'collapsing' && collapseFromWidth != null
                          ? ({ '--collapse-from-width': `${collapseFromWidth}px` } as CSSProperties)
                          : undefined
                      }
                    >
                      {galgameToggleButtonNode}
                      <span className="composer-tool-divider" aria-hidden="true">|</span>
                      {translateButtonNode}
                      <span className="composer-tool-divider" aria-hidden="true">|</span>
                      {jukeboxButtonNode}
                      <span className="composer-tool-divider" aria-hidden="true">|</span>
                      {emojiToolMenuNode}
                    </div>
                  ) : (
                    <div
                      className={`composer-overflow-menu${composerLayout === 'expanding' ? ' is-leaving' : ''}`}
                      key="composer-tools-collapsed"
                      ref={overflowMenuRef}
                    >
                      <button
                        className={`composer-tool-btn composer-overflow-btn${overflowMenuOpen ? ' is-active' : ''}`}
                        type="button"
                        aria-label={overflowMenuAriaLabel}
                        data-neko-tooltip={overflowMenuAriaLabel}
                        aria-haspopup="true"
                        aria-expanded={overflowMenuOpen}
                        disabled={composerInteractionsDisabled}
                        onClick={() => setOverflowMenuOpen(open => !open)}
                      >
                        <svg
                          width="20"
                          height="20"
                          viewBox="0 0 24 24"
                          fill="currentColor"
                          aria-hidden="true"
                          focusable="false"
                        >
                          <circle cx="6" cy="12" r="2" />
                          <circle cx="12" cy="12" r="2" />
                          <circle cx="18" cy="12" r="2" />
                        </svg>
                      </button>
                      {overflowMenuOpen ? (
                        <div
                          className="composer-overflow-popover"
                          role="group"
                          aria-label={overflowMenuAriaLabel}
                        >
                          {galgameToggleButtonNode}
                          {translateButtonNode}
                          {jukeboxButtonNode}
                          {emojiToolMenuNode}
                        </div>
                      ) : null}
                    </div>
                  )}
                </div> : null}
                <button className="send-button-circle" type="submit" aria-label={sendButtonLabel} disabled={!canSubmit}>
                  <img src="/static/icons/send_new_icon.png" alt="" aria-hidden="true" />
                </button>
              </div>
            </div>
            )}
          </form>
        </footer>
        ) : null}
      </section>
    </main>
  );
}
