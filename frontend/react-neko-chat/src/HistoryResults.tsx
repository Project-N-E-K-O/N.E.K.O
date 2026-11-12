import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { HistoryEntry } from './useHistoryFeed';

// Keep message bodies out of the DOM outside the viewport, while retaining
// measured spacer heights so revisiting loaded history needs no network call.
export default function HistoryResults({ entries, renderEntry, load, more, busy, error, active, status }: {
  entries: HistoryEntry[]; renderEntry: (entry: HistoryEntry) => ReactNode;
  load: () => void; more: boolean; busy: boolean; error: boolean; active: boolean; status: ReactNode;
}) {
  const root = useRef<HTMLElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const heights = useRef(new Map<number, number>());
  const lastWidth = useRef(800);
  const [revision, setRevision] = useState(0);
  const [viewport, setViewport] = useState({ top: 0, height: 600, width: 800 });
  const offsets = useMemo(() => {
    const result = [0];
    for (const entry of entries) result.push(result[result.length - 1] + (heights.current.get(entry.id) ?? 160));
    return result;
  }, [entries, revision]);
  function indexAt(position: number) {
    let low = 0, high = entries.length;
    while (low < high) { const mid = (low + high) >>> 1; if (offsets[mid + 1] <= position) low = mid + 1; else high = mid; }
    return low;
  }
  const start = Math.max(0, indexAt(viewport.top) - 4);
  const end = Math.min(entries.length, indexAt(viewport.top + viewport.height) + 5);
  useLayoutEffect(() => {
    const element = root.current;
    if (!element || !active) return;
    const update = () => {
      if (element.clientWidth !== lastWidth.current) {
        lastWidth.current = element.clientWidth;
        // Retain offscreen measurements as estimates. Resetting them all to
        // 160px would move the reader to another message after a width change.
        // Visible rows are remeasured below, including on reverse scrolling.
        setRevision(v => v + 1);
      }
      setViewport(previous => {
        const next = { top: element.scrollTop, height: element.clientHeight || 600, width: element.clientWidth };
        return previous.top === next.top && previous.height === next.height && previous.width === next.width ? previous : next;
      });
    };
    update();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [active]);
  useLayoutEffect(() => {
    if (!active || !root.current) return;
    let changed = false, correction = 0;
    root.current.querySelectorAll<HTMLElement>('[data-history-row]').forEach(row => {
      const index = Number(row.dataset.historyRow), entry = entries[index];
      const measured = row.getBoundingClientRect().height;
      if (!entry || measured <= 0) return;
      const previous = offsets[index + 1] - offsets[index];
      if (Math.abs(previous - measured) < 1) return;
      heights.current.set(entry.id, measured); changed = true;
      if (offsets[index + 1] <= root.current!.scrollTop) correction += measured - previous;
    });
    if (changed) {
      root.current.scrollTop += correction;
      setViewport(v => ({ ...v, top: root.current!.scrollTop }));
      setRevision(v => v + 1);
    }
  }, [entries, start, end, offsets, active, viewport.width, renderEntry]);
  useLayoutEffect(() => {
    const element = root.current;
    if (!element || !active || !more || busy || error) return;
    // Recheck after each page, including empty scan batches and short initial
    // pages. Rate-limit continuation; never drain the archive eagerly.
    const check = () => {
      if (element.clientHeight > 0 && element.scrollHeight - element.scrollTop - element.clientHeight < 240) load();
    };
    let timer: number | undefined;
    const schedule = () => {
      if (timer !== undefined) return;
      timer = window.setTimeout(() => { timer = undefined; check(); }, 180);
    };
    schedule();
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(records => {
      if (records.some(r => r.isIntersecting)) schedule();
    }, { root: element, rootMargin: '240px' });
    if (sentinel.current) observer?.observe(sentinel.current);
    element.addEventListener('scroll', schedule, { passive: true });
    return () => { clearTimeout(timer); observer?.disconnect(); element.removeEventListener('scroll', schedule); };
  }, [entries, active, more, busy, error, load]);
  return <section ref={root} className="history-results" hidden={!active} aria-busy={busy}
    onScroll={event => { const element = event.currentTarget; setViewport(v => ({ ...v, top: element.scrollTop })); }}>
    <div aria-hidden="true" style={{ height: offsets[start] }} />
    {entries.slice(start, end).map((entry, index) => <div key={entry.id} data-history-row={start + index}>{renderEntry(entry)}</div>)}
    <div aria-hidden="true" style={{ height: offsets[entries.length] - offsets[end] }} />
    <div ref={sentinel} className="history-load-state" role="status">{status}</div>
  </section>;
}
