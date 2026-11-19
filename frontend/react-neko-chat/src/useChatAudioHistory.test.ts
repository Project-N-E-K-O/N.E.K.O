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

it('shows a remote recording for the current chat without enabling local history', async () => {
  const fetchHistory = vi.fn(async () => new Response(null, { status: 403 }));
  vi.stubGlobal('fetch', fetchHistory);
  const { result, rerender } = renderHook(({ target }) => useChatAudioHistory(target, 'You'), {
    initialProps: { target: 'YUI' },
  });
  let finish!: () => void;
  const send = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  let submitted!: Promise<unknown>;
  await act(async () => { submitted = result.current.submit(payload, send); });

  expect(result.current.messages).toMatchObject([{
    id: `audio-message:${payload.requestId}`, role: 'user', status: 'sending',
    blocks: [{ type: 'audio_message', state: 'sending', available: false, transcript: '' }],
  }]);
  expect(send).toHaveBeenCalledExactlyOnceWith({ ...payload, saveLocal: false });
  act(() => { window.dispatchEvent(new Event('voice-input-socket-open')); });
  await waitFor(() => expect(fetchHistory).toHaveBeenCalledTimes(2));
  await act(async () => { finish(); await submitted; });
  expect(result.current.messages).toMatchObject([{
    status: 'sent', blocks: [{ state: 'sent', available: false, canLoadOlder: false }],
  }]);

  act(() => { window.dispatchEvent(new Event('react-chat-window:messages-cleared')); });
  expect(result.current.messages).toEqual([]);
  await act(async () => { await result.current.submit(payload, vi.fn().mockResolvedValue(undefined)); });
  expect(result.current.messages).toHaveLength(1);
  rerender({ target: 'Other' });
  rerender({ target: 'YUI' });
  expect(result.current.messages).toEqual([]);
});

it.each([true, false])('handles a remote send failure according to whether it was rejected (%s)', async rejected => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 403 })));
  const { result } = renderHook(() => useChatAudioHistory('YUI', 'You'));
  const failure = new Error(rejected ? 'busy' : 'socket closed');
  if (rejected) failure.name = 'AudioMessageRejected';
  const send = vi.fn().mockRejectedValue(failure);

  await act(async () => {
    await expect(result.current.submit(payload, send)).rejects.toBe(failure);
  });
  expect(send).toHaveBeenCalledTimes(1);
  if (rejected) {
    expect(result.current.messages).toEqual([]);
  } else {
    expect(result.current.messages).toMatchObject([{
      blocks: [{ state: 'unconfirmed', available: false }],
    }]);
  }
});

it('loads a fifth recording page when each request meets its timeout', async () => {
  vi.useFakeTimers();
  const fetchHistory = vi.fn((url: string, { signal }: RequestInit) => {
    const before = new URL(url, 'http://localhost').searchParams.get('before');
    const page = before === null ? 1 : Number(before);
    return new Promise<Response>((resolve, reject) => {
      const timer = setTimeout(() => resolve(Response.json({
        messages: [{ ...historyRow, request_id: String(page) }],
        next_before: page < 5 ? page + 1 : null,
      })), 1100);
      signal!.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(signal!.reason);
      }, { once: true });
    });
  });
  vi.stubGlobal('fetch', fetchHistory);
  const { result } = renderHook(() => useChatAudioHistory('YUI', 'You'));

  await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
  for (let pages = 2; pages <= 5; pages++) {
    expect(result.current.onLoadOlder).toBeDefined();
    act(() => { result.current.onLoadOlder?.(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1100 * pages); });
    expect(result.current.messages).toHaveLength(pages);
  }
  expect(result.current.onLoadOlder).toBeUndefined();
  expect(result.current.loadOlderStatus).toBe('idle');
});

it('keeps loaded recordings and allows retry when an older page times out reading JSON', async () => {
  vi.useFakeTimers();
  const timedOut = vi.fn();
  const older = { ...historyRow, request_id: 'older', created_at: 500 };
  const firstPage = () => Response.json({ messages: [historyRow], next_before: 2 });
  const fetchHistory = vi.fn()
    .mockImplementationOnce(async () => firstPage())
    .mockImplementationOnce(() => new Promise(resolve => setTimeout(() => resolve(firstPage()), 1200)))
    .mockImplementationOnce(async (_url: string, { signal }: RequestInit) => {
      const response = historyPage([older]);
      vi.spyOn(response, 'json').mockImplementation(() => new Promise((_resolve, reject) => {
        signal!.addEventListener('abort', () => {
          timedOut();
          reject(signal!.reason);
        }, { once: true });
      }));
      return response;
    })
    .mockImplementationOnce(async () => firstPage())
    .mockImplementationOnce(async () => historyPage([older]));
  vi.stubGlobal('fetch', fetchHistory);
  const { result } = renderHook(() => useChatAudioHistory('YUI', 'You'));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const loaded = result.current.messages;

  act(() => { result.current.onLoadOlder?.(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(6199); });
  expect(result.current.loadOlderStatus).toBe('loading');
  expect(timedOut).not.toHaveBeenCalled();

  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(timedOut).toHaveBeenCalledOnce();
  expect(result.current.loadOlderStatus).toBe('failed');
  expect(result.current.messages).toEqual(loaded);
  expect(result.current.onLoadOlder).toBeDefined();

  await act(async () => {
    result.current.onLoadOlder?.();
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.messages.map(message => message.id)).toEqual([
    'audio-message:test-request', 'audio-message:older',
  ]);
  expect(result.current.loadOlderStatus).toBe('idle');
});

it('keeps a confirmed recording playable when the history refresh times out', async () => {
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
  expect(result.current.messages).toHaveLength(1);
  expect(result.current.messages[0]?.status).toBe('sent');
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

it('does not restore old local recordings after a same-character chat clear', async () => {
  const newRow = { ...historyRow, request_id: 'new-recording', created_at: Date.now() + 1000 };
  const fetchHistory = vi.fn()
    .mockResolvedValueOnce(historyPage([historyRow]))
    .mockResolvedValueOnce(historyPage([historyRow, newRow]));
  vi.stubGlobal('fetch', fetchHistory);
  const { result } = renderHook(() => useChatAudioHistory('YUI', 'You'));
  await waitFor(() => expect(result.current.messages).toHaveLength(1));

  act(() => { window.dispatchEvent(new Event('react-chat-window:messages-cleared')); });
  expect(result.current.messages).toEqual([]);

  act(() => { window.dispatchEvent(new Event('voice-input-socket-open')); });
  await waitFor(() => {
    expect(result.current.messages.map(row => row.id)).toEqual([`audio-message:${newRow.request_id}`]);
  });
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
