import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HistoryDialog } from './ChatHistory';

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes('characters') ? { characters: ['Alice', 'Bob'] } : { messages: [{ id: 5, role: 'user', text: '<script>音乐</script>', time: '2026-11-01' }], nextCursor: 5, available: true } })));
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
  fireEvent.click(screen.getByText('back'));
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.not.stringContaining('anchor='), expect.anything()));
});
it('resets pagination when changing character and persists only preferences', async () => {
  render(<HistoryDialog close={vi.fn()} />);
  fireEvent.click(await screen.findByText('more'));
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining('cursor=5'), expect.anything()));
  fireEvent.change(screen.getByLabelText('character'), { target: { value: 'Bob' } });
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining('character=Bob&q=&role=all&cursor=0'), expect.anything()));
  expect(localStorage.getItem('neko.chatHistory.preferences')).not.toContain('Bob');
});
