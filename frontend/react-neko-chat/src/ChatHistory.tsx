import { useEffect, useRef, useState } from 'react';
import { i18n } from './i18n';
import './chat-history.css';
import HistoryResults from './HistoryResults';
import { VoiceReplayIcon } from './VoiceReplayIcon';
import { useHistoryPlayback } from './useHistoryPlayback';
import { getHistory as get, useHistoryFeed, type HistoryEntry as Entry, type HistoryPage as Page } from './useHistoryFeed';

const t = (key: string) => i18n(`chatHistory.${key}`, key);
const storageKey = 'neko.chatHistory.preferences';
function preferences() {
  try { return JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch { return {}; }
}
function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'));
  return <>{parts.map((part, index) => index % 2 ? <mark key={index}>{part}</mark> : part)}</>;
}
export function HistoryDialog({ close }: { close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [recording, setRecording] = useState<boolean | null>(null);
  const [recordingBusy, setRecordingBusy] = useState(false);
  const [recordingError, setRecordingError] = useState(false);
  const [names, setNames] = useState<string[]>([]);
  const [characterError, setCharacterError] = useState(false);
  const [character, setCharacter] = useState(() => {
    const host = window as unknown as { appState?: { lanlan_name?: string }; lanlan_config?: { lanlan_name?: string } };
    return host.appState?.lanlan_name || host.lanlan_config?.lanlan_name || '';
  });
  const [day, setDay] = useState('');
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [role, setRole] = useState('all');
  const [prefs, setPrefs] = useState(() => {
    const p = preferences();
    return { order: p?.order === 'asc' ? 'asc' : 'desc', times: p?.times !== false, autoContinue: p?.autoContinue !== false };
  });
  const [searchRevision, setSearchRevision] = useState(0);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const params = new URLSearchParams({ character, q: query, role, limit: '30', order: prefs.order });
  if (day) params.set('day', day);
  const feed = useHistoryFeed(params.toString(), Boolean(character), searchRevision);
  // The day limits browsing, not continuation across midnight. It still resets playback on selection changes.
  const playbackScope = new URLSearchParams({ q: query }).toString();
  const playback = useHistoryPlayback(character, `${playbackScope}&role=${role}&revision=${searchRevision}`, prefs.autoContinue, day);
  const playbackActive = ['loading', 'playing', 'paused'].includes(playback.phase);
  useEffect(() => {
    const prior = document.activeElement as HTMLElement | null;
    const fallback = prior?.closest('main')?.querySelector<HTMLElement>('.compact-input-tool-toggle, .composer-overflow-btn');
    const element = dialog.current;
    element?.showModal();
    const refresh = () => window.dispatchEvent(new Event('neko:compact-interaction-geometry-refresh'));
    refresh();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(refresh);
    if (dialog.current) observer?.observe(dialog.current);
    window.addEventListener('resize', refresh);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', refresh);
      element?.close();
      // Wait for React to remove the modal before restoring native hit regions.
      requestAnimationFrame(refresh);
      requestAnimationFrame(() => {
        prior?.focus();
        if (!prior?.isConnected || prior.matches(':disabled') || document.activeElement !== prior) fallback?.focus();
      });
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setCharacterError(false);
    get<{ characters: string[] }>('characters', controller.signal).then(data => {
      setNames(data.characters);
      setCharacter(current => data.characters.includes(current) ? current : data.characters[0] || '');
    }).catch(e => { if (e.name !== 'AbortError') setCharacterError(true); });
    return () => controller.abort();
  }, [retry]);
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(prefs)); } catch { /* Storage can be disabled. */ }
  }, [prefs]);
  useEffect(() => {
    if (!character || anchor === null) { setPage(null); setBusy(false); setError(false); return; }
    const controller = new AbortController();
    setBusy(true); setError(false); setPage(null);
    const params = new URLSearchParams({ character, anchor: String(anchor) });
    get<Page>(`messages?${params}`, controller.signal).then(data => {
      if (!controller.signal.aborted) setPage(data);
    }).catch(e => { if (e.name !== 'AbortError') setError(true); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [character, anchor, retry]);
  useEffect(() => {
    if (anchor && page) dialog.current?.querySelector(`.history-results:not([hidden]) [data-history-id="${anchor}"]`)?.scrollIntoView?.({ block: 'center' });
  }, [page, anchor]);
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/voice-archive/settings', { signal: controller.signal, cache: 'no-store' })
      .then(response => { if (!response.ok) throw new Error('settings'); return response.json(); })
      .then(data => { if (!controller.signal.aborted) { setRecording(data.enabled === true); setRecordingError(false); } })
      .catch(error => { if (error.name !== 'AbortError') setRecordingError(true); });
    return () => controller.abort();
  }, [retry]);
  const saveRecording = async (enabled: boolean) => {
    setRecordingBusy(true); setRecordingError(false);
    try {
      const response = await fetch('/api/voice-archive/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
      });
      if (!response.ok) throw new Error('settings');
      const data = await response.json(); setRecording(data.enabled === true);
    } catch { setRecordingError(true); } finally { setRecordingBusy(false); }
  };
  const reset = () => { setAnchor(null); };
  const moveDay = (delta: number) => {
    const value = day ? new Date(`${day}T12:00:00`) : new Date();
    value.setDate(value.getDate() + delta);
    setDay(`${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`); reset();
  };
  const renderEntry = (entry: Entry) => <article key={entry.id} data-history-id={entry.id} className={[entry.id === anchor ? 'history-hit' : '', playbackActive && playback.current?.id === entry.id ? 'history-playing' : ''].join(' ')}>
    <div className="history-meta"><strong>{entry.role === 'user' ? t('user') : character}</strong>{prefs.times && <time>{entry.time}</time>}<span>#{entry.id}</span></div>
    <p><Highlight text={entry.text} query={query} /></p>
    {entry.voice && <span className="message-voice-replay" data-voice-state={playback.current?.id === entry.id ? playback.phase : 'idle'}>
      <button type="button" className="message-voice-replay-button"
        aria-label={t(playbackActive && playback.current?.id === entry.id ? 'voiceStop' : 'voicePlay')}
        aria-pressed={playbackActive && playback.current?.id === entry.id}
        onClick={() => playbackActive && playback.current?.id === entry.id ? playback.stop() : playback.start(entry)}>
        <VoiceReplayIcon playing={playbackActive && playback.current?.id === entry.id} />
      </button>
    </span>}
    {anchor === null && <button onClick={() => setAnchor(entry.id)}>{t('context')}</button>}
  </article>;
  return <dialog ref={dialog} className="history-dialog neko-dialog-overlay" data-compact-geometry-owner="surface" data-compact-geometry-item="chatHistoryDialog" data-compact-no-drag="true" aria-labelledby="history-title" onCancel={close} onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation(); }}>
    <header><div><h2 id="history-title">{t('title')}</h2><p>{t('description')}</p></div><button onClick={close} aria-label={t('close')}>×</button></header>
    <form onSubmit={e => { e.preventDefault(); setQuery(draft.trim()); reset(); setSearchRevision(v => v + 1); }}>
      <label>{t('character')}<select value={character} onChange={e => { setCharacter(e.target.value); reset(); }}>{names.map(n => <option key={n}>{n}</option>)}</select></label>
      <label className="history-search">{t('keyword')}<input type="search" maxLength={200} value={draft} onChange={e => setDraft(e.target.value)} /></label>
      <button type="submit">{t('search')}</button>
    </form>
    <nav aria-label={t('date')}>
      <button onClick={() => moveDay(-1)} aria-label={t('previousDay')}>‹</button>
      <label>{t('date')}<input type="date" value={day} onChange={e => { setDay(e.target.value); reset(); }} /></label>
      <button onClick={() => moveDay(1)} aria-label={t('nextDay')}>›</button>
      <button onClick={() => { setDay(''); reset(); }}>{t('allDates')}</button>
      <label>{t('speaker')}<select value={role} onChange={e => { setRole(e.target.value); reset(); }}>{['all', 'user', 'assistant'].map(r => <option value={r} key={r}>{t(r)}</option>)}</select></label>
      <details><summary>{t('settings')}</summary><div className="history-settings">
        <label>{t('order')}<select value={prefs.order} onChange={e => { setPrefs({ ...prefs, order: e.target.value }); reset(); }}><option value="desc">{t('newest')}</option><option value="asc">{t('oldest')}</option></select></label>
        <label><input type="checkbox" checked={recording === true} disabled={recording === null || recordingBusy} onChange={e => void saveRecording(e.target.checked)} />{t('recordVoice')}</label>
        <p>{t('recordVoiceHint')}</p>
        {recordingError && <p role="alert">{t('error')} <button type="button" onClick={() => setRetry(v => v + 1)}>{t('retry')}</button></p>}
        <label><input type="checkbox" checked={prefs.autoContinue} onChange={e => setPrefs({ ...prefs, autoContinue: e.target.checked })} />{t('autoContinue')}</label>
        <p>{t('autoContinueHint')}</p>
        <label><input type="checkbox" checked={prefs.times} onChange={e => setPrefs({ ...prefs, times: e.target.checked })} />{t('showTime')}</label>
      </div></details>
    </nav>
    {playback.phase !== 'idle' && <section className="history-playback" aria-label={t('review')}>
      <div className="history-playback-controls">
        {playbackActive && <><button onClick={playback.phase === 'paused' ? playback.resume : playback.pause}>{t(playback.phase === 'paused' ? 'resume' : 'pause')}</button>
            <button onClick={playback.stop}>{t('voiceStop')}</button></>}
        <span role="status">{t(`playback_${playback.phase}`)}</span>
      </div>
      {playback.current && <div className="history-now-playing"><time>{playback.current.time}</time><span>{playback.current.text}</span>
        <button onClick={() => setAnchor(playback.current!.id)}>{t('locatePlaying')}</button></div>}
      <span className="history-playback-note" role="status">{playback.partial ? t('voicePartial') : ''}</span>
    </section>}
    {anchor !== null && <button className="history-back" onClick={() => setAnchor(null)}>{t('back')}</button>}
    {characterError && <div role="alert" className="history-state">{t('error')} <button onClick={() => setRetry(v => v + 1)}>{t('retry')}</button></div>}
    <HistoryResults key={params.toString() + searchRevision} entries={feed.messages} renderEntry={renderEntry}
      active={anchor === null} load={feed.load} more={feed.more} busy={feed.busy} error={feed.error}
      status={<>
        {feed.busy && <p>{t('loading')}</p>}
        {feed.error && <div role="alert">{t('error')} <button onClick={feed.load}>{t('retry')}</button></div>}
        {!feed.busy && !feed.error && feed.started && !feed.more && <p>{t(!feed.available ? 'unavailable' : feed.messages.length ? 'end' : 'empty')}</p>}
      </>} />
    {anchor !== null && <section className="history-results" aria-busy={busy}>
      {busy && <p className="history-state">{t('loading')}</p>}
      {error && <div role="alert" className="history-state">{t('error')} <button onClick={() => setRetry(v => v + 1)}>{t('retry')}</button></div>}
      {page?.messages.map(renderEntry)}
    </section>}
    <footer><span>{t('savedOnly')}</span></footer>
  </dialog>;
}
