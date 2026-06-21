import { useEffect, useRef, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin as callHostedPlugin, ensureBrandCSS } from './study_surface_utils';

type StudyStatus = {
  status?: string;
  active_mode?: string;
  mode?: string;
  last_ocr_text?: string;
  last_error?: string;
  screen_classification?: {
    screen_type?: string;
    confidence?: number;
    reason?: string;
  };
  last_answer_evaluation?: {
    verdict?: string;
    score?: number;
    feedback?: string;
    next_action?: string;
  };
  last_session_summary?: string;
  config?: {
    llm_vision_max_image_px?: number;
  };
};

type StudyMode = 'companion' | 'interactive' | 'teaching';

type QuestionContext = {
  selection_context_id?: string;
  selected_topic_id?: string;
  selected_topic_name?: string;
  selection_reason?: string;
  no_data?: boolean;
};

type GeneratedQuestion = {
  question?: string;
  hint?: string;
  difficulty?: number;
  question_id?: string;
  attempt_id?: string;
  selected_topic_id?: string;
  selected_topic_name?: string;
};

const ENTRY_TIMEOUT_MS: Record<string, number> = {
  study_status: 15000,
  study_ocr_snapshot: 60000,
  study_set_mode: 15000,
  study_explain_text: 310000,
  study_generate_question: 310000,
  study_question_context: 30000,
  study_generate_targeted_question: 310000,
  study_evaluate_answer: 310000,
  study_summarize_session: 90000,
};

const MODE_ORDER: Array<{ id: StudyMode; labelKey: string; fallback: string }> = [
  { id: 'companion', labelKey: 'status.mode.companion', fallback: 'Companion' },
  { id: 'interactive', labelKey: 'status.mode.interactive', fallback: 'Interactive' },
  { id: 'teaching', labelKey: 'status.mode.teaching', fallback: 'Teaching' },
];
const KATEX_ASSET_VERSION = 'study-hotfix-20260615v';
const KATEX_CSS_URL = `/plugin/study_companion/ui/katex.min.css?v=${KATEX_ASSET_VERSION}`;
const KATEX_SCRIPT_URL = `/plugin/study_companion/ui/katex.min.js?v=${KATEX_ASSET_VERSION}`;
const KATEX_RENDER_SCRIPT_URL = `/plugin/study_companion/ui/katex-render.js?v=${KATEX_ASSET_VERSION}`;
let katexLoadPromise: Promise<void> | null = null;

type MathTextPart = {
  type: 'text' | 'math';
  value: string;
  display?: boolean;
};

type StudyReplySectionVariant = 'analysis' | 'process' | 'answer' | 'transfer';

type StudyReplyBlock =
  | { type: 'text'; value: string }
  | { type: 'section'; variant: StudyReplySectionVariant; title: string; value: string };

const STUDY_REPLY_SECTION_CLASS_BY_VARIANT: Record<StudyReplySectionVariant, string> = {
  analysis: 'study-reply-section--analysis',
  process: 'study-reply-section--process',
  answer: 'study-reply-section--answer',
  transfer: 'study-reply-section--transfer',
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
      if (existing.getAttribute('src') !== src) {
        existing.remove();
      } else if (existing.dataset.studyKatexLoaded === 'true') {
        resolve();
        return;
      } else if (existing.dataset.studyKatexFailed === 'true') {
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
    const existingCss = document.getElementById('study-companion-katex-css') as HTMLLinkElement | null;
    if (existingCss && existingCss.getAttribute('href') !== KATEX_CSS_URL) {
      existingCss.href = KATEX_CSS_URL;
    }
    if (!existingCss) {
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

function studyReplySectionMeta(value: string): { variant: StudyReplySectionVariant; title: string } | null {
  const normalized = String(value || '')
    .replace(/^#{1,4}\s+/, '')
    .replace(/^\*\*(.+?)\*\*$/, '$1')
    .replace(/[：:]\s*$/, '')
    .trim()
    .toLowerCase();
  const variants: Record<string, { variant: StudyReplySectionVariant; title: string }> = {
    解析: { variant: 'analysis', title: '解析' },
    题目解析: { variant: 'analysis', title: '题目解析' },
    題目解析: { variant: 'analysis', title: '題目解析' },
    'problem analysis': { variant: 'analysis', title: 'Problem Analysis' },
    解题过程: { variant: 'process', title: '解题过程' },
    解題過程: { variant: 'process', title: '解題過程' },
    'solution process': { variant: 'process', title: 'Solution Process' },
    答案: { variant: 'answer', title: '答案' },
    'final answer': { variant: 'answer', title: 'Final Answer' },
    举一反三: { variant: 'transfer', title: '举一反三' },
    舉一反三: { variant: 'transfer', title: '舉一反三' },
    'transfer practice': { variant: 'transfer', title: 'Transfer Practice' },
  };
  return variants[normalized] || null;
}

function buildStudyReplyBlocks(text: string): StudyReplyBlock[] {
  const lines = String(text || '').split(/\r?\n/);
  const blocks: StudyReplyBlock[] = [];
  let textLines: string[] = [];
  let section: Extract<StudyReplyBlock, { type: 'section' }> | null = null;
  const flushText = () => {
    if (textLines.length > 0) {
      blocks.push({ type: 'text', value: textLines.join('\n') });
      textLines = [];
    }
  };
  const flushSection = () => {
    if (section) {
      blocks.push(section);
      section = null;
    }
  };
  for (const line of lines) {
    const meta = studyReplySectionMeta(line.trim());
    if (meta) {
      flushText();
      flushSection();
      section = { type: 'section', variant: meta.variant, title: meta.title, value: '' };
      continue;
    }
    if (section) {
      section.value = section.value ? `${section.value}\n${line}` : line;
    } else {
      textLines.push(line);
    }
  }
  flushText();
  flushSection();
  return blocks.length > 0 ? blocks : [{ type: 'text', value: text }];
}

function MathReply({ text, label }: { text: string; label: string }) {
  const containerRef = useRef<HTMLElement | null>(null);
  const [mathReady, setMathReady] = useState(() => Boolean(getStudyMathTools()));
  const [mathRenderTick, setMathRenderTick] = useState(0);
  useEffect(() => {
    let active = true;
    ensureHostedKatex().then(() => {
      if (active) {
        const ready = Boolean(getStudyMathTools());
        setMathReady(ready);
        if (ready && hasHostedKatex()) {
          setMathRenderTick((tick) => tick + 1);
        }
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
  }, [mathReady, mathRenderTick, text]);
  const mathTools = mathReady ? getStudyMathTools() : null;
  const parts: MathTextPart[] = mathTools ? mathTools.splitByMath(text) : [{ type: 'text', value: text }];
  const renderParts = (items: MathTextPart[], keyPrefix: string) => items.map((part, index) => {
    if (part.type === 'math') {
      const wrapper = part.display ? '$$' : '$';
      return (
        <span
          key={`${keyPrefix}-math-${index}`}
          data-study-math="true"
          data-display={part.display ? 'true' : 'false'}
          data-math={part.value}
        >
          {wrapper}{part.value}{wrapper}
        </span>
      );
    }
    return <span key={`${keyPrefix}-text-${index}`}>{part.value}</span>;
  });
  const blocks = buildStudyReplyBlocks(text);
  const hasStudySections = blocks.some((block) => block.type === 'section');
  return (
    <div
      ref={containerRef}
      className="study-panel__math-reply"
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      {hasStudySections
        ? blocks.map((block, index) => {
          if (block.type === 'section') {
            const sectionParts = mathTools ? mathTools.splitByMath(block.value) : [{ type: 'text' as const, value: block.value }];
            return (
              <section
                key={`section-${index}`}
                className={`study-reply-section ${STUDY_REPLY_SECTION_CLASS_BY_VARIANT[block.variant]}`}
              >
                <h3 className="study-reply-section__title">{block.title}</h3>
                <div className="study-reply-section__body">
                  {renderParts(sectionParts, `section-${index}`)}
                </div>
              </section>
            );
          }
          const textParts = mathTools ? mathTools.splitByMath(block.value) : [{ type: 'text' as const, value: block.value }];
          return <span key={`text-block-${index}`}>{renderParts(textParts, `text-block-${index}`)}</span>;
        })
        : renderParts(parts, 'reply')}
    </div>
  );
}

function timeoutForEntry(entryId: string) {
  return ENTRY_TIMEOUT_MS[entryId] || 60000;
}

const DEFAULT_VISION_MAX_IMAGE_PX = 768;
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

function normalizeVisionMaxImagePx(value: unknown) {
  const parsed = Math.round(Number(value));
  if (!Number.isFinite(parsed)) {
    return DEFAULT_VISION_MAX_IMAGE_PX;
  }
  return Math.max(64, Math.min(4096, parsed));
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

async function compressImageForStudy(
  blob: Blob,
  signal?: AbortSignal,
  maxImagePx = DEFAULT_VISION_MAX_IMAGE_PX,
): Promise<string | null> {
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
    const maxLongSide = normalizeVisionMaxImagePx(maxImagePx);
    const longSide = Math.max(width, height);
    if (longSide > maxLongSide) {
      const scale = maxLongSide / longSide;
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
      width = Math.max(1, Math.min(maxLongSide, Math.round(width * scale)));
      height = Math.max(1, Math.min(maxLongSide, Math.round(height * scale)));
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
  setPastePending?: (value: boolean) => void;
  onImageAccepted?: () => void;
  getMaxImagePx?: () => number;
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
    setters.setPastePending?.(true);

    try {
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
            const image = await compressImageForStudy(
              blob,
              signal,
              setters.getMaxImagePx?.() ?? DEFAULT_VISION_MAX_IMAGE_PX,
            );
            if (signal.aborted || !isMounted()) {
              return;
            }
            if (image === null) {
              setters.setPasteError(setters.pasteErrorMessage);
            } else {
              setters.onImageAccepted?.();
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
    } finally {
      if (!signal.aborted && isMounted()) {
        setters.setPastePending?.(false);
      }
    }
  };
}

function callStudyPlugin<T = Record<string, unknown>>(
  api: PluginSurfaceProps['api'],
  entryId: string,
  args: Record<string, unknown> = {},
  signal?: AbortSignal,
) {
  return callHostedPlugin<T>(api, entryId, args, { signal, timeoutMs: timeoutForEntry(entryId) });
}

export default function StudyPanel(props: PluginSurfaceProps) {
  const t = (key: string, defaultValue?: string) => {
    const translated = props.t?.(key);
    return translated && translated !== key ? translated : defaultValue || key;
  };
  const [status, setStatus] = useState<StudyStatus>({});
  const [text, setText] = useState('');
  const [question, setQuestion] = useState('');
  const [questionContext, setQuestionContext] = useState<QuestionContext | null>(null);
  const [currentQuestion, setCurrentQuestion] = useState<GeneratedQuestion | null>(null);
  const [answer, setAnswer] = useState('');
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const [pastePending, setPastePending] = useState(false);
  const [textImage, setTextImage] = useState('');
  const [answerImage, setAnswerImage] = useState('');
  const [textPasteError, setTextPasteError] = useState('');
  const [answerPasteError, setAnswerPasteError] = useState('');
  const explainControllerRef = useRef<AbortController | null>(null);
  const pasteControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const textImageRef = useRef('');
  const pastePendingRef = useRef(false);
  const visionMaxImagePxRef = useRef(DEFAULT_VISION_MAX_IMAGE_PX);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const replySectionRef = useRef<HTMLDivElement | null>(null);
  const currentMode = String(status.active_mode || status.mode || 'companion');
  const interactionBusy = busy || pastePending;

  useEffect(() => {
    ensureBrandCSS();
  }, []);

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

  function setPastePendingState(value: boolean) {
    pastePendingRef.current = value;
    setPastePending(value);
  }

  function isInteractionBusy() {
    return busy || pastePendingRef.current;
  }

  function scrollReplyIntoView() {
    replySectionRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
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
    const evaluation = data.last_answer_evaluation && typeof data.last_answer_evaluation === 'object'
      ? data.last_answer_evaluation as Record<string, unknown>
      : undefined;
    const config = data.config && typeof data.config === 'object'
      ? data.config as Record<string, unknown>
      : undefined;
    return {
      status: typeof data.status === 'string' ? data.status : undefined,
      active_mode: typeof data.active_mode === 'string' ? data.active_mode : undefined,
      mode: typeof data.mode === 'string' ? data.mode : undefined,
      last_ocr_text: typeof data.last_ocr_text === 'string' ? data.last_ocr_text : undefined,
      last_error: typeof data.last_error === 'string' ? data.last_error : undefined,
      screen_classification: screen ? {
        screen_type: typeof screen.screen_type === 'string' ? screen.screen_type : undefined,
        confidence: typeof screen.confidence === 'number' ? screen.confidence : undefined,
        reason: typeof screen.reason === 'string' ? screen.reason : undefined,
      } : undefined,
      last_answer_evaluation: evaluation ? {
        verdict: typeof evaluation.verdict === 'string' ? evaluation.verdict : undefined,
        score: typeof evaluation.score === 'number' ? evaluation.score : undefined,
        feedback: typeof evaluation.feedback === 'string' ? evaluation.feedback : undefined,
        next_action: typeof evaluation.next_action === 'string' ? evaluation.next_action : undefined,
      } : undefined,
      last_session_summary: typeof data.last_session_summary === 'string' ? data.last_session_summary : undefined,
      config: config ? {
        llm_vision_max_image_px: typeof config.llm_vision_max_image_px === 'number'
          ? config.llm_vision_max_image_px
          : undefined,
      } : undefined,
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
  }

  function setTextImageValue(value: string) {
    textImageRef.current = value;
    setTextImage(value);
  }

  function getVisionMaxImagePx() {
    return visionMaxImagePxRef.current;
  }

  async function refresh(signal?: AbortSignal, _options: { updateReply?: boolean } = {}) {
    const data = normalizeStudyStatus(await callStudyPlugin(props.api, 'study_status', {}, signal));
    if (signal?.aborted) {
      return;
    }
    visionMaxImagePxRef.current = normalizeVisionMaxImagePx(
      data.config?.llm_vision_max_image_px,
    );
    setStatusLine(data);
  }

  async function loadQuestionContext(signal?: AbortSignal) {
    const data = await callStudyPlugin<QuestionContext>(props.api, 'study_question_context', {}, signal);
    if (!signal?.aborted) {
      setQuestionContext(data);
    }
    return data;
  }

  async function setMode(mode: StudyMode) {
    if (isInteractionBusy() || mode === currentMode) {
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      setReply('');
      const data = await callStudyPlugin(props.api, 'study_set_mode', { mode, reason: 'ui' }, controller.signal) as {
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
    if (isInteractionBusy()) {
      return;
    }
    const sourceText = text.trim();
    if (!sourceText && !textImage) {
      setReply(t('ui.error.missing_study_input', 'Please enter text or paste an image first.'));
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    const explainArgs: Record<string, unknown> = { text: sourceText };
    if (textImage) explainArgs.vision_image_base64 = textImage;
    let shouldClearTextImage = false;
    try {
      setStatus((prev) => ({
        ...prev,
        status: textImage ? 'solving_problem' : 'explaining',
      }));
      setReply(textImage ? t('ui.status.solving_problem', 'Solving problem...') : t('ui.status.explaining', 'Explaining...'));
      scrollReplyIntoView();
      const data = await callStudyPlugin(props.api, 'study_explain_text', explainArgs, controller.signal) as {
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
          setTextImageValue('');
          setTextPasteError('');
        }
        setBusy(false);
      }
      endStudyRequest(controller);
    }
  }

  async function generateQuestion() {
    if (isInteractionBusy()) {
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      const context = questionContext?.selection_context_id
        ? questionContext
        : await loadQuestionContext(controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      if (!context?.selection_context_id || context.no_data) {
        setReply(t('ui.error.no_targeted_question_data', 'Not enough study records to generate a practice question yet.'));
        return;
      }
      const data = await callStudyPlugin<GeneratedQuestion & { summary?: string; reply?: string }>(
        props.api,
        'study_generate_targeted_question',
        { selection_context_id: context.selection_context_id },
        controller.signal,
      );
      if (controller.signal.aborted) {
        return;
      }
      setQuestion(data.question || '');
      setCurrentQuestion(data);
      setQuestionContext({ ...context, ...data, no_data: false, selection_context_id: '' });
      setAnswer('');
      setAnswerImage('');
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
    if (isInteractionBusy()) {
      return;
    }
    if (!answer.trim() && !answerImage) {
      setReply(t('ui.error.missing_answer', 'Please enter an answer first.'));
      return;
    }
    if (!currentQuestion?.question_id || !currentQuestion?.attempt_id) {
      setReply(t('ui.error.question_missing', 'Please generate a practice question first.'));
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    const evalArgs: Record<string, unknown> = {
      answer,
      question_id: currentQuestion.question_id,
      attempt_id: currentQuestion.attempt_id,
      selected_topic_id: currentQuestion.selected_topic_id || '',
    };
    if (answerImage) evalArgs.vision_image_base64 = answerImage;
    let shouldClearAnswerImage = false;
    try {
      const data = await callStudyPlugin(props.api, 'study_evaluate_answer', evalArgs, controller.signal) as {
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
    if (isInteractionBusy()) {
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      const data = await callStudyPlugin(props.api, 'study_summarize_session', {}, controller.signal) as {
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
    refresh(controller.signal)
      .then(() => loadQuestionContext(controller.signal))
      .catch((error) => {
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
  const explainLabel = interactionBusy ? t('ui.button.loading', 'Loading...') : t('ui.button.explain', 'Explain');
  const screenType = status.screen_classification?.screen_type || 'idle';
  const evaluation = status.last_answer_evaluation;
  const handleTextPaste = createPasteHandler(
    {
      setImage: setTextImageValue,
      setTextValue: setText,
      setPasteError: setTextPasteError,
      setPastePending: setPastePendingState,
      getMaxImagePx: getVisionMaxImagePx,
      pasteErrorMessage: t('ui.error.image_paste_failed', 'Image paste failed. Please try a smaller JPEG or PNG image.'),
      unsupportedTypeMessage: t('ui.error.image_paste_unsupported', 'Only JPEG and PNG images can be pasted here.'),
    },
    () => isInteractionBusy(),
    () => mountedRef.current,
    beginPasteSignal,
  );
  const handleAnswerPaste = createPasteHandler(
    {
      setImage: setAnswerImage,
      setTextValue: setAnswer,
      setPasteError: setAnswerPasteError,
      setPastePending: setPastePendingState,
      getMaxImagePx: getVisionMaxImagePx,
      pasteErrorMessage: t('ui.error.image_paste_failed', 'Image paste failed. Please try a smaller JPEG or PNG image.'),
      unsupportedTypeMessage: t('ui.error.image_paste_unsupported', 'Only JPEG and PNG images can be pasted here.'),
    },
    () => isInteractionBusy(),
    () => mountedRef.current,
    beginPasteSignal,
  );

  return (
    <div
      ref={panelRef}
      className="study-panel surface-shell"
      role="region"
      aria-label={t('ui.surface.study_panel', 'Study Panel')}
      data-busy={interactionBusy ? "true" : "false"}
    >
      <header className="study-panel__header">
        <div>
          <h1>{t('ui.title', 'Study Companion')}</h1>
          <span>{stateLabel} / {modeLabel(currentMode)}</span>
        </div>
        <div
          className="mode-switch study-panel__modes"
          role="group"
          aria-label={t('ui.label.mode', 'Mode')}
          data-active={currentMode}
        >
          {MODE_ORDER.map((item) => {
            const pressed = currentMode === item.id;
            return (
              <button
                key={item.id}
                type="button"
                className={pressed ? 'mode-btn active is-active' : 'mode-btn'}
                aria-pressed={pressed}
                data-mode={item.id}
                disabled={interactionBusy}
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
          <strong>{compactText(question)}</strong>
        </div>
        <div>
          <span>{t('ui.label.answer', 'Answer')}</span>
          <strong>{evaluation?.verdict ? `${evaluation.verdict}${evaluation.score !== undefined ? ` / ${evaluation.score}` : ''}` : '-'}</strong>
        </div>
      </section>
      <section className="study-panel__state">
        <div>
          <span>{t('ui.practice.context_label', 'Selection')}</span>
          <strong>{questionContext?.selected_topic_name || questionContext?.selected_topic_id || t('ui.practice.no_data_title', 'Not enough data')}</strong>
        </div>
        <div>
          <span>{t('ui.practice.reason_label', 'Reason')}</span>
          <strong>{questionContext?.selection_reason || '-'}</strong>
        </div>
      </section>
      <textarea
        aria-label={t('ui.label.text', 'Text')}
        placeholder={t('ui.placeholder.input', 'Paste a concept, problem statement, or OCR text here.')}
        value={text}
        readOnly={interactionBusy}
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
            disabled={interactionBusy}
            onClick={() => {
              setTextImageValue('');
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
          disabled={interactionBusy}
          onClick={interactionBusy ? undefined : generateQuestion}
        >
          {interactionBusy ? t('ui.button.loading', 'Loading...') : t('ui.button.generate_question', 'Generate Question')}
        </button>
      </div>
      <button
        type="button"
        className={interactionBusy ? 'loading' : ''}
        disabled={interactionBusy}
        aria-busy={interactionBusy}
        aria-label={explainLabel}
        onClick={interactionBusy ? undefined : explain}
      >
        {explainLabel}
      </button>
      <div className="study-panel__reply-label">{t('ui.label.question', 'Question')}</div>
      <pre>{question}</pre>
      <textarea
        aria-label={t('ui.label.answer', 'Answer')}
        value={answer}
        readOnly={interactionBusy}
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
            disabled={interactionBusy}
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
        <button type="button" disabled={interactionBusy} onClick={interactionBusy ? undefined : evaluateAnswer}>
          {interactionBusy ? t('ui.button.loading', 'Loading...') : t('ui.button.evaluate_answer', 'Evaluate Answer')}
        </button>
        <button type="button" disabled={interactionBusy} onClick={interactionBusy ? undefined : summarizeSession}>
          {interactionBusy ? t('ui.button.loading', 'Loading...') : t('ui.button.summarize_session', 'Summarize Session')}
        </button>
      </div>
      <div ref={replySectionRef}>
        <div className="study-panel__reply-label">{t('ui.label.reply', 'Reply')}</div>
        <MathReply text={reply} label={t('ui.label.reply', 'Reply')} />
      </div>
    </div>
  );
}
