import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';
import { ensureBrandCSS, memoryItemTypeLabel, postStudySurfaceMessage, STUDY_SURFACE_MESSAGE_TYPES } from './study_surface_utils';
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
    answer?: string;
    item_type?: string;
  };
  deck?: {
    id?: string;
    name?: string;
  };
};

type ReviewResult = {
  habit_progress?: {
    applied?: number;
  };
};

const REVIEW_RATINGS = ['again', 'hard', 'good', 'easy'] as const;

export default function DueReviewPanel(props: PluginSurfaceProps) {
  const [reviews, setReviews] = useState<DueReview[]>([]);
  const [habitStatus, setHabitStatus] = useState<MemoryHabitStatus>({});
  const [focusMinutes, setFocusMinutes] = useState(25);
  const [showAnswer, setShowAnswer] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const current = reviews[0];

  async function refresh(signal?: AbortSignal): Promise<DueReview[]> {
    const payload = await callPlugin<{ due_reviews?: DueReview[] }>(props.api, 'study_memory_due_reviews', { limit: 100 }, signal);
    const nextReviews = Array.isArray(payload.due_reviews) ? payload.due_reviews : [];
    setReviews(nextReviews);
    setShowAnswer(false);
    postStudySurfaceMessage({
      type: STUDY_SURFACE_MESSAGE_TYPES.memoryDeckUpdated,
      payload: { due_count: nextReviews.length, due_cards: nextReviews },
    });
    return nextReviews;
  }

  function notifyReviewCompleted(review: DueReview, remaining: number) {
    postStudySurfaceMessage({
      type: STUDY_SURFACE_MESSAGE_TYPES.reviewCompleted,
      payload: {
        deck_id: review.deck?.id || '',
        remaining,
        reviewed_count: 1,
        timestamp: Date.now(),
      },
    });
  }

  async function handleRefresh() {
    try {
      await refresh();
      setStatus('');
    } catch (error) {
      setStatus(errorMessage(error));
    }
  }

  async function rate(rating: (typeof REVIEW_RATINGS)[number]) {
    if (!current?.item_id || busy) {
      return;
    }
    setBusy(true);
    try {
      const reviewed = current;
      const payload = await callPlugin<ReviewResult>(props.api, 'study_memory_review_item', { item_id: reviewed.item_id, rating });
      const nextReviews = await refresh();
      notifyReviewCompleted(reviewed, nextReviews.length);
      setStatus(
        payload.habit_progress?.applied
          ? text(props, 'ui.memory.review_goal_updated', 'Goal updated')
          : text(props, 'ui.memory.review_saved', 'Review saved'),
      );
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function handleStartFocus(deckId: string) {
    setBusy(true);
    try {
      const before = await getPomodoroStatus(props.api);
      const after = await startDeckFocus(props.api, deckId, normalizePositiveInteger(focusMinutes, 1));
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
    ensureBrandCSS();
    const controller = new AbortController();
    getMemoryHabitStatus(props.api, controller.signal)
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
    <div className="study-panel surface-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.due_review_panel', 'Due Reviews')}</h1>
          <span>{status || `${reviews.length}`}</span>
        </div>
      </header>
      <pre>
        {current
          ? `${current.deck?.name || ''}\n\n${current.item?.prompt || current.item_id}\n\n${showAnswer ? current.item?.answer || '' : ''}`
          : text(props, 'ui.memory.empty_due', 'No due memory cards')}
      </pre>
      <div className="study-panel__actions">
        <button type="button" disabled={!current || busy} onClick={() => setShowAnswer((value) => !value)}>
          {text(props, 'ui.button.flip', 'Flip')}
        </button>
        {REVIEW_RATINGS.map((rating) => (
          <button key={rating} type="button" data-rating={rating} disabled={!current || busy} onClick={() => rate(rating)}>
            {text(props, `ui.button.rating.${rating}`, rating)}
          </button>
        ))}
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
              <span>{review.deck?.name || ''} / {memoryItemTypeLabel(props, review.item?.item_type)} / {r}</span>
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
