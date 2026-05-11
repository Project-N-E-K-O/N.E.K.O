import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

type StudyStatus = {
  status?: string;
  active_mode?: string;
  last_reply?: string;
  last_ocr_text?: string;
};

function delay(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timeout = window.setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timeout);
      reject(new DOMException('Aborted', 'AbortError'));
    }, { once: true });
  });
}

async function callPlugin(entryId: string, args: Record<string, unknown> = {}, signal?: AbortSignal) {
  const createResp = await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
    signal,
  });
  if (!createResp.ok) {
    throw new Error(`Run create failed: HTTP ${createResp.status}`);
  }
  const created = await createResp.json();
  const runId = created.run_id || created.id;
  let failureCount = 0;
  for (let i = 0; i < 40; i += 1) {
    await delay(300, signal);
    const runResp = await fetch(`/runs/${runId}`, { signal });
    if (!runResp.ok) {
      failureCount += 1;
      console.warn('study_companion run polling failed', { runId, status: runResp.status });
      if (failureCount >= 3) {
        throw new Error(`Run poll failed: HTTP ${runResp.status}`);
      }
      continue;
    }
    failureCount = 0;
    const run = await runResp.json();
    if (run.status === 'succeeded') {
      const exportResp = await fetch(`/runs/${runId}/export`, { signal });
      const exported = exportResp.ok ? await exportResp.json() : {};
      const item = (exported.items || []).find((candidate: any) => candidate.type === 'json' && candidate.json);
      return item?.json?.data || {};
    }
    if (['failed', 'canceled', 'timeout'].includes(run.status)) {
      throw new Error(run.error?.message || run.message || run.status);
    }
  }
  throw new Error('plugin_call_timeout');
}

export default function StudyPanel(props: PluginSurfaceProps) {
  const t = (key: string, defaultValue?: string) => props.t?.(key) || defaultValue || key;
  const [status, setStatus] = useState<StudyStatus>({});
  const [text, setText] = useState('');
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh(signal?: AbortSignal) {
    const data = await callPlugin('study_status', {}, signal) as StudyStatus;
    if (signal?.aborted) {
      return;
    }
    setStatus(data);
    setReply(data.last_reply || '');
    if (!text.trim() && data.last_ocr_text) {
      setText(data.last_ocr_text);
    }
  }

  async function explain() {
    setBusy(true);
    try {
      const data = await callPlugin('study_explain_text', { text }) as { reply?: string; summary?: string };
      setReply(data.reply || data.summary || '');
      await refresh();
    } catch (error) {
      const message = error instanceof Error && error.message === 'plugin_call_timeout'
        ? t('ui.error.plugin_call_timeout', 'Plugin call timed out')
        : error instanceof Error
          ? error.message
          : String(error);
      setReply(message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal).catch((error) => {
      if (controller.signal.aborted) {
        return;
      }
      const message = error instanceof Error && error.message === 'plugin_call_timeout'
        ? t('ui.error.plugin_call_timeout', 'Plugin call timed out')
        : error instanceof Error
          ? error.message
          : String(error);
      setReply(message);
    });
    return () => controller.abort();
  }, []);

  return (
    <div className="study-panel">
      <header>
        <h1>{t('ui.title', 'Study Companion')}</h1>
        <span>{status.status || 'unknown'} / {status.active_mode || 'concept_explain'}</span>
      </header>
      <textarea
        aria-label={t('ui.label.text', 'Text')}
        placeholder={t('ui.placeholder.input', 'Paste a concept, problem statement, or OCR text here.')}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <button type="button" disabled={busy} onClick={explain}>{t('ui.button.explain', 'Explain')}</button>
      <div className="study-panel__reply-label">{t('ui.label.reply', 'Reply')}</div>
      <pre>{reply}</pre>
    </div>
  );
}
