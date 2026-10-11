import { i18n } from './i18n';
import { type ChatMessage } from './message-schema';

export default function MessageReactions({ message }: { message: ChatMessage }) {
  const { reaction } = message;
  if (message.role !== 'user' || !reaction || message.status === 'sending' || message.status === 'failed') {
    return null;
  }

  const label = i18n('chat.messageReaction', '{{author}} reacted with {{emoji}}', {
    author: reaction.author,
    emoji: reaction.emoji,
  });

  return (
    <span className="message-reactions" role="img" aria-label={label} title={label}>
      <span aria-hidden="true">{reaction.emoji}</span>
    </span>
  );
}
