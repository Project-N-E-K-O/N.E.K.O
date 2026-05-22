import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

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

export default function DueReviewPanel(props: PluginSurfaceProps) {
  const [reviews, setReviews] = useState<DueReview[]>([]);
  const [status, setStatus] = useState('');

  async function refresh() {
    const payload = await callPlugin('study_memory_due_reviews', { limit: 100 });
    setReviews(Array.isArray(payload.due_reviews) ? payload.due_reviews : []);
  }

  useEffect(() => {
    refresh().catch((error) => setStatus(error instanceof Error ? error.message : String(error)));
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
        <button type="button" onClick={refresh}>{text(props, 'ui.button.refresh', 'Refresh')}</button>
      </div>
      <pre>{reviews.map((review) => {
        const r = Number.isFinite(Number(review.retrievability)) ? `${Math.round(Number(review.retrievability) * 100)}%` : '-';
        return `${review.deck?.name || ''} / ${review.item?.item_type || ''} / ${r}\n${review.item?.prompt || review.item_id}`;
      }).join('\n\n')}</pre>
    </div>
  );
}
