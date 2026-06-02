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

const MAX_PASTE_IMAGE_LONG_SIDE = 1920;
const TARGET_DATA_URL_LENGTH = 1_000_000;
const LOAD_IMAGE_TIMEOUT_MS = 30000;
const SUPPORTED_PASTE_IMAGE_TYPES = new Set(['image/jpeg', 'image/png']);

function warnInDev(...args: unknown[]) {
  const meta = import.meta as unknown as { env?: { DEV?: boolean } };
  if (meta.env?.DEV) {
    console.warn(...args);
  }
}

function assertNotAborted(signal?: AbortSignal) {
  if (signal?.aborted) {
    throw new DOMException('Aborted', 'AbortError');
  }
}

function loadImage(
  src: string,
  signal?: AbortSignal,
  timeoutMs = LOAD_IMAGE_TIMEOUT_MS,
): Promise<HTMLImageElement> {
  let img: HTMLImageElement | null = null;
  let timeoutId = 0;
  let abortHandler: (() => void) | null = null;
  const imagePromise = new Promise<HTMLImageElement>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    img = new Image();
    img.onload = () => resolve(img as HTMLImageElement);
    img.onerror = () => reject(new Error('Failed to load image'));
    img.src = src;
  });

  const timeoutPromise = new Promise<never>((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error('Image load timeout')), timeoutMs);
  });

  const abortPromise = new Promise<never>((_, reject) => {
    if (!signal) {
      return;
    }
    abortHandler = () => reject(new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', abortHandler, { once: true });
  });

  return Promise.race([imagePromise, timeoutPromise, abortPromise]).finally(() => {
    if (timeoutId) {
      window.clearTimeout(timeoutId);
    }
    if (signal && abortHandler) {
      signal.removeEventListener('abort', abortHandler);
    }
    if (img) {
      img.onload = null;
      img.onerror = null;
    }
  });
}

function requireCanvasContext(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    throw new Error('Canvas 2D context is unavailable');
  }
  return ctx;
}

function encodeJpegWithinTarget(canvas: HTMLCanvasElement) {
  let low = 0.3;
  let high = 0.82;
  let best = '';
  let fallback = '';
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const quality = Math.round(((low + high) / 2) * 100) / 100;
    const dataUrl = canvas.toDataURL('image/jpeg', quality);
    fallback = dataUrl;
    if (dataUrl.length <= TARGET_DATA_URL_LENGTH) {
      best = dataUrl;
      low = quality;
    } else {
      high = quality;
    }
  }
  return best || fallback;
}

async function compressImageForStudy(blob: Blob, signal?: AbortSignal): Promise<string | null> {
  if (!SUPPORTED_PASTE_IMAGE_TYPES.has(blob.type)) {
    return null;
  }
  const url = URL.createObjectURL(blob);
  try {
    const img = await loadImage(url, signal);
    assertNotAborted(signal);
    let width = img.naturalWidth;
    let height = img.naturalHeight;
    if (!width || !height) {
      throw new Error('Image dimensions are unavailable');
    }
    const longSide = Math.max(width, height);
    if (longSide > MAX_PASTE_IMAGE_LONG_SIDE) {
      const scale = MAX_PASTE_IMAGE_LONG_SIDE / longSide;
      width = Math.round(width * scale);
      height = Math.round(height * scale);
    }
    let canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    let ctx = requireCanvasContext(canvas);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);
    let dataUrl = encodeJpegWithinTarget(canvas);
    for (let attempt = 0; dataUrl.length > TARGET_DATA_URL_LENGTH && attempt < 3; attempt += 1) {
      assertNotAborted(signal);
      const scale = Math.max(
        0.5,
        Math.min(0.85, Math.sqrt(TARGET_DATA_URL_LENGTH / dataUrl.length) * 0.9),
      );
      width = Math.max(320, Math.round(width * scale));
      height = Math.max(320, Math.round(height * scale));
      const resized = document.createElement('canvas');
      resized.width = width;
      resized.height = height;
      ctx = requireCanvasContext(resized);
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(canvas, 0, 0, width, height);
      canvas = resized;
      dataUrl = canvas.toDataURL('image/jpeg', 0.3);
    }
    return dataUrl;
  } catch (error) {
    if (signal?.aborted) {
      return null;
    }
    warnInDev('compressImageForStudy failed', error);
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}

type PasteSetters = {
  setImage: (value: string) => void;
  setTextValue: (value: string) => void;
  setPasteError: (value: string) => void;
  pasteErrorMessage: string;
  unsupportedTypeMessage: string;
};

function createPasteHandler(
  setters: PasteSetters,
  getBusy: () => boolean,
  isMounted: () => boolean,
  beginPasteSignal: () => AbortSignal,
) {
  return async function handlePaste(event: {
    clipboardData?: DataTransfer;
    preventDefault: () => void;
    target: EventTarget | null;
  }) {
    if (getBusy()) return;
    const items = event.clipboardData?.items;
    if (!items) return;
    const target = event.target as HTMLTextAreaElement | null;
    const itemList = Array.from(items);
    if (!itemList.some((item) => item.type.startsWith('image/'))) {
      return;
    }
    event.preventDefault();
    const signal = beginPasteSignal();
    setters.setPasteError('');

    for (const item of itemList) {
      if (item.type.startsWith('image/')) {
        if (!SUPPORTED_PASTE_IMAGE_TYPES.has(item.type)) {
          if (!signal.aborted && isMounted()) {
            setters.setPasteError(setters.unsupportedTypeMessage);
          }
          continue;
        }
        const blob = item.getAsFile();
        if (!blob) {
          if (!signal.aborted && isMounted()) {
            setters.setPasteError(setters.pasteErrorMessage);
          }
          continue;
        }
        try {
          const image = await compressImageForStudy(blob, signal);
          if (signal.aborted || !isMounted()) {
            return;
          }
          if (image === null) {
            setters.setPasteError(setters.pasteErrorMessage);
          } else {
            setters.setImage(image);
            setters.setPasteError('');
          }
        } catch (error) {
          if (!signal.aborted && isMounted()) {
            setters.setPasteError(setters.pasteErrorMessage);
          }
          warnInDev('study image paste failed', error);
        }
      } else if (item.type === 'text/plain') {
        item.getAsString((pastedText) => {
          if (!target || signal.aborted || !isMounted() || !target.isConnected) return;
          const start = target.selectionStart ?? target.value.length;
          const end = target.selectionEnd ?? start;
          setters.setTextValue(
            target.value.slice(0, start) + pastedText + target.value.slice(end),
          );
          requestAnimationFrame(() => {
            if (!signal.aborted && isMounted() && target.isConnected) {
              target.setSelectionRange(start + pastedText.length, start + pastedText.length);
            }
          });
        });
      }
    }
  };
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
  const [textImage, setTextImage] = useState('');
  const [answerImage, setAnswerImage] = useState('');
  const [textPasteError, setTextPasteError] = useState('');
  const [answerPasteError, setAnswerPasteError] = useState('');
  const explainControllerRef = useRef<AbortController | null>(null);
  const pasteControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
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

  function beginPasteSignal() {
    pasteControllerRef.current?.abort();
    const controller = new AbortController();
    pasteControllerRef.current = controller;
    return controller.signal;
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
    const explainArgs: Record<string, unknown> = { text };
    if (textImage) explainArgs.vision_image_base64 = textImage;
    let shouldClearTextImage = false;
    try {
      const data = await callPlugin('study_explain_text', explainArgs, controller.signal) as {
        reply?: string;
        summary?: string;
        transition_phrase?: string;
      };
      if (controller.signal.aborted) {
        return;
      }
      shouldClearTextImage = true;
      const nextReply = data.reply || data.summary || '';
      setReply(nextReply);
      await refresh(controller.signal, { updateReply: false });
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      shouldClearTextImage = true;
      setReply(formatPluginError(error));
    } finally {
      if (!controller.signal.aborted) {
        if (shouldClearTextImage) {
          setTextImage('');
          setTextPasteError('');
        }
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
    const genArgs: Record<string, unknown> = { text };
    if (textImage) genArgs.vision_image_base64 = textImage;
    let shouldClearTextImage = false;
    try {
      const data = await callPlugin('study_generate_question', genArgs, controller.signal) as {
        question?: string;
        hint?: string;
        summary?: string;
        reply?: string;
      };
      if (controller.signal.aborted) {
        return;
      }
      shouldClearTextImage = true;
      setQuestion(data.question || '');
      setReply(data.hint || data.question || data.summary || data.reply || '');
      await refresh(controller.signal, { updateReply: false });
    } catch (error) {
      if (!controller.signal.aborted) {
        shouldClearTextImage = true;
        setReply(formatPluginError(error));
      }
    } finally {
      if (!controller.signal.aborted) {
        if (shouldClearTextImage) {
          setTextImage('');
          setTextPasteError('');
        }
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
    const evalArgs: Record<string, unknown> = { answer, question };
    if (answerImage) evalArgs.vision_image_base64 = answerImage;
    let shouldClearAnswerImage = false;
    try {
      const data = await callPlugin('study_evaluate_answer', evalArgs, controller.signal) as {
        feedback?: string;
        next_action?: string;
        summary?: string;
        reply?: string;
      };
      if (controller.signal.aborted) {
        return;
      }
      shouldClearAnswerImage = true;
      const replyParts = [data.feedback || data.reply || '', data.next_action ? `Next: ${data.next_action}` : ''].filter(Boolean);
      setReply(replyParts.join('\n\n') || data.summary || '');
      await refresh(controller.signal, { updateReply: false });
    } catch (error) {
      if (!controller.signal.aborted) {
        shouldClearAnswerImage = true;
        setReply(formatPluginError(error));
      }
    } finally {
      if (!controller.signal.aborted) {
        if (shouldClearAnswerImage) {
          setAnswerImage('');
          setAnswerPasteError('');
        }
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
    mountedRef.current = true;
    const controller = beginStudyRequest();
    refresh(controller.signal).catch((error) => {
      if (controller.signal.aborted) {
        return;
      }
      setReply(formatPluginError(error));
    });
    return () => {
      mountedRef.current = false;
      controller.abort();
      explainControllerRef.current?.abort();
      explainControllerRef.current = null;
      pasteControllerRef.current?.abort();
      pasteControllerRef.current = null;
    };
  }, []);

  const stateValue = status.status || 'unknown';
  const stateLabel = t(`status.state.${stateValue}`, stateValue);
  const explainLabel = busy ? t('ui.button.loading', 'Loading...') : t('ui.button.explain', 'Explain');
  const screenType = status.screen_classification?.screen_type || 'idle';
  const evaluation = status.last_answer_evaluation;
  const handleTextPaste = createPasteHandler(
    {
      setImage: setTextImage,
      setTextValue: setText,
      setPasteError: setTextPasteError,
      pasteErrorMessage: t('ui.error.image_paste_failed', 'Image paste failed. Please try a smaller JPEG or PNG image.'),
      unsupportedTypeMessage: t('ui.error.image_paste_unsupported', 'Only JPEG and PNG images can be pasted here.'),
    },
    () => busy,
    () => mountedRef.current,
    beginPasteSignal,
  );
  const handleAnswerPaste = createPasteHandler(
    {
      setImage: setAnswerImage,
      setTextValue: setAnswer,
      setPasteError: setAnswerPasteError,
      pasteErrorMessage: t('ui.error.image_paste_failed', 'Image paste failed. Please try a smaller JPEG or PNG image.'),
      unsupportedTypeMessage: t('ui.error.image_paste_unsupported', 'Only JPEG and PNG images can be pasted here.'),
    },
    () => busy,
    () => mountedRef.current,
    beginPasteSignal,
  );

  return (
    <div className="study-panel" data-busy={busy ? "true" : "false"}>
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
        readOnly={busy}
        onChange={(event) => setText(event.target.value)}
        onPaste={handleTextPaste}
      />
      {textImage ? (
        <div className="study-panel__image-preview">
          <img src={textImage} alt="pasted study context" />
          <button
            className="study-panel__image-remove"
            type="button"
            aria-label="Remove pasted image"
            disabled={busy}
            onClick={() => {
              setTextImage('');
              setTextPasteError('');
            }}
          >
            x
          </button>
        </div>
      ) : null}
      {textPasteError ? (
        <div className="study-panel__paste-error" role="alert">{textPasteError}</div>
      ) : null}
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
        readOnly={busy}
        onChange={(event) => setAnswer(event.target.value)}
        onPaste={handleAnswerPaste}
      />
      {answerImage ? (
        <div className="study-panel__image-preview">
          <img src={answerImage} alt="pasted answer context" />
          <button
            className="study-panel__image-remove"
            type="button"
            aria-label="Remove pasted answer image"
            disabled={busy}
            onClick={() => {
              setAnswerImage('');
              setAnswerPasteError('');
            }}
          >
            x
          </button>
        </div>
      ) : null}
      {answerPasteError ? (
        <div className="study-panel__paste-error" role="alert">{answerPasteError}</div>
      ) : null}
      <div className="study-panel__actions">
        <button type="button" disabled={busy} onClick={busy ? undefined : evaluateAnswer}>
          {busy ? t('ui.button.loading', 'Loading...') : t('ui.button.evaluate_answer', 'Evaluate Answer')}
        </button>
        <button type="button" disabled={busy} onClick={busy ? undefined : summarizeSession}>
          {busy ? t('ui.button.loading', 'Loading...') : t('ui.button.summarize_session', 'Summarize Session')}
        </button>
      </div>
      <div className="study-panel__reply-label">{t('ui.label.reply', 'Reply')}</div>
      <pre>{reply}</pre>
    </div>
  );
}
