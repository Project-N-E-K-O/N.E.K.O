/* Copyright 2025-2026 Project N.E.K.O. Team; SPDX-License-Identifier: Apache-2.0 */
// Speech-recognition (ASR) slot in Per-Feature Models.
//
// "Follow the voice model provider" leaves recognition to the Core as before.
// Any other choice overrides the Core's recognition on every Core, the free one
// included, for both the live microphone and voice messages; a failure stops
// with an error instead of falling back to another service. The choices come
// from /api/config/api_providers (asr_slot_vendors), never from this file.
//
// Loaded after api_key_settings.js and uses its helpers (getRealKey,
// setSecretInputValue, Key Book links, the model picker). api_key_settings.js
// calls the entry points below through typeof guards.

const ASR_SLOT_FOLLOW_CORE = 'follow_core';
const ASR_SLOT_CUSTOM_VENDOR = 'custom';
const ASR_SLOT_DEFAULT_TRANSPORT = 'request';
const ASR_SLOT_TRANSCRIPTIONS_PATH = '/audio/transcriptions';
const ASR_SLOT_TEST_TIMEOUT_MS = 70000;

let _asrSlotVendors = {};
// The values the page loaded, so a save can tell whether the custom endpoint
// moved while its saved key is still masked.
let _asrSlotSaved = { url: '', transport: '' };
let _asrSlotTestGeneration = 0;

function asrSlotText(key, fallback, params = null) {
    if (window.t) {
        const translated = window.t(key, { ...(params || {}), defaultValue: fallback });
        if (translated && translated !== key) return translated;
    }
    return String(fallback).replace(/\{\{\s*(\w+)\s*\}\}/g, (_, name) => (
        params && name in params ? String(params[name]) : ''
    ));
}

function asrSlotElement(id) {
    return document.getElementById(id);
}

function asrSlotVendorLabel(vendor) {
    if (vendor === ASR_SLOT_CUSTOM_VENDOR) {
        return asrSlotText('api.customModelProviderCustom', '自定义');
    }
    const translationKey = `api.assistProviderNames.${vendor}`;
    const translated = window.t ? window.t(translationKey) : '';
    if (translated && translated !== translationKey) return translated;
    const providers = typeof _assistApiProviders === 'object' && _assistApiProviders ? _assistApiProviders : {};
    return (providers[vendor] && providers[vendor].name) || vendor;
}

// Inside a sentence: the label without its trailing note, e.g. "Qwen (recommended)" -> "Qwen".
function asrSlotShortVendorLabel(vendor) {
    const label = asrSlotVendorLabel(vendor);
    return label.replace(/\s*[（(][^（()）]*[)）]\s*$/, '') || label;
}

/**
 * The request URL a custom endpoint resolves to, or '' when it is unusable.
 * Mirrors main_logic/asr_client/slot.py normalize_custom_asr_url: an absolute
 * http(s) URL with a host and a valid port; a base URL gets
 * /v1/audio/transcriptions, a /v1-style path gets /audio/transcriptions.
 */
function normalizeCustomAsrUrl(rawUrl) {
    const url = String(rawUrl || '').trim();
    if (!url || /\s/.test(url)) return '';
    // URL() accepts "http:///v1" (host "v1") and an empty port; check the
    // authority by hand first so the result matches the server.
    const authority = /^https?:\/\/(?:[^/?#@]*@)?(\[[^\]/?#]+\]|[^/?#:@[\]]+)(?::(\d*))?(?=[/?#]|$)/i.exec(url);
    if (!authority) return '';
    // An empty port ("host:") means the default one, as on the server.
    if (authority[2]) {
        const port = Number(authority[2]);
        if (!Number.isInteger(port) || port < 1 || port > 65535) return '';
    }
    let parts;
    try {
        parts = new URL(url);
    } catch (_) {
        return '';
    }
    let path = parts.pathname.replace(/\/+$/, '');
    if (!path.toLowerCase().endsWith(ASR_SLOT_TRANSCRIPTIONS_PATH)) {
        path = (path || '/v1') + ASR_SLOT_TRANSCRIPTIONS_PATH;
    }
    const userinfo = parts.username
        ? `${parts.username}${parts.password ? ':' + parts.password : ''}@`
        : '';
    return `${parts.protocol}//${userinfo}${parts.host}${path}${parts.search}`;
}

function sameAsrEndpoint(left, right) {
    const a = normalizeCustomAsrUrl(left) || String(left || '').trim();
    const b = normalizeCustomAsrUrl(right) || String(right || '').trim();
    return Boolean(a) && a === b;
}

function isMaskedAsrKey(value) {
    const sentinel = typeof MASKED_SECRET_SENTINEL === 'string' ? MASKED_SECRET_SENTINEL : '__NEKO_SECRET_MASKED__';
    return value === sentinel;
}

function populateAsrSlotVendors(vendors) {
    const list = Array.isArray(vendors)
        ? vendors.filter(entry => entry && typeof entry.key === 'string' && entry.key)
        : [];
    _asrSlotVendors = {};
    list.forEach(entry => { _asrSlotVendors[entry.key] = entry; });
    const select = asrSlotElement('asrModelProvider');
    if (!select) return;
    const previous = select.value;
    select.replaceChildren();
    appendModelProviderOption(select, ASR_SLOT_FOLLOW_CORE, 'api.customModelProviderFollowCore', '跟随语音模型供应商');
    list.forEach(entry => {
        const restricted = !!isProviderRestricted(entry.key);
        // Keep a saved restricted choice visible without offering it.
        if (restricted && entry.key !== previous) return;
        appendAsrSlotOption(select, entry.key, restricted);
    });
    if (previous && previous !== ASR_SLOT_FOLLOW_CORE && !_asrSlotVendors[previous]) {
        appendAsrSlotOption(select, previous, !!isProviderRestricted(previous));
    }
    select.value = previous || ASR_SLOT_FOLLOW_CORE;
    if (!select.dataset.asrSlotChangeAttached) {
        select.addEventListener('change', () => onAsrSlotProviderChange());
        select.dataset.asrSlotChangeAttached = 'true';
    }
    updateAsrSlotHint();
}

function appendAsrSlotOption(select, vendor, disabled) {
    const option = document.createElement('option');
    option.value = vendor;
    option.textContent = asrSlotVendorLabel(vendor);
    option.disabled = disabled;
    select.appendChild(option);
    return option;
}

function updateAsrSlotHint() {
    const hint = asrSlotElement('asr-slot-hint');
    const select = asrSlotElement('asrModelProvider');
    if (!hint || !select) return;
    const vendor = select.value || ASR_SLOT_FOLLOW_CORE;
    const key = asrSlotElement('asrModelApiKey');
    if (key) {
        key.placeholder = vendor === ASR_SLOT_CUSTOM_VENDOR || !_asrSlotVendors[vendor]
            ? asrSlotText('api.asrSlot.keyPlaceholderCustom', '自建服务没有设置 Key 时可以留空')
            : asrSlotText('api.asrSlot.keyPlaceholderKeyBook', '使用 API 管理簿里该服务商的 Key');
    }
    if (vendor === ASR_SLOT_FOLLOW_CORE) {
        hint.textContent = asrSlotText(
            'api.asrSlot.hintFollow',
            '识别方式和以前一样：由语音模型供应商和麦克风面板的设置决定。'
        );
        return;
    }
    let text = asrSlotText(
        'api.asrSlot.hintOverride',
        '选好后，所有语音模型供应商（包括免费版）都改用这里识别，实时麦克风和语音消息都一样，麦克风面板的识别开关不影响它。识别失败时会报错，不会改用其他服务。下一次语音会话生效。'
    );
    if (vendor === ASR_SLOT_CUSTOM_VENDOR) {
        // Its own line (the hint keeps line breaks), so no language needs a joining space.
        text += '\n' + asrSlotText(
            'api.asrSlot.hintCustom',
            '自定义端点需要兼容 OpenAI 的 /v1/audio/transcriptions 接口，地址填到 /v1 或只填主机和端口都可以。'
        );
    }
    hint.textContent = text;
}

function onAsrSlotProviderChange({ loading = false } = {}) {
    const select = asrSlotElement('asrModelProvider');
    const url = asrSlotElement('asrModelUrl');
    const model = asrSlotElement('asrModelId');
    const key = asrSlotElement('asrModelApiKey');
    if (!select || !url || !model || !key) return;
    const vendor = select.value || ASR_SLOT_FOLLOW_CORE;
    const meta = _asrSlotVendors[vendor];
    const follow = vendor === ASR_SLOT_FOLLOW_CORE;
    const custom = vendor === ASR_SLOT_CUSTOM_VENDOR;
    const fields = asrSlotElement('asr-endpoint-fields');
    if (fields) fields.style.display = follow ? 'none' : '';
    if (!loading) {
        // A key never follows a provider change: it belongs to the endpoint it
        // was entered for.
        url.value = custom ? '' : (meta?.default_url || '');
        model.value = meta?.default_model || '';
        setSecretInputValue('asrModelApiKey', '');
        hideAsrSlotStatus();
    }
    if (follow || (meta && !custom)) {
        // Named vendors use their fixed endpoint and their Key Book key.
        url.value = follow ? '' : (meta.default_url || '');
        if (follow) model.value = '';
        else if (!model.value) model.value = meta.default_model || '';
        setSecretInputValue('asrModelApiKey', '');
    }
    const known = follow || !!meta;
    url.readOnly = known && !custom;
    key.disabled = known && !custom;
    if (meta && !custom && !follow) {
        ensureKeyBookLink(key, vendor);
    } else {
        removeKeyBookLink(key);
    }
    clearAsrSlotTestResult();
    updateAsrSlotHint();
    syncProviderSelectDropdowns(select);
}

function loadAsrSlotSettings(data) {
    const select = asrSlotElement('asrModelProvider');
    if (!select || !data) return;
    const provider = String(data.asrModelProvider || '').trim() || ASR_SLOT_FOLLOW_CORE;
    if (!Array.from(select.options).some(option => option.value === provider)) {
        appendAsrSlotOption(select, provider, !!isProviderRestricted(provider));
    }
    select.value = provider;
    asrSlotElement('asrModelUrl').value = data.asrModelUrl || '';
    asrSlotElement('asrModelId').value = data.asrModelId || '';
    setSecretInputValue('asrModelApiKey', data.asrModelApiKey || '');
    _asrSlotSaved = {
        url: data.asrModelUrl || '',
        transport: String(data.asrModelTransport || '').trim(),
    };
    if (provider === ASR_SLOT_FOLLOW_CORE || _asrSlotVendors[provider]) {
        onAsrSlotProviderChange({ loading: true });
    } else {
        updateAsrSlotHint();
    }
    renderAsrSlotStatus(data);
}

function asrSlotTransport(vendor) {
    const meta = _asrSlotVendors[vendor];
    if (!meta) return _asrSlotSaved.transport;
    const transports = Array.isArray(meta.transports) ? meta.transports : [];
    if (transports.includes(_asrSlotSaved.transport)) return _asrSlotSaved.transport;
    return transports[0] || ASR_SLOT_DEFAULT_TRANSPORT;
}

function asrSlotSettingsPayload() {
    const select = asrSlotElement('asrModelProvider');
    if (!select) return {};
    const vendor = select.value || ASR_SLOT_FOLLOW_CORE;
    const known = vendor === ASR_SLOT_FOLLOW_CORE || Object.hasOwn(_asrSlotVendors, vendor);
    const read = id => {
        const value = asrSlotElement(id).value;
        // Unknown providers (metadata missing) round-trip verbatim.
        return known ? value.trim() : value;
    };
    return {
        asrModelProvider: vendor,
        asrModelTransport: vendor === ASR_SLOT_FOLLOW_CORE
            ? (_asrSlotSaved.transport || ASR_SLOT_DEFAULT_TRANSPORT)
            : asrSlotTransport(vendor),
        asrModelUrl: read('asrModelUrl'),
        asrModelId: read('asrModelId'),
        asrModelApiKey: getRealKey(asrSlotElement('asrModelApiKey')),
    };
}

/** A localized reason the server would reject the slot, or null. */
function validateAsrSlotSettings() {
    const select = asrSlotElement('asrModelProvider');
    if (!select || select.value !== ASR_SLOT_CUSTOM_VENDOR) return null;
    const url = asrSlotElement('asrModelUrl').value;
    if (!normalizeCustomAsrUrl(url)) {
        return asrSlotText(
            'api.asrSlot.invalidUrl',
            '语音识别的 API URL 需要是 http:// 或 https:// 开头的完整地址，例如 http://192.168.1.5:8081'
        );
    }
    const key = getRealKey(asrSlotElement('asrModelApiKey'));
    if (isMaskedAsrKey(key) && !sameAsrEndpoint(_asrSlotSaved.url, url)) {
        return asrSlotText(
            'api.asrSlot.keyRetype',
            '语音识别的地址改了，已保存的 Key 不会带到新地址。请重新填写 Key，或清空它。'
        );
    }
    return null;
}

function resetAsrSlotSettings() {
    const select = asrSlotElement('asrModelProvider');
    if (!select) return;
    select.value = ASR_SLOT_FOLLOW_CORE;
    onAsrSlotProviderChange();
}

function resolveAsrModelPickerRequest() {
    const select = asrSlotElement('asrModelProvider');
    const vendor = select ? select.value : '';
    if (!vendor) return { reason: 'noProvider' };
    if (vendor === ASR_SLOT_FOLLOW_CORE) return { reason: 'follow' };
    if (vendor === ASR_SLOT_CUSTOM_VENDOR) {
        return buildCustomModelListRequest(
            asrSlotElement('asrModelUrl').value.trim(),
            getRealKey(asrSlotElement('asrModelApiKey')),
            'asr'
        );
    }
    if (!_asrSlotVendors[vendor]) return { reason: 'unsupported' };
    const request = buildProviderModelListRequest(
        vendor, getEffectiveAssistKey(vendor, null, { useTokenPlan: false })
    );
    if (request.body) request.body.model_type = 'asr';
    return request;
}

// ---- saved-state warning --------------------------------------------------

function renderAsrSlotStatus(data) {
    const box = asrSlotElement('asr-slot-status');
    if (!box) return;
    const availability = data && data.asrSlotActive ? data.asrSlotAvailability : null;
    let text = '';
    if (availability === 'missing_credentials') {
        text = asrSlotText(
            'api.asrSlot.statusMissingKey',
            '已保存的语音识别还没有可用的 Key，开启语音时会报错。请在 API 管理簿里填写 {{provider}} 的 Key。',
            { provider: asrSlotShortVendorLabel(data.asrSlotProvider || '') }
        );
    } else if (availability === 'misconfigured') {
        text = asrSlotText(
            'api.asrSlot.statusMisconfigured',
            '已保存的语音识别配置无效，开启语音时会报错。请重新选择服务商或修改地址。'
        );
    }
    box.textContent = text;
    box.hidden = !text;
}

function hideAsrSlotStatus() {
    const box = asrSlotElement('asr-slot-status');
    if (!box) return;
    box.textContent = '';
    box.hidden = true;
}

// ---- connectivity test ----------------------------------------------------

const ASR_SLOT_TEST_ERRORS = {
    auth_failed: 'API Key 无效或已过期',
    rate_limited: '请求过于频繁，请稍后再试',
    timeout: '请求超时',
    invalid_response: '返回的不是可识别的转写结果',
    rejected: '服务拒绝了这次识别请求，请检查 URL 和模型 ID',
    server_error: '识别服务已连通，但处理请求时出错，请查看服务端日志',
    connection_refused: '无法连接到识别服务，请检查 URL 是否可达',
    missing_params: '请填写 http(s) 开头的 API URL',
    key_required: '还没有可用的 Key',
    unsupported: '该服务商不支持语音识别',
    backend_unavailable: '无法连接到本地服务',
    unknown: '识别请求失败',
};

function asrSlotTestErrorText(result) {
    const code = result && Object.hasOwn(ASR_SLOT_TEST_ERRORS, result.error_code) ? result.error_code : 'unknown';
    return asrSlotText(`api.asrSlot.error.${code}`, ASR_SLOT_TEST_ERRORS[code]);
}

function setAsrSlotTestResult(text, state) {
    const result = asrSlotElement('asrSlotTestResult');
    if (!result) return;
    result.textContent = text;
    result.dataset.state = state || '';
    result.style.color = state === 'ok' ? '#2e9d5b' : (state === 'failed' ? '#d35400' : '');
}

function clearAsrSlotTestResult() {
    _asrSlotTestGeneration += 1;
    const button = asrSlotElement('asrSlotTestBtn');
    if (button) button.disabled = false;
    setAsrSlotTestResult('', '');
}

function asrSlotTestRequestBody() {
    const vendor = asrSlotElement('asrModelProvider').value;
    const custom = vendor === ASR_SLOT_CUSTOM_VENDOR;
    return {
        provider_key: vendor,
        url: custom ? asrSlotElement('asrModelUrl').value.trim() : '',
        // A vendor's key is its Key Book entry, including one typed there but
        // not saved yet; a masked value means the saved key.
        api_key: custom
            ? getRealKey(asrSlotElement('asrModelApiKey'))
            : getEffectiveAssistKey(vendor, null, { useTokenPlan: false }),
        model: asrSlotElement('asrModelId').value.trim(),
    };
}

async function testAsrSlotConnectivity() {
    const select = asrSlotElement('asrModelProvider');
    const button = asrSlotElement('asrSlotTestBtn');
    if (!select || select.value === ASR_SLOT_FOLLOW_CORE) return;
    if (select.value === ASR_SLOT_CUSTOM_VENDOR && !normalizeCustomAsrUrl(asrSlotElement('asrModelUrl').value)) {
        setAsrSlotTestResult(asrSlotText(
            'api.asrSlot.invalidUrl',
            '语音识别的 API URL 需要是 http:// 或 https:// 开头的完整地址，例如 http://192.168.1.5:8081'
        ), 'failed');
        return;
    }
    const generation = ++_asrSlotTestGeneration;
    if (button) button.disabled = true;
    setAsrSlotTestResult(asrSlotText('api.asrSlot.testing', '正在测试…'), 'testing');
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), ASR_SLOT_TEST_TIMEOUT_MS) : null;
    let result;
    try {
        const response = await fetch('/api/config/test_asr_connectivity', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(asrSlotTestRequestBody()),
            signal: controller ? controller.signal : undefined,
        });
        result = response.ok ? await response.json() : { success: false, error_code: 'backend_unavailable' };
    } catch (error) {
        result = { success: false, error_code: error && error.name === 'AbortError' ? 'timeout' : 'backend_unavailable' };
    } finally {
        if (timer) clearTimeout(timer);
    }
    if (generation !== _asrSlotTestGeneration) return;
    if (button) button.disabled = false;
    if (result && result.success) {
        setAsrSlotTestResult(asrSlotText('api.asrSlot.testOk', '连接成功，可以识别'), 'ok');
    } else {
        setAsrSlotTestResult(asrSlotText(
            'api.asrSlot.testFailed', '测试失败：{{error}}', { error: asrSlotTestErrorText(result) }
        ), 'failed');
    }
}

function initAsrSlotSettings() {
    const button = asrSlotElement('asrSlotTestBtn');
    if (button && !button.dataset.asrSlotTestAttached) {
        button.addEventListener('click', () => { testAsrSlotConnectivity(); });
        button.dataset.asrSlotTestAttached = 'true';
    }
    ['asrModelUrl', 'asrModelId', 'asrModelApiKey'].forEach(id => {
        const input = asrSlotElement(id);
        if (input && !input.dataset.asrSlotInputAttached) {
            input.addEventListener('input', () => { clearAsrSlotTestResult(); hideAsrSlotStatus(); });
            input.dataset.asrSlotInputAttached = 'true';
        }
    });
    updateAsrSlotHint();
}

if (typeof window !== 'undefined' && typeof document !== 'undefined' && document.addEventListener) {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAsrSlotSettings);
    } else {
        initAsrSlotSettings();
    }
    window.addEventListener('localechange', () => {
        const select = asrSlotElement('asrModelProvider');
        if (!select) return;
        Array.from(select.options).forEach(option => {
            if (option.value !== ASR_SLOT_FOLLOW_CORE) option.textContent = asrSlotVendorLabel(option.value);
        });
        updateAsrSlotHint();
        syncProviderSelectDropdowns(select, { rebuild: true });
    });
}
