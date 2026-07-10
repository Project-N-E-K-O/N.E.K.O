import { render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import TopicHintBubble, { isTopicHintMessage } from './TopicHintBubble';
import MessageBubble from './MessageBubble';
import { isCompactExportMessageSelectable } from './CompactExportHistoryPanel';
import { parseChatMessage, type ChatMessage } from './message-schema';

function topicHintMessage(author = '桃奈'): ChatMessage {
  return {
    id: 'topic-hint-1',
    role: 'system',
    author,
    time: '10:00',
    blocks: [{ type: 'topic-hint', author }],
  };
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).safeT;
  delete (window as unknown as Record<string, unknown>).t;
});

describe('TopicHintBubble', () => {
  it('accepts a topic-hint block on the message schema', () => {
    const parsed = parseChatMessage(topicHintMessage());
    expect(parsed.blocks[0]?.type).toBe('topic-hint');
  });

  it('detects topic-hint system messages and ignores ordinary ones', () => {
    expect(isTopicHintMessage(topicHintMessage())).toBe(true);
    expect(
      isTopicHintMessage({
        id: 's-1',
        role: 'system',
        author: 'x',
        time: '10:00',
        blocks: [{ type: 'status', text: 'hi' }],
      }),
    ).toBe(false);
  });

  it('interpolates the character name when the backend returns a raw template', () => {
    // Mirrors the crash-proof window.t stub: returns defaultValue verbatim.
    (window as unknown as Record<string, unknown>).safeT = (_k: string, arg: unknown) =>
      typeof arg === 'string' ? arg : (arg as { defaultValue: string }).defaultValue;
    const { container } = render(<TopicHintBubble message={topicHintMessage('YUI')} />);
    const chip = container.querySelector('.topic-hint-chip');
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toContain('YUI');
    expect(chip?.textContent).not.toContain('{author}');
    expect(chip?.textContent).not.toContain('{{author}}');
  });

  it('uses the already-interpolated string when real i18next resolves the var', () => {
    // Mirrors real i18next: given { author }, it interpolates and returns the
    // finished string; the local re-apply must be a no-op (no double work).
    (window as unknown as Record<string, unknown>).safeT = (key: string, arg: unknown) => {
      const author = (arg as { author?: string })?.author ?? '';
      return key === 'chat.topicHint' ? `${author} has something to say` : '';
    };
    const { container } = render(<TopicHintBubble message={topicHintMessage('Neko')} />);
    expect(container.querySelector('.topic-hint-chip')?.textContent).toBe('Neko has something to say');
  });

  it('routes topic-hint messages through MessageBubble to the dedicated chip', () => {
    (window as unknown as Record<string, unknown>).safeT = (_k: string, arg: unknown) =>
      typeof arg === 'string' ? arg : (arg as { defaultValue: string }).defaultValue;
    const { container } = render(<MessageBubble message={topicHintMessage()} />);
    expect(container.querySelector('.topic-hint-chip')).not.toBeNull();
    // Must NOT fall back to the generic system-chip.
    expect(container.querySelector('.system-chip')).toBeNull();
  });

  it('falls back to the default copy when safeT echoes the key back', () => {
    // A non-i18next safeT returning the key (missing translation) must not leak
    // the raw key into the bubble.
    (window as unknown as Record<string, unknown>).safeT = (key: string) => key;
    const { container } = render(<TopicHintBubble message={topicHintMessage('桃奈')} />);
    const text = container.querySelector('.topic-hint-chip')?.textContent ?? '';
    expect(text).toContain('桃奈');
    expect(text).not.toContain('chat.topicHint');
  });

  it('excludes topic-hint teasers from compact-export selection accounting', () => {
    expect(isCompactExportMessageSelectable(topicHintMessage())).toBe(false);
    expect(
      isCompactExportMessageSelectable({
        id: 'a-1',
        role: 'assistant',
        author: 'Neko',
        time: '10:00',
        blocks: [{ type: 'text', text: 'hi' }],
      }),
    ).toBe(true);
  });

  it('rejects a whitespace-only author at the schema boundary', () => {
    expect(() =>
      parseChatMessage({
        id: 'topic-hint-x',
        role: 'system',
        author: 'x',
        time: '10:00',
        blocks: [{ type: 'topic-hint', author: '   ' }],
      }),
    ).toThrow();
  });
});
