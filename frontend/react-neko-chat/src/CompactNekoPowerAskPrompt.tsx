import { useRef } from 'react';
import { useChatScreenVisibility } from './ChatScreenVisibility';
import CompactMusicRecommendationPrompt from './CompactMusicRecommendationPrompt';
import NekoPowerAskBlock from './NekoPowerAskBlock';
import type { ChatMessage, MessageAction } from './message-schema';

/** The latest question of a NekoPower commission that is still to be answered, among these messages. */
export function pendingNekoPowerAsk(messages: ChatMessage[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const block = message.blocks.find(part => part.type === 'nekopower_ask');
    if (block && block.type === 'nekopower_ask' && (block.state === 'pending' || block.state === 'sending')) return { message, block };
  }
  return null;
}

/**
 * What floats above the composer of the compact chat: what a commission asks comes before a
 * recommendation, since its work waits for the answer. A question whose message the screen hides
 * is not shown, and keeps no recommendation from being shown.
 */
export function CompactAskOrRecommendationPrompt({ messages, onAction }: {
  messages: ChatMessage[];
  onAction?: (message: ChatMessage, action: MessageAction) => void;
}) {
  const { isMessageHidden } = useChatScreenVisibility();
  const seen = messages.filter(message => !isMessageHidden(message));
  // Which question floated last is kept here, where it outlasts the moments with none to show: one
  // that comes after another was answered has its buttons wait as well.
  const shown = useRef({ last: '', replaced: '' });
  return pendingNekoPowerAsk(seen)
    ? <CompactNekoPowerAskPrompt messages={seen} onAction={onAction} shown={shown} />
    : <CompactMusicRecommendationPrompt messages={messages} onAction={onAction} />;
}

/**
 * In the compact chat, what a NekoPower commission asks floats above the composer, where the
 * recommendations do: a commission waits for the answer, so it comes before those.
 */
export default function CompactNekoPowerAskPrompt({ messages, onAction, shown: kept }: {
  messages: ChatMessage[];
  onAction?: (message: ChatMessage, action: MessageAction) => void;
  /** Where whoever shows this keeps which question floated last, across the times it is not shown. */
  shown?: { current: { last: string; replaced: string } };
}) {
  const item = pendingNekoPowerAsk(messages);
  // Which question was shown last, and which one took another's place: that one has its
  // buttons wait a moment. Kept so that drawing it again says the same.
  const own = useRef({ last: '', replaced: '' });
  const shown = kept ?? own;
  const key = item ? `${item.block.taskId}:${item.block.questionId}` : '';
  if (key && key !== shown.current.last) shown.current = { last: key, replaced: shown.current.last ? key : '' };
  const replaced = Boolean(key) && shown.current.replaced === key;
  if (!item) return null;
  return (
    <aside id="compact-nekopower-ask-prompt" className="compact-music-recommendation-prompt compact-nekopower-ask-prompt"
      data-compact-geometry-owner="surface" data-compact-geometry-item="musicRecommendation"
      data-compact-hit-region="true" data-compact-hit-region-kind="message"
      data-compact-hit-region-id={`nekopower-ask:${key}`} data-compact-no-drag="true">
      <NekoPowerAskBlock key={key} block={item.block} message={item.message} interactive presentation="bubble"
        settle={replaced} onAction={onAction} />
    </aside>
  );
}
