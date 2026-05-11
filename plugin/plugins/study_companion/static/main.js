const PLUGIN_ID = 'study_companion';
const RUNS_URL = '/runs';
const RUN_TIMEOUT_MS = 60000;
const RUN_EXPORT_RETRY_COUNT = 3;
const RUN_EXPORT_RETRY_DELAY_MS = 400;

const statusLine = document.getElementById('statusLine');
const replyText = document.getElementById('replyText');
const studyInput = document.getElementById('studyInput');
const refreshBtn = document.getElementById('refreshBtn');
const ocrBtn = document.getElementById('ocrBtn');
const explainBtn = document.getElementById('explainBtn');

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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function createRun(entryId, args = {}) {
  const response = await fetch(RUNS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: PLUGIN_ID, entry_id: entryId, args }),
  });
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

async function exportRunResult(runId) {
  let lastStatus = 0;
  for (let attempt = 0; attempt < RUN_EXPORT_RETRY_COUNT; attempt += 1) {
    const response = await fetch(`${RUNS_URL}/${runId}/export`);
    lastStatus = response.status;
    if (response.ok) {
      const payload = await response.json();
      const items = payload.items || [];
      const item = items.find((candidate) => candidate.type === 'json' && candidate.json) || items[0];
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
      await sleep(RUN_EXPORT_RETRY_DELAY_MS * (attempt + 1));
    }
  }
  throw new Error(tf('ui.error.run_export_failed', 'Run export failed: HTTP {status}', { status: lastStatus }));
}

async function callPlugin(entryId, args = {}) {
  const runId = await createRun(entryId, args);
  const deadline = Date.now() + RUN_TIMEOUT_MS;
  let delay = 250;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, delay));
    delay = Math.min(Math.round(delay * 1.5), 2000);
    const response = await fetch(`${RUNS_URL}/${runId}`);
    if (!response.ok) {
      continue;
    }
    const record = await response.json();
    if (record.status === 'succeeded') {
      return await exportRunResult(runId);
    }
    if (['failed', 'canceled', 'timeout'].includes(record.status)) {
      throw new Error(record.error?.message || record.message || record.status);
    }
  }
  throw new Error(t('ui.error.plugin_call_timeout', 'Plugin call timed out'));
}

async function refreshStatus() {
  setStatus(t('ui.status.refreshing', 'Refreshing...'));
  const data = await callPlugin('study_status');
  setStatus(`${data.status || 'unknown'} / ${data.active_mode || 'concept_explain'}`);
  if (data.last_reply) {
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
}

async function explainText() {
  const text = studyInput.value.trim();
  setStatus(t('ui.status.explaining', 'Explaining...'));
  const data = await callPlugin('study_explain_text', { text });
  setStatus(data.degraded
    ? t('ui.status.reply_ready_fallback', 'Reply ready (fallback)')
    : t('ui.status.reply_ready', 'Reply ready'));
  setReply(data.reply || data.summary || '');
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
  await refreshStatus();
}

bootstrap().catch((error) => {
  setStatus(t('ui.status.not_ready', 'Not ready'));
  setReply(error instanceof Error ? error.message : String(error));
});
