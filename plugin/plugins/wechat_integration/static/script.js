const pluginId = 'wechat_integration';
const RUNS_URL = '/runs';

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
        if (!poll.ok) continue;
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
    }
    throw new Error('调用超时');
}

let state = {
    settings: {
        baseUrl: 'https://ilinkai.weixin.qq.com',
        botType: '3',
    },
    dashboard: null,
    pollingTimer: null,
    isLoggedIn: false,
    qrcodeSessionActive: false,
};

function uiT(key, fallback) {
    return window.I18n && typeof window.I18n.t === 'function'
        ? window.I18n.t(key, fallback)
        : (fallback || key);
}

function t(key, fallback) { return uiT(key, fallback); }

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

    document.getElementById('cfg-base-url').value = state.settings.baseUrl;
    document.getElementById('cfg-bot-type').value = state.settings.botType;

    // Login status pill
    const loginStatus = document.getElementById('login-status-pill');
    if (state.isLoggedIn) {
        loginStatus.textContent = t('ui.status.logged_in', '已登录');
        loginStatus.style.background = '#dcfce7';
        loginStatus.style.color = '#166534';
    } else {
        loginStatus.textContent = t('ui.status.idle', '未登录');
        loginStatus.style.background = '';
        loginStatus.style.color = '';
    }

    // QR Code
    const qrcode = data.qrcode || {};
    const qrcodeImage = document.getElementById('qrcode-image');
    const qrcodeEmpty = document.getElementById('qrcode-empty');
    const qrcodeLoading = document.getElementById('qrcode-loading');
    const qrcodeCard = document.getElementById('qrcode-card');
    const qrcodeToggle = document.getElementById('qrcode-toggle');
    const btnStart = document.getElementById('btn-start-login');
    const btnRefresh = document.getElementById('btn-refresh-qrcode');
    const loginTips = document.getElementById('login-tips');

    const collapsed = Boolean(qrcodeCard?.classList.contains('collapsed'));
    const qrUrl = qrcode.url || '';
    const hasSession = qrcode.has_session && qrUrl;
    const qrStatus = qrcode.status || 'idle';
    state.qrcodeSessionActive = hasSession && qrStatus === 'wait';

    if (qrcodeImage && qrcodeEmpty && qrcodeLoading) {
        if (state.isLoggedIn) {
            // Logged in
            qrcodeImage.style.display = 'none';
            qrcodeLoading.style.display = 'none';
            qrcodeEmpty.style.display = collapsed ? 'none' : 'flex';
            qrcodeEmpty.innerHTML = '<span class="qrcode-placeholder-icon">✅</span><span>' + t('ui.qrcode.logged_in', '已登录成功') + '</span>';
            if (loginTips) loginTips.style.display = 'none';
        } else if (hasSession) {
            // QR code available
            qrcodeImage.src = qrUrl;
            qrcodeImage.style.display = collapsed ? 'none' : 'block';
            qrcodeLoading.style.display = 'none';
            qrcodeEmpty.style.display = 'none';
            if (loginTips) loginTips.style.display = collapsed ? 'none' : 'block';
        } else if (qrStatus === 'expired' || qrcode.expired_count > 0) {
            // QR expired
            qrcodeImage.removeAttribute('src');
            qrcodeImage.style.display = 'none';
            qrcodeLoading.style.display = 'none';
            qrcodeEmpty.style.display = collapsed ? 'none' : 'flex';
            qrcodeEmpty.innerHTML = '<span class="qrcode-placeholder-icon">⏰</span><span>' + t('ui.qrcode.expired', '二维码已过期，请点击刷新') + '</span>';
            if (loginTips) loginTips.style.display = 'none';
        } else {
            // No session
            qrcodeImage.removeAttribute('src');
            qrcodeImage.style.display = 'none';
            qrcodeLoading.style.display = 'none';
            qrcodeEmpty.style.display = collapsed ? 'none' : 'flex';
            qrcodeEmpty.innerHTML = '<span class="qrcode-placeholder-icon">📱</span><span>' + t('ui.qrcode.empty', '点击下方按钮获取二维码') + '</span>';
            if (loginTips) loginTips.style.display = 'none';
        }
    }

    // Button visibility
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

    if (qrcodeToggle && qrcodeCard) {
        qrcodeToggle.textContent = collapsed ? t('ui.qrcode.toggle.show', '显示二维码') : t('ui.qrcode.toggle.hide', '隐藏二维码');
    }

    // Account card
    const accountCard = document.getElementById('account-card');
    if (accountCard) {
        accountCard.style.display = state.isLoggedIn ? 'block' : 'none';
        if (state.isLoggedIn) {
            document.getElementById('status-account-id').textContent = data.login.account_id || '-';
            document.getElementById('status-user-id').textContent = data.login.user_id || '-';
        }
    }

    // Error message
    const qrcodeError = document.getElementById('qrcode-error');
    if (qrcodeError) {
        const errMsg = data.login?.error || qrcode.error;
        if (errMsg && !state.isLoggedIn) {
            qrcodeError.textContent = '❌ ' + errMsg;
            qrcodeError.style.display = 'block';
        } else {
            qrcodeError.style.display = 'none';
        }
    }

    // Polling
    if (state.qrcodeSessionActive && !state.isLoggedIn) {
        startPolling();
    } else {
        stopPolling();
    }
}

// QR code polling
function startPolling() {
    if (state.pollingTimer) return;
    console.log('[wechat_integration] polling started');
    state.pollingTimer = setInterval(async () => {
        if (!state.qrcodeSessionActive || state.isLoggedIn) {
            stopPolling();
            return;
        }
        try {
            const payload = await callPlugin('poll_login_status', {});
            applyDashboardState(payload);
        } catch (error) {
            console.warn('[wechat_integration] poll failed:', error);
        }
    }, 3000);
}

function stopPolling() {
    if (state.pollingTimer) {
        console.log('[wechat_integration] polling stopped');
        clearInterval(state.pollingTimer);
        state.pollingTimer = null;
    }
}

// actions
async function startLogin() {
    if (state.isLoggedIn) {
        showToast(t('ui.toast.already_logged_in', '已登录，无需重新扫码'));
        return;
    }
    // Show loading state
    const qrcodeLoading = document.getElementById('qrcode-loading');
    const qrcodeEmpty = document.getElementById('qrcode-empty');
    const qrcodeImage = document.getElementById('qrcode-image');
    if (qrcodeLoading) qrcodeLoading.style.display = 'flex';
    if (qrcodeEmpty) qrcodeEmpty.style.display = 'none';
    if (qrcodeImage) qrcodeImage.style.display = 'none';

    try {
        const payload = await callPlugin('start_login', {});
        applyDashboardState(payload);
        if (state.qrcodeSessionActive) {
            showToast(t('ui.toast.qrcode_ready', '二维码已生成，请用微信扫码'));
        }
    } catch (error) {
        showToast(error.message || t('ui.toast.login_failed', '获取二维码失败'));
        // Reset to empty
        if (qrcodeLoading) qrcodeLoading.style.display = 'none';
        if (qrcodeEmpty) qrcodeEmpty.style.display = 'flex';
    }
}

async function refreshQrcode() {
    if (state.isLoggedIn) return;
    // Show loading
    const qrcodeLoading = document.getElementById('qrcode-loading');
    const qrcodeEmpty = document.getElementById('qrcode-empty');
    const qrcodeImage = document.getElementById('qrcode-image');
    if (qrcodeLoading) qrcodeLoading.style.display = 'flex';
    if (qrcodeEmpty) qrcodeEmpty.style.display = 'none';
    if (qrcodeImage) qrcodeImage.style.display = 'none';

    try {
        const payload = await callPlugin('refresh_qrcode', {});
        applyDashboardState(payload);
        showToast(t('ui.toast.qrcode_refreshed', '二维码已刷新'));
    } catch (error) {
        showToast(error.message || t('ui.toast.login_failed', '刷新二维码失败'));
        if (qrcodeLoading) qrcodeLoading.style.display = 'none';
        if (qrcodeEmpty) qrcodeEmpty.style.display = 'flex';
    }
}

function toggleQrcodeCard() {
    const card = document.getElementById('qrcode-card');
    if (!card) return;
    card.classList.toggle('collapsed');
    if (state.dashboard) {
        applyDashboardState(state.dashboard);
    }
}

function toggleConfig() {
    const body = document.getElementById('config-body');
    const arrow = document.getElementById('config-arrow');
    if (!body || !arrow) return;
    const isVisible = body.style.display !== 'none';
    body.style.display = isVisible ? 'none' : 'block';
    arrow.textContent = isVisible ? '▼' : '▲';
}

async function saveSettings() {
    try {
        await callPlugin('save_settings', {
            base_url: document.getElementById('cfg-base-url').value.trim(),
            bot_type: document.getElementById('cfg-bot-type').value.trim(),
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

// Event bindings
document.getElementById('save-settings-btn').addEventListener('click', saveSettings);

window.startLogin = startLogin;
window.refreshQrcode = refreshQrcode;
window.toggleQrcodeCard = toggleQrcodeCard;
window.toggleConfig = toggleConfig;

window.addEventListener('wechat-integration-i18n-refreshed', (event) => {
    if (state.dashboard) {
        applyDashboardState(state.dashboard);
    }
});

window.addEventListener('localechange', () => {
    if (state.dashboard) {
        applyDashboardState(state.dashboard);
    }
});

window.onload = async () => {
    if (window.I18n?.whenReady) {
        await new Promise((resolve) => window.I18n.whenReady(resolve));
    }
    try {
        await reloadDashboard();
        // Auto-start login if not logged in
        if (!state.isLoggedIn) {
            await startLogin();
        }
    } catch (error) {
        showToast(error.message || t('ui.toast.load_failed', '加载失败'));
    }
};
