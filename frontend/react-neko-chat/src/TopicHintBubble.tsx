import { i18n } from './i18n';
import { type ChatMessage, type TopicHintBlock } from './message-schema';

/**
 * A topic-hint message is a frontend-only teaser ("she has a topic she'd like
 * to bring up") shown right before a proactive deep-topic opener. It never
 * enters the chat-LLM context — the backend sends it on a dedicated
 * ``topic_hint`` WS frame that skips the sync queue, and it carries a single
 * ``topic-hint`` block instead of normal text.
 */
export function isTopicHintMessage(message: ChatMessage): boolean {
  return (
    message.role === 'system' &&
    message.blocks.length > 0 &&
    message.blocks.every((block) => block.type === 'topic-hint')
  );
}

type TopicHintBubbleProps = {
  message: ChatMessage;
};

export default function TopicHintBubble({ message }: TopicHintBubbleProps) {
  const block = message.blocks.find(
    (b): b is TopicHintBlock => b.type === 'topic-hint',
  );
  const author = (block?.author || message.author || '').trim();
  // Pass author through to i18next interpolation; i18n() also re-applies it
  // locally so the crash-proof window.t stub (which returns the raw template)
  // still renders the name.
  const text = i18n('chat.topicHint', '{{author}}好像有点想找你聊的话题…', { author });

  return (
    <article
      className="message-row message-row-system"
      data-message-id={message.id}
      data-message-role="system"
      data-topic-hint="true"
      data-message-sort-key={message.sortKey ?? ''}
    >
      <div className="topic-hint-chip">
        <span className="topic-hint-chip-text">{text}</span>
      </div>
    </article>
  );
}
