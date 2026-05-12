const PLUGIN_ID = 'study_companion';
const RUNS_URL = '/runs';
const RUN_TIMEOUT_MS = 60000;
const RUN_EXPORT_RETRY_COUNT = 3;
const RUN_EXPORT_RETRY_DELAY_MS = 400;
let currentMode = 'companion';

const statusLine = document.getElementById('statusLine');
const replyText = document.getElementById('replyText');
const studyInput = document.getElementById('studyInput');
const refreshBtn = document.getElementById('refreshBtn');
const ocrBtn = document.getElementById('ocrBtn');
const explainBtn = document.getElementById('explainBtn');
const modeButtons = Array.from(document.querySelectorAll('[data-mode]'));

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

function setStatus(text) {
  statusLine.textContent = text;
}

function setReply(text) {
  replyText.textContent = text || '';
}

function modeLabel(mode) {
  const known = ['companion', 'interactive', 'teaching'].includes(mode);
  return known ? t(`status.mode.${mode}`, mode) : mode;
}

function setModeButtons(mode, disabled = false) {
  currentMode = String(mode || 'companion');
  modeButtons.forEach((button) => {
    const pressed = button.getAttribute('data-mode') === currentMode;
    button.disabled = disabled;
    button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    button.classList.toggle('is-active', pressed);
  });
}

function setStatusLine(data) {
  const statusValue = data.status || 'unknown';
  const modeValue = String(data.active_mode || data.mode || 'companion');
  const statusLabel = t(`status.state.${statusValue}`, statusValue);
  setStatus(`${statusLabel} / ${modeLabel(modeValue)}`);
  setModeButtons(modeValue, false);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function timeLeft(deadline) {
  return Math.max(0, deadline - Date.now());
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
  const deadline = Date.now() + RUN_TIMEOUT_MS;
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

async function refreshStatus(options = {}) {
  const updateReply = options.updateReply !== false;
  setStatus(t('ui.status.refreshing', 'Refreshing...'));
  const data = await callPlugin('study_status');
  setStatusLine(data);
  if (updateReply && data.last_reply) {
    setReply(data.last_reply);
  }
  if (data.last_ocr_text && !studyInput.value.trim()) {
    studyInput.value = data.last_ocr_text;
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
  setStatus(t('ui.status.explaining', 'Explaining...'));
  const data = await callPlugin('study_explain_text', { text });
  setStatus(data.degraded
    ? t('ui.status.reply_ready_fallback', 'Reply ready (fallback)')
    : t('ui.status.reply_ready', 'Reply ready'));
  setReply(data.reply || data.summary || data.transition_phrase || '');
  await refreshStatus({ updateReply: false });
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

function bindButton(button, handler) {
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      setStatus(t('ui.status.error', 'Error'));
      setReply(error instanceof Error ? error.message : String(error));
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
  bindButton(refreshBtn, refreshStatus);
  bindButton(ocrBtn, runOcr);
  bindButton(explainBtn, explainText);
  modeButtons.forEach((button) => {
    button.addEventListener('click', async () => {
      if (button.disabled) {
        return;
      }
      try {
        modeButtons.forEach((candidate) => {
          candidate.disabled = true;
        });
        await setMode(button.getAttribute('data-mode') || 'companion');
      } catch (error) {
        setStatus(t('ui.status.error', 'Error'));
        setReply(error instanceof Error ? error.message : String(error));
      } finally {
        setModeButtons(currentMode, false);
      }
    });
  });
  await refreshStatus();
}

bootstrap().catch((error) => {
  setStatus(t('ui.status.not_ready', 'Not ready'));
  setReply(error instanceof Error ? error.message : String(error));
});
