const PLUGIN_ID = 'study_companion';
const RUNS_URL = '/runs';
const RUN_TIMEOUT_MS = 60000;

const statusLine = document.getElementById('statusLine');
const replyText = document.getElementById('replyText');
const studyInput = document.getElementById('studyInput');
const refreshBtn = document.getElementById('refreshBtn');
const ocrBtn = document.getElementById('ocrBtn');
const explainBtn = document.getElementById('explainBtn');

function setStatus(text) {
  statusLine.textContent = text;
}

function setReply(text) {
  replyText.textContent = text || '';
}

async function createRun(entryId, args = {}) {
  const response = await fetch(RUNS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: PLUGIN_ID, entry_id: entryId, args }),
  });
  if (!response.ok) {
    throw new Error(`Run create failed: HTTP ${response.status}`);
  }
  const payload = await response.json();
  const runId = payload.run_id || payload.id;
  if (!runId) {
    throw new Error('Run id missing');
  }
  return runId;
}

async function exportRunResult(runId) {
  const response = await fetch(`${RUNS_URL}/${runId}/export`);
  if (!response.ok) {
    return {};
  }
  const payload = await response.json();
  const items = payload.items || [];
  const item = items.find((candidate) => candidate.type === 'json' && candidate.json) || items[0];
  const pluginResponse = item ? (item.json || {}) : {};
  if (pluginResponse.success === false || pluginResponse.error) {
    throw new Error(pluginResponse.error?.message || pluginResponse.message || 'Plugin call failed');
  }
  return pluginResponse.data || {};
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
  throw new Error('Plugin call timed out');
}

async function refreshStatus() {
  setStatus('Refreshing...');
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
  setStatus('Capturing OCR...');
  const data = await callPlugin('study_ocr_snapshot');
  setStatus(`OCR ${data.status || 'unknown'}`);
  if (data.text) {
    studyInput.value = data.text;
  }
  setReply(data.text || data.diagnostic || data.summary || '');
}

async function explainText() {
  const text = studyInput.value.trim();
  setStatus('Explaining...');
  const data = await callPlugin('study_explain_text', { text });
  setStatus(data.degraded ? 'Reply ready (fallback)' : 'Reply ready');
  setReply(data.reply || data.summary || '');
}

function bindButton(button, handler) {
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      setStatus('Error');
      setReply(error instanceof Error ? error.message : String(error));
    } finally {
      button.disabled = false;
    }
  });
}

bindButton(refreshBtn, refreshStatus);
bindButton(ocrBtn, runOcr);
bindButton(explainBtn, explainText);

refreshStatus().catch((error) => {
  setStatus('Not ready');
  setReply(error instanceof Error ? error.message : String(error));
});
