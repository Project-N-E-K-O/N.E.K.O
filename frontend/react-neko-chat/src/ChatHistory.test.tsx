import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HistoryDialog } from './ChatHistory';
import { chatWindowPropsSchema } from './message-schema';

it('accepts the history tool position through the public mount contract', () => {
  expect(chatWindowPropsSchema.safeParse({ compactToolWheelIndexRequest: { id: 'history', index: 7 } }).success).toBe(true);
});

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn();
  HTMLDialogElement.prototype.close = vi.fn();
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes('characters') ? { characters: ['Alice', 'Bob'] } : { messages: [{ id: 5, role: 'user', text: '<script>音乐</script>', time: '2026-11-01' }], nextCursor: null, available: true } })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });
it('searches literal text, locates context and returns to the same result page', async () => {
  render(<HistoryDialog close={vi.fn()} />);
  await screen.findByText('<script>音乐</script>');
  fireEvent.change(screen.getByLabelText('keyword'), { target: { value: '音乐' } });
  fireEvent.click(screen.getByText('search'));
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining('q=%E9%9F%B3%E4%B9%90'), expect.anything()));
  await screen.findByText('context');
  expect(document.querySelector('mark')?.textContent).toBe('音乐');
  expect(document.querySelector('script')).toBeNull();
  fireEvent.click(screen.getByText('context'));
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining('anchor=5'), expect.anything()));
  await waitFor(() => expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled());
  const targets = vi.mocked(HTMLElement.prototype.scrollIntoView).mock.instances;
  const target = targets[targets.length - 1] as HTMLElement;
  expect(target.closest('[hidden]')).toBeNull();
  expect(target.dataset.historyId).toBe('5');
  fireEvent.click(screen.getByText('back'));
  await screen.findByText('context');
  expect(document.querySelector('.history-results:not([hidden]) [data-history-id="5"]')).not.toBeNull();
});
it('resets pagination when changing character and persists only preferences', async () => {
  render(<HistoryDialog close={vi.fn()} />);
  await screen.findByText('<script>音乐</script>');
  expect(screen.queryByText('more')).toBeNull();
  fireEvent.change(screen.getByLabelText('character'), { target: { value: 'Bob' } });
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining('character=Bob&q=&role=all&limit=30&order=desc&cursor=0'), expect.anything()));
  expect(localStorage.getItem('neko.chatHistory.preferences')).not.toContain('Bob');
});

it('registers the modal with desktop hit testing and refreshes geometry on close', async () => {
  const refresh = vi.fn();
  window.addEventListener('neko:compact-interaction-geometry-refresh', refresh);
  const view = render(<HistoryDialog close={vi.fn()} />);
  expect(screen.getByRole('dialog', { hidden: true })).toHaveClass('neko-dialog-overlay');
  expect(screen.getByRole('dialog', { hidden: true })).toHaveAttribute('data-compact-geometry-owner', 'surface');
  expect(refresh).toHaveBeenCalled();
  view.unmount();
  expect(HTMLDialogElement.prototype.close).toHaveBeenCalled();
  window.removeEventListener('neko:compact-interaction-geometry-refresh', refresh);
});

it('opens history from the existing compact wheel and closes only the modal', async () => {
  const { default: App } = await import('./App');
  const view = render(<App chatSurfaceMode="compact" compactChatState="input"
    compactToolFanOpenRequest={{ id: 'history-test', open: true }}
    compactToolWheelIndexRequest={{ id: 'history-index', index: 7 }} />);
  const button = view.container.querySelector('.compact-input-tool-item-history') as HTMLButtonElement;
  await waitFor(() => expect(button).not.toBeDisabled());
  expect(view.container.querySelector('.history-launch')).toBeNull();
  fireEvent.click(button);
  await screen.findByText('<script>音乐</script>');
  fireEvent.click(screen.getByLabelText('close'));
  expect(view.container.querySelector('.history-dialog')).toBeNull();
  expect(view.container.querySelector('.app-shell')).not.toBeNull();
});

it('defaults automatic continuation on in settings and remembers the switch without starting audio', async () => {
  const view = render(<HistoryDialog close={vi.fn()} />);
  await screen.findByText('<script>音乐</script>');
  const setting = screen.getByLabelText('autoContinue');
  expect(setting).toBeChecked();
  expect(setting.closest('details')).not.toBeNull();
  expect(screen.queryByText('playAll')).toBeNull();
  expect(document.querySelector('.history-playback')).toBeNull();
  fireEvent.click(setting);
  expect(JSON.parse(localStorage.getItem('neko.chatHistory.preferences')!).autoContinue).toBe(false);
  view.unmount();
  render(<HistoryDialog close={vi.fn()} />);
  expect(screen.getByLabelText('autoContinue')).not.toBeChecked();
  expect(vi.mocked(fetch).mock.calls.every(([url]) => !String(url).includes('/playback?'))).toBe(true);
});

it('continues across midnight from a date-filtered view and stops when the browsing date changes', async () => {
  const before = { id: 10, role: 'assistant', text: 'night voice', time: '2026-11-10 23:55:00', voice: { recordId: 'night' } };
  const after = { id: 11, role: 'assistant', text: 'next morning voice', time: '2026-11-11 00:05:00', voice: { recordId: 'morning' } };
  let audio!: { onended: (() => void) | null; pause: ReturnType<typeof vi.fn>; play: ReturnType<typeof vi.fn> };
  class AudioFixture {
    onended = null; onerror = null; src = ''; preload = '';
    pause = vi.fn(); play = vi.fn(async () => {}); load = vi.fn(); removeAttribute = vi.fn();
    constructor() { audio = this; }
  }
  vi.stubGlobal('Audio', AudioFixture);
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () =>
    url.includes('characters') ? { characters: ['Alice'] } : url.includes('/voice-archive/message?')
      ? { clips: [{ url: '/api/voice-archive/clips/' + 'a'.repeat(64) + '/0', complete: true }] }
      : { messages: url.includes('/playback?') ? [after] : [before], nextCursor: null, available: true } })));
  render(<HistoryDialog close={vi.fn()} />);
  await screen.findByText('night voice');
  fireEvent.change(screen.getByLabelText('date', { selector: 'input' }), { target: { value: '2026-11-10' } });
  fireEvent.change(screen.getByLabelText('keyword'), { target: { value: 'voice' } });
  fireEvent.click(screen.getByText('search'));
  fireEvent.click(await screen.findByLabelText('voicePlay'));
  await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(1));
  await act(async () => audio.onended?.());
  await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(2));
  expect(screen.getByText('next morning voice')).toBeInTheDocument();
  const call = vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes('/playback?'))!;
  const params = new URL(String(call[0]), 'http://localhost').searchParams;
  expect(params.has('day')).toBe(false);
  expect(Object.fromEntries(params)).toMatchObject({ character: 'Alice', q: 'voice', cursor: '10', after_time: before.time });
  expect(screen.getByLabelText('date', { selector: 'input' })).toHaveValue('2026-11-10');
  fireEvent.change(screen.getByLabelText('date', { selector: 'input' }), { target: { value: '2026-11-11' } });
  expect(audio.onended).toBeNull();
  expect(document.querySelector('.history-playback')).toBeNull();
});


it('loads recording opt-in from the server and saves it independently of autoplay', async () => {
  let enabled = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => ({ ok: true, json: async () => {
    if (url.includes('/voice-archive/settings')) {
      if (options?.method === 'POST') enabled = JSON.parse(String(options.body)).enabled;
      return { enabled };
    }
    return url.includes('characters') ? { characters: ['Alice'] } : { messages: [], nextCursor: null, available: true };
  } })));
  const { unmount } = render(<HistoryDialog close={vi.fn()} />);
  const toggle = screen.getByLabelText('recordVoice');
  await waitFor(() => expect(toggle).toBeEnabled());
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toBeChecked());
  expect(screen.getByLabelText('autoContinue')).toBeChecked();
  unmount();
  render(<HistoryDialog close={vi.fn()} />);
  await waitFor(() => expect(screen.getByLabelText('recordVoice')).toBeChecked());
  fireEvent.click(screen.getByLabelText('recordVoice'));
  await waitFor(() => expect(screen.getByLabelText('recordVoice')).not.toBeChecked());
});

it('restores the tool toggle when the original entry is disabled on close', async () => {
  const main = document.createElement('main');
  const toggle = document.createElement('button'); toggle.className = 'compact-input-tool-toggle';
  const entry = document.createElement('button');
  main.append(toggle, entry); document.body.append(main); entry.focus();
  const { unmount } = render(<HistoryDialog close={vi.fn()} />);
  await screen.findByText('<script>音乐</script>');
  entry.disabled = true; entry.blur(); unmount();
  await waitFor(() => expect(document.activeElement).toBe(toggle));
  main.remove();
});


it('ignores a stale settings GET after a newer recording POST', async () => {
  let resolveSlowGet!: (value: { ok: boolean; json: () => Promise<{enabled: boolean}> }) => void;
  const slowGet = new Promise<{ ok: boolean; json: () => Promise<{enabled: boolean}> }>(
    resolve => { resolveSlowGet = resolve; }
  );
  let gets = 0;
  let posts = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes('/voice-archive/settings')) {
      if (options?.method === 'POST') {
        posts++;
        return { ok: posts > 1, json: async () => ({ enabled: true }) };
      }
      return ++gets === 1 ? { ok: true, json: async () => ({ enabled: false }) } : slowGet;
    }
    return { ok: true, json: async () => url.includes('characters')
      ? { characters: ['Alice'] } : { messages: [], nextCursor: null, available: true } };
  }));
  render(<HistoryDialog close={vi.fn()} />);
  const toggle = screen.getByLabelText('recordVoice');
  await waitFor(() => expect(toggle).toBeEnabled());
  fireEvent.click(toggle); // First POST fails, exposing Retry without changing the setting.
  const retry = await screen.findByText('retry');
  fireEvent.click(retry);
  await waitFor(() => expect(gets).toBe(2));
  fireEvent.click(toggle); // A newer POST succeeds while the retry GET is in flight.
  await waitFor(() => expect(toggle).toBeChecked());
  await act(async () => resolveSlowGet({ ok: true, json: async () => ({ enabled: false }) }));
  expect(toggle).toBeChecked();
});

