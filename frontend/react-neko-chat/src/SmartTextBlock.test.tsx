import { render, screen } from '@testing-library/react';
import SmartTextBlock from './SmartTextBlock';

describe('SmartTextBlock', () => {
  it('keeps plain text in lightweight mode', () => {
    const { container } = render(<SmartTextBlock text={'这是一段普通文本\n带换行。'} />);

    expect(container.querySelector('[data-render-mode="markdown"]')).toBeNull();
    expect(container.querySelector('.message-block-text')?.textContent).toBe('这是一段普通文本\n带换行。');
  });

  it('renders markdown links and lists when markdown syntax is detected', () => {
    render(<SmartTextBlock text={'# 标题\n- 条目一\n- 条目二\nhttps://example.com'} />);

    expect(screen.getByRole('heading', { name: '标题' })).toBeInTheDocument();
    expect(screen.getByText('条目一')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://example.com' })).toHaveAttribute('href', 'https://example.com');
  });

  it('renders fenced code blocks and math expressions', () => {
    const text = '```python\nprint("hi")\n```\n\n$$a^2+b^2=c^2$$';
    const { container } = render(<SmartTextBlock text={text} />);

    expect(container.querySelector('.message-code-block')).not.toBeNull();
    expect(screen.getByText('python')).toBeInTheDocument();
    expect(container.querySelector('.katex')).not.toBeNull();
  });
});
