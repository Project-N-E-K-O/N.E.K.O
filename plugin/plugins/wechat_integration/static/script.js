const pluginId = 'wechat_integration';
const RUNS_URL = '/runs';
const RUN_POLL_DELAY_MS = 500;

function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function callPlugin(entry, args = {}) {
    const resp = await fetch(RUNS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId, entry_id: entry, args })
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const { run_id, id } = await resp.json();
    const runId = run_id || id;
    if (!runId) throw new Error('未获取到 run_id');

    const deadline = Date.now() + 25000;
    while (Date.now() < deadline) {
        const poll = await fetch(`${RUNS_URL}/${runId}`);
        if (!poll.ok) {
            await delay(RUN_POLL_DELAY_MS);
            continue;
        }
        const rec = await poll.json();
        if (rec.status === 'succeeded') {
            const exp = await fetch(`${RUNS_URL}/${runId}/export`);
            if (!exp.ok) return {};
            const { items = [] } = await exp.json();
            const item = items.find(i => i.type === 'json' && i.json) || items[0];
            if (!item) return {};
            let raw = item.json || {};
            while (raw && raw.data && typeof raw.data === 'object' && ('success' in raw.data || 'error' in raw.data)) {
                raw = raw.data;
            }
            return raw;
        }
        if (['failed', 'canceled', 'timeout'].includes(rec.status)) {
            throw new Error(rec.error?.message || rec.message || rec.status);
        }
        await delay(RUN_POLL_DELAY_MS);
    }
    throw new Error('调用超时');
}

let state = {
    settings: { baseUrl: 'https://ilinkai.weixin.qq.com', botType: '3' },
    dashboard: null,
    pollingTimer: null,
    loginPollInFlight: false,
    isLoggedIn: false,
    qrcodeSessionActive: false,
    autoReplyRunning: false,
};

function t(key, fallback) {
    return window.I18n && typeof window.I18n.t === 'function'
        ? window.I18n.t(key, fallback)
        : (fallback || key);
}

function showToast(message) {
    const el = document.getElementById('toast');
    el.textContent = message;
    el.classList.add('show');
    window.clearTimeout(showToast._timer);
    showToast._timer = window.setTimeout(() => el.classList.remove('show'), 3000);
}

function applyDashboardState(payload) {
    const raw = payload || {};
    const data = raw.value || raw.data || raw;
    state.dashboard = data;
    state.isLoggedIn = !!(data.login && data.login.logged_in);

    // Settings
    const settings = data.settings || {};
    state.settings.baseUrl = String(settings.base_url || 'https://ilinkai.weixin.qq.com');
    state.settings.botType = String(settings.bot_type || '3');
    const elBaseUrl = document.getElementById('cfg-base-url');
    const elBotType = document.getElementById('cfg-bot-type');
    if (elBaseUrl) elBaseUrl.value = state.settings.baseUrl;
    if (elBotType) elBotType.value = state.settings.botType;

    // Login status pill
    const pill = document.getElementById('login-status-pill');
    if (pill) {
        if (state.isLoggedIn) {
            pill.textContent = t('ui.status.logged_in', '已登录');
            pill.style.background = '#dcfce7'; pill.style.color = '#166534';
        } else {
            pill.textContent = t('ui.status.idle', '未登录');
            pill.style.background = ''; pill.style.color = '';
        }
    }

    // QR elements
    const qrImage = document.getElementById('qrcode-image');
    const qrLoading = document.getElementById('qrcode-loading');
    const qrEmpty = document.getElementById('qrcode-empty');
    const qrCard = document.getElementById('qrcode-card');
    const qrToggle = document.getElementById('qrcode-toggle');
    const btnStart = document.getElementById('btn-start-login');
    const btnRefresh = document.getElementById('btn-refresh-qrcode');
    const loginTips = document.getElementById('login-tips');
    const qrcodeHint = document.getElementById('qrcode-hint');
    const accountOverlay = document.getElementById('account-overlay');

    const collapsed = qrCard ? qrCard.classList.contains('collapsed') : false;
    const qrUrl = (data.qrcode && data.qrcode.url) || '';
    const hasSession = data.qrcode && data.qrcode.has_session && qrUrl;
    const qrStatus = (data.qrcode && data.qrcode.status) || 'idle';
    state.qrcodeSessionActive = hasSession && qrStatus === 'wait';

    // ---- QR area display logic ----
    if (state.isLoggedIn) {
        // ✅ Logged in: hide QR elements, show account overlay
        if (qrImage) qrImage.style.display = 'none';
        if (qrLoading) qrLoading.style.display = 'none';
        if (qrEmpty) qrEmpty.style.display = 'none';
        if (accountOverlay) accountOverlay.style.display = collapsed ? 'none' : 'flex';
        if (loginTips) loginTips.style.display = 'none';
        if (qrcodeHint) qrcodeHint.style.display = 'none';
        if (qrCard) qrCard.classList.add('is-logged-in');
        // Fill account info
        const elAcct = document.getElementById('status-account-id');
        const elUser = document.getElementById('status-user-id');
        if (elAcct) elAcct.textContent = data.login.account_id || '-';
        if (elUser) elUser.textContent = data.login.user_id || '-';
    } else if (hasSession) {
        // 📱 QR active
        if (qrImage) { qrImage.src = qrUrl; qrImage.style.display = collapsed ? 'none' : 'block'; }
        if (qrLoading) qrLoading.style.display = 'none';
        if (qrEmpty) qrEmpty.style.display = 'none';
        if (accountOverlay) accountOverlay.style.display = 'none';
        if (loginTips) loginTips.style.display = collapsed ? 'none' : 'block';
        if (qrcodeHint) qrcodeHint.style.display = collapsed ? 'none' : 'block';
        if (qrCard) qrCard.classList.remove('is-logged-in');
    } else {
        // 🔄 No QR / loading
        if (qrImage) { qrImage.removeAttribute('src'); qrImage.style.display = 'none'; }
        if (qrLoading) qrLoading.style.display = 'none';
        if (accountOverlay) accountOverlay.style.display = 'none';
        if (loginTips) loginTips.style.display = 'none';
        if (qrcodeHint) qrcodeHint.style.display = collapsed ? 'none' : 'block';
        if (qrCard) qrCard.classList.remove('is-logged-in');

        if (qrEmpty) {
            qrEmpty.style.display = collapsed ? 'none' : 'flex';
            const icon = qrStatus === 'expired' || (data.qrcode && data.qrcode.expired_count > 0) ? '⏰' : '📱';
            const msg = qrStatus === 'expired' || (data.qrcode && data.qrcode.expired_count > 0)
                ? t('ui.qrcode.expired', '二维码已过期，请点击刷新')
                : t('ui.qrcode.empty', '点击下方按钮获取二维码');
            qrEmpty.innerHTML = '<span class="qrcode-placeholder-icon">' + icon + '</span><span>' + msg + '</span>';
        }
    }

    // Buttons
    if (btnStart && btnRefresh) {
        if (state.isLoggedIn) {
            btnStart.style.display = 'none';
            btnRefresh.style.display = 'none';
        } else if (state.qrcodeSessionActive) {
            btnStart.style.display = 'none';
            btnRefresh.style.display = 'inline-block';
        } else {
            btnStart.textContent = t('ui.qrcode.start', '开始扫码登录');
            btnStart.style.display = 'inline-block';
            btnRefresh.style.display = 'none';
        }
    }
    if (qrToggle) {
        qrToggle.textContent = collapsed ? t('ui.qrcode.toggle.show', '显示') : t('ui.qrcode.toggle.hide', '隐藏');
    }

    // Error
    const qrError = document.getElementById('qrcode-error');
    if (qrError) {
        const errMsg = (data.login && data.login.error) || (data.qrcode && data.qrcode.error);
        if (errMsg && !state.isLoggedIn) {
            qrError.textContent = '❌ ' + errMsg;
            qrError.style.display = 'block';
        } else {
            qrError.style.display = 'none';
        }
    }

    // Polling
    if (state.qrcodeSessionActive && !state.isLoggedIn) {
        startPolling();
    } else {
        stopPolling();
    }

    // ---- Message monitor section ----
    const autoReplyRunning = !!(settings.auto_reply_running);
    state.autoReplyRunning = autoReplyRunning;
    const monitorPill = document.getElementById('monitor-status-pill');
    const btnStartMonitor = document.getElementById('btn-start-monitor');
    const btnStopMonitor = document.getElementById('btn-stop-monitor');

    if (monitorPill) {
        if (autoReplyRunning) {
            monitorPill.textContent = t('ui.monitor.running', '监听中');
            monitorPill.style.background = '#dcfce7'; monitorPill.style.color = '#166534';
        } else {
            monitorPill.textContent = t('ui.monitor.stopped', '已停止');
            monitorPill.style.background = ''; monitorPill.style.color = '';
        }
    }
    if (btnStartMonitor && btnStopMonitor) {
        if (!state.isLoggedIn) {
            btnStartMonitor.style.display = 'none';
            btnStopMonitor.style.display = 'none';
        } else if (autoReplyRunning) {
            btnStartMonitor.style.display = 'none';
            btnStopMonitor.style.display = 'inline-block';
        } else {
            btnStartMonitor.style.display = 'inline-block';
            btnStopMonitor.style.display = 'none';
        }
    }
}

// ---- Polling ----
function startPolling() {
    if (state.pollingTimer) return;
    state.pollingTimer = setInterval(async () => {
        if (!state.qrcodeSessionActive || state.isLoggedIn) { stopPolling(); return; }
        if (state.loginPollInFlight) return;
        state.loginPollInFlight = true;
        try {
            const payload = await callPlugin('poll_login_status', {});
            applyDashboardState(payload);
        } catch (e) { /* ignore poll errors */ }
        finally {
            state.loginPollInFlight = false;
        }
    }, 3000);
}

function stopPolling() {
    if (state.pollingTimer) { clearInterval(state.pollingTimer); state.pollingTimer = null; }
    state.loginPollInFlight = false;
}

// ---- Actions ----
async function startLogin() {
    if (state.isLoggedIn) { showToast(t('ui.toast.already_logged_in', '已登录')); return; }
    const qrLoading = document.getElementById('qrcode-loading');
    const qrEmpty = document.getElementById('qrcode-empty');
    const qrImage = document.getElementById('qrcode-image');
    const accountOverlay = document.getElementById('account-overlay');
    if (qrLoading) qrLoading.style.display = 'flex';
    if (qrEmpty) qrEmpty.style.display = 'none';
    if (qrImage) qrImage.style.display = 'none';
    if (accountOverlay) accountOverlay.style.display = 'none';

    try {
        const payload = await callPlugin('start_login', {});
        applyDashboardState(payload);
        if (state.qrcodeSessionActive) showToast(t('ui.toast.qrcode_ready', '二维码已生成，请用微信扫码'));
    } catch (error) {
        showToast(error.message || t('ui.toast.login_failed', '获取二维码失败'));
        if (qrLoading) qrLoading.style.display = 'none';
        if (qrEmpty) qrEmpty.style.display = 'flex';
    }
}

async function refreshQrcode() {
    if (state.isLoggedIn) return;
    const qrLoading = document.getElementById('qrcode-loading');
    const qrEmpty = document.getElementById('qrcode-empty');
    const qrImage = document.getElementById('qrcode-image');
    if (qrLoading) qrLoading.style.display = 'flex';
    if (qrEmpty) qrEmpty.style.display = 'none';
    if (qrImage) qrImage.style.display = 'none';

    try {
        const payload = await callPlugin('refresh_qrcode', {});
        applyDashboardState(payload);
        showToast(t('ui.toast.qrcode_refreshed', '二维码已刷新'));
    } catch (error) {
        showToast(error.message || t('ui.toast.login_failed', '刷新失败'));
        if (qrLoading) qrLoading.style.display = 'none';
        if (qrEmpty) qrEmpty.style.display = 'flex';
    }
}

function toggleQrcodeCard() {
    const card = document.getElementById('qrcode-card');
    if (!card) return;
    card.classList.toggle('collapsed');
    if (state.dashboard) applyDashboardState(state.dashboard);
}

async function saveSettings() {
    try {
        const elBaseUrl = document.getElementById('cfg-base-url');
        const elBotType = document.getElementById('cfg-bot-type');
        await callPlugin('save_settings', {
            base_url: elBaseUrl ? elBaseUrl.value.trim() : '',
            bot_type: elBotType ? elBotType.value.trim() : '',
        });
        await reloadDashboard();
        showToast(t('ui.toast.saved', '设置已保存'));
    } catch (error) {
        showToast(error.message || t('ui.toast.save_failed', '保存失败'));
    }
}

async function reloadDashboard() {
    try {
        const payload = await callPlugin('get_dashboard_state', {});
        applyDashboardState(payload);
        return payload;
    } catch (error) {
        showToast(error.message || t('ui.toast.load_failed', '加载失败'));
    }
}

// ---- Bootstrap ----
window.startLogin = startLogin;
window.refreshQrcode = refreshQrcode;
window.toggleQrcodeCard = toggleQrcodeCard;
window.startAutoReply = startAutoReply;
window.stopAutoReply = stopAutoReply;

async function startAutoReply() {
    if (!state.isLoggedIn) { showToast(t('ui.toast.need_login', '请先登录')); return; }
    try {
        await callPlugin('start_auto_reply', {});
        await reloadDashboard();
        showToast(t('ui.toast.monitor_started', '消息监听已开启'));
    } catch (error) {
        showToast(error.message || t('ui.toast.monitor_failed', '开启失败'));
    }
}

async function stopAutoReply() {
    try {
        await callPlugin('stop_auto_reply', {});
        await reloadDashboard();
        showToast(t('ui.toast.monitor_stopped', '消息监听已停止'));
    } catch (error) {
        showToast(error.message || t('ui.toast.monitor_failed', '停止失败'));
    }
}

window.addEventListener('localechange', function () {
    if (state.dashboard) applyDashboardState(state.dashboard);
});

(function bootstrap() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    async function init() {
        if (window.I18n && window.I18n.whenReady) {
            await new Promise(function (resolve) { window.I18n.whenReady(resolve); });
        }
        var btnSave = document.getElementById('save-settings-btn');
        if (btnSave) btnSave.addEventListener('click', saveSettings);

        try {
            await reloadDashboard();
        } catch (e) {
            showToast(t('ui.toast.load_failed', '加载失败'));
        }
    }
})();
