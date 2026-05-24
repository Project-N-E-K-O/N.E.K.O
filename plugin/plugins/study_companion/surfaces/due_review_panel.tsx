import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';
import {
  getMemoryHabitStatus,
  getPomodoroStatus,
  habitBridgeAvailable,
  normalizePositiveInteger,
  startedNewFocusSession,
  startDeckFocus,
  type MemoryHabitStatus,
} from './memory_habit_bridge';

type DueReview = {
  item_id: string;
  retrievability?: number;
  due?: string;
  item?: {
    prompt?: string;
    item_type?: string;
  };
  deck?: {
    id?: string;
    name?: string;
  };
};

export default function DueReviewPanel(props: PluginSurfaceProps) {
  const [reviews, setReviews] = useState<DueReview[]>([]);
  const [habitStatus, setHabitStatus] = useState<MemoryHabitStatus>({});
  const [focusMinutes, setFocusMinutes] = useState(25);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');

  async function refresh(signal?: AbortSignal) {
    const payload = await callPlugin<{ due_reviews?: DueReview[] }>('study_memory_due_reviews', { limit: 100 }, signal);
    setReviews(Array.isArray(payload.due_reviews) ? payload.due_reviews : []);
  }

  async function handleRefresh() {
    try {
      await refresh();
      setStatus('');
    } catch (error) {
      setStatus(errorMessage(error));
    }
  }

  async function handleStartFocus(deckId: string) {
    setBusy(true);
    try {
      const before = await getPomodoroStatus();
      const after = await startDeckFocus(deckId, normalizePositiveInteger(focusMinutes, 1));
      setStatus(
        startedNewFocusSession(before, after)
          ? text(props, 'ui.memory.focus_started', 'Focus started')
          : text(props, 'ui.memory.focus_not_started', 'Focus is already running'),
      );
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    getMemoryHabitStatus(controller.signal)
      .then(setHabitStatus)
      .catch(() => setHabitStatus({ available: false }));
    refresh(controller.signal).catch((error) => {
      if (!controller.signal.aborted) {
        setStatus(errorMessage(error));
      }
    });
    return () => controller.abort();
  }, []);

  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.due_review_panel', 'Due Reviews')}</h1>
          <span>{status || `${reviews.length}`}</span>
        </div>
      </header>
      <div className="study-panel__actions">
        <button type="button" onClick={handleRefresh}>{text(props, 'ui.button.refresh', 'Refresh')}</button>
        {habitBridgeAvailable(habitStatus) ? (
          <label>
            <span>{text(props, 'ui.summary.memory_focus_minutes', 'Focus minutes')}</span>
            <input type="number" min={1} step={1} value={focusMinutes} disabled={busy} onChange={(event) => setFocusMinutes(normalizePositiveInteger(event.target.value, 1))} />
          </label>
        ) : null}
      </div>
      <div className="study-panel__actions">
        {reviews.map((review) => {
          const r = Number.isFinite(Number(review.retrievability)) ? `${Math.round(Number(review.retrievability) * 100)}%` : '-';
          return (
            <div key={review.item_id} className="study-panel__row">
              <span>{review.deck?.name || ''} / {review.item?.item_type || ''} / {r}</span>
              <span>{review.item?.prompt || review.item_id}</span>
              {habitBridgeAvailable(habitStatus) && review.deck?.id ? (
                <button type="button" disabled={busy} onClick={() => handleStartFocus(String(review.deck?.id || ''))}>
                  {text(props, 'ui.focus.start_with_deck', 'Start Focus')}
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
