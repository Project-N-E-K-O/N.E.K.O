import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

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

async function readJsonResponse(response: Response, label: string) {
  if (!response.ok) {
    throw new Error(`${label} failed: HTTP ${response.status}`);
  }
  return await response.json();
}

async function callPlugin(entryId: string, args: Record<string, unknown> = {}) {
  const created = await readJsonResponse(await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
  }), 'Run create');
  const runId = created.run_id || created.id;
  if (!runId) {
    throw new Error('Run id missing');
  }
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    const run = await readJsonResponse(await fetch(`/runs/${runId}`), 'Run poll');
    if (run.status === 'succeeded') {
      const exported = await readJsonResponse(await fetch(`/runs/${runId}/export`), 'Run export');
      const item = (exported.items || []).find((candidate: any) => candidate.type === 'json' && candidate.json);
      if (!item) {
        throw new Error('Run export missing JSON result');
      }
      if (item.json.success === false || item.json.error) {
        throw new Error(item.json.error?.message || item.json.message || 'Plugin call failed');
      }
      return item.json.data || {};
    }
    if (['failed', 'canceled', 'timeout'].includes(run.status)) {
      throw new Error(run.error?.message || run.message || run.status);
    }
  }
  throw new Error('Plugin call timed out');
}

function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export default function WordReview(props: PluginSurfaceProps) {
  const [reviews, setReviews] = useState<DueReview[]>([]);
  const [showAnswer, setShowAnswer] = useState(false);
  const [status, setStatus] = useState('');
  const current = reviews[0];

  async function refresh() {
    const payload = await callPlugin('study_memory_due_reviews', { limit: 50 });
    const due = Array.isArray(payload.due_reviews) ? payload.due_reviews : [];
    setReviews(due.filter((item: DueReview) => item.item?.item_type === 'word'));
    setShowAnswer(false);
  }

  async function rate(rating: string) {
    if (!current?.item_id) {
      return;
    }
    try {
      await callPlugin('study_memory_review_item', { item_id: current.item_id, rating });
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  }

  useEffect(() => {
    refresh().catch((error) => setStatus(error instanceof Error ? error.message : String(error)));
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
        <button type="button" disabled={!current} onClick={() => setShowAnswer((value) => !value)}>
          {text(props, 'ui.button.flip', 'Flip')}
        </button>
        {['again', 'hard', 'good', 'easy'].map((rating) => (
          <button key={rating} type="button" disabled={!current} onClick={() => rate(rating)}>
            {rating}
          </button>
        ))}
      </div>
    </div>
  );
}
