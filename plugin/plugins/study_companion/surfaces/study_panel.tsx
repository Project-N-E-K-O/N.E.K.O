import { useEffect, useRef, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin as callHostedPlugin, ensureBrandCSS } from './study_surface_utils';
import {
  estimateDocumentChunkCount,
  estimatedDocumentAnalysisMode,
  assertParsedStudyDocumentFile,
  isParsedStudyDocumentFile,
  metadataForEditedDocument,
  oneStudyDocument,
  parsedStudyDocument,
  readStudyDocument,
  STUDY_DOCUMENT_ANALYSIS_KINDS,
  STUDY_DOCUMENT_DIRECT_MAX_ESTIMATED_TOKENS,
  STUDY_DOCUMENT_MAX_BYTES,
  STUDY_DOCUMENT_MAX_ESTIMATED_TOKENS,
  STUDY_DOCUMENT_PARSE_TIMEOUT_MS,
  StudyDocumentError,
  type StudyDocument,
  type StudyDocumentAnalysisKind,
} from './study_document_utils';

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

type StudyModelRuntime = {
  group?: string;
  model?: string;
  provider_type?: string;
  configured?: boolean;
  credential_configured?: boolean;
  transport_supported?: boolean;
  vision_capability?: string;
};

type StudyMode = 'companion' | 'interactive' | 'teaching';

type PracticeScope = {
  schema_version?: number;
  mode?: 'explicit_scope' | 'explicit_topic';
  stage?: string;
  subject?: string;
  course_family?: string;
  chapter?: string;
  unit?: string;
  topic_id?: string;
  scope_key?: string;
  scope_revision?: number;
  display_path?: string[];
};

type QuestionContext = {
  selection_context_id?: string;
  selected_topic_id?: string;
  selected_topic_name?: string;
  selection_reason?: string;
  scope_key?: string;
  scope_revision?: number;
  practice_scope?: PracticeScope;
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

type DocumentJobState = {
  jobId: string;
  status: string;
  stage: string;
  analysisMode: string;
  completedChunks: number;
  totalChunks: number;
  progress: number;
};

type DocumentJobMetadata = {
  name?: string;
  type?: string;
  chars?: number;
  tokens?: number;
  truncated?: boolean;
};

type DocumentJobPayload = {
  job_id?: string;
  status?: string;
  stage?: string;
  analysis_mode?: string;
  completed_chunks?: number;
  total_chunks?: number;
  chunks?: number;
  progress?: number;
  reply?: string;
  summary?: string;
  degraded?: boolean;
  diagnostic?: string;
  document?: DocumentJobMetadata;
};

const DOCUMENT_JOB_STORAGE_KEY = 'study_companion.document_analysis_job_id';
const PENDING_DOCUMENT_JOB_ID = '__pending__';
const PENDING_DOCUMENT_JOB_PREFIX = `${PENDING_DOCUMENT_JOB_ID}:`;

function createDocumentStartToken() {
  if (typeof window.crypto?.randomUUID === 'function') return window.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function isPendingDocumentJobId(jobId: string) {
  return jobId === PENDING_DOCUMENT_JOB_ID || jobId.startsWith(PENDING_DOCUMENT_JOB_PREFIX);
}

function pendingDocumentStartToken(jobId: string) {
  return jobId.startsWith(PENDING_DOCUMENT_JOB_PREFIX)
    ? jobId.slice(PENDING_DOCUMENT_JOB_PREFIX.length)
    : '';
}

type SolutionNarrationOutcome = {
  diagnostic?: string;
  solution_narration_scheduled?: boolean;
  solution_narration_status?: string;
  solution_narration_reason?: string;
  solution_repair_attempted?: boolean;
  solution_narration_missing_sections?: string[];
};

type GeneralNarrationOutcome = {
  general_narration_scheduled?: boolean;
  general_narration_status?: string;
  general_narration_reason?: string;
  general_narration_response_mode?: string;
};

type HistoryPersistenceOutcome = {
  history_persisted?: boolean;
};

type KnowledgeGuidanceTopic = {
  id?: string;
  label?: string;
  name?: string;
};

type KnowledgeGuidanceOutcome = {
  knowledge_guidance_applied?: boolean;
  knowledge_guidance_status?: string;
  knowledge_guidance_subject?: string;
  knowledge_guidance_content_type?: string;
  knowledge_guidance_entity?: string;
  knowledge_guidance_focus_topic?: KnowledgeGuidanceTopic;
  knowledge_guidance_related_topics?: KnowledgeGuidanceTopic[];
  knowledge_guidance_source?: string;
};

type StudyTranslate = (key: string, defaultValue?: string) => string;

function formatSolutionNarrationNotice(
  outcome: SolutionNarrationOutcome,
  translate: StudyTranslate,
) {
  const status = String(outcome.solution_narration_status || '').trim().toLowerCase();
  const reason = String(outcome.solution_narration_reason || '').trim().toLowerCase();
  const diagnostic = String(outcome.diagnostic || '').trim().toLowerCase();
  const missingSections = Array.isArray(outcome.solution_narration_missing_sections)
    ? outcome.solution_narration_missing_sections.map((section) => String(section).trim().toLowerCase())
    : [];
  const hasOutcome = typeof outcome.solution_narration_scheduled === 'boolean'
    || Boolean(status)
    || Boolean(reason)
    || missingSections.length > 0
    || typeof outcome.solution_repair_attempted === 'boolean';
  if (!hasOutcome) return '';

  if (status === 'not_applicable') return '';
  if (outcome.solution_narration_scheduled === true || status === 'scheduled') {
    return translate('ui.status.solution_narration_scheduled', 'Narration has been scheduled.');
  }
  if (status === 'disabled') {
    return translate('ui.status.solution_narration_disabled', 'Solution narration is turned off.');
  }
  if (status === 'degraded') {
    return translate(
      'ui.error.solution_narration_degraded',
      'The explanation used a fallback response, so narration was not scheduled.',
    );
  }
  if (diagnostic === 'output_truncated' && reason === 'insufficient_time_budget') {
    return translate(
      'ui.error.solution_narration_truncated_repair_timeout',
      'The solution was truncated after reaching the output length limit. Automatic completion could not finish within the time limit, so narration was not scheduled. Please regenerate a concise solution.',
    );
  }
  if (
    status === 'repair_failed'
    || reason === 'invalid_repair_response'
    || (
      !status
      && outcome.solution_repair_attempted === true
      && outcome.solution_narration_scheduled === false
    )
  ) {
    return translate(
      'ui.error.solution_narration_repair_failed',
      'The explanation structure could not be repaired, so narration was not scheduled. Please analyze it again.',
    );
  }
  if (status === 'incomplete' || reason.startsWith('missing_')) {
    if (reason === 'missing_answer' || missingSections.includes('answer')) {
      return translate(
        'ui.error.solution_narration_missing_answer',
        'The explanation is incomplete: the Answer section is missing, so narration was not scheduled. Please analyze it again.',
      );
    }
    return translate(
      'ui.error.solution_narration_incomplete',
      'The explanation is incomplete, so narration was not scheduled. Please analyze it again.',
    );
  }
  if (status === 'runtime_unavailable' || reason === 'event_bus_unavailable') {
    return translate(
      'ui.error.solution_narration_runtime_unavailable',
      'Narration is temporarily unavailable. The explanation is still shown.',
    );
  }
  if (status === 'delivery_failed' || reason === 'event_delivery_failed') {
    return translate(
      'ui.error.solution_narration_delivery_failed',
      'The narration request could not be delivered. Please try again.',
    );
  }
  if (outcome.solution_narration_scheduled === false) {
    return translate(
      'ui.error.solution_narration_not_scheduled',
      'Narration was not scheduled for this explanation.',
    );
  }
  return '';
}

function formatGeneralNarrationNotice(
  outcome: GeneralNarrationOutcome,
  translate: StudyTranslate,
) {
  const status = String(outcome.general_narration_status || '').trim().toLowerCase();
  const reason = String(outcome.general_narration_reason || '').trim().toLowerCase();
  const hasOutcome = typeof outcome.general_narration_scheduled === 'boolean'
    || Boolean(status)
    || Boolean(reason)
    || Boolean(String(outcome.general_narration_response_mode || '').trim());
  if (!hasOutcome || status === 'not_applicable') return '';

  if (status) {
    if (status === 'scheduled') {
      return translate('ui.status.general_narration_scheduled', 'General narration has been scheduled.');
    }
    if (status === 'disabled') {
      return translate('ui.status.general_narration_disabled', 'General narration is turned off.');
    }
    if (status === 'degraded') {
      return translate(
        'ui.error.general_narration_degraded',
        'The response used a fallback or had no narratable content, so general narration was not scheduled.',
      );
    }
    if (status === 'runtime_unavailable') {
      return translate(
        'ui.error.general_narration_runtime_unavailable',
        'General narration is temporarily unavailable. The response is still shown.',
      );
    }
    if (status === 'delivery_failed') {
      return translate(
        'ui.error.general_narration_delivery_failed',
        'The general narration request could not be delivered. Please try again.',
      );
    }
    return '';
  }

  if (reason === 'communication_disabled' || reason === 'general_narration_disabled') {
    return translate('ui.status.general_narration_disabled', 'General narration is turned off.');
  }
  if (reason === 'degraded_reply' || reason === 'empty_reply') {
    return translate(
      'ui.error.general_narration_degraded',
      'The response used a fallback or had no narratable content, so general narration was not scheduled.',
    );
  }
  if (reason === 'event_bus_unavailable') {
    return translate(
      'ui.error.general_narration_runtime_unavailable',
      'General narration is temporarily unavailable. The response is still shown.',
    );
  }
  if (reason === 'event_delivery_failed') {
    return translate(
      'ui.error.general_narration_delivery_failed',
      'The general narration request could not be delivered. Please try again.',
    );
  }
  if (outcome.general_narration_scheduled === true) {
    return translate('ui.status.general_narration_scheduled', 'General narration has been scheduled.');
  }
  return '';
}

function formatKnowledgeGuidanceEvidence(
  outcome: KnowledgeGuidanceOutcome,
  translate: StudyTranslate,
) {
  const status = String(outcome.knowledge_guidance_status || '').trim().toLowerCase();
  const hasOutcome = typeof outcome.knowledge_guidance_applied === 'boolean' || Boolean(status);
  if (!hasOutcome || status === 'not_applicable') return '';
  if (status === 'not_matched') {
    return translate(
      'ui.knowledge_guidance.not_matched',
      'No trustworthy related knowledge graph was matched; no nodes from other subjects were used.',
    );
  }
  if (status === 'low_confidence') {
    return translate(
      'ui.knowledge_guidance.low_confidence',
      'The knowledge graph match was uncertain, so it was not applied.',
    );
  }
  if (status === 'routing_unavailable') {
    return translate(
      'ui.knowledge_guidance.routing_unavailable',
      'Knowledge graph routing was not applicable, so the answer continued without graph guidance.',
    );
  }
  if (outcome.knowledge_guidance_applied !== true && status !== 'applied') return '';

  const focusTopic = outcome.knowledge_guidance_focus_topic;
  const focusLabel = String(focusTopic?.label || focusTopic?.name || '').trim();
  if (!focusLabel) {
    return translate(
      'ui.knowledge_guidance.not_matched',
      'No trustworthy related knowledge graph was matched; no nodes from other subjects were used.',
    );
  }
  const relatedLabels = Array.isArray(outcome.knowledge_guidance_related_topics)
    ? outcome.knowledge_guidance_related_topics
      .map((topic) => String(topic?.label || topic?.name || '').trim())
      .filter(Boolean)
    : [];
  const localizedValue = (group: 'subject' | 'content_type' | 'source', rawValue?: string) => {
    const value = String(rawValue || '').trim().toLowerCase();
    if (!value) return '';
    return translate(`ui.knowledge_guidance.${group}.${value}`, value.replace(/_/g, ' '));
  };
  const subject = localizedValue('subject', outcome.knowledge_guidance_subject);
  const contentType = localizedValue('content_type', outcome.knowledge_guidance_content_type);
  const entity = String(outcome.knowledge_guidance_entity || '').trim();
  const source = localizedValue('source', outcome.knowledge_guidance_source);
  return [
    translate('ui.knowledge_guidance.applied', 'Knowledge graph applied'),
    subject ? `${translate('ui.knowledge_guidance.subject', 'Subject')}: ${subject}` : '',
    contentType ? `${translate('ui.knowledge_guidance.content_type', 'Content type')}: ${contentType}` : '',
    entity ? `${translate('ui.knowledge_guidance.entity', 'Entity')}: ${entity}` : '',
    `${translate('ui.knowledge_guidance.focus_topic', 'Focus topic')}: ${focusLabel}`,
    relatedLabels.length > 0
      ? `${translate('ui.knowledge_guidance.related_topics', 'Related topics')}: ${relatedLabels.join(', ')}`
      : '',
    source ? `${translate('ui.knowledge_guidance.source', 'Source')}: ${source}` : '',
  ].filter(Boolean).join('\n');
}

const ENTRY_TIMEOUT_MS: Record<string, number> = {
  study_status: 15000,
  study_get_settings_config: 15000,
  study_ocr_snapshot: 60000,
  study_set_mode: 15000,
  study_explain_text: 120000,
  study_start_document_analysis: 30000,
  study_document_analysis_status: 15000,
  study_cancel_document_analysis: 15000,
  study_generate_question: 75000,
  study_question_context: 30000,
  study_generate_targeted_question: 60000,
  study_evaluate_answer: 75000,
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
  locale: PluginSurfaceProps['locale'],
  args: Record<string, unknown> = {},
  signal?: AbortSignal,
) {
  return callHostedPlugin<T>(
    api,
    entryId,
    { ...args, locale: String(locale || '').trim() },
    { signal, timeoutMs: timeoutForEntry(entryId) },
  );
}

function openHostedExternalUrl(url: string): void {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage(
      { type: 'neko-hosted-surface-open-external', payload: { url } },
      '*',
    );
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

export default function StudyPanel(props: PluginSurfaceProps) {
  const documentKeyAliases: Record<string, string> = {
    'ui.document.import': 'ui.button.import_document',
    'ui.document.analyze': 'ui.button.analyze_document',
    'ui.document.remove': 'ui.button.remove_document',
    'ui.document.cancel_reading': 'ui.button.remove_document',
    'ui.document.drop_now': 'ui.document.drop_hint',
    'ui.document.card_label': 'ui.document.ready',
    'ui.document.modified': 'ui.document.ready_modified',
    'ui.document.estimate_warning': 'ui.document.token_estimate_warning',
    'ui.document.instruction': 'ui.document.instruction_label',
    'ui.document.status.analyzing': 'ui.status.analyzing_document',
    'ui.document.error.multiple_files': 'ui.error.document_multiple',
    'ui.document.error.unsupported_type': 'ui.error.document_type',
    'ui.document.error.file_too_large': 'ui.error.document_too_large',
    'ui.document.error.empty': 'ui.error.document_empty',
    'ui.document.error.binary': 'ui.error.document_binary',
    'ui.document.error.encoding': 'ui.error.document_encoding',
    'ui.document.error.too_long': 'ui.error.document_too_long',
    'ui.document.error.unsafe_content': 'ui.error.document_unsafe_content',
    'ui.document.error.unsupported_kind': 'ui.error.document_invalid_kind',
    'ui.document.error.unsafe_model_output': 'ui.error.document_analysis_unsafe_model_output',
    'ui.document.error.read_failed': 'ui.error.document_read',
    'ui.document.error.timeout': 'ui.error.document_analysis_timeout',
    'ui.document.error.instruction_too_long': 'ui.error.document_instruction_too_long',
  };
  const t = (key: string, defaultValue?: string) => {
    const effectiveKey = documentKeyAliases[key] || key;
    const translated = props.t?.(effectiveKey);
    return translated && translated !== effectiveKey ? translated : defaultValue || key;
  };
  const tf = (key: string, fallback: string, values: Record<string, string>) => (
    Object.entries(values).reduce(
      (message, [name, value]) => message.replace(`{${name}}`, value),
      t(key, fallback),
    )
  );
  const [status, setStatus] = useState<StudyStatus>({});
  const [modelRuntime, setModelRuntime] = useState<Record<string, StudyModelRuntime>>({});
  const [modelRuntimeLoading, setModelRuntimeLoading] = useState(false);
  const [text, setText] = useState('');
  const [question, setQuestion] = useState('');
  const [questionContext, setQuestionContext] = useState<QuestionContext | null>(null);
  const [activePracticeScope, setActivePracticeScope] = useState<PracticeScope | null>(null);
  const [practiceScopeCompleted, setPracticeScopeCompleted] = useState(false);
  const [currentQuestion, setCurrentQuestion] = useState<GeneratedQuestion | null>(null);
  const [answer, setAnswer] = useState('');
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const [pastePending, setPastePending] = useState(false);
  const [textImage, setTextImage] = useState('');
  const [answerImage, setAnswerImage] = useState('');
  const [textPasteError, setTextPasteError] = useState('');
  const [answerPasteError, setAnswerPasteError] = useState('');
  const [studyDocument, setStudyDocument] = useState<StudyDocument | null>(null);
  const [documentSource, setDocumentSource] = useState('');
  const [documentKind, setDocumentKind] = useState<StudyDocumentAnalysisKind>('auto');
  const [documentEditorOpen, setDocumentEditorOpen] = useState(false);
  const [documentError, setDocumentError] = useState('');
  const [documentInstruction, setDocumentInstruction] = useState('');
  const [documentDragging, setDocumentDragging] = useState(false);
  const [documentReading, setDocumentReading] = useState(false);
  const [documentJob, setDocumentJob] = useState<DocumentJobState | null>(null);
  const explainControllerRef = useRef<AbortController | null>(null);
  const pasteControllerRef = useRef<AbortController | null>(null);
  const documentControllerRef = useRef<AbortController | null>(null);
  const documentJobControllerRef = useRef<AbortController | null>(null);
  const contextRefreshControllerRef = useRef<AbortController | null>(null);
  const documentJobIdRef = useRef('');
  const documentPendingStartTokenRef = useRef('');
  const documentPendingStartDeadlineRef = useRef(0);
  const documentCancelRequestedRef = useRef(false);
  const documentInputRef = useRef<HTMLInputElement | null>(null);
  const generateButtonRef = useRef<HTMLButtonElement | null>(null);
  const lastActivationRevisionRef = useRef(-1);
  const mountedRef = useRef(false);
  const textImageRef = useRef('');
  const pastePendingRef = useRef(false);
  const visionMaxImagePxRef = useRef(DEFAULT_VISION_MAX_IMAGE_PX);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const replySectionRef = useRef<HTMLDivElement | null>(null);
  const currentMode = String(status.active_mode || status.mode || 'companion');
  const interactionBusy = busy || pastePending;
  const documentJobBusy = Boolean(documentJob && ['starting', 'queued', 'running', 'cancel_requested'].includes(documentJob.status));
  const documentInteractionBusy = interactionBusy || documentReading || documentJobBusy;

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

  async function refreshModelRuntime(signal?: AbortSignal) {
    setModelRuntimeLoading(true);
    try {
      const data = await callStudyPlugin<{
        model_runtime?: Record<string, StudyModelRuntime>;
      }>(props.api, 'study_get_settings_config', props.locale, {}, signal);
      if (!signal?.aborted) setModelRuntime(data.model_runtime || {});
    } catch (_error) {
      if (!signal?.aborted) setModelRuntime({});
    } finally {
      if (!signal?.aborted) setModelRuntimeLoading(false);
    }
  }

  function modelRuntimeStatus(role: string, item: StudyModelRuntime) {
    if (item.configured !== true) return t('ui.settings.model_runtime.not_configured', 'Not configured');
    if (item.transport_supported === false) return t('ui.settings.model_runtime.unsupported', 'This provider protocol is not supported by Study Companion');
    if (item.credential_configured !== true) return t('ui.settings.model_runtime.credential_missing', 'Credential is not configured');
    return role === 'vision'
      ? t('ui.settings.model_runtime.configured_vision_unknown', 'Configured; image capability will be confirmed on the first request')
      : t('ui.settings.model_runtime.ready', 'Ready');
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

  function formatTutorDiagnostic(diagnostic?: string, documentOperation = false) {
    const phaseDeadlineDiagnostics = new Set([
      'document_analysis_window_exhausted',
      'document_chunk_window_exhausted',
      'document_merge_window_exhausted',
      'document_finalize_timeout',
    ]);
    const diagnosticCode = String(diagnostic || '').trim();
    const normalizedDiagnostic = phaseDeadlineDiagnostics.has(diagnosticCode)
      ? 'timeout'
      : diagnosticCode;
    const messages: Record<string, [string, string]> = {
      timeout: documentOperation
        ? ['ui.document.error.timeout', 'Document analysis timed out. Please retry shortly.']
        : ['ui.error.llm_timeout', 'The model request timed out. Please retry shortly.'],
      rate_limited: ['ui.error.llm_rate_limited', 'The model service is receiving too many requests. Please retry shortly.'],
      authentication_failed: ['ui.error.llm_authentication_failed', 'The configured model credential is invalid. Check it in N.E.K.O model settings.'],
      model_not_supported: documentOperation
        ? ['ui.error.document_analysis_model_not_supported', 'The configured model does not support document analysis.']
        : ['ui.error.llm_model_not_supported', 'The configured model is unavailable or does not support this request.'],
      provider_unavailable: ['ui.error.llm_provider_unavailable', 'The model service is temporarily unavailable. Please retry shortly.'],
      unsupported_provider: documentOperation
        ? ['ui.error.document_analysis_unsupported_provider', 'The configured model provider protocol is not supported by Study Companion.']
        : ['ui.error.llm_unsupported_provider', 'The configured model provider protocol is not supported by Study Companion.'],
      context_limit_exceeded: documentOperation
        ? ['ui.error.document_analysis_context_limit_exceeded', 'The document exceeds the configured model context limit.']
        : ['ui.error.llm_context_limit_exceeded', 'The content exceeds the configured model context limit. Shorten it and retry.'],
      vision_not_supported: documentOperation
        ? ['ui.error.document_analysis_vision_not_supported', 'The configured Vision model does not accept image input.']
        : ['ui.error.llm_vision_not_supported', 'The configured Vision model does not accept image input.'],
      agent_quota_exceeded: documentOperation
        ? ['ui.error.document_analysis_agent_quota_exceeded', 'The free Agent daily quota has been used up.']
        : ['ui.error.llm_agent_quota_exceeded', 'The free Agent daily quota has been used up. Try again later or configure another Agent model.'],
      invalid_endpoint: documentOperation
        ? ['ui.error.document_analysis_invalid_endpoint', 'The configured model endpoint is invalid or unsupported.']
        : ['ui.error.llm_invalid_endpoint', 'The configured model endpoint is invalid or unsupported.'],
      invalid_request: documentOperation
        ? ['ui.error.document_analysis_invalid_request', 'The model service rejected the document analysis request as invalid.']
        : ['ui.error.llm_invalid_request', 'The model service rejected the request as invalid.'],
      invalid_image: ['ui.error.llm_invalid_image', 'The image could not be read. Please use a valid JPEG or PNG image.'],
      document_too_large: ['ui.document.error.file_too_large', 'The document exceeds the 512 KiB size limit.'],
      document_too_long: ['ui.document.error.too_long', 'The document exceeds the 160,000-token limit. Shorten it and retry.'],
      empty_document: ['ui.document.error.empty', 'The document is empty or contains only whitespace.'],
      binary_document: ['ui.document.error.binary', 'This file appears to contain binary data.'],
      invalid_document_encoding: ['ui.document.error.encoding', 'The document encoding could not be recognized. Save it as UTF-8 and retry.'],
      unsupported_document_type: ['ui.document.error.unsupported_type', 'Only .txt, .md, and .markdown files are supported.'],
      unsupported_document_kind: ['ui.document.error.unsupported_kind', 'The selected document analysis type is not supported.'],
      unsafe_document_content: ['ui.document.error.unsafe_content', 'The document contains an oversized embedded payload or line.'],
      unsafe_model_output: ['ui.document.error.unsafe_model_output', 'The analysis was withheld because it reproduced too much of the original document. Try a more focused request.'],
      analysis_instruction_too_long: ['ui.document.error.instruction_too_long', 'The analysis instruction is too long.'],
      invalid_document_name: ['ui.document.error.read_failed', 'The document name is invalid.'],
      document_type_mismatch: ['ui.document.error.unsupported_type', 'The document type does not match its file extension.'],
      unsupported_locale: ['ui.error.plugin_call_failed', 'The current page language is not supported for document analysis.'],
      llm_call_failed: documentOperation
        ? ['ui.error.document_analysis_llm_call_failed', 'The document analysis model request failed.']
        : ['ui.error.llm_call_failed', 'The model service request failed. Please retry.'],
      document_job_busy: ['ui.error.document_job_busy', 'Another document analysis is already running.'],
      document_job_not_found: ['ui.error.document_job_not_found', 'The document analysis job is no longer available.'],
      document_split_failed: ['ui.error.document_split_failed', 'The document could not be split safely for analysis.'],
      document_chunk_failed: ['ui.error.document_chunk_failed', 'A document section could not be analyzed.'],
      document_merge_failed: ['ui.error.document_merge_failed', 'The document sections could not be merged into a final analysis.'],
      document_merge_budget_exceeded: ['ui.error.document_merge_budget_exceeded', 'The section analyses are too long to merge safely.'],
      document_canceled: ['ui.error.document_canceled', 'Document analysis canceled.'],
    };
    const [key, fallback] = messages[normalizedDiagnostic]
      || ['ui.error.llm_call_failed', 'The model service request failed. Please retry.'];
    return t(key, fallback);
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

  function formatDocumentError(error: unknown) {
    const messages: Record<string, [string, string]> = {
      multiple_files: ['ui.document.error.multiple_files', 'Please choose exactly one document.'],
      unsupported_type: ['ui.document.error.unsupported_type', 'Only .txt, .md, and .markdown files are supported.'],
      file_too_large: ['ui.document.error.file_too_large', 'The document exceeds the 512 KiB size limit.'],
      empty_document: ['ui.document.error.empty', 'The document is empty or contains only whitespace.'],
      binary_document: ['ui.document.error.binary', 'This file appears to contain binary data.'],
      encoding_unrecognized: ['ui.document.error.encoding', 'The document encoding could not be recognized. Save it as UTF-8 and retry.'],
      unsafe_document_content: ['ui.document.error.unsafe_content', 'The document contains an oversized embedded payload or line.'],
      document_too_long: ['ui.document.error.too_long', 'The document is estimated to exceed the 160,000-token limit. Shorten it and retry.'],
      unsupported_document: ['ui.error.document_type', 'Only TXT, Markdown, PDF, and DOCX files are supported.'],
      document_too_large: ['ui.error.document_parse_too_large', 'PDF and DOCX files must not exceed 16 MiB.'],
      invalid_pdf: ['ui.error.document_invalid_pdf', 'The PDF file is invalid or damaged.'],
      invalid_ooxml: ['ui.error.document_invalid_ooxml', 'The DOCX file is invalid or damaged.'],
      encrypted_pdf_unsupported: ['ui.error.document_encrypted_pdf_unsupported', 'Encrypted PDF files are not supported.'],
      legacy_office_unsupported: ['ui.error.document_legacy_office_unsupported', 'Legacy Microsoft Office files are not supported. Use DOCX instead.'],
      macro_document_unsupported: ['ui.error.document_macro_document_unsupported', 'Macro-enabled Office documents are not supported.'],
      no_readable_text: ['ui.error.document_no_readable_text', 'No readable text was found. Scanned PDF OCR is not supported yet.'],
      garbled_text: ['ui.error.document_garbled_text', 'The extracted document text is unreadable.'],
      document_parse_failed: ['ui.error.document_parse_failed', 'The document could not be parsed.'],
      document_parse_timeout: ['ui.error.document_parse_timeout', 'Document parsing timed out. Please retry.'],
      document_parse_permission_denied: ['ui.error.document_parse_permission_denied', 'This panel is not permitted to parse documents.'],
    };
    const candidate = error as { code?: unknown; message?: unknown } | null;
    const code = error instanceof StudyDocumentError
      ? error.code
      : String(candidate?.code || candidate?.message || '');
    const [key, fallback] = messages[code]
      || ['ui.document.error.read_failed', 'The document could not be read.'];
    return t(key, fallback);
  }

  async function importDocumentFiles(files: FileList | File[]) {
    if (isInteractionBusy() || documentJobBusy) return;
    documentControllerRef.current?.abort();
    const controller = new AbortController();
    documentControllerRef.current = controller;
    setPastePendingState(true);
    setDocumentReading(true);
    setDocumentError('');
    try {
      const file = oneStudyDocument(files);
      let loaded;
      if (isParsedStudyDocumentFile(file)) {
        assertParsedStudyDocumentFile(file);
        const response = await props.api.parseDocument(file, { timeoutMs: STUDY_DOCUMENT_PARSE_TIMEOUT_MS, signal: controller.signal });
        if (controller.signal.aborted) return;
        const raw = response && typeof response === 'object' ? response as Record<string, unknown> : {};
        const payload = raw.document && typeof raw.document === 'object'
          ? raw.document as Record<string, unknown>
          : raw;
        loaded = parsedStudyDocument(file, payload);
      } else {
        loaded = await readStudyDocument(file, controller.signal);
      }
      if (controller.signal.aborted || !mountedRef.current) return;
      setDocumentSource(loaded.text);
      setStudyDocument(loaded.document);
      setDocumentKind('auto');
      setDocumentEditorOpen(false);
    } catch (error) {
      if (!controller.signal.aborted && mountedRef.current) {
        setDocumentError(formatDocumentError(error));
      }
    } finally {
      if (documentControllerRef.current === controller) {
        documentControllerRef.current = null;
      }
      if (!controller.signal.aborted && mountedRef.current) {
        setPastePendingState(false);
        setDocumentReading(false);
      }
    }
  }

  function removeStudyDocument() {
    if (documentJobBusy) return;
    documentControllerRef.current?.abort();
    documentControllerRef.current = null;
    setStudyDocument(null);
    setDocumentSource('');
    setDocumentKind('auto');
    setDocumentEditorOpen(false);
    setDocumentError('');
    setDocumentInstruction('');
    if (documentInputRef.current) documentInputRef.current.value = '';
  }

  function editDocumentSource(value: string) {
    if (documentJobBusy) return;
    setDocumentSource(value);
    if (studyDocument) {
      setStudyDocument(metadataForEditedDocument(studyDocument, value));
      setDocumentError('');
    }
  }

  function normalizeDocumentJob(payload: DocumentJobPayload, fallbackJobId = ''): DocumentJobState {
    const totalChunks = Math.max(0, Math.floor(Number(payload.total_chunks ?? payload.chunks) || 0));
    const completedChunks = Math.max(0, Math.min(totalChunks || Number.MAX_SAFE_INTEGER, Math.floor(Number(payload.completed_chunks) || 0)));
    return {
      jobId: String(payload.job_id || fallbackJobId),
      status: String(payload.status || 'running'),
      stage: String(payload.stage || 'validating'),
      analysisMode: String(payload.analysis_mode || ''),
      completedChunks,
      totalChunks,
      progress: Math.max(0, Math.min(1, Number(payload.progress) || 0)),
    };
  }

  function restoredStudyDocument(metadata?: DocumentJobMetadata): StudyDocument | null {
    const name = String(metadata?.name || '').trim();
    const analysisType = String(metadata?.type || '') as StudyDocument['analysisType'];
    const sourceTypeByAnalysisType: Partial<Record<StudyDocument['analysisType'], StudyDocument['sourceType']>> = {
      'text/plain': 'txt',
      'text/markdown': 'markdown',
      'application/pdf': 'pdf',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    };
    const sourceType = sourceTypeByAnalysisType[analysisType];
    if (!name || !sourceType) return null;
    return {
      name,
      sourceType,
      analysisType,
      originalSize: 0,
      encoding: 'recovered',
      chars: Math.max(0, Math.floor(Number(metadata?.chars) || 0)),
      estimatedTokens: Math.max(0, Math.floor(Number(metadata?.tokens) || 0)),
      truncated: metadata?.truncated === true,
      meta: { source_retained: false },
      modified: false,
    };
  }

  function formatDocumentCompletion(payload: DocumentJobPayload) {
    const result = payload.reply || payload.summary || '';
    if (payload.diagnostic !== 'output_truncated') return result;
    return [
      result,
      t(
        'ui.error.document_output_truncated',
        'The document analysis reached the output limit. The result above may be incomplete.',
      ),
    ].filter(Boolean).join('\n\n');
  }

  function rememberDocumentJobId(jobId: string) {
    documentJobIdRef.current = jobId;
    documentPendingStartTokenRef.current = '';
    documentPendingStartDeadlineRef.current = 0;
    try {
      if (jobId) window.sessionStorage.setItem(DOCUMENT_JOB_STORAGE_KEY, jobId);
      else window.sessionStorage.removeItem(DOCUMENT_JOB_STORAGE_KEY);
    } catch {
      // Storage may be unavailable in a restricted hosted surface.
    }
  }

  function savedDocumentJobId() {
    const inMemoryJobId = String(documentJobIdRef.current || '');
    const inMemoryPendingJobId = documentPendingStartTokenRef.current
      ? `${PENDING_DOCUMENT_JOB_PREFIX}${documentPendingStartTokenRef.current}`
      : '';
    try {
      return inMemoryJobId
        || String(window.sessionStorage.getItem(DOCUMENT_JOB_STORAGE_KEY) || '')
        || inMemoryPendingJobId;
    } catch {
      return inMemoryJobId || inMemoryPendingJobId;
    }
  }

  function rememberPendingDocumentJob(startToken: string) {
    documentPendingStartTokenRef.current = startToken;
    documentPendingStartDeadlineRef.current = Date.now()
      + timeoutForEntry('study_start_document_analysis');
    try {
      window.sessionStorage.setItem(
        DOCUMENT_JOB_STORAGE_KEY,
        `${PENDING_DOCUMENT_JOB_PREFIX}${startToken}`,
      );
    } catch {
      // Storage may be unavailable in a restricted hosted surface.
    }
  }

  function waitForDocumentPoll(ms: number, signal: AbortSignal) {
    return new Promise<void>((resolve, reject) => {
      if (signal.aborted) {
        reject(new DOMException('Aborted', 'AbortError'));
        return;
      }
      const timeoutId = window.setTimeout(resolve, ms);
      signal.addEventListener('abort', () => {
        window.clearTimeout(timeoutId);
        reject(new DOMException('Aborted', 'AbortError'));
      }, { once: true });
    });
  }

  function documentPollingController(parentSignal?: AbortSignal) {
    const current = documentJobControllerRef.current;
    if (parentSignal && current?.signal === parentSignal) return current;
    current?.abort();
    const controller = new AbortController();
    if (parentSignal?.aborted) controller.abort();
    else parentSignal?.addEventListener('abort', () => controller.abort(), { once: true });
    documentJobControllerRef.current = controller;
    return controller;
  }

  function recoveringDocumentJob(jobId: string): DocumentJobState {
    const pendingStart = isPendingDocumentJobId(jobId);
    return {
      jobId: pendingStart ? '' : jobId,
      status: pendingStart ? 'starting' : 'running',
      stage: 'validating',
      analysisMode: '',
      completedChunks: 0,
      totalChunks: 0,
      progress: 0,
    };
  }

  async function refreshAfterDocumentCompletion(signal: AbortSignal) {
    try {
      await refresh(signal, { updateReply: false });
    } catch {
      // The analysis result is terminal; a status refresh is best-effort only.
    }
  }

  async function acknowledgeDocumentJob(jobId: string, signal?: AbortSignal) {
    if (!jobId) return;
    try {
      await callStudyPlugin<DocumentJobPayload>(
        props.api,
        'study_document_analysis_status',
        props.locale,
        { job_id: jobId, acknowledge: true },
        signal,
      );
      rememberDocumentJobId('');
    } catch {
      rememberDocumentJobId(jobId);
    }
  }

  async function pollDocumentJob(jobId: string, controller: AbortController) {
    let pollDelayMs = 1000;
    let consecutiveFailures = 0;
    let terminal = false;
    try {
      while (!controller.signal.aborted && mountedRef.current) {
        await waitForDocumentPoll(pollDelayMs, controller.signal);
        let data: DocumentJobPayload;
        try {
          data = await callStudyPlugin<DocumentJobPayload>(
            props.api,
            'study_document_analysis_status',
            String(props.locale || '').trim(),
            { job_id: jobId },
            controller.signal,
          );
          consecutiveFailures = 0;
        } catch (error) {
          if (controller.signal.aborted) return;
          consecutiveFailures += 1;
          if (consecutiveFailures < 3) {
            pollDelayMs = 2000;
            continue;
          }
          setReply(formatPluginError(error));
          pollDelayMs = Math.min(
            30_000,
            2_000 * (2 ** Math.min(consecutiveFailures - 2, 4)),
          );
          continue;
        }
        if (controller.signal.aborted || !mountedRef.current) return;
        const nextJob = normalizeDocumentJob(data, jobId);
        setDocumentJob(nextJob);
        if (['completed', 'succeeded'].includes(nextJob.status)) {
          terminal = true;
          setReply(data.degraded
            ? formatTutorDiagnostic(data.diagnostic, true)
            : formatDocumentCompletion(data));
          await acknowledgeDocumentJob(jobId, controller.signal);
          await refreshAfterDocumentCompletion(controller.signal);
          return;
        }
        if (['failed', 'canceled', 'timeout'].includes(nextJob.status)) {
          terminal = true;
          setReply(formatTutorDiagnostic(data.diagnostic || (nextJob.status === 'canceled' ? 'document_canceled' : nextJob.status), true));
          await acknowledgeDocumentJob(jobId, controller.signal);
          return;
        }
        pollDelayMs = 2000;
      }
    } catch (error) {
      if (!controller.signal.aborted && mountedRef.current) {
        setReply(formatPluginError(error));
      }
    } finally {
      if (documentJobControllerRef.current === controller) {
        documentJobControllerRef.current = null;
        if (terminal && !controller.signal.aborted && mountedRef.current) {
          setDocumentJob(null);
        }
      }
    }
  }

  async function resumeDocumentJob(signal: AbortSignal, pendingStartTokenOverride = '') {
    let recoveryFailures = 0;
    const pendingStartRecoveryDeadline = documentPendingStartDeadlineRef.current
      || Date.now() + timeoutForEntry('study_start_document_analysis');
    while (!signal.aborted && mountedRef.current) {
      const savedJobId = savedDocumentJobId() || (pendingStartTokenOverride
        ? `${PENDING_DOCUMENT_JOB_PREFIX}${pendingStartTokenOverride}`
        : '');
      const pendingStart = isPendingDocumentJobId(savedJobId);
      const hasSavedJobId = Boolean(savedJobId && !pendingStart);
      const startToken = pendingDocumentStartToken(savedJobId);
      let data: DocumentJobPayload | null = null;
      let savedJobNotFound = false;
      let lookupFailed = false;

      if (savedJobId) {
        setDocumentJob((current) => current || recoveringDocumentJob(savedJobId));
      }
      if (hasSavedJobId) {
        try {
          data = await callStudyPlugin<DocumentJobPayload>(
            props.api,
            'study_document_analysis_status',
            props.locale,
            { job_id: savedJobId },
            signal,
          );
          if (data?.diagnostic === 'document_job_not_found') {
            savedJobNotFound = true;
            data = null;
          }
        } catch (error) {
          if (signal.aborted) return;
          lookupFailed = true;
          setReply(formatPluginError(error));
        }
      }

      if (!data && !lookupFailed) {
        try {
          const activeArgs: Record<string, unknown> = pendingStart ? { pending_start: true } : {};
          if (startToken) activeArgs.start_token = startToken;
          data = await callStudyPlugin<DocumentJobPayload>(
            props.api,
            'study_active_document_analysis',
            props.locale,
            activeArgs,
            signal,
          );
        } catch (error) {
          if (signal.aborted) return;
          lookupFailed = true;
          setReply(formatPluginError(error));
        }
      }
      if (signal.aborted || !mountedRef.current) return;

      if (lookupFailed) {
        recoveryFailures += 1;
        const retryDelayMs = Math.min(30_000, 2_000 * (2 ** Math.min(recoveryFailures - 1, 4)));
        try {
          await waitForDocumentPoll(retryDelayMs, signal);
        } catch (error) {
          if (signal.aborted) return;
          throw error;
        }
        continue;
      }

      const restoredDocument = restoredStudyDocument(data?.document);
      if (restoredDocument) {
        setStudyDocument((current) => current || restoredDocument);
      }

      if (
        pendingStart
        && data?.status === 'idle'
        && Date.now() < pendingStartRecoveryDeadline
      ) {
        await waitForDocumentPoll(1_000, signal);
        continue;
      }
      if (data?.status === 'idle') {
        rememberDocumentJobId('');
        setDocumentJob(null);
        return;
      }
      const jobId = String(
        data?.job_id || (hasSavedJobId && !savedJobNotFound ? savedJobId : ''),
      );
      if (!jobId) {
        rememberDocumentJobId('');
        setDocumentJob(null);
        return;
      }

      rememberDocumentJobId(jobId);
      const nextJob = normalizeDocumentJob(data || {}, jobId);
      setDocumentJob(nextJob);
      if (['completed', 'succeeded'].includes(nextJob.status)) {
        setReply(data?.degraded
          ? formatTutorDiagnostic(data.diagnostic, true)
          : formatDocumentCompletion(data || {}));
        await acknowledgeDocumentJob(jobId, signal);
        setDocumentJob(null);
        return;
      }
      if (['failed', 'canceled', 'timeout'].includes(nextJob.status)) {
        setReply(formatTutorDiagnostic(data?.diagnostic || nextJob.status, true));
        await acknowledgeDocumentJob(jobId, signal);
        setDocumentJob(null);
        return;
      }
      if (documentCancelRequestedRef.current) {
        documentCancelRequestedRef.current = false;
        await cancelKnownDocumentJob(jobId, nextJob, signal);
        return;
      }
      await pollDocumentJob(jobId, documentPollingController(signal));
      return;
    }
  }

  async function cancelKnownDocumentJob(
    jobId: string,
    fallbackJob: DocumentJobState,
    parentSignal?: AbortSignal,
  ) {
    try {
      const data = await callHostedPlugin<DocumentJobPayload>(
        props.api,
        'study_cancel_document_analysis',
        {
          job_id: jobId,
          cancellation_source: 'user',
          locale: String(props.locale || '').trim(),
        },
        { timeoutMs: timeoutForEntry('study_cancel_document_analysis') },
      );
      const cancellationConfirmed = data.status === 'canceled'
        || data.diagnostic === 'document_canceled'
        || data.diagnostic === 'document_job_not_found';
      if (!cancellationConfirmed) {
        throw new Error(data.diagnostic || 'document cancellation was not confirmed');
      }
      if (mountedRef.current) {
        setReply(formatTutorDiagnostic(data.diagnostic || 'document_canceled', true));
        setDocumentJob(null);
      }
      await acknowledgeDocumentJob(jobId, parentSignal);
      if (documentJobControllerRef.current?.signal === parentSignal) {
        documentJobControllerRef.current = null;
      }
      return;
    } catch (error) {
      if (!mountedRef.current) return;
      rememberDocumentJobId(jobId);
      setReply(formatPluginError(error));
      setDocumentJob(fallbackJob);
      const controller = documentPollingController(parentSignal);
      await pollDocumentJob(jobId, controller);
    }
  }

  async function cancelDocumentJob() {
    const jobId = documentJobIdRef.current;
    if (!jobId) {
      documentCancelRequestedRef.current = true;
      setDocumentJob((current) => current ? { ...current, status: 'cancel_requested', stage: 'canceling' } : current);
      return;
    }
    documentCancelRequestedRef.current = false;
    documentJobControllerRef.current?.abort();
    documentJobControllerRef.current = null;
    const fallbackJob = documentJob || recoveringDocumentJob(jobId);
    setDocumentJob((current) => current ? { ...current, status: 'cancel_requested', stage: 'canceling' } : current);
    await cancelKnownDocumentJob(jobId, fallbackJob);
  }

  async function analyzeDocument() {
    if (isInteractionBusy() || documentJobBusy || !studyDocument) return;
    const sourceText = documentSource.trim();
    if (!sourceText) {
      setDocumentError(t('ui.document.error.empty', 'The document is empty or contains only whitespace.'));
      return;
    }
    const currentDocument = metadataForEditedDocument(studyDocument, documentSource);
    if (new TextEncoder().encode(documentSource).byteLength > STUDY_DOCUMENT_MAX_BYTES) {
      setStudyDocument(currentDocument);
      setDocumentError(t('ui.document.error.file_too_large', 'The edited document exceeds the 512 KiB size limit.'));
      return;
    }
    if (currentDocument.estimatedTokens > STUDY_DOCUMENT_MAX_ESTIMATED_TOKENS) {
      setStudyDocument(currentDocument);
      setDocumentError(t('ui.document.error.too_long', 'The document is estimated to exceed the 160,000-token limit. Shorten it and retry.'));
      return;
    }
    documentJobControllerRef.current?.abort();
    const controller = new AbortController();
    documentJobControllerRef.current = controller;
    documentCancelRequestedRef.current = false;
    setDocumentJob({
      jobId: '',
      status: 'starting',
      stage: 'validating',
      analysisMode: estimatedDocumentAnalysisMode(currentDocument.estimatedTokens),
      completedChunks: 0,
      totalChunks: currentDocument.estimatedTokens > STUDY_DOCUMENT_DIRECT_MAX_ESTIMATED_TOKENS
        ? estimateDocumentChunkCount(currentDocument.estimatedTokens)
        : 1,
      progress: 0,
    });
    setDocumentError('');
    setReply(t('ui.document.status.analyzing', 'Analyzing document...'));
    scrollReplyIntoView();
    const startToken = createDocumentStartToken();
    rememberPendingDocumentJob(startToken);
    try {
      const data = await callStudyPlugin<DocumentJobPayload>(props.api, 'study_start_document_analysis', props.locale, {
        document_name: currentDocument.name,
        document_type: currentDocument.analysisType,
        document_text: documentSource,
        document_truncated: currentDocument.truncated,
        analysis_kind: documentKind,
        analysis_instruction: documentInstruction.trim(),
        locale: String(props.locale || '').trim(),
        start_token: startToken,
      }, controller.signal);
      if (controller.signal.aborted) return;
      if (!mountedRef.current) {
        if (data.job_id) rememberDocumentJobId(String(data.job_id));
        return;
      }
      if (data.degraded || !data.job_id) {
        rememberDocumentJobId('');
        setReply(formatTutorDiagnostic(data.diagnostic || 'llm_call_failed', true));
        return;
      }
      const jobId = String(data.job_id);
      rememberDocumentJobId(jobId);
      if (documentCancelRequestedRef.current) {
        documentCancelRequestedRef.current = false;
        await cancelKnownDocumentJob(jobId, normalizeDocumentJob(data, jobId), controller.signal);
        return;
      }
      if (!mountedRef.current) return;
      setDocumentJob(normalizeDocumentJob(data, jobId));
      setStudyDocument(currentDocument);
      await pollDocumentJob(jobId, controller);
    } catch (error) {
      if (!controller.signal.aborted) {
        setReply(error instanceof Error && /plugin call timed out|plugin_call_timeout/i.test(error.message)
          ? t('ui.document.error.timeout', 'Document analysis timed out. Please retry shortly.')
          : formatPluginError(error));
        await resumeDocumentJob(controller.signal, startToken);
      }
    } finally {
      if (documentJobControllerRef.current === controller && !documentJobIdRef.current) {
        documentJobControllerRef.current = null;
        if (!controller.signal.aborted && mountedRef.current) setDocumentJob(null);
      }
    }
  }

  async function refresh(signal?: AbortSignal, _options: { updateReply?: boolean } = {}) {
    const data = normalizeStudyStatus(await callStudyPlugin(props.api, 'study_status', props.locale, {}, signal));
    if (signal?.aborted) {
      return;
    }
    visionMaxImagePxRef.current = normalizeVisionMaxImagePx(
      data.config?.llm_vision_max_image_px,
    );
    setStatusLine(data);
  }

  async function loadQuestionContext(signal?: AbortSignal) {
    const data = await callStudyPlugin<QuestionContext>(props.api, 'study_question_context', props.locale, {}, signal);
    if (!signal?.aborted) {
      setQuestionContext(data);
      if (data.practice_scope?.display_path) {
        setActivePracticeScope(data.practice_scope);
      }
    }
    return data;
  }

  async function loadPracticeScope(signal?: AbortSignal) {
    try {
      const data = await callStudyPlugin<{
        active?: boolean;
        scope?: PracticeScope;
        scope_revision?: number;
      }>(props.api, 'study_get_practice_scope', props.locale, {}, signal);
      const scope = data.active && data.scope && typeof data.scope === 'object'
        ? data.scope
        : null;
      if (!signal?.aborted) {
        setActivePracticeScope(scope);
      }
      return scope;
    } catch (error) {
      if (!signal?.aborted) setActivePracticeScope(null);
      throw error;
    }
  }

  async function setMode(mode: StudyMode) {
    if (isInteractionBusy() || mode === currentMode) {
      return;
    }
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      setReply('');
      const data = await callStudyPlugin(props.api, 'study_set_mode', props.locale, { mode, reason: 'ui' }, controller.signal) as {
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
      const data = await callStudyPlugin(props.api, 'study_explain_text', props.locale, explainArgs, controller.signal) as {
        reply?: string;
        summary?: string;
        transition_phrase?: string;
        degraded?: boolean;
        diagnostic?: string;
      } & SolutionNarrationOutcome & GeneralNarrationOutcome & KnowledgeGuidanceOutcome & HistoryPersistenceOutcome;
      if (controller.signal.aborted) {
        return;
      }
      if (data.degraded) {
        setReply([
          formatTutorDiagnostic(data.diagnostic),
          formatKnowledgeGuidanceEvidence(data, t),
          formatSolutionNarrationNotice(data, t),
          formatGeneralNarrationNotice(data, t),
        ].filter(Boolean).join('\n\n'));
        await refresh(controller.signal, { updateReply: false });
        return;
      }
      shouldClearTextImage = true;
      const nextReply = data.reply || data.summary || '';
      const knowledgeGuidanceEvidence = formatKnowledgeGuidanceEvidence(data, t);
      const narrationNotice = formatSolutionNarrationNotice(data, t);
      const generalNarrationNotice = formatGeneralNarrationNotice(data, t);
      const historyPersistenceNotice = data.history_persisted === false
        ? t(
          'ui.error.history_not_saved',
          'This explanation is shown, but it could not be saved to session history. It may disappear after you leave or refresh.',
        )
        : '';
      setReply([
        nextReply,
        knowledgeGuidanceEvidence,
        narrationNotice,
        generalNarrationNotice,
        historyPersistenceNotice,
      ].filter(Boolean).join('\n\n'));
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
    contextRefreshControllerRef.current?.abort();
    contextRefreshControllerRef.current = null;
    const controller = beginStudyRequest();
    setBusy(true);
    try {
      setQuestionContext(null);
      const freshScope = await loadPracticeScope(controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      const context = await loadQuestionContext(controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      if (!context?.selection_context_id || context.no_data) {
        setReply(t('ui.error.no_targeted_question_data', 'Not enough study records to generate a practice question yet.'));
        return;
      }
      const data = await callStudyPlugin<GeneratedQuestion & {
        summary?: string;
        reply?: string;
        degraded?: boolean;
        diagnostic?: string;
      }>(
        props.api,
        'study_generate_targeted_question',
        props.locale,
        { selection_context_id: context.selection_context_id },
        controller.signal,
      );
      if (controller.signal.aborted) {
        return;
      }
      if (data.degraded) {
        setReply(formatTutorDiagnostic(data.diagnostic));
        await refresh(controller.signal, { updateReply: false });
        return;
      }
      setQuestion(data.question || '');
      setCurrentQuestion(data);
      setPracticeScopeCompleted(false);
      if (context.practice_scope?.display_path) {
        setActivePracticeScope(context.practice_scope);
      } else {
        setActivePracticeScope(freshScope);
      }
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
      const data = await callStudyPlugin(props.api, 'study_evaluate_answer', props.locale, evalArgs, controller.signal) as {
        feedback?: string;
        next_action?: string;
        summary?: string;
        reply?: string;
        degraded?: boolean;
        diagnostic?: string;
        practice_scope_status?: string;
        can_continue_review?: boolean;
      };
      if (controller.signal.aborted) {
        return;
      }
      if (data.degraded) {
        setReply(formatTutorDiagnostic(data.diagnostic));
        await refresh(controller.signal, { updateReply: false });
        return;
      }
      shouldClearAnswerImage = true;
      const scopeCompleted = data.practice_scope_status === 'completed';
      setPracticeScopeCompleted(scopeCompleted);
      const replyParts = [
        scopeCompleted
          ? t('ui.practice.scope_completed', 'Scope complete. You can continue reviewing this topic.')
          : '',
        data.feedback || data.reply || '',
        data.next_action ? `${t('ui.practice.next_action', 'Next')}: ${data.next_action}` : '',
      ].filter(Boolean);
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
      const data = await callStudyPlugin(props.api, 'study_summarize_session', props.locale, {}, controller.signal) as {
        markdown?: string;
        summary?: string;
        reply?: string;
        degraded?: boolean;
        diagnostic?: string;
      };
      if (controller.signal.aborted) {
        return;
      }
      setReply(data.degraded
        ? formatTutorDiagnostic(data.diagnostic)
        : (data.markdown || data.summary || data.reply || ''));
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
    setBusy(false);
    const controller = beginStudyRequest();
    const documentController = documentPollingController();
    void resumeDocumentJob(documentController.signal).catch((error) => {
      if (documentController.signal.aborted) {
        return;
      }
      setReply(formatPluginError(error));
    });
    refresh(controller.signal)
      .then(() => refreshModelRuntime(controller.signal))
      .then(() => loadPracticeScope(controller.signal))
      .catch((error) => {
        if (controller.signal.aborted) {
          return;
        }
        setReply((current) => current || formatPluginError(error));
      });
    return () => {
      mountedRef.current = false;
      documentCancelRequestedRef.current = false;
      documentJobControllerRef.current?.abort();
      documentJobControllerRef.current = null;
      controller.abort();
      explainControllerRef.current?.abort();
      explainControllerRef.current = null;
      pasteControllerRef.current?.abort();
      pasteControllerRef.current = null;
      documentControllerRef.current?.abort();
      documentControllerRef.current = null;
      contextRefreshControllerRef.current?.abort();
      contextRefreshControllerRef.current = null;
    };
  }, [props.locale]);

  useEffect(() => {
    const expectedHostOrigin = String(props.host?.origin || '').trim();
    function handleHostedSurfaceActivated(event: MessageEvent) {
      if (event.source !== window.parent) return;
      if (!expectedHostOrigin || event.origin !== expectedHostOrigin) return;
      const data = event.data && typeof event.data === 'object'
        ? event.data as { type?: unknown; payload?: unknown }
        : null;
      if (data?.type !== 'neko-hosted-surface-activated') return;
      const payload = data.payload && typeof data.payload === 'object'
        ? data.payload as { surfaceId?: unknown; revision?: unknown; activationRevision?: unknown }
        : null;
      const surfaceId = String(payload?.surfaceId || '').trim();
      const activationRevision = payload?.revision ?? payload?.activationRevision;
      if (surfaceId !== 'study-panel') return;
      if (typeof activationRevision !== 'number'
        || !Number.isSafeInteger(activationRevision)
        || activationRevision < 0) return;
      if (activationRevision < lastActivationRevisionRef.current) return;
      lastActivationRevisionRef.current = activationRevision;
      setQuestionContext(null);
      contextRefreshControllerRef.current?.abort();
      const controller = new AbortController();
      contextRefreshControllerRef.current = controller;
      void loadPracticeScope(controller.signal)
        .then(() => loadQuestionContext(controller.signal))
        .catch((error) => {
          if (!controller.signal.aborted) setReply(formatPluginError(error));
        })
        .finally(() => {
          if (!controller.signal.aborted) generateButtonRef.current?.focus();
          if (contextRefreshControllerRef.current === controller) {
            contextRefreshControllerRef.current = null;
          }
        });
    }
    window.addEventListener('message', handleHostedSurfaceActivated);
    return () => {
      window.removeEventListener('message', handleHostedSurfaceActivated);
      contextRefreshControllerRef.current?.abort();
      contextRefreshControllerRef.current = null;
    };
  }, [props.host?.origin, props.locale]);

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
  const handleStudyPaste = async (event: {
    clipboardData?: DataTransfer;
    preventDefault: () => void;
    target: EventTarget | null;
  }) => {
    const directFiles = Array.from(event.clipboardData?.files || []);
    const itemFiles = Array.from(event.clipboardData?.items || [])
      .filter((item) => item.kind === 'file')
      .map((item) => item.getAsFile())
      .filter((file): file is File => Boolean(file));
    const files = directFiles.length ? directFiles : itemFiles;
    if (files.length > 1 || (files.length === 1 && !files[0].type.startsWith('image/'))) {
      event.preventDefault();
      await importDocumentFiles(files);
      return;
    }
    await handleTextPaste(event);
  };
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
      <section className="study-panel__model-runtime" aria-label={t('ui.settings.llm.title', 'LLM')}>
        {(['text', 'vision'] as const).map((role) => {
          const item = modelRuntime[role] || {};
          return (
            <div key={role}>
              <span>{t(`ui.settings.model_runtime.${role}`, role === 'text' ? 'Text and document model' : 'Image explanation model')}</span>
              <strong>{item.model || t('ui.settings.model_runtime.not_configured', 'Not configured')}</strong>
              <small>{tf('ui.settings.model_runtime.meta', 'Group: {group} · Protocol: {protocol}', {
                group: item.group || (role === 'text' ? 'agent' : 'vision'),
                protocol: item.provider_type || t('ui.settings.model_runtime.protocol_unknown', 'Unknown'),
              })}</small>
              <small>{modelRuntimeStatus(role, item)}</small>
            </div>
          );
        })}
        <small>{t('ui.settings.model_runtime.managed_by_neko', 'Models and credentials are managed in N.E.K.O. Long-document analysis may consume multiple free Agent calls.')}</small>
        <div className="study-panel__model-runtime-actions">
          <button type="button" disabled={modelRuntimeLoading} onClick={() => void refreshModelRuntime()}>
            {t('ui.button.refresh_model_status', 'Refresh model status')}
          </button>
          <button type="button" onClick={() => openHostedExternalUrl('/api_key')}>
            {t('ui.button.open_model_settings', 'Open N.E.K.O model settings')}
          </button>
        </div>
      </section>
      <section className="study-panel__state">
        <div>
          <span>{t('ui.practice.scope_label', 'Practice scope')}</span>
          <strong>{activePracticeScope?.display_path?.length
            ? activePracticeScope.display_path.join(' / ')
            : t('ui.practice.scope_automatic', 'Automatic selection')}</strong>
        </div>
        <div>
          <span>{t('ui.practice.context_label', 'Selection')}</span>
          <strong>{questionContext?.selected_topic_name || questionContext?.selected_topic_id || t('ui.practice.no_data_title', 'Not enough data')}</strong>
        </div>
        <div>
          <span>{t('ui.practice.reason_label', 'Reason')}</span>
          <strong>{questionContext?.selection_reason || '-'}</strong>
        </div>
      </section>
      <div
        className={`study-panel__document-drop${documentDragging ? ' is-dragging' : ''}`}
        onDragEnter={(event) => {
          event.preventDefault();
          if (!documentInteractionBusy) setDocumentDragging(true);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setDocumentDragging(false);
          }
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDocumentDragging(false);
          void importDocumentFiles(event.dataTransfer.files);
        }}
      >
        <div className="study-panel__document-toolbar">
          <input
            ref={documentInputRef}
            className="study-panel__document-input"
            type="file"
            accept=".txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            disabled={documentInteractionBusy}
            onChange={(event) => {
              const files = event.target.files;
              if (files) void importDocumentFiles(files);
              event.target.value = '';
            }}
          />
          <button
            type="button"
            disabled={documentInteractionBusy}
            onClick={() => documentInputRef.current?.click()}
          >
            {t('ui.document.import', 'Import file')}
          </button>
          {documentReading ? (
            <button
              type="button"
              onClick={() => {
                documentControllerRef.current?.abort();
                documentControllerRef.current = null;
                setDocumentReading(false);
                setPastePendingState(false);
              }}
            >
              {t('ui.document.cancel_reading', 'Cancel reading')}
            </button>
          ) : null}
          <span>{documentDragging
            ? t('ui.document.drop_now', 'Drop the document here')
            : t('ui.document.drop_hint', 'Drop a file here')}</span>
        </div>
        <textarea
          aria-label={t('ui.label.text', 'Text')}
          placeholder={t('ui.placeholder.input', 'Paste a concept, problem statement, or OCR text here.')}
          value={text}
          readOnly={interactionBusy}
          onChange={(event) => setText(event.target.value)}
          onPaste={handleStudyPaste}
        />
      </div>
      {studyDocument ? (
        <section className="study-panel__document-card" aria-label={t('ui.document.card_label', 'Imported document')}>
          <div>
            <strong>{studyDocument.name}</strong>
            <span>{tf(
              'ui.document.meta',
              '{size} · {encoding} · {chars} characters · about {tokens} tokens',
              {
                size: `${(studyDocument.originalSize / 1024).toFixed(1)} KiB`,
                encoding: studyDocument.encoding,
                chars: studyDocument.chars.toLocaleString(),
                tokens: studyDocument.estimatedTokens.toLocaleString(),
              },
            )}</span>
            {studyDocument.truncated ? (
              <small className="study-panel__document-warning">
                {t('ui.document.truncated_warning', 'The document exceeded the extraction limit. Only the extracted portion will be analyzed.')}
              </small>
            ) : null}
            <small>{studyDocument.modified
              ? t('ui.document.modified', 'Content modified; token count has been re-estimated.')
              : t('ui.document.not_retained', 'The original document will not be retained.')}</small>
            {studyDocument.estimatedTokens > STUDY_DOCUMENT_MAX_ESTIMATED_TOKENS ? (
              <small className="study-panel__document-warning">
                {t('ui.error.document_too_long', 'The estimate is above 160,000 tokens and cannot be analyzed without shortening the document.')}
              </small>
            ) : studyDocument.estimatedTokens > STUDY_DOCUMENT_DIRECT_MAX_ESTIMATED_TOKENS ? (
              <small className="study-panel__document-warning">
                {tf(
                  'ui.document.chunked_mode_hint',
                  'Long document: it will be analyzed in about {chunks} sections and then merged.',
                  { chunks: estimateDocumentChunkCount(studyDocument.estimatedTokens).toLocaleString() },
                )}
              </small>
            ) : (
              <small>{t('ui.document.direct_mode_hint', 'This document will be analyzed in one pass.')}</small>
            )}
          </div>
          <label>
            <span>{t('ui.document.kind_label', 'Analysis type')}</span>
            <select
              value={documentKind}
              disabled={documentInteractionBusy}
              onChange={(event) => setDocumentKind(event.target.value as StudyDocumentAnalysisKind)}
            >
              {STUDY_DOCUMENT_ANALYSIS_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {t(`ui.document.kind.${kind}`, {
                    auto: 'Auto-detect',
                    literary_book: 'Literary book',
                    nonfiction_book: 'Nonfiction book',
                    design_document: 'Design document',
                    academic_paper: 'Academic paper',
                    exam: 'Exam material',
                    course_material: 'Course material',
                    general_notes: 'General notes',
                  }[kind])}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{t('ui.document.instruction', 'Analysis instruction (optional)')}</span>
            <input
              value={documentInstruction}
              maxLength={1000}
              disabled={documentInteractionBusy}
              onChange={(event) => setDocumentInstruction(event.target.value)}
              placeholder={t('ui.document.instruction_placeholder', 'For example: extract exam topics')}
            />
          </label>
          <details
            open={documentEditorOpen}
            onToggle={(event) => {
              const nextOpen = event.currentTarget.open;
              if (documentInteractionBusy) {
                event.currentTarget.open = documentEditorOpen;
                return;
              }
              setDocumentEditorOpen(nextOpen);
            }}
          >
            <summary
              aria-disabled={documentInteractionBusy}
              onClick={(event) => {
                if (documentInteractionBusy) event.preventDefault();
              }}
            >
              {t('ui.document.edit', 'View or edit document text')}
            </summary>
            <textarea
              aria-label={t('ui.document.editor_label', 'Imported document text')}
              value={documentSource}
              readOnly={documentInteractionBusy}
              onChange={(event) => editDocumentSource(event.target.value)}
            />
          </details>
          {documentJob ? (
            <div className="study-panel__document-progress" aria-live="polite">
              <span>{documentJob.stage === 'analyzing_chunks' && documentJob.totalChunks > 1
                ? tf(
                  'ui.document.progress_chunks',
                  'Analyzing section {completed} of {total}',
                  {
                    completed: documentJob.completedChunks.toLocaleString(),
                    total: documentJob.totalChunks.toLocaleString(),
                  },
                )
                : t(`ui.document.stage.${documentJob.stage}`, 'Analyzing document...')}</span>
              <progress value={documentJob.progress} max={1} />
              <small>{Math.round(documentJob.progress * 100)}%</small>
            </div>
          ) : null}
          <div className="study-panel__actions">
            <button type="button" disabled={documentInteractionBusy} onClick={removeStudyDocument}>
              {t('ui.document.remove', 'Remove')}
            </button>
            {documentJobBusy ? (
              <button type="button" onClick={() => void cancelDocumentJob()}>
                {t('ui.document.cancel_analysis', 'Cancel analysis')}
              </button>
            ) : (
              <button
                type="button"
                disabled={interactionBusy || studyDocument.estimatedTokens > STUDY_DOCUMENT_MAX_ESTIMATED_TOKENS}
                onClick={() => void analyzeDocument()}
              >
                {interactionBusy ? t('ui.button.loading', 'Loading...') : t('ui.document.analyze', 'Analyze document')}
              </button>
            )}
          </div>
        </section>
      ) : null}
      {documentError ? <div className="study-panel__paste-error" role="alert">{documentError}</div> : null}
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
          ref={generateButtonRef}
          type="button"
          disabled={interactionBusy}
          onClick={interactionBusy ? undefined : generateQuestion}
        >
          {interactionBusy
            ? t('ui.button.loading', 'Loading...')
            : practiceScopeCompleted
              ? t('ui.button.continue_practice_review', 'Continue review')
              : t('ui.button.generate_question', 'Generate Question')}
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
