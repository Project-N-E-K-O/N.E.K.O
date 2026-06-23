import { render } from '@testing-library/react';
import MessageList from './MessageList';
import { parseChatMessage } from './message-schema';

const message = parseChatMessage({
  id: 'm1',
  role: 'assistant',
  author: 'Neko',
  time: '10:00',
  createdAt: 1,
  blocks: [{ type: 'text', text: 'hi' }],
  status: 'sent',
});

describe('MessageList 凝神 thinking-dots', () => {
  it('appends a thinking-dots bubble at the tail only when thinking', () => {
    const { container, rerender } = render(<MessageList messages={[message]} />);
    expect(container.querySelector('.focus-thinking-row')).toBeNull();

    rerender(<MessageList messages={[message]} thinking />);
    const row = container.querySelector('.focus-thinking-row');
    expect(row).not.toBeNull();
    expect(row?.getAttribute('data-focus-thinking')).toBe('true');
    expect(row?.querySelectorAll('.focus-thinking-dot').length).toBe(3);

    // It is the LAST row so it reads as a pending reply after the messages.
    const rows = container.querySelectorAll('.message-row');
    expect(rows[rows.length - 1]).toBe(row);
  });

  it('still shows the thinking-dots bubble when the history is empty', () => {
    const { container } = render(<MessageList messages={[]} thinking />);
    const row = container.querySelector('.focus-thinking-row');
    expect(row).not.toBeNull();
    expect(row?.querySelectorAll('.focus-thinking-dot').length).toBe(3);
  });
});
