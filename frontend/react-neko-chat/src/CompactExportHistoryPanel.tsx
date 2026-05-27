import clsx from 'clsx';
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { i18n } from './i18n';
import MessageBlockView from './MessageBlockView';
import { type ChatMessage } from './message-schema';

export const COMPACT_EXPORT_SELECTION_LIMIT = 100;

const COMPACT_EXPORT_BOTTOM_THRESHOLD = 30;
const COMPACT_EXPORT_CLICK_MOVE_THRESHOLD = 6;

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
  choiceLayerAbove: boolean;
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
};

type PointerIntentState = {
  id: number;
  x: number;
  y: number;
  messageId: string;
  cancelled: boolean;
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
  const interactive = target.closest('a, button, input, textarea, select, [data-compact-history-ignore-selection="true"], .message-block-image');
  return !!interactive && interactive !== currentTarget;
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
  const widthSteps = roleGroup === 'system'
    ? ['84%', '90%', '94%']
    : richContent
      ? ['76%', '82%', '88%']
      : ['70%', '78%', '86%', '92%'];
  const offsetSteps = richContent ? [0, 6, 10] : [0, 8, 14, 20];
  const baseOffset = offsetSteps[Math.floor(hash / 7) % offsetSteps.length];
  const signedOffset = roleGroup === 'user'
    ? -baseOffset
    : roleGroup === 'assistant'
      ? baseOffset
      : 0;
  const sameGroupGaps = ['4px', '7px', '10px'];
  const switchGroupGaps = ['15px', '19px', '23px'];
  const gapSteps = group === 'same' ? sameGroupGaps : group === 'switch' ? switchGroupGaps : ['0px'];
  const rotateSteps = richContent || roleGroup === 'system'
    ? ['0deg']
    : ['-0.5deg', '-0.25deg', '0deg', '0.25deg', '0.5deg'];

  return {
    group,
    complexity: richContent ? 'rich' : 'plain',
    style: {
      '--compact-history-bubble-max-ratio': widthSteps[hash % widthSteps.length],
      '--compact-history-stagger-x': `${signedOffset}px`,
      '--compact-history-gap-before': gapSteps[Math.floor(hash / 31) % gapSteps.length],
      '--compact-history-rotate': rotateSteps[Math.floor(hash / 127) % rotateSteps.length],
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
  choiceLayerAbove,
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
}: CompactExportHistoryPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pointerIntentRef = useRef<PointerIntentState | null>(null);
  const suppressClickMessageIdRef = useRef<string | null>(null);
  const previewObjectUrlRef = useRef<string | null>(null);
  const [controlsCollapsed, setControlsCollapsed] = useState(false);
  const [exportFormat, setExportFormat] = useState<CompactExportFormat>('markdown');
  const [imageStyle, setImageStyle] = useState<CompactExportImageStyle>('neko');
  const [imageFormat, setImageFormat] = useState<CompactExportImageFormat>('png');
  const [pendingAction, setPendingAction] = useState<'copy' | 'download' | null>(null);
  const [exportActionError, setExportActionError] = useState<string | null>(null);
  const [previewState, setPreviewState] = useState<CompactExportPreviewState>({ status: 'idle' });
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
  ].join('\u001e')).join('\u001f');
  const exportBusy = pendingAction !== null;
  const exportActionsDisabled = !previewHasSelection || exportBusy;

  function revokeCompactPreviewObjectUrl() {
    if (!previewObjectUrlRef.current) return;
    URL.revokeObjectURL(previewObjectUrlRef.current);
    previewObjectUrlRef.current = null;
  }

  useEffect(() => {
    if (!autoScrollToBottom) return;
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    const frameId = window.requestAnimationFrame(() => {
      scrollNode.scrollTop = scrollNode.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [autoScrollToBottom, messages]);

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

  function handleScroll() {
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    if (pointerIntentRef.current) {
      pointerIntentRef.current.cancelled = true;
    }
    const distanceToBottom = scrollNode.scrollHeight - scrollNode.scrollTop - scrollNode.clientHeight;
    onAutoScrollToBottomChange(distanceToBottom <= COMPACT_EXPORT_BOTTOM_THRESHOLD);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    if (!selectable) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    if (isSelectionIgnoredTarget(event.target, event.currentTarget)) return;
    pointerIntentRef.current = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      messageId: message.id,
      cancelled: false,
    };
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch (_) {}
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLElement>) {
    const intent = pointerIntentRef.current;
    if (!intent || intent.id !== event.pointerId) return;
    const dx = event.clientX - intent.x;
    const dy = event.clientY - intent.y;
    if (Math.hypot(dx, dy) > COMPACT_EXPORT_CLICK_MOVE_THRESHOLD) {
      intent.cancelled = true;
    }
  }

  function finishPointer(event: ReactPointerEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    const intent = pointerIntentRef.current;
    if (!intent || intent.id !== event.pointerId || intent.messageId !== message.id) return;
    pointerIntentRef.current = null;
    if (!intent.cancelled && selectable && !isSelectionIgnoredTarget(event.target, event.currentTarget)) {
      event.preventDefault();
      suppressClickMessageIdRef.current = message.id;
      window.setTimeout(() => {
        if (suppressClickMessageIdRef.current === message.id) {
          suppressClickMessageIdRef.current = null;
        }
      }, 120);
      onToggleMessage(message.id);
    }
  }

  function handleClick(event: ReactMouseEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    if (!selectable) return;
    if (isSelectionIgnoredTarget(event.target, event.currentTarget)) return;
    if (suppressClickMessageIdRef.current === message.id) {
      suppressClickMessageIdRef.current = null;
      return;
    }
    onToggleMessage(message.id);
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLElement>, message: ChatMessage, selectable: boolean) {
    if (!selectable) return;
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
          ‹
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
        'controls-collapsed': !previewOpen && controlsCollapsed,
      })}
      data-compact-geometry-owner="surface"
      data-compact-geometry-item="history"
      data-compact-geometry-hit-scope="children"
      data-compact-export-history-open="true"
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
            <div
              ref={scrollRef}
              className="compact-export-history-scroll"
              role="list"
              aria-label={i18n('chat.messageListAriaLabel', 'Chat messages')}
              onScroll={handleScroll}
              onWheel={(event) => event.stopPropagation()}
              onTouchMove={(event) => event.stopPropagation()}
            >
              {messages.length > 0 ? (
                <div className="compact-export-history-scroll-content">
                  {messages.map((message, index) => {
                    const selectable = isCompactExportMessageSelectable(message);
                    const selected = selectedIds.has(message.id);
                    const failed = message.status === 'failed';
                    const streaming = message.status === 'streaming';
                    const tone = getCompactHistoryBubbleTone(message, index, messages[index - 1]);
                    return (
                      <article
                        key={message.id}
                        className={getCompactHistoryMessageClassName(message, selected, selectable, selectedCount > 0)}
                        style={tone.style}
                        role="listitem"
                        data-compact-export-history-message-id={message.id}
                        data-compact-history-group={tone.group}
                        data-compact-history-complexity={tone.complexity}
                        data-message-role={message.role}
                        data-message-status={message.status || ''}
                      >
                        <div
                          className="compact-export-history-bubble"
                          role="button"
                          aria-pressed={selected}
                          aria-disabled={!selectable}
                          tabIndex={selectable ? 0 : -1}
                          data-compact-hit-region="true"
                          data-compact-hit-region-id={`history:message:${message.id}`}
                          data-compact-hit-region-kind="message"
                          onPointerDown={(event) => handlePointerDown(event, message, selectable)}
                          onPointerMove={handlePointerMove}
                          onPointerUp={(event) => finishPointer(event, message, selectable)}
                          onPointerCancel={() => { pointerIntentRef.current = null; }}
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
            <div
              className={clsx('compact-export-history-controls', {
                'is-collapsed': controlsCollapsed,
              })}
              role="group"
              aria-label={i18n('chat.exportConversation', 'Export Conversation')}
              data-compact-export-controls-collapsed={controlsCollapsed ? 'true' : 'false'}
              data-compact-hit-region="true"
              data-compact-hit-region-id="history:controls"
              data-compact-hit-region-kind="controls"
            >
              <button
                type="button"
                className="compact-export-history-controls-toggle"
                aria-expanded={!controlsCollapsed}
                aria-label={controlsCollapsed
                  ? i18n('chat.compactExportControlsExpand', 'Show selection controls')
                  : i18n('chat.compactExportControlsCollapse', 'Hide selection controls')}
                title={controlsCollapsed
                  ? i18n('chat.compactExportControlsExpand', 'Show selection controls')
                  : i18n('chat.compactExportControlsCollapse', 'Hide selection controls')}
                onClick={() => setControlsCollapsed((collapsed) => !collapsed)}
              >
                <span className="compact-export-history-controls-triangle" aria-hidden="true" />
              </button>
              <div className="compact-export-history-controls-content" hidden={controlsCollapsed}>
                <div className="compact-export-history-count" aria-live="polite">
                  {selectedCount}/{selectableCount}
                </div>
                <button type="button" className="compact-export-history-control" disabled={selectableCount <= 0} onClick={onSelectAll}>
                  {i18n('chat.exportSelectAll', 'Select All')}
                </button>
                <button type="button" className="compact-export-history-control" disabled={selectedCount <= 0} onClick={onClearSelection}>
                  {i18n('chat.exportSelectNone', 'Clear')}
                </button>
                <button type="button" className="compact-export-history-control" disabled={selectableCount <= 0} onClick={onInvertSelection}>
                  {i18n('chat.exportSelectInvert', 'Invert')}
                </button>
                <button
                  type="button"
                  className="compact-export-history-control compact-export-history-export"
                  onClick={onRequestPreview}
                >
                  {i18n('chat.exportAction', 'Export')}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
