import { useEffect, useRef, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

type StudyStatus = {
  status?: string;
  active_mode?: string;
  last_reply?: string;
  last_ocr_text?: string;
};

const RUN_POLL_INITIAL_DELAY_MS = 300;
const RUN_POLL_MAX_DELAY_MS = 2000;
const RUN_EXPORT_RETRY_COUNT = 3;
const RUN_EXPORT_RETRY_DELAY_MS = 400;
const ENTRY_TIMEOUT_MS: Record<string, number> = {
  study_status: 15000,
  study_ocr_snapshot: 60000,
  study_explain_text: 60000,
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

function timeoutForEntry(entryId: string) {
  return ENTRY_TIMEOUT_MS[entryId] || 60000;
}

async function exportRunResult(runId: string, signal?: AbortSignal) {
  let lastStatus = 0;
  for (let attempt = 0; attempt < RUN_EXPORT_RETRY_COUNT; attempt += 1) {
    const exportResp = await fetch(`/runs/${runId}/export`, { signal });
    lastStatus = exportResp.status;
    if (exportResp.ok) {
      const exported = await exportResp.json();
      const item = (exported.items || []).find((candidate: any) => candidate.type === 'json' && candidate.json);
      const pluginResponse = item ? (item.json || {}) : {};
      if (pluginResponse.success === false || pluginResponse.error) {
        throw new Error(pluginResponse.error?.message || pluginResponse.message || 'Plugin call failed');
      }
      if (!item) {
        throw new Error('Run export missing JSON result');
      }
      return pluginResponse.data || {};
    }
    if (attempt < RUN_EXPORT_RETRY_COUNT - 1) {
      await delay(RUN_EXPORT_RETRY_DELAY_MS * (attempt + 1), signal);
    }
  }
  throw new Error(`Run export failed: HTTP ${lastStatus}`);
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
  if (!runId) {
    console.error('study_companion run create response missing run id', created);
    throw new Error('run_id_missing');
  }
  let failureCount = 0;
  let pollDelay = RUN_POLL_INITIAL_DELAY_MS;
  const deadline = Date.now() + timeoutForEntry(entryId);
  while (Date.now() < deadline) {
    await delay(Math.min(pollDelay, Math.max(0, deadline - Date.now())), signal);
    pollDelay = Math.min(Math.round(pollDelay * 1.5), RUN_POLL_MAX_DELAY_MS);
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
      return await exportRunResult(runId, signal);
    }
    if (['failed', 'canceled', 'timeout'].includes(run.status)) {
      throw new Error(run.error?.message || run.message || run.status);
    }
  }
  throw new Error('plugin_call_timeout');
}

export default function StudyPanel(props: PluginSurfaceProps) {
  const t = (key: string, defaultValue?: string) => {
    const translated = props.t?.(key);
    return translated && translated !== key ? translated : defaultValue || key;
  };
  const [status, setStatus] = useState<StudyStatus>({});
  const [text, setText] = useState('');
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const explainControllerRef = useRef<AbortController | null>(null);

  async function refresh(signal?: AbortSignal, options: { updateReply?: boolean } = {}) {
    const updateReply = options.updateReply !== false;
    const data = await callPlugin('study_status', {}, signal) as StudyStatus;
    if (signal?.aborted) {
      return;
    }
    setStatus(data);
    if (updateReply) {
      setReply(data.last_reply || '');
    }
    if (!text.trim() && data.last_ocr_text) {
      setText(data.last_ocr_text);
    }
  }

  async function explain() {
    if (busy) {
      return;
    }
    const controller = new AbortController();
    explainControllerRef.current?.abort();
    explainControllerRef.current = controller;
    setBusy(true);
    try {
      const data = await callPlugin('study_explain_text', { text }, controller.signal) as { reply?: string; summary?: string };
      if (controller.signal.aborted) {
        return;
      }
      const nextReply = data.reply || data.summary || '';
      setReply(nextReply);
      await refresh(controller.signal, { updateReply: false });
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      const message = error instanceof Error && error.message === 'plugin_call_timeout'
        ? t('ui.error.plugin_call_timeout', 'Plugin call timed out')
        : error instanceof Error && error.message === 'run_id_missing'
          ? t('ui.error.run_id_missing', 'Run id missing')
        : error instanceof Error
          ? error.message
          : String(error);
      setReply(message);
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
      }
      if (explainControllerRef.current === controller) {
        explainControllerRef.current = null;
      }
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
    return () => {
      controller.abort();
      explainControllerRef.current?.abort();
    };
  }, []);

  const stateValue = status.status || 'unknown';
  const modeValue = status.active_mode || 'concept_explain';
  const stateLabel = t(`status.state.${stateValue}`, stateValue);
  const modeLabel = t(`status.mode.${modeValue}`, modeValue);
  const explainLabel = busy ? t('ui.button.loading', 'Loading...') : t('ui.button.explain', 'Explain');

  return (
    <div className="study-panel">
      <header>
        <h1>{t('ui.title', 'Study Companion')}</h1>
        <span>{stateLabel} / {modeLabel}</span>
      </header>
      <textarea
        aria-label={t('ui.label.text', 'Text')}
        placeholder={t('ui.placeholder.input', 'Paste a concept, problem statement, or OCR text here.')}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <button
        type="button"
        className={busy ? 'loading' : ''}
        disabled={busy}
        aria-busy={busy}
        aria-label={explainLabel}
        onClick={busy ? undefined : explain}
      >
        {explainLabel}
      </button>
      <div className="study-panel__reply-label">{t('ui.label.reply', 'Reply')}</div>
      <pre>{reply}</pre>
    </div>
  );
}
