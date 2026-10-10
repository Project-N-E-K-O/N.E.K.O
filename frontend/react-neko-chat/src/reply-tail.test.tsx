import { act, render } from '@testing-library/react';
import App from './App';
import { parseChatMessage } from './message-schema';

it('reports compact completion only after the final character is committed', async () => {
  vi.useFakeTimers();
  const reports: { turnId?: string; complete?: boolean }[] = [];
  const observe = (event: Event) => reports.push((event as CustomEvent).detail);
  window.addEventListener('neko-reply-tail-presentation', observe);
  const text = 'This original reply is intentionally long enough that its final characters are still being revealed after generation ends.';
  const message = parseChatMessage({
    id: 'tail-original',
    role: 'assistant',
    author: 'Neko',
    time: '12:00',
    turnId: 'tail-turn',
    sortKey: 1,
    blocks: [{ type: 'text', text }],
    status: 'streaming',
  });
  try {
    const { container, unmount } = render(
      <App chatSurfaceMode="compact" messages={[message]} composerHidden />,
    );
    act(() => {
      window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
        detail: { turnId: 'tail-turn', requestId: 'tail-request' },
      }));
      window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
        detail: { turnId: 'tail-turn', segmentId: 'segment-A', text },
      }));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    act(() => {
      window.dispatchEvent(new CustomEvent('neko-assistant-speech-unavailable', {
        detail: { turnId: 'tail-turn' },
      }));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    act(() => {
      window.dispatchEvent(new CustomEvent('neko-assistant-turn-end', {
        detail: { turnId: 'tail-turn', requestId: 'tail-request' },
      }));
    });
    const before = reports.filter(report => report.turnId === 'tail-turn');
    expect(before[before.length - 1]?.complete).toBe(false);
    expect(container.querySelector('.compact-chat-capsule-text')?.textContent).not.toBe(text);
    for (let frame = 0; frame < 20; frame += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    }
    expect(container.querySelector('.compact-chat-capsule-text')).toHaveTextContent(text);
    const after = reports.filter(report => report.turnId === 'tail-turn');
    expect(after[after.length - 1]?.complete).toBe(true);
    unmount();
  } finally {
    window.removeEventListener('neko-reply-tail-presentation', observe);
    vi.useRealTimers();
  }
});
