import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, ensureBrandCSS, exportFormatLabel, exportStyleLabel } from './study_surface_utils';

type ExportFormat = 'markdown' | 'pdf' | 'docx' | 'xmind';
type ExportNotesPayload = {
  markdown?: string;
  content_base64?: string;
  filename?: string;
  content_type?: string;
};

const EXPORT_FORMAT_OPTIONS: Array<{ value: ExportFormat }> = [
  { value: 'markdown' },
  { value: 'pdf' },
  { value: 'docx' },
  { value: 'xmind' },
];
const DEFAULT_EXPORT_TIMEOUT_MS = 80_000;
const POLL_TIMEOUT_BUFFER_MS = 5_000;

function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

function getExportEntry(props: PluginSurfaceProps) {
  return (props.entries || []).find((entry: any) => entry.id === 'study_export_notes');
}

function getEntryTimeoutMs(entry: any) {
  const timeoutSeconds = Number(entry?.timeout);
  if (Number.isFinite(timeoutSeconds) && timeoutSeconds > 0) {
    return timeoutSeconds * 1000 + POLL_TIMEOUT_BUFFER_MS;
  }
  return DEFAULT_EXPORT_TIMEOUT_MS;
}

function getAllowedFormats(props: PluginSurfaceProps): ExportFormat[] {
  const entry = getExportEntry(props);
  if (!entry) {
    return [];
  }
  const schemaEnum = entry.input_schema?.properties?.fmt?.enum;
  if (Array.isArray(schemaEnum)) {
    const knownFormats = EXPORT_FORMAT_OPTIONS.map((option) => option.value);
    return schemaEnum.filter((value: unknown): value is ExportFormat => knownFormats.includes(value as ExportFormat));
  }
  const xmindEnabled = Boolean(props.config?.value?.doc_export?.xmind_enabled);
  return EXPORT_FORMAT_OPTIONS
    .filter((option) => option.value !== 'xmind' || xmindEnabled)
    .map((option) => option.value);
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
  const allowedFormats = getAllowedFormats(props);
  const exportEntry = getExportEntry(props);
  const pollTimeoutMs = getEntryTimeoutMs(exportEntry);
  const selectedFmt = allowedFormats.includes(fmt as ExportFormat) ? fmt : allowedFormats[0] || 'markdown';
  const exportUnavailable = allowedFormats.length === 0;
  const xmindUnavailable = !exportUnavailable && !allowedFormats.includes('xmind');
  const statusText = status || (exportUnavailable ? text(props, 'ui.status.export_unavailable', 'Export is disabled by doc_export.enabled') : '');

  useEffect(() => {
    ensureBrandCSS();
  }, []);

  async function exportNotes(previewOnly: boolean) {
    if (exportUnavailable) {
      setStatus(text(props, 'ui.status.export_unavailable', 'Export is disabled by doc_export.enabled'));
      return;
    }
    setBusy(true);
    setStatus(text(props, 'ui.status.exporting', 'Exporting...'));
    try {
      const payload = await callPlugin<ExportNotesPayload>(props.api, 'study_export_notes', { fmt: selectedFmt, style, preview_only: previewOnly }, { timeoutMs: pollTimeoutMs });
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
    <div className="study-panel surface-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.note_exporter', 'Note Exporter')}</h1>
          <span>{statusText}</span>
        </div>
      </header>
      <section className="study-panel__state">
        <label>
          <span>{text(props, 'ui.label.format', 'Format')}</span>
          <select value={selectedFmt} disabled={busy || exportUnavailable} onChange={(event) => setFmt(event.target.value)}>
            {EXPORT_FORMAT_OPTIONS
              .filter((option) => allowedFormats.includes(option.value))
              .map((option) => <option key={option.value} value={option.value}>{exportFormatLabel(props, option.value)}</option>)}
          </select>
        </label>
        {xmindUnavailable ? <span>{text(props, 'ui.status.xmind_disabled', 'XMind export is disabled by doc_export.xmind_enabled')}</span> : null}
        <label>
          <span>{text(props, 'ui.label.style', 'Style')}</span>
          <select value={style} disabled={busy || exportUnavailable} onChange={(event) => setStyle(event.target.value)}>
            <option value="neko">{exportStyleLabel(props, 'neko')}</option>
            <option value="academic">{exportStyleLabel(props, 'academic')}</option>
            <option value="compact">{exportStyleLabel(props, 'compact')}</option>
          </select>
        </label>
        <button type="button" disabled={busy || exportUnavailable} onClick={() => exportNotes(true)}>
          {text(props, 'ui.button.preview', 'Preview')}
        </button>
        <button type="button" disabled={busy || exportUnavailable} onClick={() => exportNotes(false)}>
          {text(props, 'ui.button.export', 'Export')}
        </button>
      </section>
      <pre>{markdown}</pre>
    </div>
  );
}
