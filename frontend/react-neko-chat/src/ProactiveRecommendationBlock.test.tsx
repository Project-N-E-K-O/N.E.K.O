import { fireEvent, render, screen } from '@testing-library/react';
import App from './App';
import MessageBlockView from './MessageBlockView';
import { parseChatMessage, type MessageBlock } from './message-schema';

type ContentBlock = Extract<MessageBlock, { type: 'proactive_recommendation' }>;
const base: ContentBlock = { type: 'proactive_recommendation', recommendationId: '023bf0f3-77ac-433a-8138-5ba7dc6da15f',
  category: 'news', content: { title: 'A story to share', source: 'Example', url: 'https://example.com/story' }, state: 'pending' };
const message = (block: ContentBlock) => parseChatMessage({ id: 'proactive-content', role: 'assistant', author: 'Neko',
  time: '12:00', turnId: 'topic-turn', blocks: [block] });

describe('proactive recommendation presentation', () => {
  beforeEach(() => window.localStorage.clear());
  it('delivers new memes and screen observations directly without fabricating acceptance', () => {
    const action = vi.fn();
    const meme: ContentBlock = { ...base, category: 'meme', directDelivery: true };
    const view = render(<MessageBlockView block={meme} message={message(meme)} onAction={action} />);
    expect(view.container.querySelector('img')).toHaveAttribute('src', '/api/meme/proxy-image?url=https%3A%2F%2Fexample.com%2Fstory');
    expect(screen.queryByRole('button', { name: 'Show me' })).toBeNull();
    expect(action.mock.calls.some(([, event]) => event.action === 'proactive_recommendation_decision')).toBe(false);
    const vision: ContentBlock = { ...base, category: 'vision', directDelivery: true, content: { title: 'You are tending a farm.', source: '', url: '' } };
    view.rerender(<MessageBlockView block={vision} message={message(vision)} onAction={action} />);
    expect(screen.getByText('You are tending a farm.')).toBeInTheDocument();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryByText('Interest saved')).toBeNull();
  });
  it('retires a direct meme bubble after closing or the next user turn', () => {
    const block: ContentBlock = { ...base, category: 'meme', directDelivery: true };
    const view = render(<App chatSurfaceMode="compact" messages={[message(block)]} />);
    expect(view.container.querySelector('.compact-proactive-recommendation-prompt img')).not.toBeNull();
    view.rerender(<App chatSurfaceMode="compact" messages={[message({ ...block, dismissed: true })]} />);
    expect(view.container.querySelector('.compact-proactive-recommendation-prompt')).toBeNull();
    const user = parseChatMessage({ id: 'next', role: 'user', author: 'User', time: '', blocks: [{ type: 'text', text: 'Next' }] });
    view.rerender(<App chatSurfaceMode="compact" messages={[message(block), user]} />);
    expect(view.container.querySelector('.compact-proactive-recommendation-prompt')).toBeNull();
  });

  it.each(['news', 'community', 'video', 'personal', 'meme', 'vision', 'window', 'web', 'work_break', 'anti_slack'] as const)(
    'shows %s as a full card and a compact bubble without loading content', category => {
      const block = { ...base, category };
      const { container, rerender } = render(<App chatSurfaceMode="full" messages={[message(block)]} />);
      expect(container.querySelector('.proactive-recommendation-card')).toHaveAttribute('data-proactive-category', category);
      expect(container.querySelector('.proactive-recommendation img')).toBeNull();
      expect(container.querySelector('.proactive-recommendation a')).toBeNull();
      rerender(<App chatSurfaceMode="compact" messages={[message(block)]} />);
      expect(container.querySelector('.compact-proactive-recommendation-prompt .proactive-recommendation-bubble')).toHaveAttribute('data-proactive-category', category);
      expect(container.querySelector('.proactive-recommendation-card')).toBeNull();
    });

  it('routes consent and exposure by recommendation identity', () => {
    const onAction = vi.fn();
    const m = message(base);
    render(<MessageBlockView block={base} message={m} onAction={onAction} />);
    expect(onAction).toHaveBeenCalledWith(m, expect.objectContaining({ action: 'proactive_recommendation_shown' }));
    fireEvent.click(screen.getByRole('button', { name: 'View' }));
    expect(onAction).toHaveBeenCalledWith(m, expect.objectContaining({ action: 'proactive_recommendation_decision', payload: { recommendationId: base.recommendationId, kind: 'accepted' } }));
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    expect(onAction).toHaveBeenCalledWith(m, expect.objectContaining({ payload: { recommendationId: base.recommendationId, kind: 'skipped' } }));
  });

  it('renders an actual screen observation without a URL as a card and compact interest bubble', () => {
    const block: ContentBlock = { ...base, category: 'vision', content: { title: 'You are tending a farm.', source: '', url: '' } };
    const onMessageAction = vi.fn();
    const { container, rerender } = render(<App chatSurfaceMode="full" messages={[message(block)]} onMessageAction={onMessageAction} />);
    expect(container.querySelector('.proactive-recommendation-card')).toHaveAttribute('data-proactive-category', 'vision');
    expect(screen.getByText('You are tending a farm.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Interested' }));
    expect(onMessageAction).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ payload: { recommendationId: base.recommendationId, kind: 'accepted' } }));
    rerender(<App chatSurfaceMode="compact" messages={[message(block)]} onMessageAction={onMessageAction} />);
    expect(container.querySelector('.compact-proactive-recommendation-prompt .proactive-recommendation-bubble')).toHaveAttribute('data-proactive-category', 'vision');
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    expect(onMessageAction).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ payload: { recommendationId: base.recommendationId, kind: 'skipped' } }));
    rerender(<App chatSurfaceMode="full" messages={[message({ ...block, state: 'accepted' })]} />);
    expect(screen.getByText('Interest saved')).toBeInTheDocument();
    expect(container.querySelector('a')).toBeNull();
    rerender(<App chatSurfaceMode="full" messages={[message({ ...block, content: { title: ' ', source: '', url: '' } })]} />);
    expect(container.querySelector('.proactive-recommendation')).toBeNull();
  });

  it('replaces generated meme filenames with a readable title while preserving descriptive titles', () => {
    const block = { ...base, category: 'meme' as const, content: { ...base.content, title: '表情包_2' } };
    const { container, rerender } = render(<MessageBlockView block={block} message={message(block)} />);
    expect(screen.queryByText('表情包_2')).not.toBeInTheDocument();
    expect(screen.getByText('A meme I wanted to share with you')).toBeInTheDocument();
    expect(container.querySelector('.proactive-recommendation img')).toBeNull();
    const descriptive = { ...block, content: { ...block.content, title: 'A cat waving hello' } };
    rerender(<MessageBlockView block={descriptive} message={message(descriptive)} />);
    expect(screen.getByText('A cat waving hello')).toBeInTheDocument();
  });

  it('keeps an accepted meme visible in compact mode until closed or a new user turn', () => {
    const block: ContentBlock = { ...base, category: 'meme', state: 'accepted' };
    const onMessageAction = vi.fn();
    const { container, rerender } = render(<App chatSurfaceMode="compact" messages={[message(block)]} onMessageAction={onMessageAction} />);
    expect(container.querySelector('.proactive-recommendation img')).toHaveAttribute('src', '/api/meme/proxy-image?url=https%3A%2F%2Fexample.com%2Fstory');
    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }));
    expect(onMessageAction).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ action: 'proactive_recommendation_dismiss' }));
    rerender(<App chatSurfaceMode="compact" messages={[message({ ...block, dismissed: true })]} />);
    expect(container.querySelector('.compact-proactive-recommendation-prompt')).toBeNull();
    const laterRecommendation = parseChatMessage({ ...message({ ...base, state: 'skipped' }), id: 'later-news', turnId: 'later-turn' });
    rerender(<App chatSurfaceMode="compact" messages={[message(block), laterRecommendation]} />);
    expect(container.querySelector('.compact-proactive-recommendation-prompt img')).not.toBeNull();
    const assistant = parseChatMessage({ id: 'next-answer', role: 'assistant', author: 'Neko', time: '', turnId: 'next-answer', blocks: [{ type: 'text', text: 'Next topic' }] });
    rerender(<App chatSurfaceMode="compact" messages={[message(block), assistant]} />);
    expect(container.querySelector('.compact-proactive-recommendation-prompt')).toBeNull();
    const user = parseChatMessage({ id: 'new-user', role: 'user', author: 'User', time: '', blocks: [{ type: 'text', text: 'Next topic' }] });
    rerender(<App chatSurfaceMode="compact" messages={[message(block), user]} />);
    expect(container.querySelector('.compact-proactive-recommendation-prompt')).toBeNull();
  });

  it('disables saving/read-only actions on real source cards', () => {
    const block = base;
    const { rerender } = render(<MessageBlockView block={block} message={message(block)} />);
    expect(screen.getByRole('button', { name: 'View' })).toBeEnabled();
    rerender(<MessageBlockView block={{ ...block, state: 'saving' }} message={message(block)} />);
    expect(screen.getByRole('button', { name: 'View' })).toBeDisabled();
    rerender(<MessageBlockView block={block} message={message(block)} interactive={false} />);
    expect(screen.getByRole('button', { name: 'View' })).toBeDisabled();
  });

  it.each(['pending', 'saving', 'accepted', 'skipped', 'unavailable'] as const)('hides old generic %s cards in both surfaces without exposure', state => {
    const onMessageAction = vi.fn();
    const block = { ...base, category: 'chat' as const, state };
    const { container, rerender } = render(<App chatSurfaceMode="full" messages={[message(block)]} onMessageAction={onMessageAction} />);
    expect(container.querySelector('.proactive-recommendation')).toBeNull();
    expect(container.querySelector('[data-message-id="proactive-content"]')).toBeNull();
    rerender(<App chatSurfaceMode="compact" messages={[message(block)]} onMessageAction={onMessageAction} />);
    expect(container.querySelector('.compact-proactive-recommendation-prompt')).toBeNull();
    expect(onMessageAction).not.toHaveBeenCalled();
  });

  it('keeps a real news bubble actionable when a newer source-free placeholder arrives', () => {
    const empty = parseChatMessage({ ...message({ ...base, recommendationId: '123bf0f3-77ac-433a-8138-5ba7dc6da15f', content: { title: '', source: '', url: '' } }), id: 'empty-topic' });
    const { container, rerender } = render(<App chatSurfaceMode="compact" messages={[message(base), empty]} />);
    expect(container.querySelector('.compact-proactive-recommendation-prompt [data-recommendation-id]')).toHaveAttribute('data-recommendation-id', base.recommendationId);
    rerender(<App chatSurfaceMode="full" messages={[empty]} />);
    expect(container.querySelector('.proactive-recommendation')).toBeNull();
  });

  it('preserves ordinary dialogue when an old placeholder shares its message', () => {
    const mixed = parseChatMessage({ ...message({ ...base, category: 'chat' }), blocks: [
      { type: 'text', text: 'The actual conversation stays here.' }, { ...base, category: 'chat' },
    ] });
    const { container } = render(<App chatSurfaceMode="full" messages={[mixed]} />);
    expect(screen.getByText('The actual conversation stays here.')).toBeInTheDocument();
    expect(container.querySelector('.proactive-recommendation')).toBeNull();
  });

  it('does not resurrect an expired pending recommendation in the compact prompt', () => {
    const { container } = render(<App chatSurfaceMode="compact" messages={[message({ ...base, expiresAt: Date.now() - 1 })]} />);
    expect(container.querySelector('.compact-proactive-recommendation-prompt')).toBeNull();
  });
});
