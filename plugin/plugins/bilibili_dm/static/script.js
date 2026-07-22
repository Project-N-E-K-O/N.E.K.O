const RUNS_URL = '/runs';
const pluginMatch = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
const pluginId = pluginMatch ? decodeURIComponent(pluginMatch[1]) : 'bilibili_dm';

const state = { dashboard: null, busy: false };

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function callPlugin(entryId, args = {}) {
  const response = await fetch(RUNS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: pluginId, entry_id: entryId, args }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const record = await response.json();
  const runId = record.run_id || record.id;
  if (!runId) throw new Error('未获取到 run_id');

  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    const poll = await fetch(`${RUNS_URL}/${encodeURIComponent(runId)}`);
    if (poll.ok) {
      const run = await poll.json();
      if (run.status === 'succeeded') {
        const exported = await fetch(`${RUNS_URL}/${encodeURIComponent(runId)}/export`);
        if (!exported.ok) return {};
        const payload = await exported.json();
        const item = (payload.items || []).find((candidate) => candidate.type === 'json' && candidate.json) || (payload.items || [])[0];
        let raw = item ? (item.json || {}) : {};
        while (raw && raw.data && typeof raw.data === 'object') raw = raw.data;
        if (raw && raw.error) throw new Error(raw.error.message || raw.error || '操作失败');
        return raw && raw.value && typeof raw.value === 'object' ? raw.value : raw;
      }
      if (['failed', 'canceled', 'timeout'].includes(run.status)) {
        throw new Error((run.error && run.error.message) || run.message || run.status);
      }
    }
    await delay(400);
  }
  throw new Error('调用超时');
}

function showToast(message, error = false) {
  const toast = document.getElementById('toast');
  toast.textContent = String(message || '');
  toast.classList.toggle('error', error);
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 3200);
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
}

function fieldStatus(id, configured) {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = configured ? '已保存（留空则保持）' : '未保存';
  element.classList.toggle('configured', configured);
}

function renderUsers(users) {
  const list = document.getElementById('trusted-users');
  list.replaceChildren();
  const normalized = Array.isArray(users) ? users : [];
  document.getElementById('trusted-count').textContent = `${normalized.length} 人`;
  if (!normalized.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '暂无信任用户';
    list.appendChild(empty);
    return;
  }
  normalized.forEach((user) => {
    const row = document.createElement('div');
    row.className = 'user-row';
    const identity = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = user.nickname || `UID ${user.uid}`;
    const meta = document.createElement('span');
    meta.textContent = `${user.nickname ? `UID ${user.uid} · ` : ''}${user.level}`;
    identity.append(name, meta);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'link-danger';
    remove.textContent = '移除';
    remove.addEventListener('click', () => removeTrustedUser(String(user.uid || '')));
    row.append(identity, remove);
    list.appendChild(row);
  });
}

function applyDashboard(payload) {
  const dashboard = payload && payload.value ? payload.value : (payload || {});
  state.dashboard = dashboard;
  const runtime = dashboard.status || {};
  const credentials = dashboard.credentials || {};
  const settings = dashboard.settings || {};

  const listening = !!runtime.listening;
  const configured = !!runtime.credentials_configured;
  const listener = document.getElementById('listener-status');
  listener.textContent = listening ? '监听中' : '已停止';
  listener.className = `pill ${listening ? 'active' : 'idle'}`;
  document.getElementById('btn-start').hidden = listening;
  document.getElementById('btn-stop').hidden = !listening;

  const credentialPill = document.getElementById('credential-pill');
  credentialPill.textContent = configured ? '已配置' : '未配置';
  credentialPill.className = `pill ${configured ? 'active' : 'idle'}`;
  const missingRequired = [];
  if (!credentials.sesdata_configured) missingRequired.push('SESSDATA');
  if (!credentials.bili_jct_configured) missingRequired.push('bili_jct');
  document.getElementById('credential-summary').textContent = configured
    ? `登录凭据已保存${credentials.dedeuserid_masked ? ` · UID ${credentials.dedeuserid_masked}` : ''}。`
    : `尚未配置 ${missingRequired.join(' 和 ')}，请先保存登录凭据。`;

  fieldStatus('state-sesdata', credentials.sesdata_configured);
  fieldStatus('state-bili-jct', credentials.bili_jct_configured);
  fieldStatus('state-buvid3', credentials.buvid3_configured);
  fieldStatus('state-dedeuserid', credentials.dedeuserid_configured);
  fieldStatus('state-ac-time-value', credentials.ac_time_value_configured);

  document.getElementById('cfg-permission-mode').value = settings.permission_mode || 'allow_list';
  document.getElementById('cfg-max-concurrent').value = settings.max_concurrent_messages || 3;
  document.getElementById('cfg-connect-timeout').value = settings.ai_connect_timeout_seconds || 10;
  document.getElementById('cfg-turn-timeout').value = settings.ai_turn_timeout_seconds || 60;
  renderUsers(dashboard.trusted_users || []);
}

async function refreshDashboard(silent = false) {
  try {
    applyDashboard(await callPlugin('get_dashboard_state', {}));
  } catch (error) {
    if (!silent) showToast(error.message || '刷新失败', true);
  }
}

function optionalSecret(payload, key, elementId) {
  const value = document.getElementById(elementId).value.trim();
  if (value) payload[key] = value;
}

async function saveSettings() {
  const payload = {
    permission_mode: document.getElementById('cfg-permission-mode').value,
    max_concurrent_messages: Number(document.getElementById('cfg-max-concurrent').value || 3),
    ai_connect_timeout_seconds: Number(document.getElementById('cfg-connect-timeout').value || 10),
    ai_turn_timeout_seconds: Number(document.getElementById('cfg-turn-timeout').value || 60),
  };
  optionalSecret(payload, 'sesdata', 'cfg-sesdata');
  optionalSecret(payload, 'bili_jct', 'cfg-bili-jct');
  optionalSecret(payload, 'buvid3', 'cfg-buvid3');
  optionalSecret(payload, 'dedeuserid', 'cfg-dedeuserid');
  optionalSecret(payload, 'ac_time_value', 'cfg-ac-time-value');
  setBusy(true);
  try {
    applyDashboard(await callPlugin('save_settings', payload));
    document.querySelectorAll('.credential-grid input').forEach((input) => { input.value = ''; });
    showToast('配置已保存到本机插件数据目录');
  } catch (error) {
    showToast(error.message || '保存失败', true);
  } finally {
    setBusy(false);
  }
}

async function clearCredentials() {
  setBusy(true);
  try {
    applyDashboard(await callPlugin('clear_credentials', {}));
    showToast('本地凭据已清除');
  } catch (error) {
    showToast(error.message || '清除失败', true);
  } finally {
    setBusy(false);
  }
}

async function toggleListening(start) {
  setBusy(true);
  try {
    applyDashboard(await callPlugin(start ? 'start_listening' : 'stop_listening', {}));
    await refreshDashboard(true);
    showToast(start ? 'B站私信监听已启动' : 'B站私信监听已停止');
  } catch (error) {
    showToast(error.message || '操作失败', true);
  } finally {
    setBusy(false);
  }
}

async function addTrustedUser() {
  const uid = document.getElementById('user-uid').value.trim();
  if (!/^\d+$/.test(uid)) {
    showToast('请输入纯数字 B站 UID', true);
    return;
  }
  setBusy(true);
  try {
    await callPlugin('add_trusted_user', {
      uid,
      level: document.getElementById('user-level').value,
      nickname: document.getElementById('user-nickname').value.trim(),
    });
    document.getElementById('user-uid').value = '';
    document.getElementById('user-nickname').value = '';
    await refreshDashboard(true);
    showToast('信任用户已保存');
  } catch (error) {
    showToast(error.message || '添加失败', true);
  } finally {
    setBusy(false);
  }
}

async function removeTrustedUser(uid) {
  if (!uid) return;
  setBusy(true);
  try {
    await callPlugin('remove_trusted_user', { uid });
    await refreshDashboard(true);
    showToast('信任用户已移除');
  } catch (error) {
    showToast(error.message || '移除失败', true);
  } finally {
    setBusy(false);
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  if (window.I18n) window.I18n.scanDOM();
  document.getElementById('btn-refresh').addEventListener('click', () => refreshDashboard(false));
  document.getElementById('btn-save').addEventListener('click', saveSettings);
  document.getElementById('btn-clear').addEventListener('click', clearCredentials);
  document.getElementById('btn-start').addEventListener('click', () => toggleListening(true));
  document.getElementById('btn-stop').addEventListener('click', () => toggleListening(false));
  document.getElementById('btn-add-user').addEventListener('click', addTrustedUser);
  await refreshDashboard(false);
});
