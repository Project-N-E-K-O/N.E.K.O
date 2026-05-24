import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';

type DueReview = {
  item_id: string;
  retrievability?: number;
  due?: string;
  item?: {
    prompt?: string;
    item_type?: string;
  };
  deck?: {
    name?: string;
  };
};

export default function DueReviewPanel(props: PluginSurfaceProps) {
  const [reviews, setReviews] = useState<DueReview[]>([]);
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
          <h1>{text(props, 'ui.surface.due_review_panel', 'Due Reviews')}</h1>
          <span>{status || `${reviews.length}`}</span>
        </div>
      </header>
      <div className="study-panel__actions">
        <button type="button" onClick={handleRefresh}>{text(props, 'ui.button.refresh', 'Refresh')}</button>
      </div>
      <pre>{reviews.map((review) => {
        const r = Number.isFinite(Number(review.retrievability)) ? `${Math.round(Number(review.retrievability) * 100)}%` : '-';
        return `${review.deck?.name || ''} / ${review.item?.item_type || ''} / ${r}\n${review.item?.prompt || review.item_id}`;
      }).join('\n\n')}</pre>
    </div>
  );
}
