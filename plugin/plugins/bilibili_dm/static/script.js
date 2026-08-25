const RUNS_URL = '/runs';
const pluginMatch = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
const pluginId = pluginMatch ? decodeURIComponent(pluginMatch[1]) : 'bilibili_dm';

function tr(key, fallback, params = {}) {
  return window.I18n?.t ? window.I18n.t(key, fallback, params) : String(fallback || key).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
  ));
}

const BACKEND_ERROR_KEYS = {
  LISTENING_ACTIVE: 'ui.error.stop_before_credentials',
  QR_LOGIN_START_FAILED: 'ui.error.qr_fetch_failed',
  QR_LOGIN_POLL_FAILED: 'ui.error.qr_status_failed',
  START_ERROR: 'ui.error.operation_failed',
  NOT_INITIALIZED: 'ui.error.operation_failed',
  INVALID_ARGUMENT: 'ui.error.operation_failed',
  SEND_FAILED: 'ui.error.operation_failed',
  USER_NOT_FOUND: 'ui.error.operation_failed',
  ADMIN_NO_NICKNAME: 'ui.error.operation_failed',
  SET_FAILED: 'ui.error.operation_failed',
};

function localizedError(error, fallbackKey, fallback) {
  const message = String(error?.message || error || '').trim();
  const code = /^([A-Z_]+):/.exec(message)?.[1];
  const key = code && BACKEND_ERROR_KEYS[code];
  if (key) return tr(key, fallback);
  // Backend errors without a recognized code may still be source-language
  // strings. Keep transport/browser diagnostics, but never leak those into a
  // translated panel.
  if (!message || /[\u3400-\u9fff]/.test(message)) return tr(fallbackKey, fallback);
  return message;
}

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
  if (!runId) throw new Error(tr('ui.error.no_run_id', 'Unable to obtain run ID'));

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
        if (raw && raw.error) throw new Error(raw.error.message || raw.error || tr('ui.error.operation_failed', 'Operation failed'));
        return raw && raw.value && typeof raw.value === 'object' ? raw.value : raw;
      }
      if (['failed', 'canceled', 'timeout'].includes(run.status)) {
        throw new Error((run.error && run.error.message) || run.message || run.status);
      }
    }
    await delay(400);
  }
  throw new Error(tr('ui.error.timeout', 'Request timed out'));
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
  countdown.textContent = seconds ? tr('ui.qr.countdown', '{seconds} seconds remaining', { seconds }) : tr('ui.qr.expired', 'QR code expired. Please refresh.');
  if (!seconds) clearQrLogin();
}

function setQrStatus(message) {
  const status = document.getElementById('qr-login-status');
  if (status) status.textContent = String(message || tr('ui.qr.waiting', 'Waiting for scan…'));
}

async function requestQrLogin() {
  if (state.dashboard?.status?.listening) {
    showToast(tr('ui.error.stop_before_credentials', 'Stop listening before updating credentials.'), true);
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
  setQrStatus(tr('ui.qr.loading', 'Getting QR code…'));
  document.getElementById('qr-login-countdown').textContent = '';
  try {
    const result = await callPlugin('start_qr_login', {
      client_id: qrClientId,
      request_generation: generation,
    });
    if (!result?.qrcode_image || !result?.session_id) throw new Error(tr('ui.error.qr_fetch_failed', 'Unable to get QR code. Please try again.'));
    if (generation !== qrLogin.generation) {
      await callPlugin('cancel_qr_login', { session_id: result.session_id });
      return;
    }
    qrLogin.key = 'plugin-session';
    qrLogin.sessionId = result.session_id;
    qrLogin.starting = false;
    qrLogin.expiresAt = Date.now() + Number(result.timeout || 180) * 1000;
    image.src = result.qrcode_image;
    setQrStatus(tr('ui.qr.waiting', 'Waiting for scan…'));
    updateQrCountdown();
    qrLogin.countdownTimer = setInterval(updateQrCountdown, 1000);
    pollQrLogin(generation);
  } catch (error) {
    if (generation !== qrLogin.generation) return;
    qrLogin.starting = false;
    const message = localizedError(error, 'ui.error.qr_fetch_failed', 'Unable to get QR code. Please try again.');
    setQrStatus(message);
    showToast(message, true);
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
      setQrStatus(tr('ui.qr.login_saved', 'Login successful. Settings were saved automatically.'));
      qrLogin.closeTimer = setTimeout(() => {
        if (qrLogin.generation !== completionGeneration) return;
        qrLogin.closeTimer = null;
        document.getElementById('qr-login-panel').hidden = true;
      }, Math.max(0, closeAt - Date.now()));
      await refreshDashboard(true);
      if (completionGeneration !== qrLogin.generation) return;
      showToast(tr('ui.qr.login_saved', 'Login successful. Settings were saved automatically.'));
      return;
    }
    if (data.status === 'expired') {
      clearQrLogin();
      setQrStatus(tr('ui.qr.expired', 'QR code expired. Please refresh.'));
      return;
    }
    if (data.status === 'no_session' || data.status === 'cancelled') {
      clearQrLogin();
      setQrStatus(tr('ui.qr.session_ended', 'QR login ended. Please get a new QR code.'));
      return;
    }
    setQrStatus(data.status === 'scanned'
      ? tr('ui.qr.scanned', 'Scanned. Please confirm on your phone…')
      : tr('ui.qr.waiting', 'Waiting for scan…'));
    qrLogin.pollTimer = setTimeout(() => pollQrLogin(generation), 1500);
  } catch (error) {
    if (generation !== qrLogin.generation) return;
    clearQrLogin();
    setQrStatus(localizedError(error, 'ui.error.qr_status_failed', 'Unable to check QR status. Please refresh the QR code.'));
  }
}

function fieldStatus(id, configured) {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = configured ? tr('ui.credentials.saved_keep_blank', 'Saved (leave blank to keep)') : tr('ui.credentials.not_saved', 'Not saved');
  element.classList.toggle('configured', configured);
}

function renderUsers(users) {
  const list = document.getElementById('trusted-users');
  list.replaceChildren();
  const normalized = Array.isArray(users) ? users : [];
  document.getElementById('trusted-count').textContent = tr('ui.users.count', '{count} users', { count: normalized.length });
  if (!normalized.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = tr('user_list.empty', 'No trusted users yet');
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
    const level = tr(`ui.users.level.${user.level}`, user.level);
    meta.textContent = `${user.nickname ? `UID ${user.uid} · ` : ''}${level}`;
    identity.append(name, meta);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'link-danger';
    remove.textContent = tr('ui.actions.remove', 'Remove');
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
  listener.textContent = listening ? tr('ui.status.listening', 'Listening') : tr('ui.status.stopped', 'Stopped');
  listener.className = `pill ${listening ? 'active' : 'idle'}`;
  document.getElementById('btn-start').hidden = listening;
  document.getElementById('btn-stop').hidden = !listening;

  const credentialPill = document.getElementById('credential-pill');
  credentialPill.textContent = configured ? tr('ui.status.configured', 'Configured') : tr('ui.status.not_configured', 'Not configured');
  credentialPill.className = `pill ${configured ? 'active' : 'idle'}`;
  const missingRequired = [];
  if (!credentials.sesdata_configured) missingRequired.push('SESSDATA');
  if (!credentials.bili_jct_configured) missingRequired.push('bili_jct');
  document.getElementById('credential-summary').textContent = configured
    ? tr('ui.credentials.summary_saved', 'Login credentials saved{uid}.', { uid: credentials.dedeuserid_masked ? ` · UID ${credentials.dedeuserid_masked}` : '' })
    : tr('ui.credentials.summary_missing', '{fields} is not configured. Save your login credentials first.', { fields: missingRequired.join(' / ') });

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
    if (!silent) showToast(localizedError(error, 'ui.error.refresh_failed', 'Refresh failed'), true);
  }
}

function optionalSecret(payload, key, elementId) {
  const value = document.getElementById(elementId).value.trim();
  if (value) payload[key] = value;
}

async function saveSettings(successMessage = tr('ui.toast.settings_saved', 'Settings saved to this device')) {
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
    showToast(localizedError(error, 'ui.error.save_failed', 'Save failed'), true);
  } finally {
    setBusy(false);
  }
}

async function clearCredentials() {
  setBusy(true);
  try {
    applyDashboard(await callPlugin('clear_credentials', {}));
    showToast(tr('ui.toast.credentials_cleared', 'Local credentials cleared'));
  } catch (error) {
    showToast(localizedError(error, 'ui.error.clear_failed', 'Clear failed'), true);
  } finally {
    setBusy(false);
  }
}

async function toggleListening(start) {
  setBusy(true);
  try {
    applyDashboard(await callPlugin(start ? 'start_listening' : 'stop_listening', {}));
    await refreshDashboard(true);
    showToast(start ? tr('ui.toast.listening_started', 'Bilibili listening started') : tr('ui.toast.listening_stopped', 'Bilibili listening stopped'));
  } catch (error) {
    showToast(localizedError(error, 'ui.error.operation_failed', 'Operation failed'), true);
  } finally {
    setBusy(false);
  }
}

async function addTrustedUser() {
  const uid = document.getElementById('user-uid').value.trim();
  if (!/^\d+$/.test(uid)) {
    showToast(tr('ui.error.invalid_uid', 'Enter a numeric Bilibili UID'), true);
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
    showToast(tr('ui.toast.user_saved', 'Trusted user saved'));
  } catch (error) {
    showToast(localizedError(error, 'ui.error.add_failed', 'Add failed'), true);
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
    showToast(tr('ui.toast.user_removed', 'Trusted user removed'));
  } catch (error) {
    showToast(localizedError(error, 'ui.error.remove_failed', 'Remove failed'), true);
  } finally {
    setBusy(false);
  }
}

function initializePanel() {
  document.getElementById('btn-refresh').addEventListener('click', () => refreshDashboard(false));
  document.getElementById('btn-save').addEventListener('click', () => saveSettings());
  document.getElementById('btn-qr-login').addEventListener('click', requestQrLogin);
  document.getElementById('btn-qr-refresh').addEventListener('click', requestQrLogin);
  document.getElementById('btn-qr-cancel').addEventListener('click', cancelQrLogin);
  document.getElementById('btn-clear').addEventListener('click', clearCredentials);
  document.getElementById('btn-start').addEventListener('click', () => toggleListening(true));
  document.getElementById('btn-stop').addEventListener('click', () => toggleListening(false));
  document.getElementById('btn-add-user').addEventListener('click', addTrustedUser);
  return refreshDashboard(false);
}

window.addEventListener('DOMContentLoaded', () => {
  let initialized = false;
  let startedByFallback = false;
  const startPanel = (fromFallback = false) => {
    if (initialized) return;
    initialized = true;
    startedByFallback = fromFallback;
    void initializePanel();
  };
  const fallbackTimer = setTimeout(() => startPanel(true), 5000);
  if (window.I18n?.whenReady) {
    window.I18n.whenReady(() => {
      clearTimeout(fallbackTimer);
      startPanel();
    });
    window.addEventListener('i18n-ready', () => {
      if (startedByFallback) void refreshDashboard(true);
    }, { once: true });
  } else {
    clearTimeout(fallbackTimer);
    startPanel();
  }
});
