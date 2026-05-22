import clsx from 'clsx';
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { i18n } from './i18n';
import MessageBlockView from './MessageBlockView';
import { type ChatMessage, type MessageAction } from './message-schema';

export const COMPACT_EXPORT_SELECTION_LIMIT = 100;

const COMPACT_EXPORT_BOTTOM_THRESHOLD = 30;
const COMPACT_EXPORT_CLICK_MOVE_THRESHOLD = 6;

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
  onAction?: (message: ChatMessage, action: MessageAction) => void;
};

type PointerIntentState = {
  id: number;
  x: number;
  y: number;
  messageId: string;
  cancelled: boolean;
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
  onAction,
}: CompactExportHistoryPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pointerIntentRef = useRef<PointerIntentState | null>(null);
  const suppressClickMessageIdRef = useRef<string | null>(null);
  const [controlsCollapsed, setControlsCollapsed] = useState(false);
  const selectedMessages = messages.filter(message => selectedIds.has(message.id));
  const previewHasSelection = selectedMessages.length > 0;

  useEffect(() => {
    if (!autoScrollToBottom) return;
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    const frameId = window.requestAnimationFrame(() => {
      scrollNode.scrollTop = scrollNode.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [autoScrollToBottom, messages]);

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
    suppressClickMessageIdRef.current = message.id;
    if (!intent.cancelled && selectable && !isSelectionIgnoredTarget(event.target, event.currentTarget)) {
      event.preventDefault();
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
        <span className="compact-export-preview-chip is-active">{i18n('chat.exportFormatMarkdown', 'Markdown')}</span>
        <span className="compact-export-preview-chip">{i18n('chat.exportFormatImage', 'Image')}</span>
      </div>
      {previewHasSelection ? (
        <div className="compact-export-preview-messages" role="list" aria-label={i18n('chat.exportPreviewTitle', 'Export Preview')}>
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
      ) : (
        <div className="compact-export-preview-empty" role="status" aria-live="polite">
          {i18n('chat.exportSelectionEmpty', 'Select at least one message to export.')}
        </div>
      )}
      <div className="compact-export-preview-actions" role="group" aria-label={i18n('chat.exportAction', 'Export')}>
        <button type="button" className="compact-export-preview-action" disabled>
          {i18n('chat.copyToClipboard', 'Copy to Clipboard')}
        </button>
        <button type="button" className="compact-export-preview-action" disabled>
          {i18n('chat.exportAction', 'Export')}
        </button>
      </div>
    </div>
  );

  return (
    <section
      className={clsx('compact-export-history-anchor', {
        'under-choice-prompt': choiceLayerAbove,
        'has-preview': previewOpen,
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
                  {messages.map((message) => {
                    const selectable = isCompactExportMessageSelectable(message);
                    const selected = selectedIds.has(message.id);
                    const failed = message.status === 'failed';
                    const streaming = message.status === 'streaming';
                    return (
                      <article
                        key={message.id}
                        className={getCompactHistoryMessageClassName(message, selected, selectable, selectedCount > 0)}
                        role="listitem"
                        data-compact-export-history-message-id={message.id}
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
                  disabled={selectedCount <= 0}
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
