import clsx from 'clsx';
import { i18n } from './i18n';
import MessageBlockView, { isGuideMessage } from './MessageBlockView';
import TopicHintBubble, { isTopicHintMessage } from './TopicHintBubble';
import {
  type ChatMessage,
  type MessageAction,
} from './message-schema';

type MessageBubbleProps = {
  message: ChatMessage;
  isGroupedWithPrevious?: boolean;
  failedStatusLabel?: string;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
};

function getAvatarLabel(message: ChatMessage) {
  if (message.avatarLabel) return message.avatarLabel;
  return message.author.trim().slice(0, 1).toUpperCase() || '?';
}

function getBubbleClassName(message: ChatMessage) {
  return clsx('message-bubble', {
    'message-bubble-user': message.role === 'user',
    'message-bubble-system': message.role === 'system',
    'message-bubble-tool': message.role === 'tool',
    'message-bubble-assistant': message.role === 'assistant',
  });
}

function getRowClassName(message: ChatMessage) {
  return clsx('message-row', {
    'message-row-user': message.role === 'user',
    'message-row-system': message.role === 'system',
    'message-row-assistant': message.role === 'assistant' || message.role === 'tool',
  });
}

function getAvatarClassName(message: ChatMessage) {
  return clsx('avatar', {
    'avatar-user': message.role === 'user',
    'avatar-tool': message.role === 'tool',
    'avatar-assistant': message.role === 'assistant',
  });
}

export default function MessageBubble({
  message,
  isGroupedWithPrevious = false,
  failedStatusLabel = i18n('chat.messageFailed', 'Failed'),
  onAction,
}: MessageBubbleProps) {
  const bubbleClassName = getBubbleClassName(message);
  const rowClassName = getRowClassName(message);
  const showAvatar = message.role !== 'system' && !isGroupedWithPrevious;
  const showMeta = message.role !== 'system' && !isGroupedWithPrevious;
  const showFailed = message.role !== 'system' && message.status === 'failed';

  if (isTopicHintMessage(message)) {
    return <TopicHintBubble message={message} />;
  }

  if (message.role === 'system') {
    return (
      <article
        className={rowClassName}
        data-message-id={message.id}
        data-message-role={message.role}
        data-guide-message={isGuideMessage(message) ? 'true' : undefined}
        data-message-sort-key={message.sortKey ?? ''}
      >
        <div className="system-chip">
          <span className="system-chip-time">{message.time}</span>
          <div className="system-chip-content">
            {message.blocks.map((block, index) => (
              <MessageBlockView
                key={`${message.id}-${block.type}-${index}`}
                block={block}
                message={message}
                onAction={onAction}
              />
            ))}
          </div>
        </div>
      </article>
    );
  }

  const streaming = message.status === 'streaming';

  return (
    <article
      className={rowClassName}
      data-message-id={message.id}
      data-message-role={message.role}
      data-message-status={message.status || ''}
      data-guide-message={isGuideMessage(message) ? 'true' : undefined}
      data-message-sort-key={message.sortKey ?? ''}
    >
      {showAvatar ? (
        message.avatarUrl ? (
          <img className={`${getAvatarClassName(message)} avatar-image`} src={message.avatarUrl} alt={message.author} />
        ) : (
          <div className={getAvatarClassName(message)}>{getAvatarLabel(message)}</div>
        )
      ) : (
        <div className="avatar avatar-placeholder" aria-hidden="true" />
      )}

      <div className="message-stack">
        {(showMeta || showFailed) ? (
          <div className="message-meta">
            {showMeta ? <span className="message-author">{message.author}</span> : null}
            {showMeta ? <span className="message-time">{message.time}</span> : null}
            {showFailed ? <span className="message-delivery message-delivery-failed">{failedStatusLabel}</span> : null}
          </div>
        ) : null}
        <div className={bubbleClassName}>
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
        {message.actions && message.actions.length > 0 ? (
          <div className="message-inline-actions">
            {message.actions.map((action) => (
              <button
                key={action.id}
                className={`message-action-button variant-${action.variant || 'secondary'}`}
                type="button"
                disabled={action.disabled}
                onClick={() => onAction?.(message, action)}
              >
                {action.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}
