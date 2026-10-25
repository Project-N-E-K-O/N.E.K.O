import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import CompactMusicRecommendationPrompt from './CompactMusicRecommendationPrompt';
import { parseChatMessage } from './message-schema';

const musicId = '19f3bbca-a275-43d4-923d-1b2ceee91943';
const contentId = '023bf0f3-77ac-433a-8138-5ba7dc6da15f';
const music = (fields: Record<string, unknown> = {}) => parseChatMessage({
  id: 'music', role: 'assistant', author: 'Neko', time: '', turnId: 'topic',
  blocks: [{ type: 'music_recommendation', recommendationId: musicId,
    state: 'pending', autoPlayAt: Date.now() + 10000,
    track: { name: 'Countdown song', artist: 'Artist', url: 'https://example.com/song.mp3', cover: '' }, ...fields }],
});
const content = (category: 'meme' | 'vision') => parseChatMessage({
  id: 'content', role: 'assistant', author: 'Neko', time: '', turnId: 'topic',
  blocks: [{ type: 'proactive_recommendation', recommendationId: contentId,
    state: 'pending', directDelivery: true, category,
    content: { title: 'New direct content', source: '', url: category === 'meme' ? 'https://example.com/meme.png' : '' } }],
});

describe('compact recommendation priority', () => {
  afterEach(() => vi.useRealTimers());

  it.each(['meme', 'vision'] as const)('keeps countdown choices visible when a %s arrives, then displays it after a decision', category => {
    vi.useFakeTimers();
    const message = music();
    const onAction = vi.fn();
    const view = render(<CompactMusicRecommendationPrompt messages={[message]} onAction={onAction} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '10');
    view.rerender(<CompactMusicRecommendationPrompt messages={[message, content(category)]} onAction={onAction} />);
    expect(screen.getByText('Countdown song')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(4000));
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '6');
    fireEvent.click(screen.getByRole('button', { name: /Not now/ }));
    expect(onAction).toHaveBeenLastCalledWith(message, expect.objectContaining({
      action: 'music_recommendation_decision', payload: { recommendationId: musicId, kind: 'skipped' },
    }));
    view.rerender(<CompactMusicRecommendationPrompt messages={[music({ state: 'skipped' }), content(category)]} onAction={onAction} />);
    expect(view.container.querySelector('[data-proactive-category]')).toHaveAttribute('data-proactive-category', category);
    expect(screen.queryByText('Countdown song')).toBeNull();
    view.unmount();
  });

  it.each([
    { autoPlayAt: 0 },
    { autoPlayAt: Date.now() - 1 },
    { error: true },
    { state: 'accepted' },
  ])('preserves latest-content selection when music has no active countdown: %j', fields => {
    const view = render(<CompactMusicRecommendationPrompt messages={[music(fields), content('vision')]} />);
    expect(screen.getByText('New direct content')).toBeInTheDocument();
    expect(screen.queryByText('Countdown song')).toBeNull();
    view.unmount();
  });
});
