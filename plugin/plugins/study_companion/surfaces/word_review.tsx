import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';

type DueReview = {
  item_id: string;
  retrievability?: number;
  item?: {
    prompt?: string;
    answer?: string;
    item_type?: string;
  };
  deck?: {
    name?: string;
  };
};

type ReviewResult = {
  habit_progress?: {
    applied?: number;
  };
};

const REVIEW_RATINGS = ['again', 'hard', 'good', 'easy'] as const;

export default function WordReview(props: PluginSurfaceProps) {
  const [reviews, setReviews] = useState<DueReview[]>([]);
  const [showAnswer, setShowAnswer] = useState(false);
  const [status, setStatus] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const current = reviews[0];

  async function refresh(signal?: AbortSignal) {
    const payload = await callPlugin<{ due_reviews?: DueReview[] }>('study_memory_due_reviews', { item_type: 'word', limit: 50 }, signal);
    const due = Array.isArray(payload.due_reviews) ? payload.due_reviews : [];
    setReviews(due.filter((item: DueReview) => item.item?.item_type === 'word'));
    setShowAnswer(false);
    setStatus('');
  }

  async function rate(rating: (typeof REVIEW_RATINGS)[number]) {
    if (!current?.item_id || submitting) {
      return;
    }
    setSubmitting(true);
    try {
      const payload = await callPlugin<ReviewResult>('study_memory_review_item', { item_id: current.item_id, rating });
      await refresh();
      if (payload.habit_progress?.applied) {
        setStatus(text(props, 'ui.memory.review_goal_updated', 'Goal updated'));
      }
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
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
          <h1>{text(props, 'ui.surface.word_review', 'Word Review')}</h1>
          <span>{status || `${reviews.length}`}</span>
        </div>
      </header>
      <pre>{current ? `${current.deck?.name || ''}\n\n${current.item?.prompt || ''}\n\n${showAnswer ? current.item?.answer || '' : ''}` : text(props, 'ui.memory.empty_due', 'No due memory cards')}</pre>
      <div className="study-panel__actions">
        <button type="button" disabled={!current || submitting} onClick={() => setShowAnswer((value) => !value)}>
          {text(props, 'ui.button.flip', 'Flip')}
        </button>
        {REVIEW_RATINGS.map((rating) => (
          <button key={rating} type="button" disabled={!current || submitting} onClick={() => rate(rating)}>
            {text(props, `ui.button.rating.${rating}`, rating)}
          </button>
        ))}
      </div>
    </div>
  );
}
