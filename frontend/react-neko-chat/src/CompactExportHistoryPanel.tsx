import clsx from 'clsx';
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react';
import { i18n } from './i18n';
import MessageBlockView from './MessageBlockView';
import { type ChatMessage, type MessageAction } from './message-schema';

export const COMPACT_EXPORT_SELECTION_LIMIT = 100;

const COMPACT_EXPORT_BOTTOM_THRESHOLD = 30;
const COMPACT_HISTORY_SCROLL_SETTLE_FRAMES = 36;
export const COMPACT_HISTORY_SCROLLBAR_VISIBLE_MS = 860;
const COMPACT_HISTORY_SCROLLBAR_THUMB_MIN_HEIGHT = 24;
export const COMPACT_HISTORY_ENTER_DELAY_STEP_MS = 42;
export const COMPACT_HISTORY_ENTER_DELAY_MAX_MS = 420;
export const COMPACT_HISTORY_EXIT_DELAY_STEP_MS = 30;
export const COMPACT_HISTORY_EXIT_DELAY_MAX_MS = 320;

export function computeCompactHistoryEnterDelay(index: number, totalMessages: number): string {
  return `${Math.min(
    Math.max(totalMessages - 1 - index, 0) * COMPACT_HISTORY_ENTER_DELAY_STEP_MS,
    COMPACT_HISTORY_ENTER_DELAY_MAX_MS,
  )}ms`;
}

export function computeCompactHistoryExitDelay(index: number): string {
  return `${Math.min(index * COMPACT_HISTORY_EXIT_DELAY_STEP_MS, COMPACT_HISTORY_EXIT_DELAY_MAX_MS)}ms`;
}

export type CompactExportFormat = 'markdown' | 'image';
export type CompactExportImageStyle = 'neko' | 'original' | 'poster' | 'lyrics';
export type CompactExportImageFormat = 'png' | 'jpeg' | 'webp';

export type CompactExportActionRequest = {
  messageIds: string[];
  format: CompactExportFormat;
  imageStyle: CompactExportImageStyle;
  imageFormat: CompactExportImageFormat;
};

export type CompactExportPreviewResult =
  | { previewKind: 'empty' }
  | { previewKind: 'document'; previewDocument: string }
  | { previewKind: 'image'; previewUrl: string };

type CompactExportPreviewState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; result: CompactExportPreviewResult }
  | { status: 'failed'; message: string };

type CompactExportHistoryPanelProps = {
  messages: ChatMessage[];
  selectedIds: Set<string>;
  selectedCount: number;
  selectableCount: number;
  autoScrollToBottom: boolean;
  previewOpen: boolean;
  controlsOpen: boolean;
  choiceLayerAbove: boolean;
  visibilityState?: 'open' | 'closing';
  failedStatusLabel: string;
  onAutoScrollToBottomChange: (enabled: boolean) => void;
  onToggleMessage: (messageId: string) => void;
  onSelectAll: () => void;
  onClearSelection: () => void;
  onInvertSelection: () => void;
  onRequestPreview: () => void;
  onClosePreview: () => void;
  onBuildPreview: (request: CompactExportActionRequest) => Promise<CompactExportPreviewResult> | CompactExportPreviewResult;
  onCopyExport: (request: CompactExportActionRequest) => Promise<void> | void;
  onDownloadExport: (request: CompactExportActionRequest) => Promise<void> | void;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
  historyResizeActive?: boolean;
  onHistoryResizePointerDown?: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onHistoryResizePointerMove?: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onHistoryResizePointerUp?: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onHistoryResizePointerCancel?: (event: ReactPointerEvent<HTMLDivElement>) => void;
};

type ScrollbarDragState = {
  pointerId: number;
  startY: number;
  startScrollTop: number;
  scrollableHeight: number;
  draggableTrackHeight: number;
};

type CompactHistoryBubbleTone = {
  group: 'first' | 'same' | 'switch';
  complexity: 'plain' | 'rich';
  style: CSSProperties & Record<string, string>;
};

export function isCompactExportMessageSelectable(message: ChatMessage) {
  return !!message.id && message.status !== 'sending';
}

function isSelectionIgnoredTarget(target: EventTarget | null, currentTarget: EventTarget) {
  if (!(target instanceof Element) || !(currentTarget instanceof Element)) return false;
  const interactive = target.closest(
    'a, button, input, textarea, select, [data-compact-history-ignore-selection="true"], .message-block-image',
  );
  return !!interactive && interactive !== currentTarget;
}

function clampCompactHistoryValue(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function getCompactHistoryScrollbarMetrics(scrollNode: HTMLDivElement) {
  const scrollableHeight = scrollNode.scrollHeight - scrollNode.clientHeight;
  const trackHeight = scrollNode.clientHeight;
  if (scrollableHeight <= 0 || trackHeight <= 0 || scrollNode.scrollHeight <= 0) return null;
  const proportionalThumbHeight = (scrollNode.clientHeight / scrollNode.scrollHeight) * trackHeight;
  const thumbHeight = clampCompactHistoryValue(
    proportionalThumbHeight,
    Math.min(COMPACT_HISTORY_SCROLLBAR_THUMB_MIN_HEIGHT, trackHeight),
    trackHeight,
  );
  const draggableTrackHeight = trackHeight - thumbHeight;
  if (draggableTrackHeight <= 0) return null;
  return {
    scrollableHeight,
    trackHeight,
    thumbHeight,
    draggableTrackHeight,
  };
}

function getCompactHistoryMessageClassName(message: ChatMessage, selected: boolean, selectable: boolean, hasSelection: boolean) {
  return clsx('compact-export-history-message', {
    'is-user': message.role === 'user',
    'is-assistant': message.role === 'assistant' || message.role === 'tool',
    'is-system': message.role === 'system',
    'is-selected': selected,
    'is-unselected': hasSelection && selectable && !selected,
    'is-disabled': !selectable,
    'is-streaming': message.status === 'streaming',
    'is-failed': message.status === 'failed',
  });
}

function getCompactHistoryRoleGroup(message?: ChatMessage) {
  if (!message) return 'none';
  if (message.role === 'user') return 'user';
  if (message.role === 'assistant' || message.role === 'tool') return 'assistant';
  return 'system';
}

function getStableCompactHistoryHash(seed: string) {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = ((hash << 5) - hash + seed.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

function hasRichCompactHistoryContent(message: ChatMessage) {
  return message.blocks.some(block => block.type === 'image' || block.type === 'buttons');
}

function getCompactHistoryBubbleTone(
  message: ChatMessage,
  index: number,
  previousMessage?: ChatMessage,
): CompactHistoryBubbleTone {
  const roleGroup = getCompactHistoryRoleGroup(message);
  const previousRoleGroup = getCompactHistoryRoleGroup(previousMessage);
  const group: CompactHistoryBubbleTone['group'] = index === 0
    ? 'first'
    : previousRoleGroup === roleGroup
      ? 'same'
      : 'switch';
  const richContent = hasRichCompactHistoryContent(message);
  const seed = message.id || `${message.role}:${message.createdAt ?? message.time}:${index}`;
  const hash = getStableCompactHistoryHash(seed);
  const sameGroupGaps = ['4px', '7px', '10px'];
  const switchGroupGaps = ['15px', '19px', '23px'];
  const gapSteps = group === 'same' ? sameGroupGaps : group === 'switch' ? switchGroupGaps : ['0px'];

  return {
    group,
    complexity: richContent ? 'rich' : 'plain',
    style: {
      // 去掉随机宽度 / 水平偏移 / 旋转（强迫症友好）：气泡规整、宽度统一（比历史区窄约 48px）、不歪扭。
      // 仅保留按分组的垂直间距（gap-before）——那是消息分组节奏，不是歪扭。
      '--compact-history-bubble-max-ratio': 'calc(100% - 48px)',
      '--compact-history-stagger-x': '0px',
      '--compact-history-gap-before': gapSteps[Math.floor(hash / 31) % gapSteps.length],
      '--compact-history-rotate': '0deg',
    },
  };
}

export default function CompactExportHistoryPanel({
  messages,
  selectedIds,
  selectedCount,
  selectableCount,
  autoScrollToBottom,
  previewOpen,
  controlsOpen,
  choiceLayerAbove,
  visibilityState = 'open',
  failedStatusLabel,
  onAutoScrollToBottomChange,
  onToggleMessage,
  onSelectAll,
  onClearSelection,
  onInvertSelection,
  onRequestPreview,
  onClosePreview,
  onBuildPreview,
  onCopyExport,
  onDownloadExport,
  onAction,
  historyResizeActive,
  onHistoryResizePointerDown,
  onHistoryResizePointerMove,
  onHistoryResizePointerUp,
  onHistoryResizePointerCancel,
}: CompactExportHistoryPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const autoScrollToBottomRef = useRef(autoScrollToBottom);
  autoScrollToBottomRef.current = autoScrollToBottom;
  const scrollbarDragRef = useRef<ScrollbarDragState | null>(null);
  const desktopHistoryHoverActiveRef = useRef(false);
  const scrollbarVisibleTimerRef = useRef<number | null>(null);
  const previewObjectUrlRef = useRef<string | null>(null);
  const enterDelayByMessageIdRef = useRef<Map<string, string>>(new Map());
  const previousVisibilityStateRef = useRef<'open' | 'closing' | null>(null);
  // 记录上一次几何刷新时的可视窗口高度，用于判断 resize 方向（缩小 vs 增高）。
  const lastGeometryClientHeightRef = useRef<number | null>(null);
  const [exportFormat, setExportFormat] = useState<CompactExportFormat>('image');
  const [imageStyle, setImageStyle] = useState<CompactExportImageStyle>('neko');
  const [imageFormat, setImageFormat] = useState<CompactExportImageFormat>('png');
  const [pendingAction, setPendingAction] = useState<'copy' | 'download' | null>(null);
  const [exportActionError, setExportActionError] = useState<string | null>(null);
  const [previewState, setPreviewState] = useState<CompactExportPreviewState>({ status: 'idle' });
  const [scrollbarVisible, setScrollbarVisible] = useState(false);
  const selectedMessages = messages.filter(message => selectedIds.has(message.id));
  const previewHasSelection = selectedMessages.length > 0;
  const selectedMessageIds = selectedMessages.map(message => message.id);
  const selectedMessageSignature = selectedMessages.map(message => [
    message.id,
    message.role,
    message.author,
    message.time,
    message.status || '',
    JSON.stringify(message.blocks),
  ].join('')).join('');
  const exportBusy = pendingAction !== null;
  const exportActionsDisabled = !previewHasSelection || exportBusy;
  const historyInteractive = visibilityState === 'open';
  const selectionControlsInteractive = historyInteractive && controlsOpen;
  const scrollbarHitVisible = scrollbarVisible
    && !!scrollRef.current
    && !!getCompactHistoryScrollbarMetrics(scrollRef.current);
  const openingEnterDelayByMessageId = useMemo(() => (
    visibilityState === 'open' && previousVisibilityStateRef.current !== 'open'
      ? new Map(messages.map((message, index) => [
        message.id,
        computeCompactHistoryEnterDelay(index, messages.length),
      ]))
      : null
  ), [messages, visibilityState]);

  useLayoutEffect(() => {
    if (openingEnterDelayByMessageId) {
      enterDelayByMessageIdRef.current = openingEnterDelayByMessageId;
    }
    previousVisibilityStateRef.current = visibilityState;
  }, [openingEnterDelayByMessageId, visibilityState]);

  function resolveCompactHistoryEnterDelay(message: ChatMessage, index: number): string {
    const existingDelay = openingEnterDelayByMessageId?.get(message.id)
      ?? enterDelayByMessageIdRef.current.get(message.id);
    if (existingDelay !== undefined) return existingDelay;
    return visibilityState === 'open'
      ? '0ms'
      : computeCompactHistoryEnterDelay(index, messages.length);
  }

  function clearScrollbarVisibleTimer() {
    if (scrollbarVisibleTimerRef.current === null) return;
    window.clearTimeout(scrollbarVisibleTimerRef.current);
    scrollbarVisibleTimerRef.current = null;
  }

  function revealScrollbarForWheel() {
    if (!historyInteractive) return;
    clearScrollbarVisibleTimer();
    setScrollbarVisible(true);
    if (desktopHistoryHoverActiveRef.current) return;
    scrollbarVisibleTimerRef.current = window.setTimeout(() => {
      scrollbarVisibleTimerRef.current = null;
      setScrollbarVisible(false);
    }, COMPACT_HISTORY_SCROLLBAR_VISIBLE_MS);
  }

  function handleWheel(event: ReactWheelEvent<HTMLDivElement>) {
    event.stopPropagation();
    if (event.deltaX !== 0 || event.deltaY !== 0 || event.deltaZ !== 0) {
      revealScrollbarForWheel();
    }
  }

  function handleScrollbarWheel(event: ReactWheelEvent<HTMLDivElement>) {
    if (!historyInteractive) return;
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    const delta = event.deltaY !== 0 ? event.deltaY : event.deltaX;
    if (delta === 0) return;
    event.preventDefault();
    event.stopPropagation();
    scrollNode.scrollTop += delta;
    handleScroll();
    revealScrollbarForWheel();
  }

  function handleScrollbarPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!historyInteractive || !scrollbarVisible) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    const metrics = getCompactHistoryScrollbarMetrics(scrollNode);
    if (!metrics) return;
    const rect = scrollNode.getBoundingClientRect();
    const localY = event.clientY - rect.top;
    const thumbTop = metrics.draggableTrackHeight * (scrollNode.scrollTop / metrics.scrollableHeight);
    const thumbBottom = thumbTop + metrics.thumbHeight;
    event.preventDefault();
    event.stopPropagation();
    clearScrollbarVisibleTimer();
    setScrollbarVisible(true);
    if (localY < thumbTop || localY > thumbBottom) {
      scrollNode.scrollTop = clampCompactHistoryValue(
        ((localY - metrics.thumbHeight / 2) / metrics.draggableTrackHeight) * metrics.scrollableHeight,
        0,
        metrics.scrollableHeight,
      );
      handleScroll();
    }
    scrollbarDragRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startScrollTop: scrollNode.scrollTop,
      scrollableHeight: metrics.scrollableHeight,
      draggableTrackHeight: metrics.draggableTrackHeight,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handleScrollbarPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = scrollbarDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    event.preventDefault();
    event.stopPropagation();
    const deltaY = event.clientY - drag.startY;
    scrollNode.scrollTop = clampCompactHistoryValue(
      drag.startScrollTop + (deltaY / drag.draggableTrackHeight) * drag.scrollableHeight,
      0,
      drag.scrollableHeight,
    );
    handleScroll();
  }

  function finishScrollbarPointerDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = scrollbarDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    scrollbarDragRef.current = null;
    event.stopPropagation();
    try {
      if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
        event.currentTarget.releasePointerCapture?.(event.pointerId);
      }
    } catch (_) {
      // Some hosts release capture before dispatching the final capture event.
    }
    revealScrollbarForWheel();
  }

  useEffect(() => {
    if (historyInteractive) return;
    clearScrollbarVisibleTimer();
    setScrollbarVisible(false);
    scrollbarDragRef.current = null;
    desktopHistoryHoverActiveRef.current = false;
  }, [historyInteractive]);

  useEffect(() => {
    function handleDesktopHistoryHoverStateChange(event: Event) {
      if (!historyInteractive) return;
      const detail = (event as CustomEvent<{ active?: boolean }>).detail;
      desktopHistoryHoverActiveRef.current = detail?.active === true;
      clearScrollbarVisibleTimer();
      setScrollbarVisible(desktopHistoryHoverActiveRef.current);
    }

    window.addEventListener('neko:compact-history-hover-state-change', handleDesktopHistoryHoverStateChange);
    return () => {
      window.removeEventListener('neko:compact-history-hover-state-change', handleDesktopHistoryHoverStateChange);
    };
  }, [historyInteractive]);

  function revokeCompactPreviewObjectUrl() {
    if (!previewObjectUrlRef.current) return;
    URL.revokeObjectURL(previewObjectUrlRef.current);
    previewObjectUrlRef.current = null;
  }

  useLayoutEffect(() => {
    if (!autoScrollToBottom) return;
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    let frameId: number | null = null;
    let remainingFrames = COMPACT_HISTORY_SCROLL_SETTLE_FRAMES;
    const pinScrollToBottom = () => {
      scrollNode.scrollTop = scrollNode.scrollHeight;
      remainingFrames -= 1;
      if (remainingFrames <= 0) {
        frameId = null;
        return;
      }
      frameId = window.requestAnimationFrame(pinScrollToBottom);
    };
    pinScrollToBottom();
    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
    };
  }, [autoScrollToBottom, messages, previewOpen, visibilityState]);

  // 拖动开始/结束两个边界：content 的布局高度在 resizing 切换瞬间从 100% ↔ max 跳变（见 styles.css），
  // 若用户停在底部需同步把可视窗口重新锚定到下端，否则开始拖时内容会因 content 突然撑高而相对上移、
  // 结束时又下落，产生跳动。useLayoutEffect 在 data 属性应用、布局更新后、绘制前同步钉底，无闪烁。
  // 同时把方向判断的基线（lastGeometryClientHeight）重置为当前值，让边界后第一帧 refresh 与正确基线比较。
  useLayoutEffect(() => {
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    lastGeometryClientHeightRef.current = scrollNode.clientHeight;
    if (!autoScrollToBottomRef.current) return;
    scrollNode.scrollTop = scrollNode.scrollHeight - scrollNode.clientHeight;
  }, [historyResizeActive]);

  // slot / max 高度变化（拖 resize bar、视口/工作区/宽度变化触发的 reapply）只裁剪 scroll 盒可视窗口，
  // 内容不再 reflow。仅当用户当前停在底部（autoScrollToBottom）时处理，否则尊重其向上查看老消息的位置。
  //
  // 方向化钉底：只在「缩小」（可视窗口高度变小）时强制把可视窗口重新锚定到下端——此时若不钉底，下端会随
  // 窗口收缩移出视野（实测：clientHeight 减、scrollTop 不动 → 最下气泡逐帧上移出界）。而「增高」方向
  // 下端本就在视野里，浏览器会把 scrollTop 自然 clamp 到新的 (scrollHeight - clientHeight)、底部保持锚定，
  // 无需手动写 scrollTop；继续每帧强写会与「正被增高揭出的上方新气泡的重排」打架，产生跳变抖动（问题 2 根因）。
  // 故增高/不变时跳过强制钉底，交给浏览器自然 clamp —— 卷帘从上往下收照旧，且消除展开抖动。
  useEffect(() => {
    function handleGeometryRefresh() {
      const scrollNode = scrollRef.current;
      if (!scrollNode) return;
      // 同步读 clientHeight（事件在 CSS 变量写入后同步派发，已是新值）判断方向。
      const nextClientHeight = scrollNode.clientHeight;
      const previousClientHeight = lastGeometryClientHeightRef.current;
      lastGeometryClientHeightRef.current = nextClientHeight;
      if (!autoScrollToBottomRef.current) return;
      // 增高 / 首帧基线缺失 / 不变：不强制写 scrollTop，浏览器自然 clamp 维持底部锚定，避免展开抖动。
      if (previousClientHeight === null || nextClientHeight >= previousClientHeight) return;
      // 缩小：同步钉底，把下端重新锚回可视窗口底部。
      scrollNode.scrollTop = scrollNode.scrollHeight - nextClientHeight;
    }
    window.addEventListener('neko:compact-interaction-geometry-refresh', handleGeometryRefresh);
    return () => {
      window.removeEventListener('neko:compact-interaction-geometry-refresh', handleGeometryRefresh);
    };
  }, []);

  useEffect(() => {
    if (!previewOpen) {
      revokeCompactPreviewObjectUrl();
      setExportActionError(null);
      setPreviewState({ status: 'idle' });
      return;
    }
    if (!previewHasSelection) {
      revokeCompactPreviewObjectUrl();
      setPreviewState({ status: 'ready', result: { previewKind: 'empty' } });
      return;
    }

    let cancelled = false;
    const request: CompactExportActionRequest = {
      messageIds: selectedMessageIds,
      format: exportFormat,
      imageStyle,
      imageFormat,
    };
    revokeCompactPreviewObjectUrl();
    setPreviewState({ status: 'loading' });
    Promise.resolve()
      .then(() => onBuildPreview(request))
      .then((result) => {
        if (cancelled) {
          if (result.previewKind === 'image') {
            URL.revokeObjectURL(result.previewUrl);
          }
          return;
        }
        if (result.previewKind === 'image') {
          previewObjectUrlRef.current = result.previewUrl;
        }
        setPreviewState({ status: 'ready', result });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        setPreviewState({ status: 'failed', message });
      });

    return () => {
      cancelled = true;
    };
  }, [previewOpen, previewHasSelection, selectedMessageSignature, exportFormat, imageStyle, imageFormat, onBuildPreview]);

  useEffect(() => () => {
    revokeCompactPreviewObjectUrl();
  }, []);

  useEffect(() => () => {
    clearScrollbarVisibleTimer();
  }, []);

  function handleScroll() {
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    const distanceToBottom = scrollNode.scrollHeight - scrollNode.scrollTop - scrollNode.clientHeight;
    onAutoScrollToBottomChange(distanceToBottom <= COMPACT_EXPORT_BOTTOM_THRESHOLD);
  }

  function handleClick(event: ReactMouseEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    if (!selectable) return;
    if (isSelectionIgnoredTarget(event.target, event.currentTarget)) return;
    if (!selectionControlsInteractive) return;
    // 气泡是 user-select:text，可划词复制；拖选文字后 mouseup 也会再冒出一次 click。
    // 此刻若存在非折叠的非空选区，说明用户在复制文本而非勾选，跳过 toggle 避免误选。
    const selection = typeof window !== 'undefined' ? window.getSelection() : null;
    if (selection && !selection.isCollapsed && selection.toString().trim().length > 0) return;
    onToggleMessage(message.id);
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    if (!selectable) return;
    if (!selectionControlsInteractive) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onToggleMessage(message.id);
  }

  function buildExportActionRequest(): CompactExportActionRequest {
    return {
      messageIds: selectedMessageIds,
      format: exportFormat,
      imageStyle,
      imageFormat,
    };
  }

  async function runExportAction(kind: 'copy' | 'download') {
    if (exportActionsDisabled) return;
    setPendingAction(kind);
    setExportActionError(null);
    try {
      const request = buildExportActionRequest();
      if (kind === 'copy') {
        await onCopyExport(request);
      } else {
        await onDownloadExport(request);
      }
    } catch (error) {
      console.error('[CompactExportHistoryPanel] export action failed', error);
      setExportActionError(i18n('chat.exportActionFailed', 'Export failed. Please try again.'));
    } finally {
      setPendingAction(null);
    }
  }

  const exportFormatOptions: { id: CompactExportFormat; label: string }[] = [
    { id: 'markdown', label: i18n('chat.exportFormatMarkdown', 'Markdown') },
    { id: 'image', label: i18n('chat.exportFormatImage', 'Image') },
  ];
  const imageStyleOptions: { id: CompactExportImageStyle; label: string }[] = [
    { id: 'neko', label: i18n('chat.exportImageStyleNeko', 'N.E.K.O') },
    { id: 'original', label: i18n('chat.exportImageStyleOriginal', 'Original') },
    { id: 'poster', label: i18n('chat.exportImageStylePoster', 'Fresh') },
    { id: 'lyrics', label: i18n('chat.exportImageStyleLyrics', 'Lyrics') },
  ];
  const imageFormatOptions: { id: CompactExportImageFormat; label: string }[] = [
    { id: 'png', label: i18n('chat.exportImageFormatPng', 'PNG') },
    { id: 'jpeg', label: i18n('chat.exportImageFormatJpeg', 'JPEG') },
    { id: 'webp', label: i18n('chat.exportImageFormatWebp', 'WebP') },
  ];

  function renderSelectedMessageFallback() {
    if (!previewHasSelection) return null;
    return (
      <div className="compact-export-preview-fallback" role="list" aria-label={i18n('chat.exportPreviewTitle', 'Export Preview')}>
        {selectedMessages.map((message) => {
          const streaming = message.status === 'streaming';
          return (
            <article
              key={message.id}
              className={clsx('compact-export-preview-message', {
                'is-user': message.role === 'user',
                'is-assistant': message.role === 'assistant' || message.role === 'tool',
                'is-system': message.role === 'system',
              })}
              role="listitem"
              data-compact-export-preview-message-id={message.id}
              data-message-role={message.role}
            >
              <div className="compact-export-preview-bubble">
                <div className="compact-export-preview-meta">
                  <span>{message.author}</span>
                  <span>{message.time}</span>
                </div>
                <div className="compact-export-preview-content">
                  {message.blocks.map((block, index) => (
                    <MessageBlockView
                      key={`${message.id}-preview-${block.type}-${index}`}
                      block={block}
                      message={message}
                      isStreaming={streaming}
                      onAction={onAction}
                    />
                  ))}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    );
  }

  function renderPreviewStage() {
    if (!previewHasSelection) {
      return (
        <div className="compact-export-preview-empty" role="status" aria-live="polite">
          {i18n('chat.exportSelectionEmpty', 'Select at least one message to export.')}
        </div>
      );
    }
    if (previewState.status === 'loading' || previewState.status === 'idle') {
      return (
        <div className="compact-export-preview-placeholder" role="status" aria-live="polite">
          {i18n('chat.exportPreviewLoading', 'Generating preview...')}
        </div>
      );
    }
    if (previewState.status === 'failed') {
      return (
        <div className="compact-export-preview-stage is-fallback">
          <div className="compact-export-preview-placeholder" role="status" aria-live="polite">
            {i18n('chat.exportPreviewFailed', 'Failed to build the preview.')}
          </div>
          {renderSelectedMessageFallback()}
        </div>
      );
    }
    if (previewState.result.previewKind === 'empty') {
      return (
        <div className="compact-export-preview-empty" role="status" aria-live="polite">
          {i18n('chat.exportSelectionEmpty', 'Select at least one message to export.')}
        </div>
      );
    }
    if (previewState.result.previewKind === 'image') {
      return (
        <div className="compact-export-preview-stage" data-compact-history-ignore-selection="true">
          <img
            className="compact-export-preview-image"
            src={previewState.result.previewUrl}
            alt={i18n('chat.exportPreviewTitle', 'Export Preview')}
          />
        </div>
      );
    }
    return (
      <div className="compact-export-preview-stage" data-compact-history-ignore-selection="true">
        <iframe
          className="compact-export-preview-frame"
          title={i18n('chat.exportPreviewTitle', 'Export Preview')}
          srcDoc={previewState.result.previewDocument}
          sandbox=""
        />
      </div>
    );
  }

  const previewNode = (
    <div
      className="compact-export-preview-region"
      data-compact-export-preview-open="true"
      data-compact-hit-region="true"
      data-compact-hit-region-id="history:preview"
      data-compact-hit-region-kind="preview"
    >
      <div className="compact-export-preview-header">
        <button
          type="button"
          className="compact-export-preview-back"
          onClick={onClosePreview}
          aria-label={i18n('chat.previewClose', 'Close')}
          title={i18n('chat.previewClose', 'Close')}
        >
          <svg
            className="compact-export-preview-back-icon"
            viewBox="0 0 16 16"
            aria-hidden="true"
            focusable="false"
          >
            <path
              d="M9.75 4.25 6.25 8l3.5 3.75"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <div className="compact-export-preview-heading">
          <div className="compact-export-preview-title">{i18n('chat.exportPreviewTitle', 'Export Preview')}</div>
          <div className="compact-export-preview-subtitle" aria-live="polite">
            {selectedCount}/{selectableCount}
          </div>
        </div>
      </div>
      <div className="compact-export-preview-format-strip" aria-label={i18n('chat.exportFormatLabel', 'Export Format')}>
        {exportFormatOptions.map(option => (
          <button
            key={option.id}
            type="button"
            className={clsx('compact-export-preview-chip', {
              'is-active': exportFormat === option.id,
            })}
            aria-pressed={exportFormat === option.id}
            onClick={() => setExportFormat(option.id)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {exportFormat === 'image' ? (
        <div className="compact-export-preview-options" aria-label={i18n('chat.exportFormatImage', 'Image')}>
          <div className="compact-export-preview-option-row">
            {imageStyleOptions.map(option => (
              <button
                key={option.id}
                type="button"
                className={clsx('compact-export-preview-chip', {
                  'is-active': imageStyle === option.id,
                })}
                aria-pressed={imageStyle === option.id}
                onClick={() => setImageStyle(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="compact-export-preview-option-row">
            {imageFormatOptions.map(option => (
              <button
                key={option.id}
                type="button"
                className={clsx('compact-export-preview-chip', {
                  'is-active': imageFormat === option.id,
                })}
                aria-pressed={imageFormat === option.id}
                onClick={() => setImageFormat(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {renderPreviewStage()}
      <div className="compact-export-preview-actions" role="group" aria-label={i18n('chat.exportAction', 'Export')}>
        <button
          type="button"
          className="compact-export-preview-action"
          disabled={exportActionsDisabled}
          onClick={() => { void runExportAction('copy'); }}
        >
          {i18n('chat.copyToClipboard', 'Copy to Clipboard')}
        </button>
        <button
          type="button"
          className="compact-export-preview-action compact-export-preview-action-primary"
          disabled={exportActionsDisabled}
          onClick={() => { void runExportAction('download'); }}
        >
          {i18n('chat.exportAction', 'Export')}
        </button>
      </div>
      {exportActionError ? (
        <div className="compact-export-preview-error" role="status" aria-live="polite">
          {exportActionError}
        </div>
      ) : null}
    </div>
  );

  return (
    <section
      className={clsx('compact-export-history-anchor', {
        'under-choice-prompt': choiceLayerAbove,
        'has-preview': previewOpen,
        'controls-collapsed': !previewOpen && !controlsOpen,
      })}
      data-compact-geometry-owner="surface"
      data-compact-geometry-item="history"
      data-compact-geometry-hit-scope="children"
      data-compact-no-drag="true"
      data-compact-export-history-open={historyInteractive ? 'true' : 'false'}
      data-compact-export-history-visibility={visibilityState}
      data-compact-export-history-resizing={historyResizeActive ? 'true' : 'false'}
      data-compact-export-preview-open={previewOpen ? 'true' : 'false'}
      data-compact-export-under-choice={choiceLayerAbove ? 'true' : 'false'}
      aria-label={i18n('chat.exportConversation', 'Export Conversation')}
      onPointerDown={(event) => event.stopPropagation()}
      onPointerMove={(event) => event.stopPropagation()}
      onPointerUp={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="compact-export-history-panel">
      {previewOpen ? previewNode : (
        <>
          {/* 堆砌区顶部的高度 resize bar：平时透明，hover / 拖动中半透明显现（is-active）。
              放在 scroll 之前，命中区随 anchor 的 children hit-scope 上报给宿主、Electron 下可点不穿透。 */}
          <div
            className={clsx('compact-export-history-resize-bar', { 'is-active': historyResizeActive })}
            data-compact-hit-region={historyInteractive && !choiceLayerAbove ? 'true' : undefined}
            data-compact-hit-region-id={historyInteractive && !choiceLayerAbove ? 'history:resize' : undefined}
            data-compact-hit-region-kind={historyInteractive && !choiceLayerAbove ? 'resize' : undefined}
            data-compact-no-drag="true"
            aria-hidden="true"
            onPointerDown={onHistoryResizePointerDown}
            onPointerMove={onHistoryResizePointerMove}
            onPointerUp={onHistoryResizePointerUp}
            onPointerCancel={onHistoryResizePointerCancel}
            onLostPointerCapture={onHistoryResizePointerCancel}
          />
          <div
            ref={scrollRef}
            className="compact-export-history-scroll"
            role="list"
            aria-label={i18n('chat.messageListAriaLabel', 'Chat messages')}
            data-compact-scrollbar-visible={scrollbarVisible ? 'true' : undefined}
            onScroll={handleScroll}
            onWheel={handleWheel}
            onTouchMove={(event) => event.stopPropagation()}
          >
            {messages.length > 0 ? (
              <div className="compact-export-history-scroll-content">
                {messages.map((message, index) => {
                  const selectable = isCompactExportMessageSelectable(message);
                  const selected = selectedIds.has(message.id);
                  const selectionEnabled = selectionControlsInteractive && selectable;
                  const failed = message.status === 'failed';
                  const streaming = message.status === 'streaming';
                  const tone = getCompactHistoryBubbleTone(message, index, messages[index - 1]);
                  const motionStyle: CSSProperties & Record<string, string> = {
                    '--compact-history-enter-delay': resolveCompactHistoryEnterDelay(message, index),
                    '--compact-history-exit-delay': computeCompactHistoryExitDelay(index),
                  };
                  return (
                    <article
                      key={message.id}
                      className={getCompactHistoryMessageClassName(message, selected, selectable, selectedCount > 0)}
                      style={{
                        ...tone.style,
                        ...motionStyle,
                      }}
                      role="listitem"
                      data-compact-export-history-message-id={message.id}
                      data-compact-history-group={tone.group}
                      data-compact-history-complexity={tone.complexity}
                      data-message-role={message.role}
                      data-message-status={message.status || ''}
                    >
                      <div
                        className="compact-export-history-bubble"
                        role={selectionEnabled ? 'button' : undefined}
                        aria-pressed={selectionEnabled ? selected : undefined}
                        aria-disabled={!selectionEnabled}
                        tabIndex={selectionEnabled ? 0 : -1}
                        data-compact-hit-region={historyInteractive ? 'true' : undefined}
                        data-compact-hit-region-id={historyInteractive ? `history:message:${message.id}` : undefined}
                        data-compact-hit-region-kind={historyInteractive ? 'message' : undefined}
                        onClick={(event) => handleClick(event, message, selectable)}
                        onKeyDown={(event) => handleKeyDown(event, message, selectable)}
                      >
                        <span className="compact-export-history-check" aria-hidden="true" />
                        <div className="compact-export-history-meta">
                          <span className="compact-export-history-author">{message.author}</span>
                          <span className="compact-export-history-time">{message.time}</span>
                          {failed ? <span className="compact-export-history-status">{failedStatusLabel}</span> : null}
                          {streaming ? <span className="compact-export-history-status">...</span> : null}
                        </div>
                        <div className="compact-export-history-content">
                          {message.blocks.map((block, index) => (
                            <MessageBlockView
                              key={`${message.id}-${block.type}-${index}`}
                              block={block}
                              message={message}
                              isStreaming={streaming}
                              onAction={onAction}
                            />
                          ))}
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : null}
          </div>
          {scrollbarHitVisible ? (
            <div
              className="compact-export-history-scrollbar-hit"
              data-compact-scrollbar-hit="true"
              aria-hidden="true"
              onWheel={handleScrollbarWheel}
              onPointerDown={handleScrollbarPointerDown}
              onPointerMove={handleScrollbarPointerMove}
              onPointerUp={finishScrollbarPointerDrag}
              onPointerCancel={finishScrollbarPointerDrag}
              onLostPointerCapture={finishScrollbarPointerDrag}
            />
          ) : null}
          {controlsOpen ? (
            <div
              className="compact-export-history-controls"
              role="group"
              aria-label={i18n('chat.exportConversation', 'Export Conversation')}
              aria-disabled={!historyInteractive}
              data-compact-export-controls-open="true"
              data-compact-hit-region={historyInteractive ? 'true' : undefined}
              data-compact-hit-region-id={historyInteractive ? 'history:controls' : undefined}
              data-compact-hit-region-kind={historyInteractive ? 'controls' : undefined}
            >
              <div className="compact-export-history-controls-content">
                <div className="compact-export-history-count" aria-live="polite">
                  {selectedCount}/{selectableCount}
                </div>
                <button type="button" className="compact-export-history-control" disabled={!historyInteractive || selectableCount <= 0} onClick={onSelectAll}>
                  {i18n('chat.exportSelectAll', 'Select All')}
                </button>
                <button type="button" className="compact-export-history-control" disabled={!historyInteractive || selectedCount <= 0} onClick={onClearSelection}>
                  {i18n('chat.exportSelectNone', 'Clear')}
                </button>
                <button type="button" className="compact-export-history-control" disabled={!historyInteractive || selectableCount <= 0} onClick={onInvertSelection}>
                  {i18n('chat.exportSelectInvert', 'Invert')}
                </button>
                <button
                  type="button"
                  className="compact-export-history-control compact-export-history-export"
                  disabled={!historyInteractive}
                  onClick={onRequestPreview}
                >
                  {i18n('chat.exportAction', 'Export')}
                </button>
              </div>
            </div>
          ) : null}
        </>
      )}
      </div>
    </section>
  );
}
