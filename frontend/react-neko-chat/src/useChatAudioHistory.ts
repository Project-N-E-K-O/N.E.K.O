import { useEffect, useMemo, useRef, useState } from 'react';
import type { AudioMessagePayload } from './audioRecording';
import type { ChatMessage } from './message-schema';

type AudioRow = {
  request_id: string;
  created_at: number;
  duration_ms: number;
  state: 'sending' | 'sent' | 'unconfirmed';
  transcript: string;
  available?: boolean;
};

type HistoryAvailability = boolean | undefined;
const HISTORY_TIMEOUT_MS = 5000;

export function chatAudioUrl(character: string, id?: string) {
  const path = id ? `/${encodeURIComponent(id)}` : '';
  return `/api/chat-audio${path}?${new URLSearchParams({ character })}`;
}

export function useChatAudioHistory(target: string | undefined, author: string) {
  const [loaded, setLoaded] = useState<{ target: string; rows: AudioRow[]; more: boolean }>();
  const [pending, setPending] = useState<{ target: string; row: AudioRow }[]>([]);
  const active = useRef<{
    target: string;
    availability: () => Promise<HistoryAvailability>;
    refresh: () => Promise<HistoryAvailability>;
  }>();

  useEffect(() => {
    if (!target) return;
    const character = target;
    let pages = 1;
    let controller: AbortController;
    let socket: EventTarget | undefined;
    let disposed = false;
    let clearedAt = -Infinity;
    let available: HistoryAvailability;
    let latest: Promise<HistoryAvailability>;

    function refresh(): Promise<HistoryAvailability> {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      const { signal } = request;
      const timeout = setTimeout(() => request.abort(), HISTORY_TIMEOUT_MS);
      latest = (async () => {
        const rows: AudioRow[] = [];
        let before: number | null = null;
        for (let index = 0; index < pages; index++) {
          const url = chatAudioUrl(character) + (before === null ? '' : `&before=${before}`);
          const response = await fetch(url, { signal, cache: 'no-store' });
          if (!response.ok) {
            if (signal.aborted || disposed) return undefined;
            if (response.status !== 403 && response.status !== 404) return undefined;
            setLoaded({ target: character, rows: [], more: false });
            setPending(entries => entries.filter(entry => entry.target !== character));
            available = false;
            return available;
          }
          const page: { messages: AudioRow[]; next_before: number | null } = await response.json();
          rows.push(...page.messages);
          before = page.next_before;
          if (before === null) break;
        }
        if (signal.aborted || disposed) return undefined;
        const ids = new Set(rows.map(row => row.request_id));
        const visibleRows = rows.filter(row => row.created_at > clearedAt);
        setLoaded({ target: character, rows: visibleRows, more: before !== null && visibleRows.length === rows.length });
        // Once history has shown a row, only history can display it again.
        setPending(entries => entries.filter(entry => entry.target !== character || !ids.has(entry.row.request_id)));
        available = true;
        return available;
      })().catch(() => undefined).finally(() => clearTimeout(timeout));
      return latest;
    }

    function onFrame(event: Event) {
      const data = (event as MessageEvent).data;
      if (typeof data !== 'string') return;
      try {
        const message = JSON.parse(data);
        if (message.type === 'chat_audio_changed' && message.character === character) void refresh();
      } catch { /* Ignore non-JSON frames. */ }
    }

    function bind(next?: EventTarget) {
      socket?.removeEventListener('message', onFrame);
      socket = next;
      socket?.addEventListener('message', onFrame);
    }

    function onSocketOpen(event: Event) {
      bind((event as CustomEvent<{ socket: EventTarget }>).detail?.socket);
      void refresh();
    }

    function onLoadOlder(event: Event) {
      if ((event as CustomEvent<string>).detail !== character) return;
      pages++;
      void refresh();
    }

    function onLocalChange(event: Event) {
      const detail = (event as CustomEvent<{ character: string; requestId?: string }>).detail;
      if (detail && detail.character !== character) return;
      if (detail) {
        const keep = (row: AudioRow) => detail.requestId !== undefined && row.request_id !== detail.requestId;
        setLoaded(value => value?.target === character
          ? { ...value, rows: value.rows.filter(keep), more: detail.requestId !== undefined && value.more }
          : value);
        setPending(entries => entries.filter(entry => entry.target !== character || keep(entry.row)));
      }
      void refresh();
    }

    function onMessagesCleared() {
      clearedAt = Date.now();
      pages = 1;
      controller.abort();
      setLoaded({ target: character, rows: [], more: false });
      setPending(entries => entries.filter(entry => entry.target !== character));
    }

    bind((window as Window & { appState?: { socket?: EventTarget } }).appState?.socket);
    window.addEventListener('voice-input-socket-open', onSocketOpen);
    window.addEventListener('chat-audio-local-change', onLocalChange);
    window.addEventListener('chat-audio-load-older', onLoadOlder);
    window.addEventListener('react-chat-window:messages-cleared', onMessagesCleared);
    const session = {
      target: character,
      availability: async () => {
        if (available === undefined) await latest;
        return available;
      },
      refresh,
    };
    void refresh();
    active.current = session;
    return () => {
      disposed = true;
      controller.abort();
      bind();
      setPending(entries => entries.filter(entry => entry.target !== character));
      window.removeEventListener('voice-input-socket-open', onSocketOpen);
      window.removeEventListener('chat-audio-local-change', onLocalChange);
      window.removeEventListener('chat-audio-load-older', onLoadOlder);
      window.removeEventListener('react-chat-window:messages-cleared', onMessagesCleared);
      if (active.current === session) active.current = undefined;
    };
  }, [target]);

  async function submit(payload: AudioMessagePayload, send: (payload: AudioMessagePayload) => Promise<unknown>) {
    const session = active.current;
    if (!session || session.target !== payload.target) return send(payload);
    let saveLocal = await session.availability();
    if (saveLocal === undefined && active.current === session) saveLocal = await session.refresh();
    if (active.current !== session) throw new Error('Audio message target changed');
    if (saveLocal === undefined) {
      const error = new Error('storage');
      error.name = 'AudioMessageRejected';
      throw error;
    }
    if (!saveLocal) return send(payload);
    const padding = payload.audio.endsWith('==') ? 2 : payload.audio.endsWith('=') ? 1 : 0;
    const bytes = payload.audio.length * 3 / 4 - padding;
    const matches = (entry: { target: string; row: AudioRow }) =>
      entry.target === payload.target && entry.row.request_id === payload.requestId;
    setPending(rows => [...rows.filter(entry => !matches(entry)), {
      target: payload.target,
      row: { request_id: payload.requestId, created_at: Date.now(), state: 'sending', transcript: '',
        duration_ms: Math.floor(bytes * 1000 / (payload.sampleRateHz * 2)), available: false },
    }]);
    try {
      const result = await send({ ...payload, saveLocal: true });
      if (active.current === session) {
        setPending(rows => rows.map(entry => matches(entry)
          ? { ...entry, row: { ...entry.row, state: 'sent', available: true } }
          : entry));
      }
      return result;
    } catch (error) {
      if (active.current === session) {
        setPending(rows => rows.filter(entry => !matches(entry)));
      }
      throw error;
    } finally {
      if (active.current === session) void session.refresh();
    }
  }

  const messages = useMemo<ChatMessage[]>(() => {
    if (!target) return [];
    const rows = loaded?.target === target ? loaded.rows : [];
    const optimistic = pending.filter(entry => entry.target === target);
    return [...rows, ...optimistic.map(entry => entry.row)].map(row => ({
      id: `audio-message:${row.request_id}`, role: 'user', author,
      time: new Date(row.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      createdAt: row.created_at,
      sortKey: row.created_at,
      status: row.state === 'sending' ? 'sending' : 'sent',
      blocks: [{
        type: 'audio_message', character: target, requestId: row.request_id,
        durationMs: row.duration_ms, transcript: row.transcript, state: row.state,
        available: row.available !== false,
        canLoadOlder: loaded?.target === target && loaded.more && row === rows[rows.length - 1],
      }],
    }));
  }, [author, loaded, pending, target]);

  return { messages, submit };
}
