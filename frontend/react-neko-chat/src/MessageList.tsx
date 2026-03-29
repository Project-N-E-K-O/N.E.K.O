import MessageBubble from './MessageBubble';
import { type ChatMessage, type MessageAction } from './message-schema';

type MessageListProps = {
  messages: ChatMessage[];
  emptyText?: string;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
};

function shouldGroupWithPrevious(current: ChatMessage, previous?: ChatMessage) {
  if (!previous) return false;
  if (current.role !== previous.role) return false;
  if (current.author !== previous.author) return false;
  if (current.role === 'system') return false;
  return true;
}

export default function MessageList({
  messages,
  emptyText = '聊天内容接入后会显示在这里。',
  onAction,
}: MessageListProps) {
  if (messages.length === 0) {
    return (
      <div className="message-list" aria-label="Chat messages">
        <div className="message-empty-state">{emptyText}</div>
      </div>
    );
  }

  return (
    <div className="message-list" aria-label="Chat messages" data-virtual-list-ready="true">
      {messages.map((message, index) => (
        <MessageBubble
          key={message.id}
          message={message}
          isGroupedWithPrevious={shouldGroupWithPrevious(message, messages[index - 1])}
          onAction={onAction}
        />
      ))}
    </div>
  );
}
