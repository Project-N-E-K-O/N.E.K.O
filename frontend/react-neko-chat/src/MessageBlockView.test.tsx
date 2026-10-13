import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import MessageBlockView from './MessageBlockView';
import { parseChatMessage, type ChatMessage, type MessageBlock } from './message-schema';

// 串门台词（docs/design/visit-infrastructure.md §3.6.6）：对端可以发 Markdown 图片 / 链接，
// 纯文本块不能加载远程图片、不能出可点的链接，文字与原文逐字一致。
const ATTACK_TEXT = '看 ![x](https://attacker.example/p.png) 和 [点我](http://10.0.0.1/admin)';

function messageWith(block: MessageBlock): ChatMessage {
  return parseChatMessage({
    id: 'visit-line-1',
    role: 'tool',
    author: '小白',
    time: '12:00',
    blocks: [block],
  });
}

describe('MessageBlockView plain_text', () => {
  let srcSetter: Mock<(value: string) => void>;
  let restore: (() => void) | null = null;

  beforeEach(() => {
    // jsdom 不真发请求：以 img.src 的赋值次数代替网络计数
    const descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
    srcSetter = vi.fn<(value: string) => void>();
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      configurable: true,
      get() { return descriptor?.get?.call(this) ?? ''; },
      set(value: string) {
        srcSetter(value);
        descriptor?.set?.call(this, value);
      },
    });
    restore = () => {
      if (descriptor) Object.defineProperty(HTMLImageElement.prototype, 'src', descriptor);
    };
  });

  afterEach(() => {
    restore?.();
    restore = null;
  });

  it.each([true, false])('renders a plain_text block as text only (streaming=%s)', (isStreaming) => {
    // 经 schema 解析：plain_text 必须被声明，否则 zod 剥掉它、走回 Markdown
    const message = messageWith({ type: 'text', text: ATTACK_TEXT, plain_text: true });
    const block = message.blocks[0];
    const { container } = render(
      <MessageBlockView block={block} message={message} isStreaming={isStreaming} />,
    );

    expect(container.querySelectorAll('img')).toHaveLength(0);
    expect(container.querySelectorAll('a')).toHaveLength(0);
    expect(container.querySelector('[data-render-mode="markdown"]')).toBeNull();
    expect(srcSetter).not.toHaveBeenCalled();
    expect(container.textContent).toBe(ATTACK_TEXT);
    // 带专属类：系统消息（日记预览）里也按 pre-wrap 逐字显示
    expect(container.querySelector('.message-block-text-plain')).not.toBeNull();
  });

  it('keeps the Markdown path for the same text without plain_text (the test is live)', () => {
    const message = messageWith({ type: 'text', text: ATTACK_TEXT });
    const { container } = render(
      <MessageBlockView block={message.blocks[0]} message={message} />,
    );

    expect(container.querySelectorAll('img').length).toBeGreaterThan(0);
  });

  it('keeps plain_text through the schema', () => {
    const message = messageWith({ type: 'text', text: 'hi', plain_text: true });
    expect(message.blocks[0]).toEqual({ type: 'text', text: 'hi', plain_text: true });
  });
});
