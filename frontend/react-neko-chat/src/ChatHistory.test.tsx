import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
