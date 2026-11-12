import { useCallback, useEffect, useRef, useState } from 'react';

export type HistoryEntry = { id: number; role: string; text: string; time: string };
export type HistoryPage = { messages: HistoryEntry[]; nextCursor: number | null; available: boolean };
export async function getHistory<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api/chat-history/${path}`, { signal, cache: 'no-store' });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}
export function useHistoryFeed(filter: string, enabled: boolean, revision = 0) {
  const [state, setState] = useState({ messages: [] as HistoryEntry[], busy: false, error: false, available: true, started: false, more: true });
  const session = useRef<{ filter: string; cursor: number | null; controller: AbortController | null; seen: Set<number> }>();
  const load = useCallback(() => {
    const current = session.current;
    if (!enabled || !current || current.filter !== filter || current.controller || current.cursor === null) return;
    const controller = new AbortController();
    current.controller = controller;
    setState(s => ({ ...s, busy: true, error: false }));
    getHistory<HistoryPage>(`messages?${filter}&cursor=${current.cursor}`, controller.signal).then(page => {
      if (session.current !== current || controller.signal.aborted) return;
      if (page.nextCursor !== null && page.nextCursor === current.cursor) throw new Error('non_advancing_cursor');
      const added = page.messages.filter(entry => {
        if (current.seen.has(entry.id)) return false;
        current.seen.add(entry.id); return true;
      });
      current.cursor = page.nextCursor;
      setState(s => ({ ...s, messages: [...s.messages, ...added], started: true, available: page.available, more: page.nextCursor !== null }));
    }).catch(() => {
      if (session.current === current && !controller.signal.aborted) setState(s => ({ ...s, error: true }));
    }).finally(() => {
      if (session.current === current && !controller.signal.aborted) {
        current.controller = null;
        setState(s => ({ ...s, busy: false }));
      }
    });
  }, [filter, enabled]);
  useEffect(() => {
    const current = { filter, cursor: 0, controller: null, seen: new Set<number>() };
    session.current = current;
    setState({ messages: [], busy: false, error: false, available: true, started: false, more: true });
    load();
    return () => { session.current?.controller?.abort(); session.current = undefined; };
  }, [filter, load, revision]);
  return { ...state, load };
}
