import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import MessageBlockView from './MessageBlockView';
import App from './App';
import { parseChatMessage } from './message-schema';

const id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
function message(state = 'pending') {
  return parseChatMessage({ id: 'music-recommendation-' + id, role: 'assistant', author: 'Neko', time: '12:00',
    blocks: [{ type: 'music_recommendation', recommendationId: id, state,
      track: { name: '<script>A song</script>', artist: 'Artist', url: 'https://example.com/a.mp3', cover: '' } }] });
}

describe('music recommendation card', () => {
  it('shows the shared ten-second deadline below the buttons and removes it after a decision', () => {
    vi.useFakeTimers();
    try {
      const msg = message();
      if (msg.blocks[0].type !== 'music_recommendation') throw new Error('Expected music block');
      const block = { ...msg.blocks[0], autoPlayAt: Date.now() + 10000 };
      const view = render(<MessageBlockView block={block} message={msg} />);
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '10');
      expect(screen.getByText('Plays automatically in 10 s')).toBeInTheDocument();
      act(() => vi.advanceTimersByTime(4000));
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '6');
      view.rerender(<MessageBlockView block={{ ...block, state: 'skipped' }} message={msg} />);
      expect(screen.queryByRole('progressbar')).toBeNull();
      view.unmount();
    } finally { vi.useRealTimers(); }
  });
  it('uses a text bubble without a cover in compact presentation and keeps the same decisions', () => {
    const msg = message();
    const action = vi.fn();
    const { container } = render(<MessageBlockView block={msg.blocks[0]} message={msg} presentation="bubble" onAction={action} />);
    expect(container.querySelector('.music-recommendation-bubble')).toBeInTheDocument();
    expect(container.querySelector('.music-recommendation-card,img')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Not now/ }));
    expect(action).toHaveBeenLastCalledWith(msg, expect.objectContaining({ payload: { recommendationId: id, kind: 'skipped' } }));
  });
  it('shows an actionable bubble with compact history closed, then a card when switching to full', () => {
    const msg = message();
    const action = vi.fn();
    const view = render(<App chatSurfaceMode="compact" messages={[msg]} onMessageAction={action} />);
    expect(view.container.querySelector('.compact-music-recommendation-prompt .music-recommendation-bubble')).toBeInTheDocument();
    expect(view.container.querySelector('.compact-music-recommendation-prompt')).toHaveAttribute('data-compact-geometry-owner', 'surface');
    expect(view.container.querySelector('.music-recommendation-card')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Listen/ }));
    expect(action).toHaveBeenLastCalledWith(msg, expect.objectContaining({ payload: { recommendationId: id, kind: 'accepted' } }));
    fireEvent.click(view.container.querySelector('.compact-history-visibility-handle')!);
    expect(view.container.querySelector('.compact-export-history-bubble .music-recommendation-bubble')).toBeInTheDocument();
    expect(view.container.querySelector('.compact-music-recommendation-prompt')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Not now/ }));
    expect(action).toHaveBeenLastCalledWith(msg, expect.objectContaining({ payload: { recommendationId: id, kind: 'skipped' } }));
    view.rerender(<App chatSurfaceMode="full" messages={[msg]} onMessageAction={action} />);
    expect(view.container.querySelector('.music-recommendation-card img')).toBeInTheDocument();
    expect(view.container.querySelector('.compact-music-recommendation-prompt')).not.toBeInTheDocument();
    view.rerender(<App chatSurfaceMode="compact" messages={[message('accepted')]} onMessageAction={action} />);
    expect(view.container.querySelector('.compact-music-recommendation-prompt')).not.toBeInTheDocument();
  });
  it('routes visit history actions and recommendation decisions through the shared compact host callback', () => {
    const music = message();
    const visit = parseChatMessage({ id: 'visit-history', role: 'system', author: 'Neko', time: '12:00',
      blocks: [{ type: 'buttons', buttons: [{ id: 'details', label: 'Visit details', action: 'visit_details',
        payload: { visit_id: 'visit-test' } }] }] });
    const action = vi.fn();
    window.localStorage.setItem('neko.reactChatWindow.compactExportHistoryOpen', 'false');
    const view = render(<App chatSurfaceMode="compact" messages={[visit, music]} onMessageAction={action} />);
    expect(view.container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(view.container.querySelector('.compact-history-visibility-handle')!);
    expect(view.container.querySelector('.compact-history-visibility-handle')).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Visit details' }));
    expect(action).toHaveBeenLastCalledWith(visit, expect.objectContaining({ action: 'visit_details' }));
    fireEvent.click(screen.getByRole('button', { name: /Listen/ }));
    expect(action).toHaveBeenLastCalledWith(music, expect.objectContaining({ action: 'music_recommendation_decision',
      payload: { recommendationId: id, kind: 'accepted' } }));
  });
  it('renders a safe card and emits accept/skip without creating audio or opening links', () => {
    const msg = message();
    const action = vi.fn();
    const { container } = render(<MessageBlockView block={msg.blocks[0]} message={msg} onAction={action} />);
    expect(screen.getByText('<script>A song</script>')).toBeInTheDocument();
    expect(container.querySelectorAll('audio,a,script')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: /Listen/ }));
    expect(action).toHaveBeenLastCalledWith(msg, expect.objectContaining({ payload: { recommendationId: id, kind: 'accepted' } }));
    fireEvent.click(screen.getByRole('button', { name: /Not now/ }));
    expect(action).toHaveBeenLastCalledWith(msg, expect.objectContaining({ payload: { recommendationId: id, kind: 'skipped' } }));
  });
  it.each(['saving', 'accepted', 'skipped', 'playing', 'paused', 'ended', 'stopped', 'failed', 'unavailable'])('disables decisions in %s state', state => {
    const msg = message(state);
    render(<MessageBlockView block={msg.blocks[0]} message={msg} />);
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.getByRole('status')).not.toBeEmptyDOMElement();
  });
  it('keeps history previews noninteractive and does not record an exposure', () => {
    const msg = message();
    const action = vi.fn();
    render(<MessageBlockView block={msg.blocks[0]} message={msg} interactive={false} onAction={action} />);
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(action).not.toHaveBeenCalled();
  });
  it('records a visible card when a background tab returns, once only', () => {
    let intersect: (entries: { isIntersecting: boolean }[]) => void = () => {};
    const disconnect = vi.fn();
    const descriptor = Object.getOwnPropertyDescriptor(document, 'visibilityState');
    let hidden = true;
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: typeof intersect) { intersect = callback; }
      observe() {}
      disconnect = disconnect;
    });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => hidden ? 'hidden' : 'visible' });
    const msg = message();
    const action = vi.fn();
    const view = render(<MessageBlockView block={msg.blocks[0]} message={msg} onAction={action} />);
    try {
      intersect([{ isIntersecting: true }]);
      expect(action).not.toHaveBeenCalled();
      hidden = false;
      fireEvent(document, new Event('visibilitychange'));
      fireEvent(document, new Event('visibilitychange'));
      expect(action).toHaveBeenCalledTimes(1);
      expect(action).toHaveBeenCalledWith(msg, expect.objectContaining({ action: 'music_recommendation_shown' }));
    } finally {
      view.unmount();
      if (descriptor) Object.defineProperty(document, 'visibilityState', descriptor);
      else Reflect.deleteProperty(document, 'visibilityState');
      vi.unstubAllGlobals();
    }
    expect(disconnect).toHaveBeenCalled();
  });
});
