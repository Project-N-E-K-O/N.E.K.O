import { render, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import CompactExportHistoryPanel from './CompactExportHistoryPanel';
import MessageBubble from './MessageBubble';
import MessageReactions from './MessageReactions';
import { parseChatMessage, type ChatMessage } from './message-schema';

function userMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return parseChatMessage({
    id: 'reaction-user', role: 'user', author: 'You', time: '10:00',
    blocks: [{ type: 'text', text: 'Hello Neko' }], status: 'sent',
    reaction: { emoji: '❤️', author: 'Neko' },
    ...overrides,
  });
}

function panelProps(messages: ChatMessage[]) {
  return {
    messages, selectedIds: new Set<string>(), selectedCount: 0,
    selectableCount: messages.length, autoScrollToBottom: false,
    previewOpen: false, controlsOpen: false, choiceLayerAbove: false,
    failedStatusLabel: 'Failed', onAutoScrollToBottomChange: vi.fn(),
    onToggleMessage: vi.fn(), onSelectAll: vi.fn(), onClearSelection: vi.fn(),
    onInvertSelection: vi.fn(), onRequestPreview: vi.fn(), onClosePreview: vi.fn(),
    onBuildPreview: vi.fn(() => ({ previewKind: 'empty' as const })),
    onCopyExport: vi.fn(), onDownloadExport: vi.fn(),
  };
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).safeT;
  delete (window as unknown as Record<string, unknown>).t;
});

describe('MessageReactions', () => {
  it.each(['😊', '😄', '😃', '🙂', '😌', '🤔', '🧐', '💭', '❓', '👍', '✅', '🙌', '💪', '🎉', '🙏', '🤝', '😮', '👀', '⚠️', '💡', '😔', '😢', '😅', '🙇', '🥳', '✨', '🌟', '💻', '🤖', '📚', '🔧', '❤️', '⭐', '🔥', '🚀', '📌', '😂', '🤗'] as const)('renders the supported reaction %s without changing its Unicode sequence', (emoji) => {
    const { getByRole } = render(<MessageReactions message={userMessage({ reaction: { emoji, author: 'Neko' } })} />);
    expect(getByRole('img', { name: `Neko reacted with ${emoji}` })).toHaveTextContent(emoji);
  });

  it('labels the emoji with the reacting character and exposes no reaction control', () => {
    const { container, getByRole } = render(<MessageReactions message={userMessage()} />);
    const badge = getByRole('img', { name: 'Neko reacted with ❤️' });
    expect(badge).toHaveTextContent('❤️');
    expect(badge).toHaveAttribute('title', 'Neko reacted with ❤️');
    expect(container.querySelector('button')).toBeNull();
  });

  it('uses localized reaction copy and interpolates both character and emoji', () => {
    const translate = vi.fn((key: string, args: unknown) => key === 'chat.messageReaction'
      ? '{{author}} 贴了 {{emoji}}'
      : typeof args === 'string' ? args : (args as { defaultValue: string }).defaultValue);
    (window as unknown as Record<string, unknown>).safeT = translate;
    const { getByRole } = render(<MessageReactions message={userMessage()} />);
    expect(getByRole('img', { name: 'Neko 贴了 ❤️' })).toHaveTextContent('❤️');
    expect(translate).toHaveBeenCalledWith('chat.messageReaction', {
      author: 'Neko', emoji: '❤️', defaultValue: '{{author}} reacted with {{emoji}}',
    });
  });

  it('adds a late reaction below the full chat bubble including grouped messages', () => {
    const { container, rerender } = render(<MessageBubble message={userMessage({ reaction: undefined })} isGroupedWithPrevious />);
    expect(container.querySelector('.message-reactions')).toBeNull();
    rerender(<MessageBubble message={userMessage()} isGroupedWithPrevious />);
    const bubble = container.querySelector('.message-bubble-user');
    expect(bubble?.nextElementSibling).toHaveClass('message-reactions');
    expect(bubble).toHaveTextContent('Hello Neko');
    expect(container.querySelector('.message-meta')).toBeNull();
  });

  it('renders a late reaction below the compact history bubble', () => {
    const { container, rerender } = render(<CompactExportHistoryPanel {...panelProps([userMessage({ reaction: undefined })])} />);
    expect(container.querySelector('.message-reactions')).toBeNull();
    rerender(<CompactExportHistoryPanel {...panelProps([userMessage()])} />);
    const row = container.querySelector('[data-compact-export-history-message-id="reaction-user"]') as HTMLElement;
    expect(within(row).getByRole('img', { name: 'Neko reacted with ❤️' })).toHaveTextContent('❤️');
    expect(row.querySelector('.compact-export-history-bubble')?.nextElementSibling).toHaveClass('message-reactions');
    expect(row.querySelector('.compact-export-history-content')).toHaveTextContent('Hello Neko');
  });


  it('includes reactions in the compact export fallback preview', async () => {
    const message = userMessage();
    const props = panelProps([message]);
    const { container } = render(<CompactExportHistoryPanel
      {...props}
      selectedIds={new Set([message.id])}
      selectedCount={1}
      previewOpen
      onBuildPreview={async () => { throw new Error('Preview unavailable'); }}
    />);
    await waitFor(() => expect(container.querySelector('[data-compact-export-preview-message-id="reaction-user"]')).not.toBeNull());
    const row = container.querySelector('[data-compact-export-preview-message-id="reaction-user"]') as HTMLElement;
    expect(within(row).getByRole('img', { name: 'Neko reacted with ❤️' })).toHaveTextContent('❤️');
    expect(row.querySelector('.compact-export-preview-bubble')?.nextElementSibling).toHaveClass('message-reactions');
  });

  it('rebuilds a successful export preview when a selected message receives a late reaction', async () => {
    let message = userMessage({ reaction: undefined });
    const onBuildPreview = vi.fn(() => ({
      previewKind: 'document' as const,
      previewDocument: message.reaction
        ? `<p>Hello Neko</p><p>${message.reaction.author} reacted with ${message.reaction.emoji}</p>`
        : '<p>Hello Neko</p>',
    }));
    const props = { ...panelProps([message]), selectedIds: new Set([message.id]), selectedCount: 1,
      previewOpen: true, onBuildPreview };
    const { container, rerender } = render(<CompactExportHistoryPanel {...props} />);
    await waitFor(() => expect(container.querySelector('iframe')).toHaveAttribute('srcdoc', '<p>Hello Neko</p>'));
    message = userMessage();
    rerender(<CompactExportHistoryPanel {...props} messages={[message]} />);
    await waitFor(() => expect(container.querySelector('iframe')).toHaveAttribute(
      'srcdoc', '<p>Hello Neko</p><p>Neko reacted with ❤️</p>',
    ));
    expect(onBuildPreview).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-compact-export-preview-message-id]')).toBeNull();
  });

  it.each([
    { role: 'assistant' as const },
    { role: 'system' as const },
    { role: 'tool' as const },
    { status: 'sending' as const },
    { status: 'failed' as const },
    { reaction: undefined },
  ])('hides reactions across both chat surfaces for %j', (overrides) => {
    const message = userMessage(overrides);
    const full = render(<MessageBubble message={message} />);
    expect(full.container.querySelector('.message-reactions')).toBeNull();
    full.unmount();
    const compact = render(<CompactExportHistoryPanel {...panelProps([message])} />);
    expect(compact.container.querySelector('.message-reactions')).toBeNull();
  });
});
