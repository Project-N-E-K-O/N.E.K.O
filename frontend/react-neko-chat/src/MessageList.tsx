import { useRef, useEffect, useMemo, useState } from 'react';
import MessageBubble from './MessageBubble';
import ThinkingDots from './ThinkingDots';
import { i18n } from './i18n';
import { type ChatMessage, type MessageAction } from './message-schema';

const MAX_DISPLAY_MESSAGES = 50;

type MessageListProps = {
  messages: ChatMessage[];
  ariaLabel?: string;
  failedStatusLabel?: string;
  thinking?: boolean;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
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
}: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const shouldScrollRef = useRef(true);
  const scrollbarTimerRef = useRef<number | null>(null);
  const [scrollbarState, setScrollbarState] = useState({
    visible: false,
    top: 0,
    height: 0,
    scrollable: false,
  });

  const displayMessages = useMemo(
    () => messages.length > MAX_DISPLAY_MESSAGES
      ? messages.slice(-MAX_DISPLAY_MESSAGES)
      : messages,
    [messages],
  );

  const observedMessageKey = useMemo(
    () => displayMessages.map(message => message.id).join('|'),
    [displayMessages],
  );

  // Always instant scroll: behavior:'smooth' is silently broken in our Electron
  // host (window.scrollTo / element.scrollTo with behavior:'smooth' is a no-op),
  // which left the chat stuck at scrollTop=0 on mount and after each new message.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !shouldScrollRef.current) return;
    container.scrollTop = container.scrollHeight;
  }, [displayMessages, thinking]);

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
  }, [observedMessageKey]);

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

  if (displayMessages.length === 0 && !thinking) {
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
        {displayMessages.map((message, index) => (
          <MessageBubble
            key={message.id}
            message={message}
            isGroupedWithPrevious={shouldGroupWithPrevious(message, displayMessages[index - 1])}
            failedStatusLabel={failedStatusLabel}
            onAction={onAction}
          />
        ))}
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
