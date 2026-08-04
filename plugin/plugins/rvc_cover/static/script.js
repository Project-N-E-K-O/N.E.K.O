const RUNS_URL = '/runs';
const pluginMatch = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
const pluginId = pluginMatch ? decodeURIComponent(pluginMatch[1]) : 'rvc_cover';
const RUN_POLL_DELAY_MS = 400;

const state = { dashboard: null, busy: false };

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pluginErrorMessage(error) {
  if (!error) return '';
  if (typeof error === 'string') return error;
  if (typeof error.message === 'string') return error.message;
  if (typeof error.detail === 'string') return error.detail;
  if (typeof error.code === 'string') return error.code;
  return '';
}

async function callPlugin(entryId, args = {}, timeoutMs = 60000) {
  const response = await fetch(RUNS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: pluginId, entry_id: entryId, args }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const record = await response.json();
  const runId = record.run_id || record.id;
  if (!runId) throw new Error('未获取到 run_id');

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const poll = await fetch(`${RUNS_URL}/${encodeURIComponent(runId)}`);
    if (!poll.ok) {
      await delay(RUN_POLL_DELAY_MS);
      continue;
    }
    const run = await poll.json();
    if (run.status === 'succeeded') {
      const exported = await fetch(`${RUNS_URL}/${encodeURIComponent(runId)}/export`);
      if (!exported.ok) return {};
      const payload = await exported.json();
      const items = payload.items || [];
      const item = items.find((candidate) => candidate.type === 'json' && candidate.json) || items[0];
      if (!item) return {};
      let raw = item.json || {};
      while (
        raw
        && raw.data
        && typeof raw.data === 'object'
        && ('success' in raw.data || 'error' in raw.data || 'value' in raw.data)
      ) {
        raw = raw.data;
      }
      if (raw && raw.error) {
        throw new Error(pluginErrorMessage(raw.error) || '插件调用失败');
      }
      return raw && raw.value && typeof raw.value === 'object' ? raw.value : raw;
    }
    if (['failed', 'canceled', 'timeout'].includes(run.status)) {
      throw new Error((run.error && run.error.message) || run.message || run.status);
    }
    await delay(RUN_POLL_DELAY_MS);
  }
  throw new Error('调用超时');
}

function showToast(message, error = false) {
  const toast = document.getElementById('toast');
  toast.textContent = String(message || '');
  toast.classList.toggle('error', !!error);
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 3200);
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
}

function fillModelSelect(selectId, models, selected, allowEmpty) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const list = Array.isArray(models) ? models.slice() : [];
  const current = String(selected || '').trim();
  if (current && !list.includes(current)) list.unshift(current);
  select.replaceChildren();
  if (allowEmpty) {
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '使用默认音色';
    select.appendChild(empty);
  }
  list.forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    if (name === current) option.selected = true;
    select.appendChild(option);
  });
}

function applyDashboard(payload) {
  const dashboard = (payload && payload.value) || payload || {};
  state.dashboard = dashboard;
  const ready = !!dashboard.ready;
  const problems = Array.isArray(dashboard.problems) ? dashboard.problems : [];
  const settings = dashboard.settings || {};
  const job = dashboard.job || {};
  const models = dashboard.models || [];

  const pill = document.getElementById('ready-pill');
  pill.textContent = ready ? '就绪' : '未就绪';
  pill.className = `status-pill${ready ? '' : ' warn'}`;

  document.getElementById('status-summary').textContent = ready
    ? `RVC 环境可用 · 默认音色 ${settings.model_name || '-'} · 设备 ${settings.device || '-'}`
    : 'RVC 环境未就绪，请先运行 setup 或检查路径 / 模型。';

  const jobEl = document.getElementById('job-summary');
  if (job.status && job.status !== 'idle') {
    jobEl.textContent = [
      `任务: ${job.status}`,
      job.query ? `「${job.query}」` : '',
      job.model_name || '',
      job.error || '',
      job.output_url ? '已出结果' : '',
    ].filter(Boolean).join(' · ');
  } else {
    jobEl.textContent = '当前无进行中的翻唱任务。';
  }

  document.getElementById('problem-list').textContent = problems.length ? problems.join('\n') : '';
  document.getElementById('config-path').textContent = dashboard.config_path
    ? `配置文件：${dashboard.config_path}`
    : '';

  fillModelSelect('cfg-model', models, settings.model_name, false);
  fillModelSelect('cover-model', models, '', true);
  document.getElementById('cfg-device').value = settings.device || 'cuda:0';
  document.getElementById('cfg-f0-method').value = settings.f0_method || 'rmvpe';
  document.getElementById('cfg-f0-up-key').value = settings.f0_up_key ?? 0;
  document.getElementById('cfg-index-rate').value = settings.index_rate ?? 0;
  document.getElementById('cfg-protect').value = settings.protect ?? 0.33;
  document.getElementById('cfg-timeout').value = settings.infer_timeout_seconds ?? 600;
  document.getElementById('cfg-rvc-root').value = settings.rvc_root || 'vendor/rvc';
  document.getElementById('cfg-python-path').value = settings.python_path || 'vendor/rvc/runtime/python.exe';

  const phrases = (dashboard.hints && dashboard.hints.trigger_phrases) || [];
  const phraseEl = document.getElementById('phrase-hints');
  if (phrases.length) phraseEl.textContent = `触发例句：${phrases.join(' / ')}`;

  const web = dashboard.rvc_web || {};
  const webUrl = String(web.url || settings.web_url || 'http://127.0.0.1:7897').trim();
  const openGradio = document.getElementById('btn-open-gradio');
  if (openGradio && webUrl) {
    openGradio.href = webUrl.endsWith('/') ? webUrl : `${webUrl}/`;
  }
  const projects = Array.isArray(dashboard.training_projects) ? dashboard.training_projects : [];
  const trainingEl = document.getElementById('training-summary');
  if (trainingEl) {
    const health = web.health === true ? '已启动' : (web.mode ? `状态 ${web.mode}` : '未检测');
    trainingEl.textContent = projects.length
      ? `原版训练页：${health} · 已同步实验项目 ${projects.length} 个（${projects.slice(0, 4).join('、')}${projects.length > 4 ? '…' : ''}）`
      : `原版训练页：${health} · 暂无实验项目（请运行 setup_rvc_vendor.ps1 同步 D:\\RVC\\logs，或新建训练）`;
  }
}

async function refreshDashboard(silent = false) {
  try {
    applyDashboard(await callPlugin('get_dashboard_state', {}));
  } catch (error) {
    if (!silent) showToast(error.message || '刷新失败', true);
  }
}

async function saveSettings() {
  const payload = {
    model_name: document.getElementById('cfg-model').value,
    device: document.getElementById('cfg-device').value.trim(),
    f0_method: document.getElementById('cfg-f0-method').value,
    f0_up_key: Number(document.getElementById('cfg-f0-up-key').value || 0),
    index_rate: Number(document.getElementById('cfg-index-rate').value || 0),
    protect: Number(document.getElementById('cfg-protect').value || 0.33),
    infer_timeout_seconds: Number(document.getElementById('cfg-timeout').value || 600),
    rvc_root: document.getElementById('cfg-rvc-root').value.trim(),
    python_path: document.getElementById('cfg-python-path').value.trim(),
  };
  setBusy(true);
  try {
    applyDashboard(await callPlugin('save_settings', payload));
    showToast('设置已保存到本机插件数据目录');
  } catch (error) {
    showToast(error.message || '保存失败', true);
  } finally {
    setBusy(false);
  }
}

async function startCover() {
  const query = document.getElementById('cover-query').value.trim();
  if (!query) {
    showToast('请先填写歌名或关键词', true);
    return;
  }
  const payload = {
    query,
    artist: document.getElementById('cover-artist').value.trim(),
    model_name: document.getElementById('cover-model').value.trim(),
  };
  setBusy(true);
  try {
    const result = await callPlugin('sing_cover', payload, 120000);
    showToast(result.message || '已开始翻唱');
    await refreshDashboard(true);
  } catch (error) {
    showToast(error.message || '翻唱失败', true);
  } finally {
    setBusy(false);
  }
}

function bind() {
  document.getElementById('btn-refresh').addEventListener('click', () => refreshDashboard(false));
  document.getElementById('btn-save').addEventListener('click', saveSettings);
  document.getElementById('btn-cover').addEventListener('click', startCover);
}

bind();
if (window.I18n && typeof window.I18n.whenReady === 'function') {
  window.I18n.whenReady(() => refreshDashboard(true));
} else {
  refreshDashboard(true);
}
setInterval(() => {
  if (!state.busy) refreshDashboard(true);
}, 8000);
