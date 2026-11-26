import { useEffect, useRef, useState } from 'react';
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
  const pending = pendingNekoPowerAsk(seen);
  // The one that floated last and is answered or over by now, where there is something to say of
  // how that went (the sentence added to it was not taken, it came too late): the compact chat has
  // no list of messages to read it in, so it stays for a while, unless the next question comes.
  const [read, setRead] = useState('');
  const last = shown.current.last;
  const told = pending || !last || read === last ? null : toldOf(seen, last);
  const lingering = told ? last : '';
  useEffect(() => {
    if (!lingering) return;
    const timer = setTimeout(() => setRead(lingering), NEKOPOWER_ASK_NOTICE_MS);
    return () => clearTimeout(timer);
  }, [lingering]);
  if (pending) return <CompactNekoPowerAskPrompt messages={seen} onAction={onAction} shown={shown} />;
  if (told) {
    return (
      <aside id="compact-nekopower-ask-prompt" className="compact-music-recommendation-prompt compact-nekopower-ask-prompt"
        data-compact-geometry-owner="surface" data-compact-geometry-item="musicRecommendation"
        data-compact-hit-region="true" data-compact-hit-region-kind="message"
        data-compact-hit-region-id={`nekopower-ask:${last}`} data-compact-no-drag="true" data-nekopower-ask-told="true">
        <NekoPowerAskBlock block={told.block} message={told.message} interactive={false} presentation="bubble" />
      </aside>
    );
  }
  return <CompactMusicRecommendationPrompt messages={messages} onAction={onAction} />;
}

/** How long what is said of an answer stays above the composer once its question is not asked any more. */
export const NEKOPOWER_ASK_NOTICE_MS = 8000;

/** The question with this key, if it is answered or over and there is something to say of how that went. */
function toldOf(messages: ChatMessage[], key: string) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const block = message.blocks.find(part => part.type === 'nekopower_ask');
    if (block && block.type === 'nekopower_ask' && `${block.taskId}:${block.questionId}` === key) {
      return block.notice && (block.state === 'answered' || block.state === 'closed') ? { message, block } : null;
    }
  }
  return null;
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
