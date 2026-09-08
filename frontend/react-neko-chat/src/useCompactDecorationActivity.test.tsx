import { cleanup, fireEvent, render } from '@testing-library/react';
import { useRef } from 'react';
import { useCompactDecorationActivity } from './useCompactDecorationActivity';

function Host({ enabled = true }: { enabled?: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useCompactDecorationActivity(ref, enabled);
  return <div ref={ref} data-testid="host"><input aria-label="message" /></div>;
}
const playState = (element: HTMLElement) => element.style.getPropertyValue('--compact-decoration-play-state');

describe('compact decoration activity', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('starts idle and settles even while the pointer and keyboard focus remain inside', () => {
    const { getByTestId, getByRole } = render(<Host />);
    const host = getByTestId('host');
    expect(playState(host)).toBe('');
    expect(vi.getTimerCount()).toBe(0);
    getByRole('textbox').focus();
    fireEvent.pointerMove(host);
    expect(playState(host)).toBe('running');
    vi.advanceTimersByTime(1800);
    expect(playState(host)).toBe('');
    expect(document.activeElement).toBe(getByRole('textbox'));
    expect(vi.getTimerCount()).toBe(0);
  });

  it('renews activity on captured input without accumulating timers, then resumes after idle', () => {
    const { getByTestId, getByRole } = render(<Host />);
    const host = getByTestId('host');
    fireEvent.pointerMove(host);
    vi.advanceTimersByTime(1000);
    fireEvent.keyDown(getByRole('textbox'), { key: 'a' });
    fireEvent.input(getByRole('textbox'));
    expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(1799);
    expect(playState(host)).toBe('running');
    vi.advanceTimersByTime(1);
    expect(playState(host)).toBe('');
    fireEvent.wheel(host);
    expect(playState(host)).toBe('running');
  });

  it('stops on leave, blur and visibility loss, but also times out when Electron reports visible', () => {
    const { getByTestId } = render(<Host />);
    const host = getByTestId('host');
    for (const stop of [() => fireEvent.pointerLeave(host), () => fireEvent(window, new Event('blur'))]) {
      fireEvent.pointerMove(host);
      stop();
      expect(playState(host)).toBe('');
      expect(vi.getTimerCount()).toBe(0);
    }
    fireEvent.pointerDown(host);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    fireEvent(document, new Event('visibilitychange'));
    fireEvent.pointerMove(host);
    expect(playState(host)).toBe('');
    expect(vi.getTimerCount()).toBe(0);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    fireEvent.pointerMove(host);
    vi.advanceTimersByTime(1800);
    expect(playState(host)).toBe('');
  });

  it('cleans up when minimized or unmounted and reattaches on restore', () => {
    const { getByTestId, rerender, unmount } = render(<Host />);
    const host = getByTestId('host');
    fireEvent.pointerMove(host);
    rerender(<Host enabled={false} />);
    fireEvent.pointerMove(host);
    expect(playState(host)).toBe('');
    expect(vi.getTimerCount()).toBe(0);
    rerender(<Host />);
    fireEvent.pointerMove(host);
    expect(playState(host)).toBe('running');
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    fireEvent.pointerMove(host);
    expect(playState(host)).toBe('');
  });
});
