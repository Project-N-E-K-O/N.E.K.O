import { fireEvent, render } from '@testing-library/react';
import MessageList from './MessageList';
import { MEME_IMAGE_LOAD_FAILED_STICKER_URL } from './memeImageFallback';
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

describe('MessageList image fallback', () => {
  it('keeps a normal image URL until the browser reports a load error', () => {
    const imageMessage = parseChatMessage({
      id: 'img-1',
      role: 'assistant',
      author: 'Neko',
      time: '10:01',
      createdAt: 2,
      blocks: [{ type: 'image', url: '/api/meme/proxy-image?url=ok', alt: 'ok meme' }],
      status: 'sent',
    });
    const { container } = render(<MessageList messages={[imageMessage]} />);
    const img = container.querySelector<HTMLImageElement>('.message-block-image img');

    expect(img).not.toBeNull();
    expect(img).toHaveAttribute('src', '/api/meme/proxy-image?url=ok');
    expect(img).toHaveAttribute('loading', 'eager');
    expect(img).toHaveAttribute('fetchpriority', 'high');
    expect(img).not.toHaveAttribute('data-neko-image-load-failed-sticker');

    fireEvent.error(img as HTMLImageElement);

    expect(img).toHaveAttribute('src', MEME_IMAGE_LOAD_FAILED_STICKER_URL);
    expect(img).toHaveAttribute('data-neko-image-load-failed-sticker', 'true');
  });

  it('does not use the meme failed sticker for non-meme images', () => {
    const imageMessage = parseChatMessage({
      id: 'img-2',
      role: 'assistant',
      author: 'Neko',
      time: '10:02',
      createdAt: 3,
      blocks: [{ type: 'image', url: '/static/icons/cat_icon.png', alt: 'regular image' }],
      status: 'sent',
    });
    const { container } = render(<MessageList messages={[imageMessage]} />);
    const img = container.querySelector<HTMLImageElement>('.message-block-image img');

    expect(img).not.toBeNull();
    expect(img).toHaveAttribute('src', '/static/icons/cat_icon.png');
    expect(img).toHaveAttribute('loading', 'lazy');
    expect(img).not.toHaveAttribute('fetchpriority');

    fireEvent.error(img as HTMLImageElement);

    expect(img).toHaveAttribute('src', '/static/icons/cat_icon.png');
    expect(img).not.toHaveAttribute('src', MEME_IMAGE_LOAD_FAILED_STICKER_URL);
    expect(img).not.toHaveAttribute('data-neko-image-load-failed-sticker');
  });
});
