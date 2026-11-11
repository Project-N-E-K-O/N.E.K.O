import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useChatAudioHistory } from './useChatAudioHistory';

const payload = { target: 'YUI', requestId: 'test-request', audio: 'AAAA', sampleRateHz: 16000 };
const historyRow = {
  request_id: payload.requestId, created_at: 1000, duration_ms: 600, state: 'sent', transcript: '',
};
const historyPage = (messages: object[] = []) => Response.json({ messages, next_before: null });

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('resolves the send before history responds and clears the temporary row if refresh times out', async () => {
  vi.useFakeTimers();
  const fetchHistory = vi.fn().mockResolvedValueOnce(historyPage());
  fetchHistory.mockImplementationOnce((_url: string, { signal }: RequestInit) =>
    new Promise((_resolve, reject) => {
      signal!.addEventListener('abort', () => reject(signal!.reason), { once: true });
    }));
  vi.stubGlobal('fetch', fetchHistory);
  const { result } = renderHook(() => useChatAudioHistory('YUI', 'You'));

  const send = vi.fn().mockResolvedValue('completed');
  await act(async () => {
    await expect(result.current.submit(payload, send)).resolves.toBe('completed');
  });
  expect(send).toHaveBeenCalledWith({ ...payload, saveLocal: true });
  expect(fetchHistory).toHaveBeenCalledTimes(2);
  expect(result.current.messages[0]?.status).toBe('sent');

  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(result.current.messages).toEqual([]);
});

it('keeps a deleted history row gone while the original send is still awaiting its receipt', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(historyPage())
    .mockResolvedValueOnce(historyPage([historyRow]))
    .mockImplementation(async () => historyPage()));
  const { result } = renderHook(() => useChatAudioHistory('YUI', 'You'));
  let finish!: () => void;
  const send = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  let submitted!: Promise<unknown>;
  await act(async () => {
    submitted = result.current.submit(payload, send);
  });
  expect(result.current.messages[0]?.status).toBe('sending');

  act(() => { window.dispatchEvent(new Event('chat-audio-local-change')); });
  await waitFor(() => expect(result.current.messages[0]?.status).toBe('sent'));
  act(() => { window.dispatchEvent(new Event('chat-audio-local-change')); });
  await waitFor(() => expect(result.current.messages).toEqual([]));

  await act(async () => {
    finish();
    await submitted;
  });
  expect(result.current.messages).toEqual([]);
});

it('returns a storage error when the first history reads time out and allows the next send', async () => {
  vi.useFakeTimers();
  const stalled = (_url: string, { signal }: RequestInit) =>
    new Promise((_resolve, reject) => {
      signal!.addEventListener('abort', () => reject(signal!.reason), { once: true });
    });
  const fetchHistory = vi.fn()
    .mockImplementationOnce(stalled)
    .mockImplementationOnce(stalled)
    .mockImplementation(async () => historyPage());
  vi.stubGlobal('fetch', fetchHistory);
  const { result } = renderHook(() => useChatAudioHistory('YUI', 'You'));
  const send = vi.fn().mockResolvedValue(undefined);

  await act(async () => {
    const failed = expect(result.current.submit(payload, send)).rejects.toMatchObject({
      name: 'AudioMessageRejected', message: 'storage',
    });
    await vi.advanceTimersByTimeAsync(10000);
    await failed;
  });
  expect(send).not.toHaveBeenCalled();

  await act(async () => { await result.current.submit(payload, send); });
  expect(send).toHaveBeenCalledOnce();
});

it('loads and submits with only the baseline abort controller API', async () => {
  const fetchHistory = vi.fn().mockImplementation(async () => historyPage([historyRow]));
  vi.stubGlobal('AbortSignal', {});
  vi.stubGlobal('fetch', fetchHistory);
  const { result } = renderHook(() => useChatAudioHistory('YUI', 'You'));
  await waitFor(() => expect(result.current.messages[0]?.status).toBe('sent'));
});
