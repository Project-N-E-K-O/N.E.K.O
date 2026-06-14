import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, ensureBrandCSS, text } from './study_surface_utils';

type KnowledgeContributionPayload = {
  opt_in?: boolean;
  summary?: Record<string, number>;
};

export default function KnowledgeContributionSettings(props: PluginSurfaceProps) {
  const [optIn, setOptIn] = useState(false);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function refresh() {
    const payload = await callPlugin<KnowledgeContributionPayload>(props.api, 'study_anonymous_knowledge_preview', { limit: 100 });
    setOptIn(Boolean(payload.opt_in));
    setSummary(payload.summary || {});
    setError('');
  }

  async function toggle() {
    setBusy(true);
    try {
      const payload = await callPlugin<KnowledgeContributionPayload>(props.api, 'study_set_knowledge_contribution_opt_in', { opt_in: !optIn });
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
    ensureBrandCSS();
    refresh().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <div className="study-panel surface-shell">
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
