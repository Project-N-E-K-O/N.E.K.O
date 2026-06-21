const PLUGIN_ID = 'study_companion';
const RUNS_URL = '/runs';
const RUN_TIMEOUT_MS = 60000;
const RUN_EXPORT_RETRY_COUNT = 3;
const RUN_EXPORT_RETRY_DELAY_MS = 400;
const LOAD_IMAGE_TIMEOUT_MS = 30000;
const TARGET_DATA_URL_LENGTH = 1000000;
const DEFAULT_VISION_MAX_IMAGE_PX = 768;
const SUPPORTED_PASTE_IMAGE_TYPES = new Set(['image/png', 'image/jpeg']);
const LEARNING_PROFILE_STORAGE_KEY = 'study_companion.learning_profile.v1';
const LEARNING_STAGE_OPTIONS = ['primary', 'junior_high', 'senior_high', 'college', 'postgraduate', 'custom'];
const ENTRY_TIMEOUT_MS = {
  study_status: 15000,
  study_ocr_snapshot: 60000,
  study_set_mode: 15000,
  study_explain_text: 310000,
  study_generate_question: 310000,
  study_question_context: 30000,
  study_generate_targeted_question: 310000,
  study_evaluate_answer: 310000,
  study_summarize_session: 90000,
  study_memory_card_upsert: 30000,
  study_memory_deck: 30000,
  study_memory_card_review: 30000,
  study_export_notes: 90000,
};
const STUDY_SURFACE_MESSAGE_TYPES = Object.freeze({
  openSurface: 'neko-study-open-surface',
  reviewCompleted: 'neko-study-review-completed',
  refreshSummary: 'neko-study-refresh-summary',
  memoryDeckUpdated: 'neko-study-memory-deck-updated',
});
const STUDY_SURFACE_INCOMING_MESSAGE_TYPES = new Set([
  STUDY_SURFACE_MESSAGE_TYPES.reviewCompleted,
  STUDY_SURFACE_MESSAGE_TYPES.refreshSummary,
  STUDY_SURFACE_MESSAGE_TYPES.memoryDeckUpdated,
]);
let currentMode = 'companion';
let currentMemoryCard = null;
let mapRequestId=0;

const statusLine = document.getElementById('statusLine');
const replyText = document.getElementById('replyText');
const studyInput = document.getElementById('studyInput');
const refreshBtn = document.getElementById('refreshBtn');
const ocrBtn = document.getElementById('ocrBtn');
const generateQuestionBtn = document.getElementById('generateQuestionBtn');
const explainBtn = document.getElementById('explainBtn');
const evaluateAnswerBtn = document.getElementById('evaluateAnswerBtn');
const summarizeBtn = document.getElementById('summarizeBtn');
const answerInput = document.getElementById('answerInput');
const studyInputImagePreview = document.getElementById('studyInputImagePreview');
const studyInputImage = document.getElementById('studyInputImage');
const studyInputImageRemove = document.getElementById('studyInputImageRemove');
const studyInputPasteError = document.getElementById('studyInputPasteError');
const answerInputImagePreview = document.getElementById('answerInputImagePreview');
const answerInputImage = document.getElementById('answerInputImage');
const answerInputImageRemove = document.getElementById('answerInputImageRemove');
const answerInputPasteError = document.getElementById('answerInputPasteError');
const questionText = document.getElementById('questionText');
const questionContextCard = document.getElementById('questionContextCard');
const selectedTopicName = document.getElementById('selectedTopicName');
const selectionReason = document.getElementById('selectionReason');
const questionTopicMeta = document.getElementById('questionTopicMeta');
const questionDifficultyMeta = document.getElementById('questionDifficultyMeta');
const questionAttemptMeta = document.getElementById('questionAttemptMeta');
const hintToggleBtn = document.getElementById('hintToggleBtn');
const hintText = document.getElementById('hintText');
const feedbackPanel = document.getElementById('feedbackPanel');
const feedbackText = document.getElementById('feedbackText');
const masteryDeltaText = document.getElementById('masteryDeltaText');
const screenType = document.getElementById('screenType');
const questionStatus = document.getElementById('questionStatus');
const evaluationStatus = document.getElementById('evaluationStatus');
const memoryDeckStatus = document.getElementById('memoryDeckStatus');
const memoryFrontInput = document.getElementById('memoryFrontInput');
const memoryBackInput = document.getElementById('memoryBackInput');
const memoryRefreshBtn = document.getElementById('memoryRefreshBtn');
const memoryAddBtn = document.getElementById('memoryAddBtn');
const memoryDueCard = document.getElementById('memoryDueCard');
const modeSwitch = document.getElementById('modeSwitch');
const modeSelect = document.getElementById('modeSelect');
const summaryMode = document.getElementById('summaryMode');
const summaryDuration = document.getElementById('summaryDuration');
const summaryGoal = document.getElementById('summaryGoal');
const summaryStage = document.getElementById('summaryStage');
const quickFocusState = document.getElementById('quickFocusState');
const quickReviewCount = document.getElementById('quickReviewCount');
const quickCheckinStatus = document.getElementById('quickCheckinStatus');
const diagnosisTitle = document.getElementById('diagnosisTitle');
const diagnosisBody = document.getElementById('diagnosisBody');
const primaryDiagnosis = document.getElementById('primaryDiagnosis');
const firstRunGuide = document.getElementById('firstRunGuide');
const firstRunSteps = document.getElementById('firstRunSteps');
const firstRunSkipBtn = document.getElementById('firstRunSkipBtn');
const advancedToggleBtn = document.getElementById('advancedToggleBtn');
const advancedSettings = document.getElementById('advancedSettings');
const settingsTabs = Array.from(document.querySelectorAll('[data-settings-tab]'));
const settingsTabPanels = Array.from(document.querySelectorAll('[data-settings-tab-panel]'));
const surfaceOpenButtons = Array.from(document.querySelectorAll('[data-open-surface]'));
const featureActionButtons = Array.from(document.querySelectorAll('[data-feature-action]'));
const surfaceDrawer = document.getElementById('surfaceDrawer');
const surfaceDrawerTitle = document.getElementById('surfaceDrawerTitle');
const surfaceDrawerBody = document.getElementById('surfaceDrawerBody');
const surfaceDrawerCloseBtn = document.getElementById('surfaceDrawerCloseBtn');
const settingsConfigForm = document.getElementById('settingsConfigForm');
const settingsSaveBtn = document.getElementById('settingsSaveBtn');
const settingsConfigStatus = document.getElementById('settingsConfigStatus');
const settingsDefaultMode = document.getElementById('settingsDefaultMode');
const settingsLearningProfileSummary = document.getElementById('settingsLearningProfileSummary');
const settingsLearningStage = document.getElementById('settingsLearningStage');
const settingsOcrEnabled = document.getElementById('settingsOcrEnabled');
const settingsOcrLanguages = document.getElementById('settingsOcrLanguages');
const settingsLlmTimeout = document.getElementById('settingsLlmTimeout');
const settingsLlmVisionEnabled = document.getElementById('settingsLlmVisionEnabled');
const modeButtons = Array.from(document.querySelectorAll('[data-mode]'));
const memoryReviewButtons = Array.from(document.querySelectorAll('[data-memory-rating]'));
const MODE_SHORTCUTS = Object.freeze({
  1: 'companion',
  2: 'interactive',
  3: 'teaching',
});
let lastStatusPayload = {};
let settingsConfig = null;
let settingsConfigLoading = false;
let firstRunDismissed = false;
let advancedSettingsOpen = false;
let modeChangeInFlight = false;
let refreshPending = false;
let learningProfileModal = null;
let lastReplyValue = '';
let studyInputImageValue = '';
let answerInputImageValue = '';
let pastePendingCount = 0;
let llmVisionMaxImagePx = DEFAULT_VISION_MAX_IMAGE_PX;
let currentQuestion = null;
let currentSelectionContext = null;
let learningProfile = readLearningProfile();
let knowledgeMapStage = '';
let lastKnowledgeMapPayload = null;
const pasteControllers = { study: null, answer: null };

function t(key, fallback) {
  return window.I18n && typeof window.I18n.t === 'function'
    ? window.I18n.t(key, fallback)
    : (fallback || key);
}

function tf(key, fallback, values = {}) {
  return window.I18n && typeof window.I18n.tf === 'function'
    ? window.I18n.tf(key, fallback, values)
    : (fallback || key).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
    ));
}

function readLearningProfile() {
  try { return JSON.parse(window.localStorage?.getItem(LEARNING_PROFILE_STORAGE_KEY) || '{}') || {}; } catch (error) { return {}; }
}

function writeLearningProfile(nextProfile) {
  learningProfile = { ...(nextProfile || {}) };
  try { window.localStorage?.setItem(LEARNING_PROFILE_STORAGE_KEY, JSON.stringify(learningProfile)); } catch (error) {}
}

function normalizeLearningStage(value) {
  const normalized = String(value || '').trim().toLowerCase().replaceAll('-', '_');
  return LEARNING_STAGE_OPTIONS.includes(normalized) ? normalized : '';
}

function learningStageLabel(value = learningProfile.stage) {
  const stage = normalizeLearningStage(value);
  return t(stage ? `ui.profile.stage.${stage}` : 'ui.profile.stage_unset', stage || 'Not set');
}

function syncLearningProfileUi() {
  const label = learningStageLabel();
  if (summaryStage) {
    summaryStage.textContent = label;
  }
  if (settingsLearningStage) {
    settingsLearningStage.value = normalizeLearningStage(learningProfile.stage);
  }
  if (settingsLearningProfileSummary) {
    settingsLearningProfileSummary.textContent = normalizeLearningStage(learningProfile.stage)
      ? tf('ui.profile.current_stage', 'Current stage: {stage}', { stage: label })
      : t('ui.profile.settings_summary', 'Choose a learning stage so the knowledge map and practice stay in range.');
  }
}

function learningProfileNeedsSetup() {
  return !normalizeLearningStage(learningProfile.stage) && !learningProfile.completed && !learningProfile.skipped;
}

function setLearningProfileStage(stage, options = {}) {
  const normalized = normalizeLearningStage(stage);
  writeLearningProfile({
    ...learningProfile,
    stage: normalized,
    skipped: Boolean(options.skipped),
    completed: Boolean(normalized) || Boolean(options.skipped),
  });
  syncLearningProfileUi();
  closeLearningProfileModal();
  renderFirstRunGuide(lastStatusPayload);
  if (surfaceDrawer?.dataset.surfaceId === 'knowledge-map' && surfaceDrawerBody) {
    surfaceDrawerBody.replaceChildren(renderKnowledgePanel());
  }
}

function setStatus(text) {
  statusLine.textContent = text;
}

// SECURITY: renderMathInText MUST HTML-escape all non-math text.
// LLM replies echo untrusted user input. Never replace innerHTML with
// a code path that skips escapeHTML().
function setReply(text) {
  const value = text || '';
  const replyPanel = replyText?.closest('.reply-panel');
  if (replyPanel) {
    replyPanel.hidden = value.trim().length === 0;
  }
  lastReplyValue = value;
  if (window.renderMathInText && typeof window.renderMathInText === 'function') {
    replyText.innerHTML = window.renderMathInText(value);
    if (!window.katex || typeof window.katex.renderToString !== 'function') {
      window.setTimeout(() => {
        if (
          lastReplyValue === value
          && window.katex
          && typeof window.katex.renderToString === 'function'
          && window.renderMathInText
          && typeof window.renderMathInText === 'function'
        ) {
          replyText.innerHTML = window.renderMathInText(value);
        }
      }, 100);
    }
  } else {
    replyText.textContent = value;
  }
}

function scrollReplyIntoView() {
  const replyPanel = replyText?.closest('.reply-panel');
  if (!replyPanel || replyPanel.hidden || typeof replyPanel.scrollIntoView !== 'function') {
    return;
  }
  replyPanel.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

function modeLabel(mode) {
  const known = ['companion', 'interactive', 'teaching'].includes(mode);
  return known ? t(`status.mode.${mode}`, mode) : mode;
}

function screenLabel(type) {
  const normalized = String(type || 'idle');
  const known = ['idle', 'reading', 'question', 'answering', 'review', 'notes', 'summary'].includes(normalized);
  return known ? t(`ui.status.screen.${normalized}`, normalized) : normalized;
}

function selectionReasonLabel(reason) {
  const normalized = String(reason || 'no_data');
  const known = ['retry', 'due_review', 'weak_topic', 'recommended', 'no_data', 'loading'].includes(normalized);
  return known ? t(`ui.practice.reason.${normalized}`, normalized) : normalized;
}

function setQuestionContext(data = {}) {
  currentSelectionContext = data && typeof data === 'object' ? data : null;
  const reason = String(currentSelectionContext?.selection_reason || 'no_data');
  if (questionContextCard) {
    questionContextCard.dataset.selectionReason = reason;
  }
  if (selectedTopicName) {
    selectedTopicName.textContent = currentSelectionContext?.selected_topic_name
      || currentSelectionContext?.selected_topic_id
      || t('ui.practice.no_data_title', 'Not enough study records yet');
  }
  if (selectionReason) {
    selectionReason.textContent = currentSelectionContext?.no_data
      ? t('ui.practice.no_data_body', 'Review cards, open the knowledge map, or save notes first; this view does not use manual or OCR text to make practice questions.')
      : tf('ui.practice.selection_reason_fmt', 'Reason: {reason}', { reason: selectionReasonLabel(reason) });
  }
}

function setGeneratedQuestion(data = {}) {
  currentQuestion = data && typeof data === 'object' ? data : null;
  if (questionText) {
    questionText.textContent = currentQuestion?.question || t('ui.practice.empty_question', 'Generate a practice question to begin.');
  }
  if (questionStatus) {
    questionStatus.textContent = compactText(currentQuestion?.question || '');
  }
  if (questionTopicMeta) {
    questionTopicMeta.textContent = currentQuestion?.selected_topic_name
      || currentQuestion?.selected_topic_id
      || currentQuestion?.topic
      || '-';
  }
  if (questionDifficultyMeta) {
    questionDifficultyMeta.textContent = currentQuestion?.difficulty
      ? tf('ui.practice.difficulty_fmt', 'Difficulty {difficulty}', { difficulty: currentQuestion.difficulty })
      : '-';
  }
  if (questionAttemptMeta) {
    questionAttemptMeta.textContent = currentQuestion?.attempt_id
      ? t('ui.practice.new_attempt', 'New attempt')
      : '-';
  }
  const hint = String(currentQuestion?.hint || '').trim();
  if (hintToggleBtn) {
    hintToggleBtn.hidden = !hint;
    hintToggleBtn.setAttribute('aria-expanded', 'false');
  }
  if (hintText) {
    hintText.textContent = hint;
    hintText.hidden = true;
  }
}

function clearFeedback() {
  if (feedbackPanel) feedbackPanel.hidden = true;
  if (feedbackText) feedbackText.textContent = '';
  if (masteryDeltaText) masteryDeltaText.textContent = '';
  if (evaluationStatus) evaluationStatus.textContent = t('ui.status.ready', 'Ready');
}

function renderFeedback(data = {}) {
  if (evaluationStatus) {
    evaluationStatus.textContent = data.verdict ? `${data.verdict}${Number.isFinite(Number(data.score)) ? ` / ${data.score}` : ''}` : '-';
  }
  const masteryBefore = Number(data.mastery_before);
  const masteryAfter = Number(data.mastery_after);
  if (masteryDeltaText) {
    masteryDeltaText.textContent = Number.isFinite(masteryBefore) && Number.isFinite(masteryAfter)
      ? tf('ui.practice.mastery_delta_fmt', 'Mastery {before} -> {after}', {
        before: masteryBefore.toFixed(2),
        after: masteryAfter.toFixed(2),
      })
      : '';
  }
  const lines = [
    data.feedback || data.reply || '',
    Array.isArray(data.covered_points) && data.covered_points.length
      ? `${t('ui.practice.covered_points', 'Covered')}: ${data.covered_points.join(', ')}`
      : '',
    Array.isArray(data.missing_points) && data.missing_points.length
      ? `${t('ui.practice.missing_points', 'Missing')}: ${data.missing_points.join(', ')}`
      : '',
    data.reference_answer ? `${t('ui.practice.reference_answer', 'Reference')}: ${data.reference_answer}` : '',
    data.next_action ? `${t('ui.practice.next_action', 'Next')}: ${data.next_action}` : '',
  ].filter(Boolean);
  if (feedbackText) {
    feedbackText.textContent = lines.join('\n');
  }
  if (feedbackPanel) {
    feedbackPanel.hidden = lines.length === 0 && !masteryDeltaText?.textContent;
  }
}

function formatPluginError(error) {
  if (error instanceof Error && error.message === 'plugin_call_timeout') {
    return t('ui.error.plugin_call_timeout', 'Plugin call timed out');
  }
  if (error instanceof Error && error.message === 'run_id_missing') {
    return t('ui.error.run_id_missing', 'Run id missing');
  }
  if (error instanceof Error && error.message === 'plugin_call_failed') {
    return t('ui.error.plugin_call_failed', 'Plugin call failed');
  }
  return error instanceof Error ? error.message : String(error);
}

function setPanelBusy(busy) {
  const mainView = document.getElementById('mainView');
  if (mainView) {
    mainView.dataset.busy = busy ? 'true' : 'false';
  }
}

function setPastePending(pending) {
  pastePendingCount = Math.max(0, pastePendingCount + (pending ? 1 : -1));
  setPanelBusy(pastePendingCount > 0);
}

function setPasteError(target, message) {
  if (!target) {
    return;
  }
  target.textContent = message || '';
  target.hidden = !message;
}

function setImagePreview(kind, dataUrl) {
  const isAnswer = kind === 'answer';
  const preview = isAnswer ? answerInputImagePreview : studyInputImagePreview;
  const image = isAnswer ? answerInputImage : studyInputImage;
  if (isAnswer) {
    answerInputImageValue = dataUrl || '';
  } else {
    studyInputImageValue = dataUrl || '';
  }
  if (image) {
    if (dataUrl) {
      image.src = dataUrl;
    } else {
      image.removeAttribute('src');
    }
  }
  if (preview) {
    preview.hidden = !dataUrl;
  }
}

function normalizeVisionMaxImagePx(value) {
  const parsed = Math.round(Number(value));
  if (!Number.isFinite(parsed)) {
    return DEFAULT_VISION_MAX_IMAGE_PX;
  }
  return Math.max(64, Math.min(4096, parsed));
}

function applyVisionMaxImagePx(value) {
  llmVisionMaxImagePx = normalizeVisionMaxImagePx(value);
}

function applyRuntimeConfig(data) {
  const config = data && typeof data.config === 'object' ? data.config : null;
  if (config && Object.prototype.hasOwnProperty.call(config, 'llm_vision_max_image_px')) {
    applyVisionMaxImagePx(config.llm_vision_max_image_px);
  }
}

function loadImageFromBlob(blob, signal) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const image = new Image();
    let settled = false;
    const cleanup = () => {
      settled = true;
      clearTimeout(timer);
      URL.revokeObjectURL(url);
      image.onload = null;
      image.onerror = null;
    };
    const timer = setTimeout(() => {
      if (settled) return;
      cleanup();
      reject(new Error('Image load timeout'));
    }, LOAD_IMAGE_TIMEOUT_MS);
    const abort = () => {
      if (settled) return;
      cleanup();
      resolve(null);
    };
    if (signal?.aborted) {
      abort();
      return;
    }
    signal?.addEventListener?.('abort', abort, { once: true });
    image.onload = () => {
      if (settled) return;
      cleanup();
      resolve(image);
    };
    image.onerror = () => {
      if (settled) return;
      cleanup();
      reject(new Error('Image load failed'));
    };
    image.src = url;
  });
}

async function compressImageForStudy(blob, signal) {
  try {
    const image = await loadImageFromBlob(blob, signal);
    if (!image || signal?.aborted) {
      return null;
    }
    const sourceWidth = image.naturalWidth || image.width || 0;
    const sourceHeight = image.naturalHeight || image.height || 0;
    if (!sourceWidth || !sourceHeight) {
      return null;
    }
    const scale = Math.min(1, llmVisionMaxImagePx / Math.max(sourceWidth, sourceHeight));
    const width = Math.max(1, Math.round(sourceWidth * scale));
    const height = Math.max(1, Math.round(sourceHeight * scale));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      throw new Error('Canvas 2D context is unavailable');
    }
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(image, 0, 0, width, height);
    let url = canvas.toDataURL('image/jpeg', 0.82);
    if (url.length > TARGET_DATA_URL_LENGTH) {
      url = canvas.toDataURL('image/jpeg', 0.56);
    }
    if (url.length > TARGET_DATA_URL_LENGTH) {
      url = canvas.toDataURL('image/jpeg', 0.3);
    }
    return url.length > TARGET_DATA_URL_LENGTH ? null : url;
  } catch (error) {
    console.warn('study static image paste failed', error);
    return null;
  }
}

function insertPastedText(textarea, text) {
  if (!textarea || !text) {
    return;
  }
  const start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? start;
  textarea.value = textarea.value.slice(0, start) + text + textarea.value.slice(end);
  requestAnimationFrame(() => {
    textarea.focus();
    textarea.setSelectionRange(start + text.length, start + text.length);
  });
}

function createImagePasteHandler(options) {
  const { textarea, kind, errorTarget } = options;
  return async function handleImagePaste(event) {
    const items = event.clipboardData?.items;
    if (!items) {
      return;
    }
    const itemList = Array.from(items);
    if (!itemList.some((item) => item.type.startsWith('image/'))) {
      return;
    }
    event.preventDefault();
    pasteControllers[kind]?.abort();
    const controller = new AbortController();
    pasteControllers[kind] = controller;
    setPasteError(errorTarget, '');
    setPastePending(true);
    try {
      for (const item of itemList) {
        if (item.type.startsWith('image/')) {
          if (!SUPPORTED_PASTE_IMAGE_TYPES.has(item.type)) {
            setPasteError(errorTarget, t('ui.error.image_paste_unsupported', 'Only JPEG and PNG images can be pasted here.'));
            continue;
          }
          const blob = item.getAsFile();
          if (!blob) {
            setPasteError(errorTarget, t('ui.error.image_paste_failed', 'Image paste failed. Please try a smaller JPEG or PNG image.'));
            continue;
          }
          const image = await compressImageForStudy(blob, controller.signal);
          if (controller.signal.aborted) {
            return;
          }
          if (!image) {
            setPasteError(errorTarget, t('ui.error.image_paste_failed', 'Image paste failed. Please try a smaller JPEG or PNG image.'));
            continue;
          }
          setImagePreview(kind, image);
          setPasteError(errorTarget, '');
        } else if (item.type === 'text/plain') {
          item.getAsString((pastedText) => {
            if (!controller.signal.aborted) {
              insertPastedText(textarea, pastedText);
            }
          });
        }
      }
    } finally {
      if (pasteControllers[kind] === controller) {
        pasteControllers[kind] = null;
      }
      setPastePending(false);
    }
  };
}

function compactText(value, fallback = '-') {
  const text = String(value || '').trim();
  if (!text) {
    return fallback;
  }
  return text.length > 72 ? `${text.slice(0, 72)}...` : text;
}

function buildFirstRunSteps() {
  return [
    {
      label: t('ui.onboarding.step.mode.label', 'Step 1'),
      title: t('ui.onboarding.step.mode.title', 'Choose a default mode'),
      body: t('ui.onboarding.step.mode.body', 'Companion, Interactive, and Teaching tune how the study companion responds.'),
    },
    {
      label: t('ui.onboarding.step.ocr.label', 'Step 2'),
      title: t('ui.onboarding.step.ocr.title', 'Check OCR capture'),
      body: t('ui.onboarding.step.ocr.body', 'Use OCR when you want the companion to read the current learning material.'),
    },
    {
      label: t('ui.onboarding.step.goal.label', 'Step 3'),
      title: t('ui.onboarding.step.goal.title', 'Bind a study goal'),
      body: t('ui.onboarding.step.goal.body', 'Review cards, focus sessions, and summaries stay useful when tied to a goal.'),
    },
  ];
}

function closeLearningProfileModal() {
  if (!learningProfileModal) return;
  learningProfileModal.hidden = true;
  learningProfileModal.setAttribute('aria-hidden', 'true');
}

function buildLearningProfileModal() {
  const modal = drawerElement('aside', 'learning-profile-modal');
  modal.id = 'learningProfileModal';
  modal.hidden = true;
  modal.setAttribute('aria-hidden', 'true');
  modal.setAttribute('aria-labelledby', 'learningProfileModalTitle');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('role', 'dialog');
  const panel = drawerElement('div', 'learning-profile-modal__panel');
  const header = drawerElement('header', 'learning-profile-modal__header');
  const mark = drawerElement('span', 'learning-profile-modal__mark', 'N');
  mark.setAttribute('aria-hidden', 'true');
  const titleWrap = drawerElement('div');
  const eyebrow = drawerElement('span', 'diagnosis-label', t('ui.onboarding.label', 'First launch'));
  const title = drawerElement('h2', '', t('ui.profile.prompt_title', 'Choose your learning stage'));
  title.id = 'learningProfileModalTitle';
  titleWrap.append(
    eyebrow,
    title,
    drawerElement('p', 'learning-profile-modal__body', t('ui.profile.prompt_body', 'The selected stage is used as the default scope for knowledge maps and adaptive practice.')),
  );
  const skip = drawerElement('button', 'button button-secondary', t('ui.button.skip', 'Skip'));
  skip.type = 'button';
  skip.addEventListener('click', () => setLearningProfileStage('', { skipped: true }));
  header.append(mark, titleWrap, skip);
  panel.appendChild(header);
  const actions = drawerElement('div', 'learning-stage-actions learning-stage-actions--modal');
  [
    ['junior_high', '01'],
    ['senior_high', '02'],
    ['college', '03'],
  ].forEach(([stage, order]) => {
    const button = drawerElement('button', 'learning-stage-card');
    button.type = 'button';
    button.dataset.stage = stage;
    button.append(
      drawerElement('span', 'learning-stage-card__order', order),
      drawerElement('strong', '', learningStageLabel(stage)),
    );
    button.addEventListener('click', () => setLearningProfileStage(stage));
    actions.appendChild(button);
  });
  const more = drawerElement('button', 'learning-stage-more', t('ui.profile.more_stages', 'More stages'));
  more.type = 'button';
  more.addEventListener('click', () => {
    closeLearningProfileModal();
    setAdvancedSettingsOpen(true);
    setSettingsTab('study', { focus: true });
    settingsLearningStage?.focus?.();
  });
  actions.appendChild(more);
  panel.appendChild(actions);
  modal.appendChild(panel);
  return modal;
}

function openLearningProfileModal() {
  if (!learningProfileModal) {
    learningProfileModal = buildLearningProfileModal();
    document.body.appendChild(learningProfileModal);
  }
  const wasHidden = learningProfileModal.hidden;
  learningProfileModal.hidden = false;
  learningProfileModal.setAttribute('aria-hidden', 'false');
  if (wasHidden) {
    learningProfileModal.querySelector('[data-stage]')?.focus?.();
  }
}

function renderFirstRunGuide() {
  if (!firstRunGuide || !firstRunSteps) {
    return;
  }
  const profileNeedsSetup = learningProfileNeedsSetup();
  const shouldShow = profileNeedsSetup && !firstRunDismissed && !advancedSettingsOpen;
  firstRunGuide.hidden = !shouldShow;
  if (shouldShow) {
    openLearningProfileModal();
  } else {
    closeLearningProfileModal();
  }
  if (!shouldShow) {
    return;
  }
  firstRunSteps.textContent = '';
  buildFirstRunSteps().forEach((step, index) => {
    const item = document.createElement('article');
    item.className = 'first-run-step';
    item.setAttribute('data-first-run-step', String(index + 1));
    const label = document.createElement('span');
    label.textContent = step.label;
    const title = document.createElement('strong');
    title.textContent = step.title;
    const body = document.createElement('p');
    body.textContent = step.body;
    item.append(label, title, body);
    firstRunSteps.appendChild(item);
  });
}

function dependencyReady(status) {
  if (!status || typeof status !== 'object') {
    return false;
  }
  if (status.available === true || status.ok === true || status.status === 'available') {
    return true;
  }
  if (status.installed === true || status.state === 'ready') {
    return true;
  }
  return false;
}

function countFromSummary(summary = {}, keys = []) {
  for (const key of keys) {
    const value = Number(summary[key]);
    if (Number.isFinite(value)) {
      return value;
    }
  }
  return 0;
}

function formatMinuteCount(value) {
  const minutes = Math.max(0, Number(value) || 0);
  const label = Number.isInteger(minutes) ? String(minutes) : minutes.toFixed(1);
  return `${label} min`;
}

function goalProgressFromHabit(habit = {}) {
  const summary = habit.summary || {};
  const completed = countFromSummary(summary, ['completed_goal_count', 'completed_goals', 'completed']);
  const total = countFromSummary(summary, ['goal_count', 'total_goal_count', 'goals', 'total_goals'])
    || (Array.isArray(habit.goals) ? habit.goals.length : 0);
  return { completed: Math.max(0, completed), total: Math.max(0, total) };
}

function buildDiagnosis(data = {}) {
  const dependencies = data.dependencies || {};
  const dependencyValues = Object.values(dependencies).filter((value) => value && typeof value === 'object');
  const llm = data.llm || data.llm_status || {};
  const llmStatus = String(llm.status || llm.state || '').toLowerCase();
  const llmError = data.llm_available === false || llm.available === false || llm.ok === false || ['error', 'failed', 'unavailable'].includes(llmStatus);
  const errorBody = data.last_error || llm.message || llm.error || llm.reason;
  const hasDependencyStatus = dependencyValues.length > 0;
  const dependenciesReady = hasDependencyStatus && dependencyValues.every(dependencyReady);
  const topicCount = countFromSummary(data.knowledge_summary || {}, ['topic_count', 'topics', 'node_count', 'nodes']);
  const hasKnowledge = topicCount > 0 || (Array.isArray(data.mastery_overview) && data.mastery_overview.length > 0);
  if (errorBody || data.status === 'error' || llmError) {
    return {
      severity: 'error',
      title: t('ui.diagnosis.error.title', 'Attention needed'),
      body: errorBody || t('ui.diagnosis.error.body', 'Study companion reported an error.'),
    };
  }
  if (dependenciesReady && hasKnowledge) {
    return {
      severity: 'ok',
      title: t('ui.diagnosis.ok.title', 'Ready for study'),
      body: tf('ui.diagnosis.ok.body', '{count} knowledge topics loaded and OCR dependencies are ready.', { count: topicCount }),
    };
  }
  if (hasDependencyStatus || data.status === 'ready') {
    return {
      severity: 'warning',
      title: t('ui.diagnosis.warning.title', 'Setup can be improved'),
      body: t('ui.diagnosis.warning.body', 'Check OCR dependencies or load knowledge topics for better study guidance.'),
    };
  }
  return { severity: 'info', title: t('ui.diagnosis.info.title', 'Waiting for status'), body: t('ui.diagnosis.info.body', 'Refresh status to inspect OCR, LLM, and study data.') };
}

function renderDiagnosis(data = {}) {
  if (!primaryDiagnosis || !diagnosisTitle || !diagnosisBody) {
    return;
  }
  const diagnosis = buildDiagnosis(data);
  const prefix = diagnosis.severity === 'ok'
    ? '\u2713'
    : (diagnosis.severity === 'error' ? '\u26A0' : (diagnosis.severity === 'warning' ? '!' : 'i'));
  primaryDiagnosis.dataset.severity = diagnosis.severity;
  diagnosisTitle.textContent = `${prefix} ${diagnosis.title}`;
  diagnosisBody.textContent = diagnosis.body;
}

function pomodoroStateLabel(value) {
  const normalized = String(value || 'idle').trim().toLowerCase();
  const labels = {
    idle: ['ui.status.pomodoro.idle', 'Idle'],
    focusing: ['ui.status.pomodoro.focusing', 'Focusing'],
    paused: ['ui.status.pomodoro.paused', 'Paused'],
    short_break: ['ui.status.pomodoro.short_break', 'Short break'],
    long_break: ['ui.status.pomodoro.long_break', 'Long break'],
    cancelled: ['ui.status.pomodoro.cancelled', 'Stopped'],
    completed: ['ui.status.pomodoro.completed', 'Completed'],
  };
  const pair = labels[normalized];
  return pair ? t(pair[0], pair[1]) : normalized;
}

function updateStudySummaries(data = {}) {
  const habit = data.habit || {};
  const pomodoro = habit.pomodoro || {};
  if (quickFocusState) {
    quickFocusState.textContent = pomodoroStateLabel(pomodoro.state);
  }
  if (quickCheckinStatus) {
    const checkin = habit.checkin || {};
    quickCheckinStatus.textContent = checkin.checked_in
      ? t('ui.status.enabled', 'Enabled')
      : t('ui.status.disabled', 'Disabled');
  }
  const deps = data.dependencies || {};
  const dependencyCount = Object.values(deps).filter((value) => value && typeof value === 'object').length;
  const readyCount = Object.values(deps).filter(dependencyReady).length;
  const knowledge = data.knowledge_summary || {};
  const topicCount = countFromSummary(knowledge, ['topic_count', 'topics', 'node_count', 'nodes']);
  const edgeCount = countFromSummary(knowledge, ['edge_count', 'edges']);
  const memoryDeck = data.memory_deck || {};
  const cardCount = Number.isFinite(Number(memoryDeck.card_count)) ? Number(memoryDeck.card_count) : 0;
  const dueCount = Number.isFinite(Number(memoryDeck.due_count)) ? Number(memoryDeck.due_count) : 0;
  const setText = (id, value) => {
    const node = document.getElementById(id);
    if (node) {
      node.textContent = value;
    }
  };
  setText('settingsOcrSummary', dependencyCount
    ? tf('ui.settings.ocr.ready_summary', '{ready}/{total} OCR dependencies ready', { ready: readyCount, total: dependencyCount })
    : t('ui.settings.ocr.no_status', 'Dependency status is not loaded yet.'));
  setText('settingsDependencySummary', dependencyCount
    ? tf('ui.settings.dependencies.ready_summary', '{ready}/{total} runtime dependencies available', { ready: readyCount, total: dependencyCount })
    : t('ui.settings.dependencies.no_status', 'Refresh status to inspect OCR backends.'));
  setText('settingsKnowledgeSummary', topicCount
    ? tf('ui.settings.knowledge.loaded_summary', '{topics} topics and {edges} edges loaded.', { topics: topicCount, edges: edgeCount })
    : t('ui.settings.knowledge.empty_summary', 'Knowledge map has no loaded topics yet.'));
  setText('settingsMemorySummary', tf('ui.settings.memory.loaded_summary', '{cards} cards / {due} due reviews.', { cards: cardCount, due: dueCount }));
  setText('settingsCheckinSummary', quickCheckinStatus ? quickCheckinStatus.textContent : t('ui.status.pending', 'Pending'));
  setText('settingsPomodoroSummary', quickFocusState ? quickFocusState.textContent : t('ui.status.screen.idle', 'Idle'));
}

function updateModeIndicator() {
  if (!modeSwitch) {
    return;
  }
  modeSwitch.dataset.active = currentMode;
  if (modeSwitch.offsetParent === null) {
    return;
  }
  const activeButton = modeButtons.find((button) => button.getAttribute('data-mode') === currentMode);
  if (!activeButton) {
    return;
  }
  const switchRect = modeSwitch.getBoundingClientRect();
  const buttonRect = activeButton.getBoundingClientRect();
  if (buttonRect.width > 0) {
    modeSwitch.style.setProperty('--indicator-left', `${Math.max(0, buttonRect.left - switchRect.left)}px`);
    modeSwitch.style.setProperty('--indicator-width', `${buttonRect.width}px`);
  }
  modeSwitch.setAttribute('data-ready', 'true');
}

function prefersReducedMotion() {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function scheduleModeIndicatorUpdate() {
  if (prefersReducedMotion()) {
    updateModeIndicator();
    return;
  }
  if (typeof window.requestAnimationFrame === 'function') {
    window.requestAnimationFrame(updateModeIndicator);
    return;
  }
  updateModeIndicator();
}

function setModeButtons(mode, disabled = false) {
  currentMode = String(mode || 'companion');
  if (summaryMode) {
    summaryMode.textContent = modeLabel(currentMode);
  }
  if (modeSwitch) {
    modeSwitch.dataset.active = currentMode;
  }
  if (modeSelect) {
    modeSelect.value = currentMode;
    modeSelect.disabled = disabled;
  }
  modeButtons.forEach((button) => {
    const pressed = button.getAttribute('data-mode') === currentMode;
    button.disabled = disabled;
    button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    button.classList.toggle('is-active', pressed);
    button.classList.toggle('active', pressed);
  });
  updateModeIndicator();
  scheduleModeIndicatorUpdate();
}

function setStudyState(data = {}) {
  const habit = data.habit || {};
  const habitSummary = habit.summary || {};
  const checkin = habit.checkin || {};
  if (summaryDuration) {
    const focusMinutes = Number.isFinite(Number(habitSummary.total_focus_minutes)) ? habitSummary.total_focus_minutes : checkin.total_focus_minutes;
    summaryDuration.textContent = formatMinuteCount(focusMinutes);
  }
  if (summaryGoal) {
    const progress = goalProgressFromHabit(habit);
    summaryGoal.textContent = `${progress.completed}/${progress.total}`;
  }
  const classification = data.screen_classification || {};
  const screenValue = classification.screen_type || data.screen_type || 'idle';
  if (screenType) {
    screenType.textContent = screenLabel(screenValue);
    if (classification.reason) {
      screenType.title = classification.reason;
    }
  }
  const evaluation = data.last_answer_evaluation || {};
  if (evaluationStatus) {
    const verdict = evaluation.verdict ? String(evaluation.verdict) : '';
    const score = Number.isFinite(Number(evaluation.score)) ? ` / ${evaluation.score}` : '';
    evaluationStatus.textContent = verdict ? `${verdict}${score}` : '-';
  }
  setMemoryDeckState(data.memory_deck || {});
}

function setMemoryDeckState(deck = {}) {
  const dueCards = Array.isArray(deck.due_cards)
    ? deck.due_cards
    : (Array.isArray(deck.due_reviews)
      ? deck.due_reviews
      : (Array.isArray(deck.cards) ? deck.cards.filter((item) => item && item.is_due) : []));
  const cards = Array.isArray(deck.cards) ? deck.cards : [];
  const cardCount = Number.isFinite(Number(deck.card_count))
    ? Number(deck.card_count)
    : (Number.isFinite(Number(deck.item_count)) ? Number(deck.item_count) : cards.length);
  const dueCount = Number.isFinite(Number(deck.due_count)) ? Number(deck.due_count) : dueCards.length;
  currentMemoryCard = dueCards[0] || null;
  if (quickReviewCount) {
    quickReviewCount.textContent = String(dueCount);
  }
  if (memoryDeckStatus) {
    memoryDeckStatus.textContent = tf('ui.memory.status', '{card_count} cards / {due_count} due', {
      card_count: cardCount,
      due_count: dueCount,
    });
  }
  if (memoryDueCard) {
    if (currentMemoryCard) {
      delete memoryDueCard.dataset.empty;
      const front = compactText(
        currentMemoryCard.front || currentMemoryCard.item?.prompt,
        currentMemoryCard.topic_id || currentMemoryCard.item_id || '-',
      );
      const back = compactText(currentMemoryCard.back || currentMemoryCard.item?.answer, '-');
      const retention = Number.isFinite(Number(currentMemoryCard.retrievability))
        ? `R ${(Number(currentMemoryCard.retrievability) * 100).toFixed(0)}%`
        : '';
      memoryDueCard.textContent = [front, back, retention].filter(Boolean).join('\n\n');
    } else {
      memoryDueCard.dataset.empty = 'true';
      memoryDueCard.textContent = t('ui.memory.empty_due', 'No due memory cards');
    }
  }
  memoryReviewButtons.forEach((button) => {
    button.disabled = !currentMemoryCard;
  });
}

function setStatusLine(data) {
  lastStatusPayload = data || {};
  const statusValue = data.status || 'unknown';
  const modeValue = String(data.active_mode || data.mode || 'companion');
  const statusLabel = t(`status.state.${statusValue}`, statusValue);
  setStatus(`${statusLabel} / ${modeLabel(modeValue)}`);
  syncLearningProfileUi();
  renderDiagnosis(data);
  renderFirstRunGuide(data);
  updateStudySummaries(data);
  setModeButtons(modeValue, false);
  setStudyState(data);
}

function setAdvancedSettingsOpen(open) {
  advancedSettingsOpen = Boolean(open);
  if (advancedSettings) {
    advancedSettings.hidden = !advancedSettingsOpen;
  }
  if (advancedToggleBtn) {
    advancedToggleBtn.setAttribute('aria-expanded', advancedSettingsOpen ? 'true' : 'false');
  }
  if (advancedSettingsOpen) {
    loadSettingsConfig().catch(() => setSettingsConfigStatus('ui.status.config_load_failed', 'Could not load settings'));
  }
  renderFirstRunGuide(lastStatusPayload);
}

function setSettingsTab(tabId, options = {}) {
  settingsTabs.forEach((tab) => {
    const selected = tab.getAttribute('data-settings-tab') === tabId;
    tab.setAttribute('aria-selected', selected ? 'true' : 'false');
    tab.setAttribute('tabindex', selected ? '0' : '-1');
    if (selected && options.focus) {
      tab.focus();
    }
  });
  settingsTabPanels.forEach((panel) => {
    panel.hidden = panel.getAttribute('data-settings-tab-panel') !== tabId;
  });
}

function handleSettingsTabKeydown(event) {
  const currentIndex = settingsTabs.indexOf(event.currentTarget);
  if (currentIndex < 0) return;
  if (event.key === 'Tab' && !event.shiftKey) {
    const panel = settingsTabPanels.find((item) => item.getAttribute('data-settings-tab-panel') === event.currentTarget.getAttribute('data-settings-tab'));
    if (panel) { event.preventDefault(); panel.focus(); } return;
  }
  let nextIndex = currentIndex;
  if (event.key === 'ArrowRight') {
    nextIndex = (currentIndex + 1) % settingsTabs.length;
  } else if (event.key === 'ArrowLeft') {
    nextIndex = (currentIndex - 1 + settingsTabs.length) % settingsTabs.length;
  } else if (event.key === 'Home') {
    nextIndex = 0;
  } else if (event.key === 'End') {
    nextIndex = settingsTabs.length - 1;
  } else return;
  event.preventDefault();
  setSettingsTab(settingsTabs[nextIndex].getAttribute('data-settings-tab'), { focus: true });
}

function hostedSurfaceLabel(surfaceId) {
  const labels = {
    'due-review-panel': t('ui.feature.review.title', 'Review'),
    'knowledge-map': t('ui.feature.knowledge.title', 'Knowledge Map'),
    'pomodoro-panel': t('ui.feature.pomodoro.title', 'Pomodoro'),
    'habit-dashboard': t('ui.feature.checkin.title', 'Check-in'),
    'note-exporter': t('ui.feature.export.title', 'Export'),
    'memory-deck-list': t('ui.button.open_decks', 'Open Decks'),
    'memory-importer': t('ui.button.import_memory', 'Import Cards'),
    'knowledge-contribution-settings': t('ui.button.contribution_settings', 'Contribution Settings'),
    'daily-goal-editor': t('ui.button.edit_daily_goal', 'Edit Daily Goal'),
    'session-summary': t('ui.button.session_summary', 'Session Summary'),
  };
  return labels[surfaceId] || surfaceId;
}

function setActiveFeature(action) {
  featureActionButtons.forEach((button) => {
    button.classList.toggle('is-active', button.getAttribute('data-feature-action') === action);
  });
}

function focusAfterScroll(target, focusTarget) {
  if (!target) return;
  if (typeof target.scrollIntoView === 'function') {
    target.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
  if (focusTarget && typeof focusTarget.focus === 'function') {
    window.setTimeout(() => focusTarget.focus(), 220);
  }
}

function closeSurfaceDrawer() {
  if (!surfaceDrawer) return;
  mapRequestId += 1;
  window.StudyCompanionSurfacePanels?.close?.();
  surfaceDrawer.dataset.open = 'false';
  surfaceDrawer.setAttribute('aria-hidden', 'true');
}

function drawerElement(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text) {
    node.textContent = text;
  }
  return node;
}

function surfacePanel(surfaceId, subtitle = '') {
  const root = drawerElement('div', 'study-panel surface-shell');
  const header = drawerElement('header', 'study-panel__header');
  const titleWrap = drawerElement('div');
  titleWrap.append(
    drawerElement('h1', '', hostedSurfaceLabel(surfaceId)),
    drawerElement('span', '', subtitle || t('ui.status.ready', 'Ready')),
  );
  header.appendChild(titleWrap);
  root.appendChild(header);
  return root;
}

function appendPanelState(parent, label, value) {
  const item = drawerElement('div');
  item.append(
    drawerElement('span', '', label),
    drawerElement('strong', '', value),
  );
  parent.appendChild(item);
}

function renderDrawerActions(actions = []) {
  const row = drawerElement('div', 'study-panel__actions');
  actions.forEach((action) => {
    const button = drawerElement('button', action.primary ? 'button button-primary' : 'button button-secondary', action.label);
    button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await action.handler();
      } catch (error) {
        setStatus(t('ui.status.error', 'Error'));
        setReply(formatPluginError(error));
      } finally {
        button.disabled = false;
      }
    });
    row.appendChild(button);
  });
  return row;
}

function masteryLevelForPanel(item = {}) {
  if (item.weak) {
    return 'weak';
  }
  const level = String(item.level || '').toLowerCase();
  if (['new', 'weak', 'progress', 'good', 'mastered'].includes(level)) {
    return level;
  }
  const mastery = Number(item.mastery);
  if (!Number.isFinite(mastery)) return 'new';
  if (mastery >= 0.85) return 'mastered';
  if (mastery >= 0.6) return 'good';
  if (mastery >= 0.3) return 'progress';
  return 'weak';
}

function stageValueFromNode(node = {}) {
  const raw = String(
    node.grade_level
    || node.education_level
    || node.stage
    || node.course_level
    || '',
  ).trim().toLowerCase().replaceAll('-', '_');
  const aliases = {
    elementary: 'primary',
    primary_school: 'primary',
    middle_school: 'junior_high',
    junior: 'junior_high',
    high_school: 'senior_high',
    senior: 'senior_high',
    university: 'college',
    undergraduate: 'college',
    graduate: 'postgraduate',
    master: 'postgraduate',
  };
  return normalizeLearningStage(aliases[raw] || raw);
}

function knowledgeStageLabel(stage) {
  const normalized = normalizeLearningStage(stage);
  return normalized ? learningStageLabel(normalized) : t('ui.profile.stage_uncategorized', 'Uncategorized');
}

function knowledgeMapActiveStage() {
  return knowledgeMapStage || normalizeLearningStage(learningProfile.stage) || 'all';
}

function knowledgeMapRangeLabel(stage = knowledgeMapActiveStage()) {
  return stage === 'all'
    ? t('ui.knowledge.scope_all', 'All stages')
    : knowledgeStageLabel(stage);
}

function visibleKnowledgeNodes(nodes = [], stage = knowledgeMapActiveStage()) {
  if (stage === 'all') return nodes;
  return nodes.filter((node) => {
    const nodeStage = stageValueFromNode(node);
    return nodeStage === stage || !nodeStage;
  });
}

function visibleKnowledgeEdges(edges = [], nodes = [], stage = knowledgeMapActiveStage()) {
  if (stage === 'all') return edges;
  const visibleIds = new Set(nodes.map((node) => String(node.id || '')));
  return edges.filter((edge) => visibleIds.has(String(edge.from || '')) && visibleIds.has(String(edge.to || '')));
}

function renderKnowledgeStageSelector(nodes = []) {
  const root = drawerElement('section', 'knowledge-stage-selector');
  root.appendChild(drawerElement('span', '', t('ui.knowledge.scope_label', 'Graph range')));
  const actions = drawerElement('div', 'knowledge-stage-selector__actions');
  const stages = [...LEARNING_STAGE_OPTIONS.filter((stage) => stage !== 'custom'), 'all'];
  const activeStage = knowledgeMapActiveStage();
  const counts = new Map();
  nodes.forEach((node) => {
    const stage = stageValueFromNode(node) || '';
    counts.set(stage, (counts.get(stage) || 0) + 1);
  });
  stages.forEach((stage) => {
    const label = stage === 'all' ? t('ui.knowledge.scope_all', 'All stages') : learningStageLabel(stage);
    const count = stage === 'all' ? nodes.length : (counts.get(stage) || 0);
    const button = drawerElement('button', 'knowledge-stage-option', count ? `${label} ${count}` : label);
    button.type = 'button';
    button.dataset.stage = stage;
    button.setAttribute('aria-pressed', stage === activeStage ? 'true' : 'false');
    button.addEventListener('click', () => {
      knowledgeMapStage = stage === normalizeLearningStage(learningProfile.stage) ? '' : stage;
      if (surfaceDrawerBody) {
        surfaceDrawerBody.replaceChildren(renderKnowledgePanel(lastKnowledgeMapPayload || lastStatusPayload));
      }
    });
    actions.appendChild(button);
  });
  root.appendChild(actions);
  return root;
}

function renderKnowledgeNodes(nodes = []) {
  const root = drawerElement('div', 'knowledge-stage-groups');
  const groups = new Map();
  nodes.slice(0, 80).forEach((node) => {
    const stage = stageValueFromNode(node);
    groups.set(stage, [...(groups.get(stage) || []), node]);
  });
  const selectedStage = normalizeLearningStage(learningProfile.stage);
  [...groups.entries()].sort(([stageA], [stageB]) => (
    stageA === selectedStage ? -1 : stageB === selectedStage ? 1 : [...LEARNING_STAGE_OPTIONS, ''].indexOf(stageA) - [...LEARNING_STAGE_OPTIONS, ''].indexOf(stageB)
  )).forEach(([stage, items]) => {
    const section = drawerElement('section', 'knowledge-stage-group');
    section.dataset.stage = stage || 'uncategorized';
    if (stage === selectedStage) section.dataset.selected = 'true';
    const list = drawerElement('div', 'study-panel__actions');
    items.slice(0, 24).forEach((node) => {
      const item = drawerElement('button', 'knowledge-node');
      item.type = 'button';
      item.dataset.mastery = masteryLevelForPanel(node);
      const mastery = Number(node.mastery);
      const masteryText = Number.isFinite(mastery) ? ` ${Math.round(mastery * 100)}%` : '';
      const subject = node.subject ? ` · ${node.subject}` : '';
      item.textContent = `${node.label || node.name || node.topic_name || node.topic_id || node.id || '-'}${subject}${masteryText}`;
      list.appendChild(item);
    });
    if (items.length > 24) {
      list.appendChild(drawerElement('span', 'knowledge-edge-more', tf('ui.knowledge.edge_more', '+ {count} more', { count: items.length - 24 })));
    }
    section.append(drawerElement('h3', '', `${knowledgeStageLabel(stage)} · ${items.length}`), list);
    root.appendChild(section);
  });
  return root;
}

function knowledgeNodeLabel(node) {
  return String(node?.label || node?.name || node?.topic_name || node?.topic_id || node?.id || '-');
}

function knowledgeRelationLabel(relation) {
  const normalized = String(relation || 'related').trim().toLowerCase();
  if (normalized === 'prerequisite') return t('ui.knowledge.edge_relation.prerequisite', 'Prerequisite');
  if (normalized === 'related') return t('ui.knowledge.edge_relation.related', 'Related');
  if (normalized === 'similar') return t('ui.knowledge.edge_relation.similar', 'Similar');
  if (normalized === 'extends') return t('ui.knowledge.edge_relation.extends', 'Extends');
  if (normalized === 'next') return t('ui.knowledge.edge_relation.next', 'Next');
  if (normalized === 'nearby') return t('ui.knowledge.edge_relation.nearby', 'Nearby');
  return normalized || t('ui.knowledge.edge_relation.related', 'Related');
}

function renderKnowledgeEdges(nodes = [], edges = [], edgeCount = 0, topicCount = 0) {
  const labelById = new Map(nodes.map((node) => [String(node.id || ''), knowledgeNodeLabel(node)]));
  const groups = new Map();
  edges.slice(0, 80).forEach((edge) => {
    const fromId = String(edge.from || '').trim();
    const toId = String(edge.to || '').trim();
    if (!fromId && !toId) return;
    const groupKey = fromId || '-';
    const group = groups.get(groupKey) || {
      from: labelById.get(groupKey) || groupKey,
      items: [],
    };
    group.items.push({
      to: labelById.get(toId) || toId || '-',
      relation: knowledgeRelationLabel(edge.relation),
      rawRelation: String(edge.relation || 'related').trim().toLowerCase(),
    });
    groups.set(groupKey, group);
  });

  const root = drawerElement('div', 'knowledge-edge-list');
  if (!groups.size) {
    root.appendChild(drawerElement(
      'pre',
      '',
      topicCount
        ? tf('ui.settings.knowledge.loaded_summary', '{topics} topics and {edges} edges loaded.', { topics: topicCount, edges: edgeCount })
        : t('ui.settings.knowledge.empty_summary', 'Knowledge map has no loaded topics yet.'),
    ));
    return root;
  }

  Array.from(groups.values()).slice(0, 12).forEach((group) => {
    const card = drawerElement('article', 'knowledge-edge-card');
    card.appendChild(drawerElement('h3', '', group.from));
    const list = drawerElement('div', 'knowledge-edge-card__items');
    group.items.slice(0, 6).forEach((item) => {
      const row = drawerElement('div', 'knowledge-edge-row');
      row.dataset.relation = item.rawRelation || 'related';
      row.appendChild(drawerElement('span', 'knowledge-edge-row__relation', item.relation));
      row.appendChild(drawerElement('span', 'knowledge-edge-row__target', item.to));
      list.appendChild(row);
    });
    if (group.items.length > 6) {
      list.appendChild(drawerElement('span', 'knowledge-edge-more', tf('ui.knowledge.edge_more', '+ {count} more', { count: group.items.length - 6 })));
    }
    card.appendChild(list);
    root.appendChild(card);
  });
  if (edges.length > 80 || groups.size > 12) {
    const hidden = Math.max(0, edgeCount - Math.min(edgeCount, 80));
    root.appendChild(drawerElement('span', 'knowledge-edge-more', hidden
      ? tf('ui.knowledge.edge_more', '+ {count} more', { count: hidden })
      : t('ui.knowledge.edge_more_groups', 'More relationship groups are hidden.')));
  }
  return root;
}

function renderKnowledgePanel(payload = null) {
  const data = payload && typeof payload === 'object' ? payload : (lastStatusPayload || {});
  const summary = data.summary || data.knowledge_summary || {};
  const nodes=Array.isArray(data.nodes) ? data.nodes : [];
  const edges=Array.isArray(data.edges) ? data.edges : [];
  const activeStage = knowledgeMapActiveStage();
  const shownNodes = visibleKnowledgeNodes(nodes, activeStage);
  const shownEdges = visibleKnowledgeEdges(edges, shownNodes, activeStage);
  const topicCount = countFromSummary(summary, ['topic_count', 'topics', 'node_count', 'nodes']) || nodes.length;
  const edgeCount = countFromSummary(summary, ['edge_count', 'edges']) || edges.length;
  const weakTopics = shownNodes.filter((node) => masteryLevelForPanel(node) === 'weak').length;
  const root = surfacePanel('knowledge-map', `${shownNodes.length}/${topicCount}`);
  const state = drawerElement('section', 'study-panel__state');
  appendPanelState(state, t('ui.profile.stage_label', 'Stage'), learningStageLabel());
  appendPanelState(state, t('ui.knowledge.scope_label', 'Graph range'), knowledgeMapRangeLabel(activeStage));
  appendPanelState(state, t('ui.label.topics', 'Topics'), `${shownNodes.length}/${topicCount}`);
  appendPanelState(state, t('ui.label.edges', 'Edges'), `${shownEdges.length}/${edgeCount}`);
  appendPanelState(state, t('ui.label.weak_topics', 'Weak Topics'), String(weakTopics));
  root.appendChild(state);
  root.appendChild(renderKnowledgeStageSelector(nodes));

  if (shownNodes.length) {
    root.appendChild(renderKnowledgeNodes(shownNodes));
  } else {
    root.appendChild(drawerElement('pre', '', t('ui.knowledge.scope_empty', 'No topics in this graph range yet. Switch to all stages or keep studying to build it.')));
  }
  root.appendChild(drawerElement('div', 'study-panel__reply-label', t('ui.knowledge.edge_section', 'Relationships')));
  root.appendChild(renderKnowledgeEdges(shownNodes, shownEdges, shownEdges.length, shownNodes.length));
  return root;
}

function renderGenericLocalPanel(surfaceId) {
  const root = surfacePanel(surfaceId, hostedSurfaceLabel(surfaceId));
  root.appendChild(drawerElement('pre', '', hostedSurfaceLabel(surfaceId)));
  root.appendChild(renderDrawerActions([
    { label: t('ui.button.refresh', 'Refresh'), primary: true, handler: async () => { await refreshStatus(); openSurfaceDrawer(surfaceId); } },
  ]));
  return root;
}

function renderSurfaceDrawerBody(surfaceId) {
  const hostedPanel = window.StudyCompanionSurfacePanels?.render?.(surfaceId, {
    t,
    tf,
    label: hostedSurfaceLabel,
    callPlugin,
  });
  if (hostedPanel) return hostedPanel;
  if (surfaceId === 'knowledge-map') return renderKnowledgePanel();
  return renderGenericLocalPanel(surfaceId);
}

async function loadKnowledgeMapIntoDrawer(surfaceId, requestId) {
  const payload = await callPlugin('study_knowledge_map', { limit: 200 });
  if (requestId !== mapRequestId || surfaceDrawer.dataset.open !== 'true' || (surfaceDrawer.dataset.surfaceId || surfaceId) !== 'knowledge-map') {
    return;
  }
  lastKnowledgeMapPayload = payload;
  surfaceDrawerBody.replaceChildren(renderKnowledgePanel(payload));
}

function openSurfaceDrawer(surfaceId) {
  if (!surfaceDrawer || !surfaceDrawerBody) {
    return;
  }
  if (surfaceDrawerTitle) {
    surfaceDrawerTitle.textContent = hostedSurfaceLabel(surfaceId);
  }
  surfaceDrawer.dataset.surfaceId = surfaceId;
  surfaceDrawerBody.replaceChildren(renderSurfaceDrawerBody(surfaceId));
  surfaceDrawer.dataset.open = 'true';
  surfaceDrawer.setAttribute('aria-hidden', 'false');
  if (surfaceId === 'knowledge-map') {
    const requestId=mapRequestId += 1;
    loadKnowledgeMapIntoDrawer(surfaceId, requestId);
  }
  surfaceDrawerCloseBtn?.focus?.();
}

function openHostedSurface(surfaceId, featureAction = '') {
  if (!surfaceId) {
    return;
  }
  setActiveFeature(featureAction);
  openSurfaceDrawer(surfaceId);
}

function handleFeatureAction(action) {
  closeSurfaceDrawer();
  setActiveFeature(action);
  if (action === 'practice') {
    focusAfterScroll(document.getElementById('practicePanel'), generateQuestionBtn);
  } else if (action === 'explain') {
    focusAfterScroll(document.getElementById('explainPanel'), studyInput);
  } else if (action === 'memory') {
    const memoryPanel = document.getElementById('memoryPanel');
    if (memoryPanel) {
      memoryPanel.open = true;
    }
    focusAfterScroll(memoryPanel, memoryFrontInput);
  }
}

function trustedStudySurfaceOrigin(origin) {
  return origin === window.location.origin;
}

function isTrustedStudySurfaceMessage(message) {
  if (!message || typeof message !== 'object') {
    return false;
  }
  if (!STUDY_SURFACE_INCOMING_MESSAGE_TYPES.has(message.type)) {
    return false;
  }
  if (message.type !== STUDY_SURFACE_MESSAGE_TYPES.memoryDeckUpdated) {
    return message.payload === undefined || (message.payload !== null && typeof message.payload === 'object');
  }
  return message.payload !== null && typeof message.payload === 'object' && !Array.isArray(message.payload);
}

function requestStudyStatusRefresh() {
  if (refreshPending) {
    return;
  }
  refreshPending = true;
  refreshStatus({ updateReply: false })
    .catch((error) => {
      setStatus(t('ui.status.error', 'Error'));
      setReply(formatPluginError(error));
    })
    .finally(() => {
      refreshPending = false;
    });
}

function handleStudySurfaceMessage(event) {
  if (!trustedStudySurfaceOrigin(event.origin)) {
    return;
  }
  const message = event.data || {};
  if (!isTrustedStudySurfaceMessage(message)) {
    return;
  }
  if (
    message.type === STUDY_SURFACE_MESSAGE_TYPES.reviewCompleted
    || message.type === STUDY_SURFACE_MESSAGE_TYPES.refreshSummary
  ) {
    requestStudyStatusRefresh();
    return;
  }
  // Ignore unrelated parent/child messages; this surface only owns the study message namespace above.
  if (message.type !== STUDY_SURFACE_MESSAGE_TYPES.memoryDeckUpdated) {
    return;
  }
  const payload = message.payload && typeof message.payload === 'object' ? message.payload : {};
  const nextDeck = {
    ...(lastStatusPayload.memory_deck || {}),
    ...payload,
  };
  lastStatusPayload = {
    ...lastStatusPayload,
    memory_deck: nextDeck,
  };
  setMemoryDeckState(nextDeck);
  updateStudySummaries(lastStatusPayload);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function timeLeft(deadline) {
  return Math.max(0, deadline - Date.now());
}

function timeoutForEntry(entryId) {
  return ENTRY_TIMEOUT_MS[entryId] || RUN_TIMEOUT_MS;
}

function isAbortError(error) {
  return error instanceof DOMException && error.name === 'AbortError';
}

async function fetchWithTimeout(url, init = {}, timeoutMs = RUN_TIMEOUT_MS) {
  if (timeoutMs <= 0) {
    throw new Error(t('ui.error.plugin_call_timeout', 'Plugin call timed out'));
  }
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (isAbortError(error)) {
      throw new Error(t('ui.error.plugin_call_timeout', 'Plugin call timed out'));
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function setSettingsConfigStatus(key, fallback) {
  if (settingsConfigStatus) {
    settingsConfigStatus.textContent = t(key, fallback);
  }
}

function cloneConfig(value) {
  // Config is JSON-compatible primitive data; this intentionally drops Date/undefined values.
  return JSON.parse(JSON.stringify(value || {}));
}

function getConfigRoot(payload) {
  return payload && typeof payload.config === 'object' && payload.config ? payload.config : payload;
}

function ensureConfigSection(config, key) {
  if (!config[key] || typeof config[key] !== 'object') {
    config[key] = {};
  }
  return config[key];
}

function applySettingsConfig(config) {
  const study = config.study || {};
  const ocr = config.ocr_reader || {};
  const llm = config.llm || {};
  if (settingsDefaultMode) {
    settingsDefaultMode.value = ['companion', 'interactive', 'teaching'].includes(study.default_mode) ? study.default_mode : 'companion';
  }
  syncLearningProfileUi();
  if (settingsOcrEnabled) {
    settingsOcrEnabled.checked = ocr.enabled !== false;
  }
  if (settingsOcrLanguages) {
    settingsOcrLanguages.value = String(ocr.languages || 'chi_sim+jpn+eng');
  }
  if (settingsLlmTimeout) {
    settingsLlmTimeout.value = String(Number.isFinite(Number(llm.llm_call_timeout_seconds)) ? Number(llm.llm_call_timeout_seconds) : 30);
  }
  if (settingsLlmVisionEnabled) {
    settingsLlmVisionEnabled.checked = llm.llm_vision_enabled === true;
  }
  if (Object.prototype.hasOwnProperty.call(llm, 'llm_vision_max_image_px')) {
    applyVisionMaxImagePx(llm.llm_vision_max_image_px);
  }
}

async function loadSettingsConfig(options = {}) {
  if (!settingsConfigForm || settingsConfigLoading || (settingsConfig && !options.force)) return;
  settingsConfigLoading = true;
  setSettingsConfigStatus('ui.status.config_loading', 'Loading settings...');
  try {
    settingsConfig = cloneConfig(getConfigRoot(await callPlugin('study_get_settings_config')));
    applySettingsConfig(settingsConfig);
    setSettingsConfigStatus('ui.status.config_loaded', 'Settings loaded');
  } catch (error) {
    setSettingsConfigStatus('ui.status.config_load_failed', 'Could not load settings');
  } finally {
    settingsConfigLoading = false;
  }
}

function collectSettingsConfig() {
  const next = cloneConfig(settingsConfig);
  const study = ensureConfigSection(next, 'study');
  const ocr = ensureConfigSection(next, 'ocr_reader');
  const llm = ensureConfigSection(next, 'llm');
  study.default_mode = settingsDefaultMode ? settingsDefaultMode.value : 'companion';
  ocr.enabled = settingsOcrEnabled ? settingsOcrEnabled.checked : true;
  ocr.languages = settingsOcrLanguages ? settingsOcrLanguages.value.trim() || 'chi_sim+jpn+eng' : 'chi_sim+jpn+eng';
  llm.llm_call_timeout_seconds = Math.max(1, Math.min(3600, Math.round(Number(settingsLlmTimeout?.value) || 30)));
  llm.llm_vision_enabled = settingsLlmVisionEnabled ? settingsLlmVisionEnabled.checked : false;
  llm.llm_vision_max_image_px = normalizeVisionMaxImagePx(llm.llm_vision_max_image_px);
  return next;
}

async function saveSettingsConfig() {
  if (!settingsConfig) await loadSettingsConfig({ force: true });
  if (!settingsConfig) {
    setSettingsConfigStatus('ui.status.config_load_failed', 'Could not load settings');
    return;
  }
  const next = collectSettingsConfig();
  if (settingsLearningStage) {
    setLearningProfileStage(settingsLearningStage.value);
  }
  if (settingsSaveBtn) settingsSaveBtn.disabled = true;
  setSettingsConfigStatus('ui.status.config_saving', 'Saving settings...');
  try {
    settingsConfig = cloneConfig(getConfigRoot(await callPlugin('study_update_settings_config', { config: next })) || next);
    applySettingsConfig(settingsConfig);
    setSettingsConfigStatus('ui.status.config_saved', 'Saved');
  } catch (error) {
    setSettingsConfigStatus('ui.status.config_save_failed', 'Could not save settings');
  } finally {
    if (settingsSaveBtn) settingsSaveBtn.disabled = false;
  }
}

async function createRun(entryId, args = {}, deadline = Date.now() + RUN_TIMEOUT_MS) {
  const response = await fetchWithTimeout(RUNS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: PLUGIN_ID, entry_id: entryId, args }),
  }, timeLeft(deadline));
  if (!response.ok) {
    throw new Error(tf('ui.error.run_create_failed', 'Run create failed: HTTP {status}', { status: response.status }));
  }
  const payload = await response.json();
  const runId = payload.run_id || payload.id;
  if (!runId) {
    throw new Error(t('ui.error.run_id_missing', 'Run id missing'));
  }
  return runId;
}

async function exportRunResult(runId, deadline = Date.now() + RUN_TIMEOUT_MS) {
  let lastStatus = 0;
  for (let attempt = 0; attempt < RUN_EXPORT_RETRY_COUNT; attempt += 1) {
    const response = await fetchWithTimeout(`${RUNS_URL}/${runId}/export`, {}, timeLeft(deadline));
    lastStatus = response.status;
    if (response.ok) {
      const payload = await response.json();
      const items = payload.items || [];
      const item = items.find((candidate) => candidate.type === 'json' && candidate.json);
      const pluginResponse = item ? (item.json || {}) : {};
      if (pluginResponse.success === false || pluginResponse.error) {
        throw new Error(pluginResponse.error?.message || pluginResponse.message || t('ui.error.plugin_call_failed', 'Plugin call failed'));
      }
      if (!item) {
        throw new Error(t('ui.error.plugin_call_failed', 'Plugin call failed'));
      }
      return pluginResponse.data || {};
    }
    if (attempt < RUN_EXPORT_RETRY_COUNT - 1) {
      const waitMs = Math.min(RUN_EXPORT_RETRY_DELAY_MS * (attempt + 1), timeLeft(deadline));
      if (waitMs <= 0) {
        throw new Error(t('ui.error.plugin_call_timeout', 'Plugin call timed out'));
      }
      await sleep(waitMs);
    }
  }
  throw new Error(tf('ui.error.run_export_failed', 'Run export failed: HTTP {status}', { status: lastStatus }));
}

async function callPlugin(entryId, args = {}) {
  const deadline = Date.now() + timeoutForEntry(entryId);
  const runId = await createRun(entryId, args, deadline);
  let delay = 250;
  while (Date.now() < deadline) {
    const waitMs = Math.min(delay, timeLeft(deadline));
    if (waitMs <= 0) {
      break;
    }
    await sleep(waitMs);
    delay = Math.min(Math.round(delay * 1.5), 2000);
    const response = await fetchWithTimeout(`${RUNS_URL}/${runId}`, {}, timeLeft(deadline));
    if (!response.ok) {
      continue;
    }
    const record = await response.json();
    if (record.status === 'succeeded') {
      return await exportRunResult(runId, deadline);
    }
    if (['failed', 'canceled', 'timeout'].includes(record.status)) {
      throw new Error(record.error?.message || record.message || record.status);
    }
  }
  throw new Error(t('ui.error.plugin_call_timeout', 'Plugin call timed out'));
}

async function refreshStatus(_options = {}) {
  setStatus(t('ui.status.refreshing', 'Refreshing...'));
  const data = await callPlugin('study_status');
  applyRuntimeConfig(data);
  setStatusLine(data);
}

async function loadQuestionContext(options = {}) {
  if (!options.silent) {
    setStatus(t('ui.practice.context_loading', 'Analyzing study records...'));
  }
  try {
    const data = await callPlugin('study_question_context');
    setQuestionContext(data);
    if (!options.silent) {
      setStatus(t('ui.status.ready', 'Ready'));
    }
    return data;
  } catch (error) {
    currentSelectionContext = null;
    if (selectionReason) {
      selectionReason.textContent = formatPluginError(error);
    }
    if (!options.silent) {
      setStatus(t('ui.status.error', 'Error'));
    }
    return null;
  }
}

async function runOcr() {
  setStatus(t('ui.status.capturing_ocr', 'Capturing OCR...'));
  const data = await callPlugin('study_ocr_snapshot');
  setStatus(tf('ui.status.ocr_result', 'OCR {status}', { status: data.status || 'unknown' }));
  if (data.text) {
    studyInput.value = data.text;
  }
  setReply(data.text || data.diagnostic || data.summary || '');
  await refreshStatus({ updateReply: false });
}

async function explainText() {
  const text = studyInput.value.trim();
  if (!text && !studyInputImageValue) {
    throw new Error(t('ui.error.missing_study_input', 'Please enter text or paste an image first.'));
  }
  setStatus(studyInputImageValue
    ? t('ui.status.solving_problem', 'Solving problem...')
    : t('ui.status.explaining', 'Explaining...'));
  setReply(studyInputImageValue ? t('ui.status.solving_problem', 'Solving problem...') : t('ui.status.explaining', 'Explaining...'));
  scrollReplyIntoView();
  const args = { text };
  if (studyInputImageValue) {
    args.vision_image_base64 = studyInputImageValue;
  }
  const data = await callPlugin('study_explain_text', args);
  setStatus(data.degraded
    ? t('ui.status.reply_ready_fallback', 'Reply ready (fallback)')
    : t('ui.status.reply_ready', 'Reply ready'));
  setReply(data.reply || data.summary || data.transition_phrase || '');
  await refreshStatus({ updateReply: false });
}

async function generateQuestion() {
  let context = currentSelectionContext;
  if (!context || !context.selection_context_id) {
    context = await loadQuestionContext({ silent: true });
  }
  if (!context || context.no_data || !context.selection_context_id) {
    throw new Error(t('ui.error.no_targeted_question_data', 'Not enough study records to generate a practice question yet.'));
  }
  setStatus(t('ui.status.generating_question', 'Generating question...'));
  clearFeedback();
  const data = await callPlugin('study_generate_targeted_question', {
    selection_context_id: context.selection_context_id,
  });
  setStatus(data.degraded
    ? t('ui.status.reply_ready_fallback', 'Reply ready (fallback)')
    : t('ui.status.reply_ready', 'Reply ready'));
  setGeneratedQuestion(data);
  setQuestionContext({ ...context, ...data, no_data: false, selection_context_id: '' });
  if (answerInput) answerInput.value = '';
  setImagePreview('answer', '');
  setReply(data.hint || data.question || data.summary || data.reply || '');
  await refreshStatus({ updateReply: false });
}

async function evaluateAnswer() {
  const answer = answerInput ? answerInput.value.trim() : '';
  if (!answer && !answerInputImageValue) {
    throw new Error(t('ui.error.missing_answer', 'Please enter an answer first.'));
  }
  if (!currentQuestion?.question_id || !currentQuestion?.attempt_id) {
    throw new Error(t('ui.error.question_missing', 'Please generate a practice question first.'));
  }
  setStatus(t('ui.status.evaluating_answer', 'Evaluating answer...'));
  const args = {
    answer,
    question_id: currentQuestion.question_id,
    attempt_id: currentQuestion.attempt_id,
    selected_topic_id: currentQuestion.selected_topic_id || '',
  };
  if (answerInputImageValue) {
    args.vision_image_base64 = answerInputImageValue;
  }
  const data = await callPlugin('study_evaluate_answer', args);
  setStatus(data.degraded
    ? t('ui.status.reply_ready_fallback', 'Reply ready (fallback)')
    : t('ui.status.reply_ready', 'Reply ready'));
  renderFeedback(data);
  const replyLines = [data.feedback || data.reply || '', data.next_action ? `Next: ${data.next_action}` : ''].filter(Boolean);
  setReply(replyLines.join('\n\n') || data.summary || '');
  await refreshStatus({ updateReply: false });
}

async function summarizeSession() {
  setStatus(t('ui.status.summarizing_session', 'Summarizing session...'));
  const data = await callPlugin('study_summarize_session', {});
  setStatus(data.degraded
    ? t('ui.status.reply_ready_fallback', 'Reply ready (fallback)')
    : t('ui.status.reply_ready', 'Reply ready'));
  setReply(data.markdown || data.summary || data.reply || '');
  await refreshStatus({ updateReply: false });
}

async function refreshMemoryDeck() {
  setStatus(t('ui.status.refreshing', 'Refreshing...'));
  const data = await callPlugin('study_memory_deck', { limit: 8 });
  setMemoryDeckState(data);
  setStatus(t('ui.status.reply_ready', 'Reply ready'));
}

async function saveMemoryCard() {
  const front = memoryFrontInput ? memoryFrontInput.value.trim() : '';
  const back = memoryBackInput ? memoryBackInput.value.trim() : '';
  if (!front || !back) {
    throw new Error(t('ui.memory.error_missing_card', 'Please enter both sides of the card.'));
  }
  setStatus(t('ui.memory.saving', 'Saving memory card...'));
  const data = await callPlugin('study_memory_card_upsert', {
    front,
    back,
    source: 'ui',
  });
  if (memoryFrontInput) {
    memoryFrontInput.value = '';
  }
  if (memoryBackInput) {
    memoryBackInput.value = '';
  }
  setReply(data.card ? `${data.card.front}\n\n${data.card.back}` : '');
  await refreshMemoryDeck();
}

async function reviewMemoryCard(rating) {
  const topicId = currentMemoryCard?.topic_id || currentMemoryCard?.item_id || '';
  if (!topicId) {
    return;
  }
  setStatus(t('ui.memory.reviewing', 'Reviewing memory card...'));
  const data = await callPlugin('study_memory_card_review', {
    topic_id: topicId,
    rating,
  });
  const scheduledDays = data.schedule && Number.isFinite(Number(data.schedule.scheduled_days))
    ? Number(data.schedule.scheduled_days).toFixed(1)
    : '';
  setReply(scheduledDays
    ? tf('ui.memory.review_saved_days', 'Next review in {days} days', { days: scheduledDays })
    : t('ui.memory.review_saved', 'Review saved'));
  await refreshMemoryDeck();
}

async function setMode(mode) {
  if (mode === currentMode) {
    return;
  }
  setStatus(t('ui.status.mode_switching', 'Switching mode...'));
  const data = await callPlugin('study_set_mode', { mode, reason: 'ui' });
  const appliedMode = data && data.new_mode
    ? data.new_mode
    : (data && data.changed === false ? currentMode : mode);
  currentMode = String(appliedMode || 'companion');
  setModeButtons(currentMode, false);
  setReply(data.transition_phrase || data.summary || data.message || '');
  await refreshStatus({ updateReply: false });
}

async function requestModeChange(mode) {
  const requestedMode = String(mode || 'companion');
  if (modeChangeInFlight || requestedMode === currentMode) {
    setModeButtons(currentMode, false);
    return;
  }
  modeChangeInFlight = true;
  setModeButtons(currentMode, true);
  try {
    await setMode(requestedMode);
  } finally {
    modeChangeInFlight = false;
    setModeButtons(currentMode, false);
  }
}

function isTextEntryTarget(target) {
  const tag = target && target.tagName ? String(target.tagName).toLowerCase() : '';
  return tag === 'input' || tag === 'textarea' || tag === 'select' || Boolean(target?.isContentEditable);
}

function handleModeShortcut(event) {
  if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || isTextEntryTarget(event.target)) {
    return;
  }
  const mode = MODE_SHORTCUTS[String(event.key)];
  if (!mode) {
    return;
  }
  event.preventDefault();
  requestModeChange(mode).catch((error) => {
    setStatus(t('ui.status.error', 'Error'));
    setReply(formatPluginError(error));
  });
}

function bindButton(button, handler) {
  if (!button) {
    return;
  }
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      setStatus(t('ui.status.error', 'Error'));
      setReply(formatPluginError(error));
    } finally {
      button.disabled = false;
    }
  });
}

async function bootstrap() {
  if (window.I18n && typeof window.I18n.init === 'function') {
    await window.I18n.init(PLUGIN_ID);
    window.I18n.scanDOM();
    document.title = t('ui.title', 'Study Companion');
  }
  syncLearningProfileUi();
  bindButton(refreshBtn, refreshStatus);
  bindButton(ocrBtn, runOcr);
  bindButton(generateQuestionBtn, generateQuestion);
  bindButton(explainBtn, explainText);
  bindButton(evaluateAnswerBtn, evaluateAnswer);
  bindButton(summarizeBtn, summarizeSession);
  if (hintToggleBtn) {
    hintToggleBtn.addEventListener('click', () => {
      const expanded = hintToggleBtn.getAttribute('aria-expanded') === 'true';
      hintToggleBtn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      if (hintText) {
        hintText.hidden = expanded;
      }
    });
  }
  bindButton(memoryRefreshBtn, refreshMemoryDeck);
  bindButton(memoryAddBtn, saveMemoryCard);
  if (studyInput) {
    studyInput.addEventListener('paste', createImagePasteHandler({
      textarea: studyInput,
      kind: 'study',
      errorTarget: studyInputPasteError,
    }));
  }
  if (answerInput) {
    answerInput.addEventListener('paste', createImagePasteHandler({
      textarea: answerInput,
      kind: 'answer',
      errorTarget: answerInputPasteError,
    }));
  }
  if (studyInputImageRemove) {
    studyInputImageRemove.addEventListener('click', () => {
      setImagePreview('study', '');
      setPasteError(studyInputPasteError, '');
    });
  }
  if (answerInputImageRemove) {
    answerInputImageRemove.addEventListener('click', () => {
      setImagePreview('answer', '');
      setPasteError(answerInputPasteError, '');
    });
  }
  setModeButtons(currentMode, false);
  document.addEventListener('keydown', handleModeShortcut);
  if (modeSelect) {
    modeSelect.addEventListener('change', () => {
      requestModeChange(modeSelect.value).catch((error) => {
        setStatus(t('ui.status.error', 'Error'));
        setReply(formatPluginError(error));
      });
    });
  }
  if (firstRunSkipBtn) {
    firstRunSkipBtn.addEventListener('click', () => {
      firstRunDismissed = true;
      setLearningProfileStage('', { skipped: true });
      if (firstRunGuide) {
        firstRunGuide.dataset.dismissed = 'true';
      }
      renderFirstRunGuide(lastStatusPayload);
    });
  }
  if (advancedToggleBtn) {
    advancedToggleBtn.addEventListener('click', () => {
      setAdvancedSettingsOpen(!advancedSettingsOpen);
    });
  }
  if (settingsConfigForm) {
    settingsConfigForm.addEventListener('submit', (event) => {
      event.preventDefault();
      saveSettingsConfig();
    });
  }
  if (settingsLearningStage) {
    settingsLearningStage.addEventListener('change', () => {
      setLearningProfileStage(settingsLearningStage.value);
    });
  }
  settingsTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      setSettingsTab(tab.getAttribute('data-settings-tab'));
    });
    tab.addEventListener('keydown', handleSettingsTabKeydown);
  });
  surfaceOpenButtons.forEach((button) => {
    button.addEventListener('click', () => {
      openHostedSurface(
        button.getAttribute('data-open-surface'),
        button.getAttribute('data-feature-action') || '',
      );
    });
  });
  featureActionButtons.forEach((button) => {
    if (button.getAttribute('data-open-surface')) {
      return;
    }
    button.addEventListener('click', () => {
      handleFeatureAction(button.getAttribute('data-feature-action'));
    });
  });
  if (surfaceDrawerCloseBtn) {
    surfaceDrawerCloseBtn.addEventListener('click', closeSurfaceDrawer);
  }
  if (surfaceDrawer) {
    surfaceDrawer.addEventListener('click', (event) => {
      if (event.target === surfaceDrawer) {
        closeSurfaceDrawer();
      }
    });
  }
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && surfaceDrawer?.dataset.open === 'true') {
      closeSurfaceDrawer();
    }
  });
  setSettingsTab('study');
  window.addEventListener('message', handleStudySurfaceMessage);
  memoryReviewButtons.forEach((button) => {
    button.addEventListener('click', async () => {
      if (button.disabled) {
        return;
      }
      memoryReviewButtons.forEach((candidate) => {
        candidate.disabled = true;
      });
      try {
        await reviewMemoryCard(button.getAttribute('data-memory-rating') || 'good');
      } catch (error) {
        setStatus(t('ui.status.error', 'Error'));
        setReply(formatPluginError(error));
      } finally {
        memoryReviewButtons.forEach((candidate) => {
          candidate.disabled = !currentMemoryCard;
        });
      }
    });
  });
  modeButtons.forEach((button) => {
    button.addEventListener('click', () => {
      if (button.disabled) {
        return;
      }
      requestModeChange(button.getAttribute('data-mode') || 'companion').catch((error) => {
        setStatus(t('ui.status.error', 'Error'));
        setReply(formatPluginError(error));
        setModeButtons(currentMode, false);
      });
    });
  });
  await refreshStatus();
  await loadQuestionContext({ silent: true });
}

bootstrap().catch((error) => {
  setStatus(t('ui.status.not_ready', 'Not ready'));
  setReply(formatPluginError(error));
});
