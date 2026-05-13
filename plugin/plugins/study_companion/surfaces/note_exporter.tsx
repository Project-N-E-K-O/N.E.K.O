import { useState } from '@neko/plugin-ui';
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

function downloadBase64File(contentBase64: string, filename: string, contentType: string) {
  const binary = window.atob(contentBase64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const blob = new Blob([bytes], { type: contentType || 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || 'study-notes';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export default function NoteExporter(props: PluginSurfaceProps) {
  const [fmt, setFmt] = useState('markdown');
  const [style, setStyle] = useState('neko');
  const [markdown, setMarkdown] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  async function exportNotes(previewOnly: boolean) {
    setBusy(true);
    setStatus(text(props, 'ui.status.exporting', 'Exporting...'));
    try {
      const payload = await callPlugin('study_export_notes', { fmt, style, preview_only: previewOnly });
      setMarkdown(payload.markdown || '');
      if (!previewOnly && payload.content_base64) {
        downloadBase64File(payload.content_base64, payload.filename, payload.content_type);
      }
      setStatus(payload.filename || text(props, 'ui.status.export_ready', 'Export ready'));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.note_exporter', 'Note Exporter')}</h1>
          <span>{status}</span>
        </div>
      </header>
      <section className="study-panel__state">
        <label>
          <span>{text(props, 'ui.label.format', 'Format')}</span>
          <select value={fmt} disabled={busy} onChange={(event) => setFmt(event.target.value)}>
            <option value="markdown">Markdown</option>
            <option value="pdf">PDF</option>
            <option value="docx">DOCX</option>
            <option value="xmind">XMind</option>
          </select>
        </label>
        <label>
          <span>{text(props, 'ui.label.style', 'Style')}</span>
          <select value={style} disabled={busy} onChange={(event) => setStyle(event.target.value)}>
            <option value="neko">Neko</option>
            <option value="academic">Academic</option>
            <option value="compact">Compact</option>
          </select>
        </label>
        <button type="button" disabled={busy} onClick={() => exportNotes(true)}>
          {text(props, 'ui.button.preview', 'Preview')}
        </button>
        <button type="button" disabled={busy} onClick={() => exportNotes(false)}>
          {text(props, 'ui.button.export', 'Export')}
        </button>
      </section>
      <pre>{markdown}</pre>
    </div>
  );
}
