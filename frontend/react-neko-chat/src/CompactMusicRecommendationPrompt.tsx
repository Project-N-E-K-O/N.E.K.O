import MessageBlockView from './MessageBlockView';
import { hasProactiveRecommendationContent } from './message-schema';
import type { ChatMessage, MessageAction } from './message-schema';

export default function CompactMusicRecommendationPrompt({ messages, onAction }: {
  messages: ChatMessage[];
  onAction?: (message: ChatMessage, action: MessageAction) => void;
}) {
  const pending = messages.flatMap((message, index) => message.blocks
    .filter(block => {
      if (block.type !== 'music_recommendation' && block.type !== 'proactive_recommendation') return false;
      if (block.type === 'proactive_recommendation' && !hasProactiveRecommendationContent(block)) return false;
      if (block.expiresAt && block.expiresAt <= Date.now()) return false;
      if (['pending', 'saving'].includes(block.state)) return true;
      return block.type === 'proactive_recommendation' && block.category === 'meme' && block.state === 'accepted' && !block.dismissed
        && !messages.slice(index + 1).some(next => next.role === 'user' || (next.role === 'assistant' && next.turnId && next.turnId !== message.turnId
          && next.blocks.some(part => part.type !== 'music_recommendation' && part.type !== 'proactive_recommendation')));
    })
    .map(block => ({ message, block })));
  const item = pending[pending.length - 1];
  if (!item) return null;
  const { message, block } = item;
  if (block.type !== 'music_recommendation' && block.type !== 'proactive_recommendation') return null;
  return <aside id="compact-music-recommendation-prompt" className="compact-music-recommendation-prompt compact-proactive-recommendation-prompt"
    data-compact-geometry-owner="surface" data-compact-geometry-item="musicRecommendation"
    data-compact-hit-region="true" data-compact-hit-region-kind="message"
    data-compact-hit-region-id={`music-recommendation:${block.recommendationId}`} data-compact-no-drag="true">
    <MessageBlockView key={block.recommendationId} block={block} message={message}
      interactive presentation="bubble" onAction={onAction} />
  </aside>;
}
