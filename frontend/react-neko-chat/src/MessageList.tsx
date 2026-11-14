import { useChatScreenVisibility } from './ChatScreenVisibility';
import { useContext, useRef, useEffect, useMemo, useState, type ReactNode } from 'react';
import MessageBubble from './MessageBubble';
import ScreenObservationExposure, { getScreenObservationAnchorIds } from './ScreenObservationExposure';
import ThinkingDots from './ThinkingDots';
import { i18n } from './i18n';
import { OlderRecordingsStatusContext } from './useChatAudioHistory';
import { hasVisibleMessageContent, type ChatMessage, type MessageAction } from './message-schema';

const MAX_DISPLAY_MESSAGES = 50;

type MessageListProps = {
  messages: ChatMessage[];
  ariaLabel?: string;
  failedStatusLabel?: string;
  thinking?: boolean;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
  onLoadOlderRecordings?: () => void;
  footer?: ReactNode;
  footerKey?: string;
};

function shouldGroupWithPrevious(current: ChatMessage, previous?: ChatMessage) {
  if (!previous) return false;
  if (current.role !== previous.role) return false;
  if (current.author !== previous.author) return false;
  if (current.role === 'system') return false;
  if (typeof current.createdAt === 'number' && typeof previous.createdAt === 'number') {
    if (Math.abs(current.createdAt - previous.createdAt) > 30 * 1000) {
      return false;
    }
  }
  return true;
}

export default function MessageList({
  messages,
  ariaLabel = i18n('chat.messageListAriaLabel', 'Chat messages'),
  failedStatusLabel = i18n('chat.messageFailed', 'Failed'),
  thinking = false,
  onAction,
  onLoadOlderRecordings,
  footer,
  footerKey,
}: MessageListProps) {
  const { isMessageHidden, audioHistoryScreenCleared } = useChatScreenVisibility();
  const loadOlderRecordingsStatus = useContext(OlderRecordingsStatusContext);
  const containerRef = useRef<HTMLDivElement>(null);
  const shouldScrollRef = useRef(true);
  const scrollbarTimerRef = useRef<number | null>(null);
  const [scrollbarState, setScrollbarState] = useState({
    visible: false,
    top: 0,
    height: 0,
    scrollable: false,
  });

  const { displayMessages, previousVisibleMessage } = useMemo(() => {
    const visible = messages.filter(message => !isMessageHidden(message) && hasVisibleMessageContent(message));
    // Recording history is paginated separately and every loaded recording must remain reachable.
    const isRecording = (message: ChatMessage) => message.blocks.some(block => block.type === 'audio_message');
    const recent = new Set(visible.filter(message => !isRecording(message)).slice(-MAX_DISPLAY_MESSAGES));
    return {
      displayMessages: visible.filter(message => recent.has(message) || isRecording(message)),
      previousVisibleMessage: new Map(visible.map((message, index) => [message, visible[index - 1]])),
    };
  }, [messages, isMessageHidden]);

  const showLoadOlder = Boolean(onLoadOlderRecordings) && !audioHistoryScreenCleared && displayMessages.length > 0;
  const loadingOlder = loadOlderRecordingsStatus === 'loading';

  const exposureAnchorIds = useMemo(
    () => getScreenObservationAnchorIds(displayMessages, messages),
    [displayMessages, messages],
  );

  const observedMessageKey = useMemo(
    () => displayMessages.map(message => message.id).join('|'),
    [displayMessages],
  );

  useEffect(() => {
    if (displayMessages.length === 0) shouldScrollRef.current = true;
  }, [displayMessages.length]);

  // Always instant scroll: behavior:'smooth' is silently broken in our Electron
  // host (window.scrollTo / element.scrollTo with behavior:'smooth' is a no-op),
  // which left the chat stuck at scrollTop=0 on mount and after each new message.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !shouldScrollRef.current) return;
    container.scrollTop = container.scrollHeight;
  }, [displayMessages, thinking, footerKey]);

  function clearScrollbarTimer() {
    if (scrollbarTimerRef.current === null) return;
    window.clearTimeout(scrollbarTimerRef.current);
    scrollbarTimerRef.current = null;
  }

  function updateFloatingScrollbar(show: boolean) {
    const container = containerRef.current;
    if (!container) return;
    const scrollableHeight = container.scrollHeight - container.clientHeight;
    if (scrollableHeight <= 1 || container.clientHeight <= 0 || container.scrollHeight <= 0) {
      clearScrollbarTimer();
      setScrollbarState(prev => (
        prev.scrollable || prev.visible
          ? { visible: false, top: 0, height: 0, scrollable: false }
          : prev
      ));
      return;
    }
    const height = Math.max(28, Math.round((container.clientHeight / container.scrollHeight) * container.clientHeight));
    const top = Math.round((container.scrollTop / scrollableHeight) * (container.clientHeight - height));
    setScrollbarState(prev => {
      if (
        prev.visible === show
        && prev.scrollable
        && prev.top === top
        && prev.height === height
      ) {
        return prev;
      }
      return { visible: show, top, height, scrollable: true };
    });
  }

  function revealFloatingScrollbar() {
    updateFloatingScrollbar(true);
    clearScrollbarTimer();
    scrollbarTimerRef.current = window.setTimeout(() => {
      scrollbarTimerRef.current = null;
      updateFloatingScrollbar(false);
    }, 760);
  }

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver(() => {
      if (shouldScrollRef.current) {
        container.scrollTop = container.scrollHeight;
      }
      updateFloatingScrollbar(false);
    });

    // 同时观察容器自身：galgame 模式开关 / 选项面板展开收起时
    // .message-list 的 clientHeight 会被外层压缩，没有这一条最后一条消息
    // 在面板长高的瞬间会被推出视口而不会自动跟着滚下来。
    observer.observe(container);
    for (const child of container.children) {
      observer.observe(child);
    }

    return () => observer.disconnect();
  }, [observedMessageKey, footerKey, showLoadOlder]);

  useEffect(() => {
    updateFloatingScrollbar(false);
  }, [displayMessages]);

  useEffect(() => () => {
    clearScrollbarTimer();
  }, []);

  const handleScroll = () => {
    const container = containerRef.current;
    if (!container) return;

    const isNearBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight < 60;
    shouldScrollRef.current = isNearBottom;
    revealFloatingScrollbar();
  };

  const scrollThumbStyle = {
    height: `${scrollbarState.height}px`,
    transform: `translateY(${scrollbarState.top}px)`,
  };

  if (displayMessages.length === 0 && !thinking && !footer) {
    return (
      <div className="message-list-shell">
        <div className="message-list" ref={containerRef} aria-label={ariaLabel}>
        </div>
        {scrollbarState.scrollable ? (
          <div
            className="message-list-scroll-thumb"
            data-message-list-scrollbar-visible={scrollbarState.visible ? 'true' : undefined}
            style={scrollThumbStyle}
            aria-hidden="true"
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className="message-list-shell">
      <div className="message-list" ref={containerRef} aria-label={ariaLabel} onScroll={handleScroll}>
        {showLoadOlder && (
          <>
            <button type="button" className="message-list-load-older" onClick={onLoadOlderRecordings}
              disabled={loadingOlder} aria-busy={loadingOlder}>
              {loadingOlder ? i18n('common.loading', 'Loading...') : i18n('chat.audioHistoryOlder', 'Load older recordings')}
            </button>
            {loadOlderRecordingsStatus === 'failed' && (
              <span role="alert" className="message-list-load-older-error">{i18n('common.loadFailed', 'Load failed')}</span>
            )}
          </>
        )}
        {displayMessages.map((message, index) => (
          <MessageBubble
            key={message.id}
            message={message}
            isGroupedWithPrevious={previousVisibleMessage.get(message) === displayMessages[index - 1]
              && shouldGroupWithPrevious(message, displayMessages[index - 1])}
            failedStatusLabel={failedStatusLabel}
            onAction={onAction}
            exposure={exposureAnchorIds.has(message.id)
              ? <ScreenObservationExposure messages={messages} turnId={message.turnId} onAction={onAction} />
              : undefined}
          />
        ))}
        {footer}
        {thinking ? (
          <article className="message-row message-row-assistant focus-thinking-row" data-focus-thinking="true">
            <div className="avatar avatar-placeholder" aria-hidden="true" />
            <div className="message-stack">
              <div className="message-bubble message-bubble-assistant focus-thinking-bubble">
                <ThinkingDots />
              </div>
            </div>
          </article>
        ) : null}
      </div>
      {scrollbarState.scrollable ? (
        <div
          className="message-list-scroll-thumb"
          data-message-list-scrollbar-visible={scrollbarState.visible ? 'true' : undefined}
          style={scrollThumbStyle}
          aria-hidden="true"
        />
      ) : null}
    </div>
  );
}
