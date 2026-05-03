import { useRef, useEffect, useCallback, useMemo } from 'react';
import MessageBubble from './MessageBubble';
import { i18n } from './i18n';
import { type ChatMessage, type MessageAction } from './message-schema';

const MAX_DISPLAY_MESSAGES = 50;

type MessageListProps = {
  messages: ChatMessage[];
  ariaLabel?: string;
  failedStatusLabel?: string;
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
  onAction,
}: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const shouldScrollRef = useRef(true);

  const displayMessages = useMemo(
    () => messages.length > MAX_DISPLAY_MESSAGES
      ? messages.slice(-MAX_DISPLAY_MESSAGES)
      : messages,
    [messages],
  );

  const isStreaming = displayMessages.some(m => m.status === 'streaming');

  const scrollToBottom = useCallback(() => {
    const container = containerRef.current;
    if (!container || !shouldScrollRef.current) return;

    const shouldUseInstantScroll =
      isStreaming
      || (
        typeof document !== 'undefined'
        && document.body.classList.contains('yui-taking-over')
      );

    if (shouldUseInstantScroll) {
      container.scrollTop = container.scrollHeight;
    } else {
      container.scrollTo({
        top: container.scrollHeight,
        behavior: 'smooth',
      });
    }
  }, [isStreaming]);

  useEffect(() => {
    scrollToBottom();
  }, [displayMessages, scrollToBottom]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver(() => {
      if (shouldScrollRef.current) {
        container.scrollTop = container.scrollHeight;
      }
    });

    for (const child of container.children) {
      observer.observe(child);
    }

    return () => observer.disconnect();
  }, [displayMessages.length]);

  const handleScroll = () => {
    const container = containerRef.current;
    if (!container) return;

    const isNearBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight < 60;
    shouldScrollRef.current = isNearBottom;
  };

  if (displayMessages.length === 0) {
    return (
      <div className="message-list" ref={containerRef} aria-label={ariaLabel}>
      </div>
    );
  }

  return (
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
    </div>
  );
}
