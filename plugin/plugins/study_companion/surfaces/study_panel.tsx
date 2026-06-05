import { useEffect, useRef, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

type StudyStatus = {
  status?: string;
  active_mode?: string;
  mode?: string;
  last_reply?: string;
  last_ocr_text?: string;
  last_error?: string;
  screen_classification?: {
    screen_type?: string;
    confidence?: number;
    reason?: string;
  };
  current_question?: {
    question?: string;
    answer?: string;
    hint?: string;
    topic?: string;
    difficulty?: number;
  };
  last_answer_evaluation?: {
    verdict?: string;
    score?: number;
    feedback?: string;
    next_action?: string;
  };
  last_session_summary?: string;
};

type StudyMode = 'companion' | 'interactive' | 'teaching';

const RUN_POLL_INITIAL_DELAY_MS = 300;
const RUN_POLL_MAX_DELAY_MS = 2000;
const RUN_EXPORT_RETRY_COUNT = 3;
const RUN_EXPORT_RETRY_DELAY_MS = 400;
const ENTRY_TIMEOUT_MS: Record<string, number> = {
  study_status: 15000,
  study_ocr_snapshot: 60000,
  study_set_mode: 15000,
  study_explain_text: 60000,
  study_generate_question: 75000,
  study_evaluate_answer: 75000,
  study_summarize_session: 90000,
};

const MODE_ORDER: Array<{ id: StudyMode; labelKey: string; fallback: string }> = [
  { id: 'companion', labelKey: 'status.mode.companion', fallback: 'Companion' },
  { id: 'interactive', labelKey: 'status.mode.interactive', fallback: 'Interactive' },
  { id: 'teaching', labelKey: 'status.mode.teaching', fallback: 'Teaching' },
];
const KATEX_CSS_URL = '/plugin/study_companion/ui/katex.min.css';
const KATEX_SCRIPT_URL = '/plugin/study_companion/ui/katex.min.js';
const KATEX_RENDER_SCRIPT_URL = '/plugin/study_companion/ui/katex-render.js';
let katexLoadPromise: Promise<void> | null = null;

type MathTextPart = {
  type: 'text' | 'math';
  value: string;
  display?: boolean;
};

type StudyMathTools = {
  splitByMath: (value: string) => MathTextPart[];
  normalizeLatexForKatex: (value: string) => string;
};

function getStudyMathTools(): StudyMathTools | null {
  const tools = (window as any).__studyCompanionMath;
  if (
    tools
    && typeof tools.splitByMath === 'function'
    && typeof tools.normalizeLatexForKatex === 'function'
  ) {
    return tools as StudyMathTools;
  }
  return null;
}

function hasHostedKatex() {
  const katex = (window as any).katex;
  return Boolean(
    katex
    && typeof katex.render === 'function'
    && typeof katex.renderToString === 'function',
  );
}

function ensureHostedScript(id: string, src: string) {
  return new Promise<void>((resolve) => {
    const resolveLoad = (script: HTMLScriptElement) => {
      script.dataset.studyKatexLoaded = 'true';
      resolve();
    };
    const resolveError = (script: HTMLScriptElement) => {
      script.dataset.studyKatexFailed = 'true';
      katexLoadPromise = null;
      script.remove();
      resolve();
    };
    const existing = document.getElementById(id) as HTMLScriptElement | null;
    if (existing) {
      if (existing.dataset.studyKatexLoaded === 'true') {
        resolve();
        return;
      }
      if (existing.dataset.studyKatexFailed === 'true') {
        existing.remove();
      } else {
        existing.addEventListener('load', () => resolveLoad(existing), { once: true });
        existing.addEventListener('error', () => resolveError(existing), { once: true });
        return;
      }
    }
    const script = document.createElement('script');
    script.id = id;
    script.src = src;
    script.async = true;
    script.addEventListener('load', () => resolveLoad(script), { once: true });
    script.addEventListener('error', () => resolveError(script), { once: true });
    document.head.appendChild(script);
  });
}

function ensureHostedKatex() {
  if (hasHostedKatex() && getStudyMathTools()) {
    return Promise.resolve();
  }
  if (katexLoadPromise) {
    return katexLoadPromise;
  }
  katexLoadPromise = new Promise((resolve) => {
    if (!document.getElementById('study-companion-katex-css')) {
      const link = document.createElement('link');
      link.id = 'study-companion-katex-css';
      link.rel = 'stylesheet';
      link.href = KATEX_CSS_URL;
      document.head.appendChild(link);
    }
    ensureHostedScript('study-companion-katex-script', KATEX_SCRIPT_URL)
      .then(() => ensureHostedScript('study-companion-katex-render-script', KATEX_RENDER_SCRIPT_URL))
      .then(resolve);
  });
  return katexLoadPromise;
}

function renderMathSpans(root: HTMLElement | null) {
  const katex = (window as any).katex;
  const mathTools = getStudyMathTools();
  if (!root || !mathTools || !katex || typeof katex.render !== 'function') {
    return;
  }
  root.querySelectorAll<HTMLElement>('[data-study-math]').forEach((node) => {
    const tex = mathTools.normalizeLatexForKatex(node.getAttribute('data-math') || '');
    if (!tex) {
      return;
    }
    try {
      katex.render(tex, node, {
        displayMode: node.getAttribute('data-display') === 'true',
        throwOnError: false,
        trust: false,
      });
    } catch (_error) {
      // Keep the source text fallback already rendered in the span.
    }
  });
}

function MathReply({ text, label }: { text: string; label: string }) {
  const containerRef = useRef<HTMLElement | null>(null);
  const [mathReady, setMathReady] = useState(() => Boolean(getStudyMathTools()));
  useEffect(() => {
    let active = true;
    ensureHostedKatex().then(() => {
      if (active) {
        setMathReady(Boolean(getStudyMathTools()));
      }
    });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (mathReady) {
      renderMathSpans(containerRef.current);
    }
  }, [mathReady, text]);
  const mathTools = mathReady ? getStudyMathTools() : null;
  const parts = mathTools ? mathTools.splitByMath(text) : [{ type: 'text', value: text }];
  return (
    <div
      ref={containerRef}
      className="study-panel__math-reply"
      role="status"
      aria-live="polite"
      aria-label={label}
      style={{
        minHeight: '180px',
        whiteSpace: 'pre-wrap',
        overflowWrap: 'break-word',
        border: '1px solid rgba(148, 163, 184, 0.36)',
        borderRadius: '8px',
        background: 'rgba(255, 255, 255, 0.84)',
        padding: '12px',
        lineHeight: '1.5',
      }}
    >
      {parts.map((part, index) => {
        if (part.type === 'math') {
          const wrapper = part.display ? '$$' : '$';
          return (
            <span
              key={`math-${index}`}
              data-study-math="true"
              data-display={part.display ? 'true' : 'false'}
              data-math={part.value}
            >
              {wrapper}{part.value}{wrapper}
            </span>
          );
        }
        return <span key={`text-${index}`}>{part.value}</span>;
      })}
    </div>
  );
}

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
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const explainControllerRef = useRef<AbortController | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const currentMode = String(status.active_mode || status.mode || 'companion');

  function beginStudyRequest() {
    explainControllerRef.current?.abort();
    const controller = new AbortController();
    explainControllerRef.current = controller;
    return controller;
  }

  function endStudyRequest(controller: AbortController) {
    if (explainControllerRef.current === controller) {
      explainControllerRef.current = null;
    }
  }

  function modeLabel(mode: string) {
    const entry = MODE_ORDER.find((candidate) => candidate.id === mode);
    return entry ? t(entry.labelKey, entry.fallback) : String(mode || MODE_ORDER[0].id);
  }

  function screenLabel(type: string) {
    const normalized = String(type || 'idle');
    return t(`ui.status.screen.${normalized}`, normalized);
  }

  function normalizeStudyStatus(value: unknown): StudyStatus {
    if (!value || typeof value !== 'object') {
      return {};
    }
    const data = value as Record<string, unknown>;
    const screen = data.screen_classification && typeof data.screen_classification === 'object'
      ? data.screen_classification as Record<string, unknown>
      : undefined;
    const question = data.current_question && typeof data.current_question === 'object'
      ? data.current_question as Record<string, unknown>
      : undefined;
    const evaluation = data.last_answer_evaluation && typeof data.last_answer_evaluation === 'object'
      ? data.last_answer_evaluation as Record<string, unknown>
      : undefined;
    return {
      status: typeof data.status === 'string' ? data.status : undefined,
      active_mode: typeof data.active_mode === 'string' ? data.active_mode : undefined,
      mode: typeof data.mode === 'string' ? data.mode : undefined,
      last_reply: typeof data.last_reply === 'string' ? data.last_reply : undefined,
      last_ocr_text: typeof data.last_ocr_text === 'string' ? data.last_ocr_text : undefined,
      last_error: typeof data.last_error === 'string' ? data.last_error : undefined,
      screen_classification: screen ? {
        screen_type: typeof screen.screen_type === 'string' ? screen.screen_type : undefined,
        confidence: typeof screen.confidence === 'number' ? screen.confidence : undefined,
        reason: typeof screen.reason === 'string' ? screen.reason : undefined,
      } : undefined,
      current_question: question ? {
        question: typeof question.question === 'string' ? question.question : undefined,
        answer: typeof question.answer === 'string' ? question.answer : undefined,
        hint: typeof question.hint === 'string' ? question.hint : undefined,
        topic: typeof question.topic === 'string' ? question.topic : undefined,
        difficulty: typeof question.difficulty === 'number' ? question.difficulty : undefined,
      } : undefined,
      last_answer_evaluation: evaluation ? {
        verdict: typeof evaluation.verdict === 'string' ? evaluation.verdict : undefined,
        score: typeof evaluation.score === 'number' ? evaluation.score : undefined,
        feedback: typeof evaluation.feedback === 'string' ? evaluation.feedback : undefined,
        next_action: typeof evaluation.next_action === 'string' ? evaluation.next_action : undefined,
      } : undefined,
      last_session_summary: typeof data.last_session_summary === 'string' ? data.last_session_summary : undefined,
    };
  }

  function formatPluginError(error: unknown) {
    return error instanceof Error && error.message === 'plugin_call_timeout'
      ? t('ui.error.plugin_call_timeout', 'Plugin call timed out')
      : error instanceof Error && error.message === 'run_id_missing'
        ? t('ui.error.run_id_missing', 'Run id missing')
        : error instanceof Error && error.message === 'plugin_call_failed'
          ? t('ui.error.plugin_call_failed', 'Plugin call failed')
          : error instanceof Error
            ? error.message
            : String(error);
  }

  function compactText(value: string | undefined) {
    const trimmed = String(value || '').trim();
    if (!trimmed) {
      return '-';
    }
    return trimmed.length > 72 ? `${trimmed.slice(0, 72)}...` : trimmed;
  }

  function setStatusLine(data: StudyStatus) {
    setStatus({ ...data, active_mode: String(data.active_mode || data.mode || 'companion') });
    setQuestion(data.current_question?.question || '');
  }

  async function refresh(signal?: AbortSignal, options: { updateReply?: boolean } = {}) {
    const updateReply = options.updateReply !== false;
    const data = normalizeStudyStatus(await callPlugin('study_status', {}, signal));
    if (signal?.aborted) {
      return;
    }
    setStatusLine(data);
    if (updateReply) {
      setReply(data.last_reply || '');
    }
    setText((prev) => (prev.trim() || !data.last_ocr_text ? prev : data.last_ocr_text));
  }

  async function setMode(mode: StudyMode) {
    if (busy || mode === currentMode) {
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      setReply('');
      const data = await callPlugin('study_set_mode', { mode, reason: 'ui' }, controller.signal) as {
        changed?: boolean;
        transition_phrase?: string;
        new_mode?: string;
        locked?: boolean;
        lock_reason?: string;
      };
      if (controller.signal.aborted) {
        return;
      }
      const appliedMode = String(
        data.new_mode || (data.changed === false ? currentMode : mode) || 'companion',
      ) as StudyMode;
      setStatus((prev) => ({
        ...prev,
        active_mode: appliedMode,
        mode: appliedMode,
      }));
      if (data.transition_phrase) {
        setReply(data.transition_phrase);
      }
      await refresh(controller.signal, { updateReply: false });
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      setReply(formatPluginError(error));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
      }
      endStudyRequest(controller);
    }
  }

  async function explain() {
    if (busy) {
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      const data = await callPlugin('study_explain_text', { text }, controller.signal) as {
        reply?: string;
        summary?: string;
        transition_phrase?: string;
      };
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
      setReply(formatPluginError(error));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
      }
      endStudyRequest(controller);
    }
  }

  async function generateQuestion() {
    if (busy) {
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      const data = await callPlugin('study_generate_question', { text }, controller.signal) as {
        question?: string;
        hint?: string;
        summary?: string;
        reply?: string;
      };
      if (controller.signal.aborted) {
        return;
      }
      setQuestion(data.question || '');
      setReply(data.hint || data.question || data.summary || data.reply || '');
      await refresh(controller.signal, { updateReply: false });
    } catch (error) {
      if (!controller.signal.aborted) {
        setReply(formatPluginError(error));
      }
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
      }
      endStudyRequest(controller);
    }
  }

  async function evaluateAnswer() {
    if (busy) {
      return;
    }
    if (!answer.trim()) {
      setReply(t('ui.error.missing_answer', 'Please enter an answer first.'));
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      const data = await callPlugin('study_evaluate_answer', { answer, question }, controller.signal) as {
        feedback?: string;
        next_action?: string;
        summary?: string;
        reply?: string;
      };
      if (controller.signal.aborted) {
        return;
      }
      const replyParts = [data.feedback || data.reply || '', data.next_action ? `Next: ${data.next_action}` : ''].filter(Boolean);
      setReply(replyParts.join('\n\n') || data.summary || '');
      await refresh(controller.signal, { updateReply: false });
    } catch (error) {
      if (!controller.signal.aborted) {
        setReply(formatPluginError(error));
      }
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
      }
      endStudyRequest(controller);
    }
  }

  async function summarizeSession() {
    if (busy) {
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      const data = await callPlugin('study_summarize_session', {}, controller.signal) as {
        markdown?: string;
        summary?: string;
        reply?: string;
      };
      if (controller.signal.aborted) {
        return;
      }
      setReply(data.markdown || data.summary || data.reply || '');
      await refresh(controller.signal, { updateReply: false });
    } catch (error) {
      if (!controller.signal.aborted) {
        setReply(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
      }
      endStudyRequest(controller);
    }
  }

  useEffect(() => {
    const controller = beginStudyRequest();
    refresh(controller.signal).catch((error) => {
      if (controller.signal.aborted) {
        return;
      }
      setReply(formatPluginError(error));
    });
    return () => {
      controller.abort();
      explainControllerRef.current?.abort();
      explainControllerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) {
      return undefined;
    }
    const closeOrCancelOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return;
      }
      const hasInFlightRequest = !!explainControllerRef.current;
      if (!hasInFlightRequest) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      explainControllerRef.current?.abort();
      explainControllerRef.current = null;
      setBusy(false);
      const activeElement = document.activeElement as HTMLElement | null;
      activeElement?.blur?.();
    };
    panel.addEventListener('keydown', closeOrCancelOnEscape, true);
    return () => {
      panel.removeEventListener('keydown', closeOrCancelOnEscape, true);
    };
  }, []);

  const stateValue = status.status || 'unknown';
  const stateLabel = t(`status.state.${stateValue}`, stateValue);
  const explainLabel = busy ? t('ui.button.loading', 'Loading...') : t('ui.button.explain', 'Explain');
  const screenType = status.screen_classification?.screen_type || 'idle';
  const evaluation = status.last_answer_evaluation;

  return (
    <div
      ref={panelRef}
      className="study-panel"
      role="region"
      aria-label={t('ui.surface.study_panel', 'Study Panel')}
    >
      <header className="study-panel__header">
        <div>
          <h1>{t('ui.title', 'Study Companion')}</h1>
          <span>{stateLabel} / {modeLabel(currentMode)}</span>
        </div>
        <div className="study-panel__modes" role="group" aria-label={t('ui.label.mode', 'Mode')}>
          {MODE_ORDER.map((item) => {
            const pressed = currentMode === item.id;
            return (
              <button
                key={item.id}
                type="button"
                className={pressed ? 'is-active' : ''}
                aria-pressed={pressed}
                disabled={busy}
                onClick={() => setMode(item.id)}
              >
                {modeLabel(item.id)}
              </button>
            );
          })}
        </div>
      </header>
      <section className="study-panel__state">
        <div>
          <span>{t('ui.label.screen', 'Screen')}</span>
          <strong>{screenLabel(screenType)}</strong>
        </div>
        <div>
          <span>{t('ui.label.question', 'Question')}</span>
          <strong>{compactText(question || status.current_question?.question)}</strong>
        </div>
        <div>
          <span>{t('ui.label.answer', 'Answer')}</span>
          <strong>{evaluation?.verdict ? `${evaluation.verdict}${evaluation.score !== undefined ? ` / ${evaluation.score}` : ''}` : '-'}</strong>
        </div>
      </section>
      <textarea
        aria-label={t('ui.label.text', 'Text')}
        placeholder={t('ui.placeholder.input', 'Paste a concept, problem statement, or OCR text here.')}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="study-panel__actions">
        <button
          type="button"
          disabled={busy}
          onClick={busy ? undefined : generateQuestion}
        >
          {busy ? t('ui.button.loading', 'Loading...') : t('ui.button.generate_question', 'Generate Question')}
        </button>
      </div>
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
      <div className="study-panel__reply-label">{t('ui.label.question', 'Question')}</div>
      <pre>{question}</pre>
      <textarea
        aria-label={t('ui.label.answer', 'Answer')}
        value={answer}
        onChange={(event) => setAnswer(event.target.value)}
      />
      <div className="study-panel__actions">
        <button type="button" disabled={busy} onClick={busy ? undefined : evaluateAnswer}>
          {busy ? t('ui.button.loading', 'Loading...') : t('ui.button.evaluate_answer', 'Evaluate Answer')}
        </button>
        <button type="button" disabled={busy} onClick={busy ? undefined : summarizeSession}>
          {busy ? t('ui.button.loading', 'Loading...') : t('ui.button.summarize_session', 'Summarize Session')}
        </button>
      </div>
      <div className="study-panel__reply-label">{t('ui.label.reply', 'Reply')}</div>
      <MathReply text={reply} label={t('ui.label.reply', 'Reply')} />
    </div>
  );
}
