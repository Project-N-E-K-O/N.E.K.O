import { createContext, useEffect, useMemo, useRef, useState } from 'react';
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

type PendingAudio = { target: string; row: AudioRow; saveLocal: boolean };

type HistoryAvailability = boolean | undefined;
export type OlderRecordingsStatus = 'idle' | 'loading' | 'failed';
export const OlderRecordingsStatusContext = createContext<OlderRecordingsStatus | undefined>(undefined);
const HISTORY_TIMEOUT_MS = 5000;

export function chatAudioUrl(character: string, id?: string) {
  const path = id ? `/${encodeURIComponent(id)}` : '';
  return `/api/chat-audio${path}?${new URLSearchParams({ character })}`;
}

export function useChatAudioHistory(target: string | undefined, author: string, inlinePagination = true) {
  const [loaded, setLoaded] = useState<{ target: string; rows: AudioRow[]; more: boolean }>();
  const [pending, setPending] = useState<PendingAudio[]>([]);
  const [pagination, setPagination] = useState<{ target: string; status: OlderRecordingsStatus }>();
  const active = useRef<{
    target: string;
    availability: () => Promise<HistoryAvailability>;
    refresh: () => Promise<HistoryAvailability>;
    loadOlder: () => void;
  }>();

  useEffect(() => {
    if (!target) return;
    const character = target;
    let pages = 1;
    let canLoadOlder = false;
    let olderStatus: OlderRecordingsStatus = 'idle';
    let controller: AbortController;
    let socket: EventTarget | undefined;
    let disposed = false;
    let clearedAt = -Infinity;
    let available: HistoryAvailability;
    let latest: Promise<HistoryAvailability>;

    function refresh(): Promise<HistoryAvailability> {
      const requestedPages = pages + Number(olderStatus !== 'idle');
      if (olderStatus === 'failed') {
        olderStatus = 'loading';
        setPagination({ target: character, status: olderStatus });
      }
      controller?.abort();
      const request = new AbortController();
      controller = request;
      const { signal } = request;
      const timeout = setTimeout(() => request.abort(), HISTORY_TIMEOUT_MS);
      latest = (async () => {
        const rows: AudioRow[] = [];
        let before: number | null = null;
        for (let index = 0; index < requestedPages; index++) {
          const url = chatAudioUrl(character) + (before === null ? '' : `&before=${before}`);
          const response = await fetch(url, { signal, cache: 'no-store' });
          if (!response.ok) {
            if (signal.aborted || disposed) return undefined;
            if (response.status !== 403 && response.status !== 404) return undefined;
            setLoaded({ target: character, rows: [], more: false });
            setPending(entries => entries.filter(entry => entry.target !== character || !entry.saveLocal));
            canLoadOlder = false;
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
        pages = requestedPages;
        if (olderStatus === 'loading') {
          olderStatus = 'idle';
          setPagination({ target: character, status: olderStatus });
        }
        canLoadOlder = before !== null && visibleRows.length === rows.length;
        setLoaded({ target: character, rows: visibleRows, more: canLoadOlder });
        // Once history has shown a row, only history can display it again.
        setPending(entries => entries.filter(entry => entry.target !== character || !ids.has(entry.row.request_id)));
        available = true;
        return available;
      })().catch(() => undefined).then(result => {
        // A replacement refresh inherits the requested page and owns its result.
        if (controller === request && !disposed && olderStatus === 'loading') {
          olderStatus = result === undefined ? 'failed' : 'idle';
          setPagination({ target: character, status: olderStatus });
        }
        return result;
      }).finally(() => clearTimeout(timeout));
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

    function loadOlder() {
      if (disposed || !canLoadOlder || olderStatus === 'loading') return;
      olderStatus = 'loading';
      setPagination({ target: character, status: olderStatus });
      void refresh();
    }

    function onLoadOlder(event: Event) {
      if ((event as CustomEvent<string>).detail === character) loadOlder();
    }

    function onLocalChange(event: Event) {
      const detail = (event as CustomEvent<{ character: string; requestId?: string }>).detail;
      if (detail && detail.character !== character) return;
      if (detail) {
        const keep = (row: AudioRow) => detail.requestId !== undefined && row.request_id !== detail.requestId;
        setLoaded(value => value?.target === character
          ? { ...value, rows: value.rows.filter(keep), more: detail.requestId !== undefined && value.more }
          : value);
        setPending(entries => entries.filter(entry => entry.target !== character || !entry.saveLocal || keep(entry.row)));
      }
      void refresh();
    }

    function onMessagesCleared() {
      clearedAt = Date.now();
      pages = 1;
      canLoadOlder = false;
      olderStatus = 'idle';
      setPagination(undefined);
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
      loadOlder,
    };
    setPagination(undefined);
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
    const padding = payload.audio.endsWith('==') ? 2 : payload.audio.endsWith('=') ? 1 : 0;
    const bytes = payload.audio.length * 3 / 4 - padding;
    const matches = (entry: PendingAudio) =>
      entry.target === payload.target && entry.row.request_id === payload.requestId;
    setPending(rows => [...rows.filter(entry => !matches(entry)), {
      target: payload.target,
      saveLocal,
      row: { request_id: payload.requestId, created_at: Date.now(), state: 'sending', transcript: '',
        duration_ms: Math.floor(bytes * 1000 / (payload.sampleRateHz * 2)), available: false },
    }]);
    try {
      const result = await send({ ...payload, saveLocal });
      if (active.current === session) {
        setPending(rows => rows.map(entry => matches(entry)
          ? { ...entry, row: { ...entry.row, state: 'sent', available: saveLocal } }
          : entry));
      }
      return result;
    } catch (error) {
      if (active.current === session) {
        if (saveLocal || (error instanceof Error && error.name === 'AudioMessageRejected')) {
          setPending(rows => rows.filter(entry => !matches(entry)));
        } else {
          setPending(rows => rows.map(entry => matches(entry)
            ? { ...entry, row: { ...entry.row, state: 'unconfirmed' } }
            : entry));
        }
      }
      throw error;
    } finally {
      if (saveLocal && active.current === session) void session.refresh();
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
        canLoadOlder: inlinePagination && loaded?.target === target && loaded.more && row === rows[rows.length - 1],
      }],
    }));
  }, [author, inlinePagination, loaded, pending, target]);

  const onLoadOlder = target && loaded?.target === target && loaded.more
    ? active.current?.loadOlder
    : undefined;
  const loadOlderStatus = pagination?.target === target ? pagination?.status : undefined;
  const loadedRows = loaded?.target === target ? loaded?.rows : undefined;
  // The final row is the boundary for the next cursor page.
  const oldestRecordingTime = loadedRows?.[loadedRows.length - 1]?.created_at;
  return { messages, submit, onLoadOlder, loadOlderStatus, oldestRecordingTime };
}
