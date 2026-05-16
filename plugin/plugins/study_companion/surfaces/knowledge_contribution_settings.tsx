import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

async function readJsonResponse(response: Response, label: string) {
  if (!response.ok) {
    throw new Error(`${label} failed: HTTP ${response.status}`);
  }
  return await response.json();
}

async function callPlugin(entryId: string, args: Record<string, unknown> = {}) {
  const createResp = await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
  });
  const created = await readJsonResponse(createResp, 'Run create');
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

export default function KnowledgeContributionSettings(props: PluginSurfaceProps) {
  const [optIn, setOptIn] = useState(false);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function refresh() {
    const payload = await callPlugin('study_anonymous_knowledge_preview', { limit: 100 });
    setOptIn(Boolean(payload.opt_in));
    setSummary(payload.summary || {});
    setError('');
  }

  async function toggle() {
    setBusy(true);
    try {
      const payload = await callPlugin('study_set_knowledge_contribution_opt_in', { opt_in: !optIn });
      setOptIn(Boolean(payload.opt_in));
      setSummary(payload.summary || {});
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.knowledge_contribution_settings', 'Knowledge Contribution Settings')}</h1>
          <span>{optIn ? text(props, 'ui.status.enabled', 'Enabled') : text(props, 'ui.status.disabled', 'Disabled')}</span>
        </div>
        <button type="button" disabled={busy} onClick={toggle}>
          {optIn ? text(props, 'ui.button.disable', 'Disable') : text(props, 'ui.button.enable', 'Enable')}
        </button>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="study-panel__state">
        <div>
          <span>{text(props, 'ui.label.candidates', 'Candidates')}</span>
          <strong>{summary.total || 0}</strong>
        </div>
        <div>
          <span>{text(props, 'ui.label.queue', 'Queue')}</span>
          <strong>{summary.queue_count || 0}</strong>
        </div>
      </section>
    </div>
  );
}
