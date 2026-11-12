import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useHistoryPlayback } from './useHistoryPlayback';
import { claimVoicePlayback, releaseVoicePlayback } from './voicePlaybackOwner';
import type { HistoryEntry } from './useHistoryFeed';

const clip = '/api/voice-archive/clips/' + 'a'.repeat(64) + '/0';
const entry = (id: number): HistoryEntry => ({ id, role: 'assistant', time: `2026-11-01 12:00:${id}`, text: `message ${id}`, voice: { recordId: `record-${id}` } });
let players: FakeAudio[];
class FakeAudio {
  src = ''; preload = ''; onended: (() => void) | null = null; onerror: (() => void) | null = null;
  play = vi.fn(async () => {}); pause = vi.fn(); load = vi.fn(); removeAttribute = vi.fn();
  constructor() { players.push(this); }
}
const response = (data: unknown) => ({ ok: true, status: 200, json: async () => data });
const manifest = () => response({ clips: [{ url: clip, complete: true }] });
beforeEach(() => { players = []; vi.stubGlobal('Audio', FakeAudio); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('loads only the current batch and advances after all segments end', async () => {
  const fetcher = vi.fn(async (url: string) => url.includes('/playback?')
    ? response(url.includes('cursor=0') ? { messages: [entry(1), entry(2)], nextCursor: 2, nextTime: entry(2).time }
      : { messages: [entry(3)], nextCursor: null }) : manifest());
  vi.stubGlobal('fetch', fetcher);
  const { result } = renderHook(() => useHistoryPlayback('Alice', 'day=2026-11-01&q=message'));
  act(() => result.current.start());
  await waitFor(() => expect(result.current.phase).toBe('playing'));
  expect(result.current.current?.id).toBe(1);
  expect(fetcher.mock.calls.filter(([url]) => url.includes('/playback?'))).toHaveLength(1);
  act(() => players[0].onended?.());
  await waitFor(() => expect(result.current.current?.id).toBe(2));
  await waitFor(() => expect(players[0].play).toHaveBeenCalledTimes(2));
  act(() => players[0].onended?.());
  await waitFor(() => expect(result.current.current?.id).toBe(3));
  expect(fetcher.mock.calls.find(([url]) => url.includes('cursor=2'))?.[0]).toContain('after_time=');
  expect(fetcher.mock.calls.filter(([url]) => url.includes('/playback?')).every(([url]) => url.includes('day=2026-11-01&q=message'))).toBe(true);
  await waitFor(() => expect(players[0].play).toHaveBeenCalledTimes(3));
  act(() => players[0].onended?.());
  await waitFor(() => expect(result.current.phase).toBe('done'));
});

it('pauses during metadata loading and resumes without restarting the request', async () => {
  let resolve!: (value: unknown) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise(r => { resolve = r; })));
  const { result } = renderHook(() => useHistoryPlayback('Alice', ''));
  act(() => result.current.start(entry(1)));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  act(() => result.current.pause());
  await act(async () => resolve(manifest()));
  expect(players[0].play).not.toHaveBeenCalled();
  expect(result.current.phase).toBe('paused');
  act(() => result.current.resume());
  await waitFor(() => expect(result.current.phase).toBe('playing'));
  act(() => result.current.pause());
  expect(players[0].pause).toHaveBeenCalled();
  act(() => result.current.resume());
  await waitFor(() => expect(players[0].play).toHaveBeenCalledTimes(2));
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('cancels stale requests when filters change and releases audio on close', async () => {
  let resolve!: (value: unknown) => void;
  let signal!: AbortSignal;
  vi.stubGlobal('fetch', vi.fn((_url, options) => { signal = options.signal; return new Promise(r => { resolve = r; }); }));
  const { result, rerender, unmount } = renderHook(({ scope }) => useHistoryPlayback('Alice', scope), { initialProps: { scope: '' } });
  act(() => result.current.start(entry(1)));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  rerender({ scope: 'day=2026-11-02' });
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(manifest()));
  expect(players[0].play).not.toHaveBeenCalled();
  vi.stubGlobal('fetch', vi.fn(async () => manifest()));
  act(() => result.current.start(entry(2)));
  await waitFor(() => expect(result.current.phase).toBe('playing'));
  unmount();
  expect(players[1].pause).toHaveBeenCalled();
  expect(players[1].onended).toBeNull();
});

it('shares ownership with other replay surfaces and stops for live speech', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => manifest()));
  const previous = vi.fn(); claimVoicePlayback(previous);
  const { result } = renderHook(() => useHistoryPlayback('Alice', ''));
  act(() => result.current.start(entry(1)));
  expect(previous).toHaveBeenCalled();
  await waitFor(() => expect(result.current.phase).toBe('playing'));
  act(() => window.dispatchEvent(new CustomEvent('neko-speech-playback-state', { detail: { active: true } })));
  expect(result.current.phase).toBe('idle');
  act(() => result.current.start(entry(2)));
  await waitFor(() => expect(result.current.phase).toBe('playing'));
  const other = vi.fn(); act(() => claimVoicePlayback(other));
  expect(result.current.phase).toBe('idle'); releaseVoicePlayback(other);
});

it('continues bounded empty batches and reports exhaustion without audio', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({ messages: [], nextCursor: 1, nextTime: entry(1).time }))
    .mockResolvedValueOnce(response({ messages: [], nextCursor: null })));
  const { result } = renderHook(() => useHistoryPlayback('Alice', ''));
  act(() => result.current.start());
  await waitFor(() => expect(result.current.phase).toBe('empty'));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(players[0].play).not.toHaveBeenCalled();
});

it('plays incomplete multi-segment messages and stops on decode failure', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({ clips: [{ url: clip, complete: false }, { url: clip.replace(/0$/, '3'), complete: false }] })));
  const { result } = renderHook(() => useHistoryPlayback('Alice', ''));
  act(() => result.current.start(entry(1)));
  await waitFor(() => expect(result.current.phase).toBe('playing'));
  expect(result.current.partial).toBe(true);
  act(() => players[0].onended?.());
  await waitFor(() => expect(players[0].src).toContain('/3'));
  act(() => players[0].onerror?.());
  await waitFor(() => expect(result.current.phase).toBe('error'));
  expect(players[0].pause).toHaveBeenCalled();
});

it('rejects unsafe clip URLs and does not continue after a network error', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({ clips: [{ url: 'https://example.com/private.wav', complete: true }] })));
  const { result } = renderHook(() => useHistoryPlayback('Alice', ''));
  act(() => result.current.start(entry(1)));
  await waitFor(() => expect(result.current.phase).toBe('error'));
  expect(players[0].play).not.toHaveBeenCalled();
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503 })));
  act(() => result.current.start());
  await waitFor(() => expect(result.current.phase).toBe('error'));
  expect(fetch).toHaveBeenCalledTimes(1);
});
