const RUNS_URL = '/runs';
const pluginMatch = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
const pluginId = pluginMatch ? decodeURIComponent(pluginMatch[1]) : 'bilibili_dm';

const state = { dashboard: null, busy: false };
const qrClientId = globalThis.crypto?.randomUUID?.() || `bili-dm-${Date.now()}-${Math.random()}`;
const qrLogin = { key: null, sessionId: null, starting: false, pollTimer: null, countdownTimer: null, closeTimer: null, expiresAt: 0, generation: 0 };

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

function clearQrLogin() {
  qrLogin.generation += 1;
  qrLogin.key = null;
  qrLogin.sessionId = null;
  qrLogin.starting = false;
  qrLogin.expiresAt = 0;
  if (qrLogin.pollTimer) clearTimeout(qrLogin.pollTimer);
  if (qrLogin.countdownTimer) clearInterval(qrLogin.countdownTimer);
  if (qrLogin.closeTimer) clearTimeout(qrLogin.closeTimer);
  qrLogin.pollTimer = null;
  qrLogin.countdownTimer = null;
  qrLogin.closeTimer = null;
}

function hideQrLogin() {
  clearQrLogin();
  document.getElementById('qr-login-panel').hidden = true;
}

async function cancelQrLogin() {
  const sessionId = qrLogin.sessionId;
  hideQrLogin();
  if (!sessionId) return;
  try {
    await callPlugin('cancel_qr_login', { session_id: sessionId });
  } catch (error) {
    // The UI is already closed; the server-side QR session will expire shortly.
    console.warn('Cancel QR login failed:', error);
  }
}

function updateQrCountdown() {
  const countdown = document.getElementById('qr-login-countdown');
  if (!countdown || !qrLogin.expiresAt) return;
  const seconds = Math.max(0, Math.ceil((qrLogin.expiresAt - Date.now()) / 1000));
  countdown.textContent = seconds ? `二维码剩余 ${seconds} 秒` : '二维码已过期，请刷新';
  if (!seconds) clearQrLogin();
}

function setQrStatus(message) {
  const status = document.getElementById('qr-login-status');
  if (status) status.textContent = String(message || '等待扫码…');
}

async function requestQrLogin() {
  if (state.dashboard?.status?.listening) {
    showToast('请先停止监听，再更新登录凭据', true);
    return;
  }
  if (qrLogin.starting) return;
  clearQrLogin();
  const generation = qrLogin.generation;
  qrLogin.starting = true;
  const panel = document.getElementById('qr-login-panel');
  const image = document.getElementById('qr-login-image');
  panel.hidden = false;
  image.removeAttribute('src');
  setQrStatus('正在获取二维码…');
  document.getElementById('qr-login-countdown').textContent = '';
  try {
    const result = await callPlugin('start_qr_login', {
      client_id: qrClientId,
      request_generation: generation,
    });
    if (!result?.qrcode_image || !result?.session_id) throw new Error(result?.message || '获取二维码失败，请稍后重试');
    if (generation !== qrLogin.generation) {
      await callPlugin('cancel_qr_login', { session_id: result.session_id });
      return;
    }
    qrLogin.key = 'plugin-session';
    qrLogin.sessionId = result.session_id;
    qrLogin.starting = false;
    qrLogin.expiresAt = Date.now() + Number(result.timeout || 180) * 1000;
    image.src = result.qrcode_image;
    setQrStatus('等待扫码…');
    updateQrCountdown();
    qrLogin.countdownTimer = setInterval(updateQrCountdown, 1000);
    pollQrLogin(generation);
  } catch (error) {
    if (generation !== qrLogin.generation) return;
    qrLogin.starting = false;
    setQrStatus(error.message || '获取二维码失败，请稍后重试');
    showToast(error.message || '获取二维码失败', true);
  }
}

async function pollQrLogin(generation) {
  if (generation !== qrLogin.generation || !qrLogin.key || !qrLogin.sessionId) return;
  try {
    const data = await callPlugin('poll_qr_login', { session_id: qrLogin.sessionId });
    if (generation !== qrLogin.generation) return;
    if (data.status === 'done') {
      clearQrLogin();
      const completionGeneration = qrLogin.generation;
      const closeAt = Date.now() + 2000;
      setQrStatus(data.message || '登录成功，配置已自动保存');
      qrLogin.closeTimer = setTimeout(() => {
        if (qrLogin.generation !== completionGeneration) return;
        qrLogin.closeTimer = null;
        document.getElementById('qr-login-panel').hidden = true;
      }, Math.max(0, closeAt - Date.now()));
      await refreshDashboard(true);
      if (completionGeneration !== qrLogin.generation) return;
      showToast(data.message || '扫码登录成功，配置已自动保存');
      return;
    }
    if (data.status === 'expired') {
      clearQrLogin();
      setQrStatus(data.message || '二维码已过期，请刷新');
      return;
    }
    if (data.status === 'no_session' || data.status === 'cancelled') {
      clearQrLogin();
      setQrStatus(data.message || '扫码登录已结束，请重新获取二维码');
      return;
    }
    setQrStatus(data.message || (data.status === 'scanned' ? '已扫码，请在手机上确认…' : '等待扫码…'));
    qrLogin.pollTimer = setTimeout(() => pollQrLogin(generation), 1500);
  } catch (error) {
    if (generation !== qrLogin.generation) return;
    clearQrLogin();
    setQrStatus(error.message || '检查扫码状态失败，请刷新二维码');
  }
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
  document.getElementById('cfg-comment-notifications').value = String(
    settings.enable_comment_notifications !== false
  );
  document.getElementById('cfg-notification-poll-interval').value = settings.notification_poll_interval_seconds || 20;
  document.getElementById('cfg-notification-max-items').value = settings.notification_max_items || 20;
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

async function saveSettings(successMessage = '配置已保存到本机插件数据目录') {
  const payload = {
    permission_mode: document.getElementById('cfg-permission-mode').value,
    max_concurrent_messages: Number(document.getElementById('cfg-max-concurrent').value || 3),
    ai_connect_timeout_seconds: Number(document.getElementById('cfg-connect-timeout').value || 10),
    ai_turn_timeout_seconds: Number(document.getElementById('cfg-turn-timeout').value || 60),
    enable_comment_notifications: document.getElementById('cfg-comment-notifications').value === 'true',
    notification_poll_interval_seconds: Number(document.getElementById('cfg-notification-poll-interval').value || 20),
    notification_max_items: Number(document.getElementById('cfg-notification-max-items').value || 20),
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
    showToast(successMessage);
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
    showToast(start ? 'B站集成监听已启动' : 'B站集成监听已停止');
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
  document.getElementById('btn-save').addEventListener('click', () => saveSettings());
  document.getElementById('btn-qr-login').addEventListener('click', requestQrLogin);
  document.getElementById('btn-qr-refresh').addEventListener('click', requestQrLogin);
  document.getElementById('btn-qr-cancel').addEventListener('click', cancelQrLogin);
  document.getElementById('btn-clear').addEventListener('click', clearCredentials);
  document.getElementById('btn-start').addEventListener('click', () => toggleListening(true));
  document.getElementById('btn-stop').addEventListener('click', () => toggleListening(false));
  document.getElementById('btn-add-user').addEventListener('click', addTrustedUser);
  await refreshDashboard(false);
});
