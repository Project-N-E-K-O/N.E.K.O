import { useEffect, useRef, useState } from 'react';
import type { HistoryEntry, HistoryPage } from './useHistoryFeed';
import { claimVoicePlayback, releaseVoicePlayback } from './voicePlaybackOwner';

type Phase = 'idle' | 'loading' | 'playing' | 'paused' | 'done' | 'empty' | 'error';
type PlaybackState = { phase: Phase; current: HistoryEntry | null; sequence: boolean; partial: boolean };
type Session = { stop: () => void; pause: () => void; resume: () => void };
const idle: PlaybackState = { phase: 'idle', current: null, sequence: false, partial: false };

// This player belongs to the dialog, never a virtual row. At most one page of
// identities and the current audio resource are held by the playback queue.
export function useHistoryPlayback(character: string, scope: string, autoContinue = true, selectionKey = '') {
  const [state, setState] = useState<PlaybackState>(idle);
  const session = useRef<Session>();
  const autoContinueRef = useRef(autoContinue);
  autoContinueRef.current = autoContinue;
  const stop = () => session.current?.stop();
  useEffect(() => {
    const onSpeech = (event: Event) => { if ((event as CustomEvent).detail?.active) session.current?.stop(); };
    window.addEventListener('neko-speech-playback-state', onSpeech);
    return () => { session.current?.stop(); window.removeEventListener('neko-speech-playback-state', onSpeech); };
  }, [character, scope, selectionKey]);

  function start(single?: HistoryEntry) {
    session.current?.stop();
    if ((window as Window & { NekoSpeechPlaybackState?: { active?: boolean } }).NekoSpeechPlaybackState?.active) return;
    const audio = new Audio();
    audio.preload = 'none';
    const controller = new AbortController();
    let stopped = false, paused = false, played = false, inClip = false;
    let current: HistoryEntry | null = single ?? null, partial = false;
    let wake: (() => void) | undefined, rejectClip: ((error: Error) => void) | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const publish = (phase: Phase) => { if (!stopped) setState({ phase, current, partial, sequence: autoContinueRef.current }); };
    const abortError = () => new DOMException('Playback stopped', 'AbortError');
    const check = () => { if (stopped) throw abortError(); };
    const finish = (phase: Phase = 'idle') => {
      if (stopped) return;
      stopped = true;
      controller.abort(); clearTimeout(timer);
      audio.onended = null; audio.onerror = null;
      audio.pause(); audio.removeAttribute('src'); audio.load();
      wake?.(); rejectClip?.(abortError());
      releaseVoicePlayback(cancel);
      if (session.current === handle) session.current = undefined;
      setState({ ...idle, phase, current: phase === 'error' ? current : null });
    };
    const cancel = () => finish();
    const fail = () => finish('error');
    async function play() {
      if (stopped || paused || !inClip) return;
      clearTimeout(timer); timer = setTimeout(fail, 20000);
      try {
        await audio.play();
        if (stopped) return;
        clearTimeout(timer);
        if (paused) audio.pause(); else publish('playing');
      } catch { if (!stopped && !paused) fail(); }
    }
    const handle: Session = {
      stop: cancel,
      pause: () => { if (!stopped) { paused = true; clearTimeout(timer); audio.pause(); publish('paused'); } },
      resume: () => {
        if (!paused || stopped) return;
        paused = false; publish('loading'); wake?.(); wake = undefined;
        if (inClip) void play();
      },
    };
    claimVoicePlayback(cancel); session.current = handle; publish('loading');
    async function ready() {
      check();
      if (paused) await new Promise<void>(resolve => { wake = resolve; });
      check();
    }
    async function json<T>(url: string): Promise<T | null> {
      const timeout = setTimeout(fail, 20000);
      try {
        const response = await fetch(url, { signal: controller.signal, cache: 'no-store' });
        check();
        if (response.status === 404) return null;
        if (!response.ok) throw new Error('voice_unavailable');
        const result = await response.json(); check(); return result;
      } finally { clearTimeout(timeout); }
    }
    async function message(entry: HistoryEntry) {
      await ready(); current = entry; partial = false; publish('loading');
      const params = new URLSearchParams({ character, record_id: entry.voice!.recordId });
      const data = await json<{ clips: { url: string; complete: boolean }[] }>(`/api/voice-archive/message?${params}`);
      if (!data?.clips.length) return;
      partial = data.clips.some(clip => !clip.complete);
      for (const clip of data.clips) {
        await ready();
        if (!/^\/api\/voice-archive\/clips\/[a-f0-9]{64}\/\d+$/.test(clip.url)) throw new Error('invalid_clip');
        await new Promise<void>((resolve, reject) => {
          rejectClip = reject; inClip = true;
          audio.onended = () => { clearTimeout(timer); inClip = false; rejectClip = undefined; played = true; resolve(); };
          audio.onerror = () => reject(new Error('audio_error'));
          audio.src = clip.url;
          void play();
        });
      }
    }
    void (async () => {
      try {
        if (single?.voice) await message(single);
        if ((!single || single.time) && autoContinueRef.current && new URLSearchParams(scope).get('role') !== 'user') {
          let cursor = single?.id ?? 0, afterTime = single?.time ?? '';
          do {
            await ready();
            if (!autoContinueRef.current) break;
            publish('loading');
            const params = new URLSearchParams(scope);
            params.set('character', character); params.set('cursor', String(cursor));
            if (afterTime) params.set('after_time', afterTime);
            const page = await json<HistoryPage & { nextTime?: string }>(`/api/chat-history/playback?${params}`);
            if (!page) throw new Error('history_unavailable');
            for (const entry of page.messages) {
              await ready();
              if (!autoContinueRef.current) break;
              if (entry.voice) await message(entry);
            }
            if (!autoContinueRef.current) break;
            if (page.nextCursor === null) break;
            if (!page.nextTime || (page.nextTime === afterTime && page.nextCursor <= cursor) || page.nextTime < afterTime) throw new Error('invalid_cursor');
            cursor = page.nextCursor; afterTime = page.nextTime;
            // Empty scan pages must not turn into a tight archive-draining loop.
            await new Promise<void>(resolve => { wake = resolve; timer = setTimeout(resolve, 180); });
            wake = undefined; check();
          } while (!stopped);
        }
        finish(played ? 'done' : 'empty');
      } catch { if (!stopped) fail(); }
    })();
  }
  return { ...state, start, stop, pause: () => session.current?.pause(), resume: () => session.current?.resume() };
}
