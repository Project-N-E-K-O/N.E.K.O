import {
  type ChatMessage,
  type MessageAction,
  type MessageBlock,
} from './message-schema';

type MessageBubbleProps = {
  message: ChatMessage;
  isGroupedWithPrevious?: boolean;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
};

function getAvatarLabel(message: ChatMessage) {
  if (message.avatarLabel) return message.avatarLabel;
  return message.author.trim().slice(0, 1).toUpperCase() || '?';
}

function getBubbleClassName(message: ChatMessage) {
  if (message.role === 'user') return 'message-bubble message-bubble-user';
  if (message.role === 'system') return 'message-bubble message-bubble-system';
  if (message.role === 'tool') return 'message-bubble message-bubble-tool';
  return 'message-bubble message-bubble-assistant';
}

function getRowClassName(message: ChatMessage) {
  if (message.role === 'user') return 'message-row message-row-user';
  if (message.role === 'system') return 'message-row message-row-system';
  return 'message-row message-row-assistant';
}

function getAvatarClassName(message: ChatMessage) {
  if (message.role === 'user') return 'avatar avatar-user';
  if (message.role === 'tool') return 'avatar avatar-tool';
  return 'avatar avatar-assistant';
}

function MessageBlockView({
  block,
  message,
  onAction,
}: {
  block: MessageBlock;
  message: ChatMessage;
  onAction?: (message: ChatMessage, action: MessageAction) => void;
}) {
  if (block.type === 'text') {
    return <div className="message-block message-block-text">{block.text}</div>;
  }

  if (block.type === 'image') {
    return (
      <figure
        className="message-block message-block-image"
        style={block.width && block.height ? { aspectRatio: `${block.width} / ${block.height}` } : undefined}
      >
        <img src={block.url} alt={block.alt || ''} loading="lazy" />
      </figure>
    );
  }

  if (block.type === 'link') {
    return (
      <a
        className="message-block message-block-link"
        href={block.url}
        target="_blank"
        rel="noreferrer"
      >
        {block.thumbnailUrl ? (
          <div className="message-link-thumb">
            <img src={block.thumbnailUrl} alt="" loading="lazy" />
          </div>
        ) : null}
        <div className="message-link-copy">
          <div className="message-link-title">{block.title || block.url}</div>
          {block.description ? <div className="message-link-description">{block.description}</div> : null}
          <div className="message-link-url">{block.siteName || block.url}</div>
        </div>
      </a>
    );
  }

  if (block.type === 'status') {
    return (
      <div className={`message-block message-block-status tone-${block.tone || 'info'}`}>
        {block.text}
      </div>
    );
  }

  if (block.type === 'buttons') {
    return (
      <div className="message-block message-block-buttons">
        {block.buttons.map((action) => (
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
    );
  }

  return null;
}

export default function MessageBubble({
  message,
  isGroupedWithPrevious = false,
  onAction,
}: MessageBubbleProps) {
  const bubbleClassName = getBubbleClassName(message);
  const rowClassName = getRowClassName(message);
  const showAvatar = message.role !== 'system' && !isGroupedWithPrevious;
  const showMeta = message.role !== 'system';

  if (message.role === 'system') {
    return (
      <article
        className={rowClassName}
        data-message-id={message.id}
        data-message-role={message.role}
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

  return (
    <article
      className={rowClassName}
      data-message-id={message.id}
      data-message-role={message.role}
      data-message-status={message.status || ''}
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
        {showMeta ? (
          <div className="message-meta">
            <span className="message-author">{message.author}</span>
            <span className="message-time">{message.time}</span>
            {message.status === 'streaming' ? <span className="message-delivery">生成中</span> : null}
            {message.status === 'failed' ? <span className="message-delivery message-delivery-failed">发送失败</span> : null}
          </div>
        ) : null}
        <div className={bubbleClassName}>
          {message.blocks.map((block, index) => (
            <MessageBlockView
              key={`${message.id}-${block.type}-${index}`}
              block={block}
              message={message}
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
