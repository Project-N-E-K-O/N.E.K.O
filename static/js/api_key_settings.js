/**
 * API 密钥设置模块
 * 负责处理 API 密钥的存储、验证和显示
 * 包含对中国大陆用户的特殊处理
 */
// 全局变量：是否为中国大陆用户
let isMainlandChinaUser = false;
// 全局变量：是否正在加载已保存的配置（防止 setKeyEditable 清空已设置的 API Key）
let _isLoadingSavedConfig = false;

// API Key 管理簿注册表（从后端加载）
let _apiKeyRegistry = {};
// 辅助API服务商完整信息（从后端加载）
let _assistApiProviders = {};
// 仅用于 API 管理簿 / 专用模型配置的 provider，不进入辅助 API 下拉
let _keyBookApiProviders = {};
// 核心API服务商完整信息（从后端加载）
let _coreApiProviders = {};
// 特异 TTS provider（vllm_omni 等）前端驱动元数据，key→meta；来自后端
// tts_provider_registry，统一驱动下拉过滤 / 端点字段解锁 / 连通性探测，
// 新增此类 provider 不再需要在本文件多处硬编码 provider key
let _ttsProviders = {};
// 连通性测试确认可用的区域 URL，key 形如 "assist:qwen_intl"
let _resolvedProviderUrls = {};
// 核心 Key 输入框是否被用户手动改过；未改动时优先采用服务商管理簿的专属 Key
let _coreApiKeyInputDirty = false;
// 保存/检测期间锁住设置页，避免用户中途关闭或重复操作
let _apiSaveInProgress = false;
// 本页已提醒过的阿里美国 API URL，避免同一轮检测重复弹窗。
const _aliyunUsApiWarningShownKeys = new Set();

// 所有模型类型
const MODEL_TYPES = ['conversation', 'summary', 'gameMain', 'gameSummary', 'correction', 'emotion', 'vision', 'agent', 'omni', 'tts'];
// Model types that support connectivity testing.
// All model types including TTS are testable — TTS follows the same
// provider resolution logic (follow_core/follow_assist/custom).
// Future: GPT-SoVITS custom TTS may need dedicated WebSocket test path.
const CONNECTIVITY_TESTABLE_TYPES = MODEL_TYPES;
const MIMO_TOKEN_PLAN_PROVIDER_KEY = 'mimo_token_plan';
const MIMO_TOKEN_PLAN_OPENROUTER_URLS = [
    'https://token-plan-cn.xiaomimimo.com/v1',
    'https://token-plan-sgp.xiaomimimo.com/v1',
    'https://token-plan-ams.xiaomimimo.com/v1',
];
const MODEL_DEFAULT_PROVIDER = {
    omni: 'follow_core',
    gameMain: 'follow_conversation',
    gameSummary: 'follow_summary',
};
const MODEL_CONFIG_EXPANSION_PATHS = {
    conversation: ['conversation'],
    summary: ['summary'],
    gameMain: ['game', 'game-main'],
    gameSummary: ['game', 'game-summary'],
    correction: ['correction'],
    emotion: ['emotion'],
    vision: ['vision'],
    agent: ['agent'],
    omni: ['omni'],
    tts: ['tts'],
};
const MODEL_PROVIDER_FIELD_BY_TYPE = {
    conversation: 'conversation_model',
    summary: 'summary_model',
    gameMain: 'conversation_model',
    gameSummary: 'summary_model',
    correction: 'correction_model',
    emotion: 'emotion_model',
    vision: 'vision_model',
    agent: 'agent_model',
    omni: 'core_model',
};
// 当前加载到页面中的 GPT-SoVITS 状态：none | enabled | disabled
let _loadedGptSovitsState = 'none';
// 上方普通 TTS 配置是否被用户在本页改动过
let _ttsConfigDirty = false;
function markTtsConfigDirty() {
    if (_isLoadingSavedConfig) return;
    _ttsConfigDirty = true;
}

(function registerApiKeySettingsNamedWindow() {
    const windowNames = Array.from(new Set(['neko_api_key', window.name].filter(name => typeof name === 'string' && name.trim())));
    const registryPrefix = 'neko:named-window:';
    const focusPrefix = 'neko:named-window-focus:';
    const channelName = 'neko:named-window';
    let channel = null;

    function markActive() {
        const payload = JSON.stringify({
            url: window.location.href,
            timestamp: Date.now()
        });
        for (const name of windowNames) {
            try {
                window.localStorage.setItem(registryPrefix + name, payload);
            } catch (_) {}
        }
    }

    function clearActive() {
        for (const name of windowNames) {
            try {
                window.localStorage.removeItem(registryPrefix + name);
            } catch (_) {}
        }
    }

    function restoreAndFocus(payload) {
        const restoreApi = window.nekoWindowControl;
        if (restoreApi && typeof restoreApi.restore === 'function') {
            Promise.resolve(restoreApi.restore()).catch(() => {});
        }
        try {
            window.focus();
        } catch (_) {}
        if (payload && payload.type === 'focus_api_key_book' && typeof expandAndScrollToKeyBook === 'function') {
            setTimeout(() => expandAndScrollToKeyBook(), 0);
        }
    }

    function handleSharedWindowMessage(data) {
        if (!data || !windowNames.includes(data.windowName)) return;
        if (data.type === 'neko:named-window-focus') {
            restoreAndFocus(null);
        } else if (data.type === 'neko:named-window-message') {
            restoreAndFocus(data.payload || null);
        }
    }

    markActive();
    setInterval(markActive, 1000);

    try {
        if ('BroadcastChannel' in window) {
            channel = new BroadcastChannel(channelName);
            channel.onmessage = event => handleSharedWindowMessage(event.data);
        }
    } catch (_) {
        channel = null;
    }

    window.addEventListener('storage', event => {
        if (!event.key || !event.newValue) return;
        if (!windowNames.some(name => event.key === focusPrefix + name)) return;
        try {
            handleSharedWindowMessage(JSON.parse(event.newValue));
        } catch (_) {}
    });

    function cleanupRegistry() {
        clearActive();
        if (channel && typeof channel.close === 'function') {
            try {
                channel.close();
            } catch (_) {}
        }
    }

    window.addEventListener('pagehide', cleanupRegistry);
    window.addEventListener('unload', cleanupRegistry);
})();

function setInputValue(elementId, value, placeholder) {
    const element = document.getElementById(elementId);
    if (value != null && element) {
        const stringValue = String(value);
        element.value = stringValue;
        if (placeholder !== undefined) {
            element.placeholder = stringValue || placeholder;
        }
    }
}

function looksLikeLegacyGptSovitsConfig(ttsModelUrl, ttsModelId = '', ttsModelApiKey = '') {
    const normalizedUrl = (ttsModelUrl || '').trim();
    if (!/^https?:\/\//i.test(normalizedUrl)) return false;
    if ((ttsModelId || '').trim() || (ttsModelApiKey || '').trim()) return false;

    const lowerUrl = normalizedUrl.replace(/\/+$/, '').toLowerCase();
    return lowerUrl === 'http://127.0.0.1:9881'
        || lowerUrl === 'http://localhost:9881'
        || lowerUrl.startsWith('http://127.0.0.1:')
        || lowerUrl.startsWith('http://localhost:')
        || lowerUrl.startsWith('https://127.0.0.1:')
        || lowerUrl.startsWith('https://localhost:');
}

function getProviderResolvedUrl(scope, providerKey) {
    const key = `${scope}:${providerKey}`;
    return (_resolvedProviderUrls && _resolvedProviderUrls[key]) || '';
}

function getProviderOpenrouterUrl(providerKey, profile) {
    if (!profile) return '';
    return getProviderResolvedUrl('assist', providerKey)
        || profile.openrouter_url
        || (Array.isArray(profile.openrouter_urls) ? profile.openrouter_urls[0] : '')
        || '';
}

function getProviderCoreUrl(providerKey, profile) {
    if (!profile) return '';
    return getProviderResolvedUrl('core', providerKey)
        || profile.core_url
        || (Array.isArray(profile.core_urls) ? profile.core_urls[0] : '')
        || '';
}

function isMimoAssistSelected() {
    const assistSelect = document.getElementById('assistApiSelect');
    return !!assistSelect && assistSelect.value === 'mimo';
}

function isMimoTokenPlanActive() {
    const toggle = document.getElementById('useMimoTokenPlan');
    return isMimoAssistSelected() && !!toggle && toggle.checked;
}

function getMimoTokenPlanUrl() {
    return getMimoTokenPlanUrlCandidates()[0] || MIMO_TOKEN_PLAN_OPENROUTER_URLS[0];
}

function getMimoTokenPlanUrlCandidates() {
    const mimoProfile = _assistApiProviders.mimo || {};
    const candidates = [
        getProviderResolvedUrl('assist', MIMO_TOKEN_PLAN_PROVIDER_KEY),
        mimoProfile.token_plan_openrouter_url,
        ...(Array.isArray(mimoProfile.token_plan_openrouter_urls) ? mimoProfile.token_plan_openrouter_urls : []),
        ...MIMO_TOKEN_PLAN_OPENROUTER_URLS
    ];
    const seen = new Set();
    return candidates
        .map(item => String(item || '').trim())
        .filter(item => {
            if (!item || seen.has(item)) return false;
            seen.add(item);
            return true;
        });
}

function isMimoTokenPlanUrl(url) {
    const rawUrl = String(url || '').toLowerCase();
    return rawUrl.includes('token-plan-cn.xiaomimimo.com')
        || rawUrl.includes('token-plan-sgp.xiaomimimo.com')
        || rawUrl.includes('token-plan-ams.xiaomimimo.com');
}

function getEffectiveAssistProviderKey(providerKey) {
    return providerKey === 'mimo' && isMimoTokenPlanActive()
        ? MIMO_TOKEN_PLAN_PROVIDER_KEY
        : providerKey;
}

function getEffectiveAssistKey(providerKey, fallbackInput = null, { useTokenPlan = true } = {}) {
    if (useTokenPlan && providerKey === 'mimo' && isMimoTokenPlanActive()) {
        const tokenPlanInput = document.getElementById('mimoTokenPlanKeyInput');
        return tokenPlanInput ? getRealKey(tokenPlanInput) : '';
    }
    if (fallbackInput) {
        return getRealKey(fallbackInput);
    }
    const bookKey = syncKeyFromBook(providerKey);
    return (bookKey !== null) ? bookKey : '';
}

function getEffectiveAssistUrl(providerKey, profile, { useTokenPlan = true } = {}) {
    if (useTokenPlan && providerKey === 'mimo' && isMimoTokenPlanActive()) {
        return getMimoTokenPlanUrl();
    }
    return getProviderOpenrouterUrl(providerKey, profile);
}

function isApiSettingsScrolledToBottom(container, tolerance = 4) {
    if (!container) return false;
    const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
    if (maxScrollTop <= tolerance) return false; // 展开前无有效滚动区，不应触发吸底
    return maxScrollTop - container.scrollTop <= tolerance;
}

function keepApiSettingsBottomIfNeeded(shouldStickToBottom) {
    if (!shouldStickToBottom) return;
    requestAnimationFrame(() => {
        const container = document.querySelector('.container-content');
        if (!container) return;
        container.scrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
    });
}

function updateMimoTokenPlanControls() {
    const showMimoControls = isMimoAssistSelected();
    const active = isMimoTokenPlanActive();
    const toggleRow = document.getElementById('mimoTokenPlanToggleRow');
    const toggle = document.getElementById('useMimoTokenPlan');
    const keyRow = document.getElementById('mimoTokenPlanKeyRow');
    const tokenPlanInput = document.getElementById('mimoTokenPlanKeyInput');
    const assistInput = document.getElementById('assistApiKeyInput');
    const scrollContainer = document.querySelector('.container-content');
    const wasAtBottom = isApiSettingsScrolledToBottom(scrollContainer);

    if (toggleRow) toggleRow.style.display = showMimoControls ? 'inline-flex' : 'none';
    if (toggle) toggle.disabled = !showMimoControls;
    if (keyRow) keyRow.style.display = active ? 'flex' : 'none';
    if (tokenPlanInput) tokenPlanInput.disabled = !active;

    if (assistInput) {
        assistInput.disabled = active || (assistInput.dataset.disabledByFreeAssist === 'true');
        assistInput.readOnly = active;
        if (active) {
            assistInput.placeholder = window.t
                ? window.t('api.mimoApiKeyLockedByTokenPlan')
                : 'MiMo Token Plan is enabled; this key is not used';
        } else if (assistInput.dataset.disabledByFreeAssist !== 'true') {
            assistInput.placeholder = window.t ? window.t('api.assistApiKeyPlaceholder') : '留空使用管理簿对应 Key';
        }
    }

    keepApiSettingsBottomIfNeeded(wasAtBottom);
}

function isAliyunUsApiUrl(url) {
    const rawUrl = String(url || '').trim();
    if (!rawUrl) return false;
    try {
        return new URL(rawUrl).hostname.toLowerCase() === 'dashscope-us.aliyuncs.com';
    } catch (error) {
        return rawUrl.toLowerCase().includes('dashscope-us.aliyuncs.com');
    }
}

function showAliyunUsApiWarningModal() {
    const modal = document.getElementById('aliyun-us-api-warning-modal');
    if (!modal) {
        showStatus(
            window.t
                ? window.t('api.aliyunUsApiWarning.message')
                : '当前使用了阿里的美国API，不支持TTS与实时语音，建议更换阿里的新加坡API',
            'error'
        );
        return;
    }
    modal.style.display = 'flex';
}

function closeAliyunUsApiWarningModal() {
    const modal = document.getElementById('aliyun-us-api-warning-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

function maybeShowAliyunUsApiWarning(scope, providerKey, resolvedUrl) {
    if (!isAliyunUsApiUrl(resolvedUrl)) return;
    const warningKey = `${scope}:${providerKey}:${resolvedUrl}`;
    if (_aliyunUsApiWarningShownKeys.has(warningKey)) return;
    _aliyunUsApiWarningShownKeys.add(warningKey);
    showAliyunUsApiWarningModal();
}

function rememberResolvedProviderUrl(scope, providerKey, resolvedUrl) {
    if (!scope || !providerKey || !resolvedUrl) return;
    const key = `${scope}:${providerKey}`;
    _resolvedProviderUrls[key] = resolvedUrl;
    maybeShowAliyunUsApiWarning(scope, providerKey, resolvedUrl);
}

/**
 * 遮蔽 API Key：只显示前6位和后6位，中间用 *** 替代。
 * 短于14位的 key 原样返回（不够遮蔽）。
 */
function maskApiKey(key) {
    if (!key || typeof key !== 'string') return key;
    if (key.length < 14) return key;
    const midLen = key.length - 12;
    return key.slice(0, 6) + '*'.repeat(midLen) + key.slice(-6);
}

/**
 * 将真实 key 写入 input 的 dataset，输入框显示遮蔽值。
 */
function setMaskedInput(input, realKey) {
    if (!input) return;
    if (!realKey) {
        input.dataset.realKey = '';
        input.value = '';
        return;
    }
    input.dataset.realKey = realKey;
    input.value = maskApiKey(realKey);
}

/**
 * ⚠️ 重要：所有需要读取 API Key 真实值的地方，必须使用 getRealKey(input)
 * 而不是 input.value。因为 input.value 可能是遮蔽后的值（如 sk-a04****6b53）。
 * 真实 key 存储在 input.dataset.realKey 中，由 setMaskedInput() 写入。
 * 新增读取 key 的代码时请务必使用此函数。
 */
function getRealKey(input) {
    if (!input) return '';
    // 聚焦中：用户可能正在编辑，优先使用当前 value
    if (input === document.activeElement) {
        return input.value.trim();
    }
    // 非聚焦：优先使用存储的真实 key（value 可能是遮蔽值）
    if (input.dataset.realKey) {
        return input.dataset.realKey;
    }
    // 防御：如果 value 全是星号，说明是遮蔽残留，返回空
    const val = input.value.trim();
    if (/\*{3,}/.test(val)) return '';
    return val;
}

/**
 * 为 API Key 输入框绑定 focus/blur 事件：聚焦时显示真实 key，失焦时遮蔽。
 */
function attachMaskBehavior(input) {
    if (!input || input.dataset.maskAttached) return;
    input.dataset.maskAttached = 'true';
    input.addEventListener('focus', () => {
        const real = input.dataset.realKey;
        if (real) input.value = real;
    });
    input.addEventListener('blur', () => {
        // 用户可能编辑了 value，同步回 realKey
        const current = input.value.trim();
        if (current) {
            input.dataset.realKey = current;
            input.value = maskApiKey(current);
        } else {
            input.dataset.realKey = '';
        }
    });
}

// 允许的来源列表
const ALLOWED_ORIGINS = [window.location.origin];

// 获取目标来源（用于 postMessage）
function getTargetOrigin() {
    // 优先尝试从 document.referrer 获取来源，如果不存在或无效，则回退到当前来源
    try {
        if (document.referrer) {
            const refOrigin = new URL(document.referrer).origin;
            // 只有在允许列表中的来源才被视为有效的目标
            if (ALLOWED_ORIGINS.includes(refOrigin)) {
                return refOrigin;
            }
        }
    } catch (e) {
        // URL 解析失败，忽略
    }
    return window.location.origin;
}

// 数据驱动的受限服务商判断
function isProviderRestricted(providerKey) {
    if (!isMainlandChinaUser) return false;
    const entry = _apiKeyRegistry[providerKey];
    return entry && entry.restricted;
}

function showStatus(message, type = 'info', options = {}) {
    const statusDiv = document.getElementById('status');
    if (!statusDiv) {
        console.warn('[API Key Settings] status element not found');
        return;
    }

    // 清除之前的自动隐藏定时器，避免新消息被旧定时器提前关闭
    if (showStatus._hideTimer) {
        clearTimeout(showStatus._hideTimer);
        showStatus._hideTimer = null;
    }

    statusDiv.textContent = message;
    statusDiv.className = `status ${type}`;
    statusDiv.style.display = 'block';

    if (options && options.sticky) {
        return;
    }

    const delay = type === 'error' ? 5000 : 3000;
    showStatus._hideTimer = setTimeout(() => {
        statusDiv.style.display = 'none';
        showStatus._hideTimer = null;
    }, delay);
}

function setApiSaveInProgress(isBusy) {
    _apiSaveInProgress = !!isBusy;

    const mainContent = document.getElementById('main-content');
    if (mainContent) {
        mainContent.classList.toggle('api-save-busy', _apiSaveInProgress);
        mainContent.setAttribute('aria-busy', _apiSaveInProgress ? 'true' : 'false');
        if ('inert' in mainContent) {
            mainContent.inert = _apiSaveInProgress;
        } else if (_apiSaveInProgress) {
            mainContent.setAttribute('inert', '');
        } else {
            mainContent.removeAttribute('inert');
        }
    }

    const saveButton = document.getElementById('save-settings-btn');
    if (saveButton) {
        saveButton.disabled = _apiSaveInProgress;
    }
}

function showCurrentApiKey(message, rawKey = '', hasKey = false) {
    const currentApiKeyDiv = document.getElementById('current-api-key');
    if (!currentApiKeyDiv) return;

    // 清空现有内容
    currentApiKeyDiv.textContent = '';

    // 创建图标
    const img = document.createElement('img');
    img.src = '/static/icons/exclamation.png';
    img.alt = '';
    img.style.width = '48px';
    img.style.height = '48px';
    img.style.verticalAlign = 'middle';
    currentApiKeyDiv.appendChild(img);

    // 创建文本节点
    const textNode = document.createTextNode(message);
    currentApiKeyDiv.appendChild(textNode);

    // 存储状态到 dataset
    currentApiKeyDiv.dataset.apiKey = rawKey;
    currentApiKeyDiv.dataset.hasKey = hasKey ? 'true' : 'false';

    currentApiKeyDiv.style.display = 'flex';
}

// 检测用户是否为中国大陆用户
// 逻辑：如果存在 Steam 语言设置（即有 Steam 环境），则检查 GeoIP
// 如果不存在 Steam 语言设置（无 Steam 环境），默认为非大陆用户
async function checkMainlandChinaUser() {
    try {
        const response = await fetch('/api/config/steam_language', {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            signal: AbortSignal.timeout(3000) // 3 秒超时
        });

        if (!response.ok) {
            console.log('[Region] Steam 语言 API 响应异常:', response.status);
            return false;
        }

        const data = await response.json();

        // 如果 API 返回成功且有 is_mainland_china 字段
        if (data.is_mainland_china === true) {
            console.log('[Region] 检测到中国大陆用户（基于 Steam 环境 + GeoIP）');
            return true;
        }

        // 其他情况（无 Steam 环境、非大陆 IP）默认为非大陆用户
        console.log('[Region] 非中国大陆用户，ip_country:', data.ip_country);
        return false;
    } catch (error) {
        // 网络错误或超时，默认为非大陆用户
        console.log('[Region] 检测区域时出错，默认为非大陆用户:', error.message);
        return false;
    }
}

// 隐藏大陆用户不可用的 Key Book 输入行
function hideRestrictedKeyBookInputs() {
    if (!isMainlandChinaUser) return;

    Object.keys(_apiKeyRegistry).forEach(providerKey => {
        if (isProviderRestricted(providerKey)) {
            const input = document.getElementById(`keyBookInput_${providerKey}`);
            const row = input ? input.closest('.key-book-row') : null;
            if (row) {
                row.style.display = 'none';
            }
        }
    });
}

// 清空 API 服务商下拉框
function clearApiProviderSelects() {
    const coreSelect = document.getElementById('coreApiSelect');
    const assistSelect = document.getElementById('assistApiSelect');
    if (coreSelect) {
        coreSelect.innerHTML = '';
        coreSelect.value = '';
    }
    if (assistSelect) {
        assistSelect.innerHTML = '';
        assistSelect.value = '';
    }

    syncProviderSelectDropdowns(null, { rebuild: true });
}

// 等待下拉选项加载完成再设置值，避免单次 setTimeout 竞态
function waitForOptions(select, targetValue, { maxAttempts = 20, interval = 50, onSuccess } = {}) {
    if (!select || !targetValue) return;

    let attempts = 0;
    const checkAndSet = () => {
        if (select.options.length > 0) {
            const optionExists = Array.from(select.options).some(opt => opt.value === targetValue);
            if (optionExists) {
                select.value = targetValue;
                syncProviderSelectDropdowns(select);
                // 选项设置完成后执行回调
                if (onSuccess && typeof onSuccess === 'function') {
                    onSuccess();
                }
                return;
            }
        }

        if (attempts < maxAttempts) {
            attempts += 1;
            setTimeout(checkAndSet, interval);
        }
    };

    checkAndSet();
}

let providerDropdownHandlersBound = false;

function getProviderDropdownPlaceholder(select) {
    const fallbackText = window.t ? window.t('api.providerSelectPlaceholder') : '请选择服务商';
    if (!select) return fallbackText;

    const label = select.id ? document.querySelector(`label[for="${select.id}"]`) : null;
    const labelText = label ? label.querySelector('span')?.textContent?.trim() : '';
    return labelText || fallbackText;
}

function closeProviderSelectDropdown(wrapper) {
    if (!wrapper) return;

    wrapper.classList.remove('open');

    const trigger = wrapper.querySelector('.api-provider-dropdown-trigger');
    if (trigger) {
        trigger.setAttribute('aria-expanded', 'false');
    }
}

function closeAllProviderSelectDropdowns(exceptWrapper = null) {
    document.querySelectorAll('.api-provider-dropdown.open').forEach(wrapper => {
        if (wrapper !== exceptWrapper) {
            closeProviderSelectDropdown(wrapper);
        }
    });
}

function openProviderSelectDropdown(wrapper) {
    if (!wrapper || wrapper.classList.contains('disabled')) return;

    closeAllProviderSelectDropdowns(wrapper);
    wrapper.classList.add('open');

    const trigger = wrapper.querySelector('.api-provider-dropdown-trigger');
    if (trigger) {
        trigger.setAttribute('aria-expanded', 'true');
    }
}

function buildProviderSelectDropdownMenu(select) {
    if (!select) return;

    const wrapper = select.closest('.api-provider-dropdown');
    const menu = wrapper ? wrapper.querySelector('.api-provider-dropdown-menu') : null;
    const menuScroll = menu ? menu.querySelector('.api-provider-dropdown-menu-scroll') : null;
    if (!wrapper || !menu || !menuScroll) return;

    menuScroll.innerHTML = '';

    const options = Array.from(select.options);
    if (options.length === 0) {
        const emptyState = document.createElement('div');
        emptyState.className = 'api-provider-dropdown-empty';
        emptyState.textContent = window.t ? window.t('api.noOptionsAvailable') : '暂无可选项';
        menuScroll.appendChild(emptyState);
        return;
    }

    options.forEach(option => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'api-provider-dropdown-option';
        item.setAttribute('role', 'option');
        item.dataset.value = option.value;
        item.textContent = option.textContent;

        if (option.disabled) {
            item.disabled = true;
            item.setAttribute('aria-disabled', 'true');
        }

        item.addEventListener('click', event => {
            event.preventDefault();

            if (option.disabled || select.disabled) {
                return;
            }

            select.value = option.value;
            syncProviderSelectDropdowns(select);
            select.dispatchEvent(new Event('change', { bubbles: true }));
            closeProviderSelectDropdown(wrapper);
        });

        menuScroll.appendChild(item);
    });
}

function syncProviderSelectDropdowns(targetSelect = null, { rebuild = false } = {}) {
    const selects = targetSelect
        ? [targetSelect]
        : Array.from(document.querySelectorAll('.api-provider-select[data-dropdown-enhanced="true"]'));

    selects.forEach(select => {
        if (!select) return;

        const wrapper = select.closest('.api-provider-dropdown');
        const trigger = wrapper ? wrapper.querySelector('.api-provider-dropdown-trigger') : null;
        const current = wrapper ? wrapper.querySelector('.api-provider-dropdown-current') : null;
        const menu = wrapper ? wrapper.querySelector('.api-provider-dropdown-menu') : null;

        if (!wrapper || !trigger || !current || !menu) return;

        if (rebuild) {
            buildProviderSelectDropdownMenu(select);
        }

        const selectedOption = select.options[select.selectedIndex] || null;
        const placeholder = getProviderDropdownPlaceholder(select);

        current.textContent = selectedOption ? selectedOption.textContent : placeholder;
        current.classList.toggle('placeholder', !selectedOption);

        trigger.disabled = !!select.disabled;
        wrapper.classList.toggle('disabled', !!select.disabled);

        menu.querySelectorAll('.api-provider-dropdown-option').forEach(item => {
            const isSelected = item.dataset.value === select.value;
            item.classList.toggle('selected', isSelected);
            item.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        });

        if (select.disabled) {
            closeProviderSelectDropdown(wrapper);
        }
    });
}

function bindProviderDropdownGlobalHandlers() {
    if (providerDropdownHandlersBound) return;

    document.addEventListener('click', event => {
        if (!event.target.closest('.api-provider-dropdown')) {
            closeAllProviderSelectDropdowns();
        }
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            closeAllProviderSelectDropdowns();
        }
    });

    window.addEventListener('resize', () => closeAllProviderSelectDropdowns());

    providerDropdownHandlersBound = true;
}

function initProviderSelectDropdown(select) {
    if (!select || select.dataset.dropdownEnhanced === 'true') return;

    bindProviderDropdownGlobalHandlers();

    const wrapper = document.createElement('div');
    wrapper.className = 'api-provider-dropdown';

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'api-provider-dropdown-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-label', getProviderDropdownPlaceholder(select));
    trigger.innerHTML = '<span class="api-provider-dropdown-current"></span><span class="api-provider-dropdown-arrow" aria-hidden="true"></span>';

    const menu = document.createElement('div');
    menu.className = 'api-provider-dropdown-menu';
    menu.setAttribute('role', 'listbox');

    const menuScroll = document.createElement('div');
    menuScroll.className = 'api-provider-dropdown-menu-scroll';

    if (select.id) {
        menu.id = `${select.id}-menu`;
        trigger.id = `${select.id}-dropdown-trigger`;
        trigger.setAttribute('aria-controls', menu.id);
    }

    menu.appendChild(menuScroll);
    wrapper.appendChild(trigger);
    wrapper.appendChild(menu);

    select.classList.add('is-enhanced');
    select.dataset.dropdownEnhanced = 'true';

    trigger.addEventListener('click', event => {
        event.preventDefault();

        if (wrapper.classList.contains('open')) {
            closeProviderSelectDropdown(wrapper);
        } else {
            openProviderSelectDropdown(wrapper);
        }
    });

    select.addEventListener('change', () => syncProviderSelectDropdowns(select));

    const observer = new MutationObserver(() => {
        syncProviderSelectDropdowns(select, { rebuild: true });
    });

    observer.observe(select, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['disabled', 'label', 'value', 'selected']
    });

    syncProviderSelectDropdowns(select, { rebuild: true });
}

function initProviderSelectDropdowns() {
    document.querySelectorAll('.api-provider-select').forEach(initProviderSelectDropdown);
    syncProviderSelectDropdowns(null, { rebuild: true });
}

async function clearVoiceIds() {
    try {
        const response = await fetch('/api/characters/clear_voice_ids', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        if (!response.ok) {
            const errorText = await response.text();
            console.error(`自动清除Voice ID记录失败: HTTP ${response.status}`, errorText);
            return;
        }

        const data = await response.json();

        if (data.success) {
            console.log(`API Key已更改，已自动清除 ${data.cleared_count} 个角色的Voice ID记录`);
        } else {
            console.error('自动清除Voice ID记录失败:', data.error);
        }
    } catch (error) {
        console.error('自动清除Voice ID记录时出错:', error);
    }
}

// ==================== Key Book 相关函数 ====================

/**
 * 渲染 API 管理簿输入区域
 */
function renderKeyBook(registry, providers) {
    const container = document.getElementById('key-book-inputs');
    if (!container) return;
    container.innerHTML = '';

    Object.keys(registry).forEach(providerKey => {
        // 跳过 free
        if (providerKey === 'free') return;
        // 跳过大陆受限
        if (isProviderRestricted(providerKey)) return;

        const entry = registry[providerKey];
        const row = document.createElement('div');
        row.className = 'key-book-row';

        const label = document.createElement('label');
        label.setAttribute('data-i18n', `api.keyBook.${providerKey}`);
        const i18nKey = `api.keyBook.${providerKey}`;
        const translated = window.t ? window.t(i18nKey) : null;
        label.textContent = (translated && translated !== i18nKey) ? translated : (entry.label || providerKey);
        row.appendChild(label);

        const input = document.createElement('input');
        input.type = 'text';
        input.id = `keyBookInput_${providerKey}`;
        input.placeholder = window.t ? window.t('api.keyBookKeyPlaceholder') : 'Enter API Key';
        input.dataset.providerKey = providerKey;
        attachMaskBehavior(input);
        row.appendChild(input);

        container.appendChild(row);
    });
}

/**
 * 切换 Key Book 显示
 */
function toggleKeyBook() {
    const options = document.getElementById('key-book-options');
    const btn = document.getElementById('key-book-toggle-btn');
    if (options.style.display === 'none') {
        options.style.display = 'block';
        btn.classList.add('rotated');
    } else {
        options.style.display = 'none';
        btn.classList.remove('rotated');
    }
}

function expandAndScrollToKeyBook(options = {}) {
    const keyBookOptions = document.getElementById('key-book-options');
    const keyBookButton = document.getElementById('key-book-toggle-btn');
    if (keyBookOptions && keyBookOptions.style.display === 'none') {
        keyBookOptions.style.display = 'block';
        if (keyBookButton) keyBookButton.classList.add('rotated');
    }

    const section = document.getElementById('key-book-section');
    const providerKey = typeof options.providerKey === 'string' ? options.providerKey : '';
    const targetInputId = providerKey === MIMO_TOKEN_PLAN_PROVIDER_KEY
        ? 'mimoTokenPlanKeyInput'
        : (providerKey ? `keyBookInput_${providerKey}` : '');
    const targetInput = targetInputId ? document.getElementById(targetInputId) : null;
    const scrollTarget = targetInput?.closest('.key-book-row, .field-row') || section;

    if (scrollTarget) {
        scrollTarget.scrollIntoView({
            behavior: options.instant ? 'auto' : 'smooth',
            block: 'center'
        });
    }

    if (targetInput) {
        targetInput.focus({ preventScroll: true });
        targetInput.select();
    }
}

function shouldFocusKeyBookFromLocation() {
    try {
        const params = new URLSearchParams(window.location.search || '');
        const focus = (params.get('focus') || params.get('target') || '').toLowerCase();
        if (focus === 'key_book' || focus === 'key-book' || focus === 'keybook') return true;
    } catch (_) { }
    const hash = String(window.location.hash || '').toLowerCase();
    return hash === '#key-book' || hash === '#key_book' || hash === '#keybook';
}

/**
 * 从 Key Book 读取某个 provider 的 key。
 * 返回 null 表示该 provider 的输入框不存在（如被 restricted 隐藏），
 * 返回 '' 表示输入框存在但为空。调用方据此区分"不应覆盖"和"应清空"。
 */
function syncKeyFromBook(providerKey) {
    const input = document.getElementById(`keyBookInput_${providerKey}`);
    if (!input) return null;
    return getRealKey(input);
}

/**
 * 向 Key Book 写入某个 provider 的 key
 */
function syncKeyToBook(providerKey, keyValue, sourceInput = null) {
    const input = document.getElementById(`keyBookInput_${providerKey}`);
    if (input) {
        if (input !== sourceInput) {
            setMaskedInput(input, keyValue || '');
        } else {
            input.dataset.realKey = (keyValue || '').trim();
        }
        attachMaskBehavior(input);
    }
}

// ==================== Model Provider Dropdowns ====================

function getDefaultProviderForModelType(modelType) {
    return MODEL_DEFAULT_PROVIDER[modelType] || 'follow_assist';
}

function appendModelProviderOption(selectEl, value, i18nKey, fallbackText) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = window.t ? window.t(i18nKey, fallbackText) : fallbackText;
    opt.setAttribute('data-i18n', i18nKey);
    selectEl.appendChild(opt);
    return opt;
}

/**
 * 仅靠 api_providers.json 的结构推断某个 provider 是否「只做 TTS」。
 *
 * 用于 fail-safe：当后端 /api_providers 的 tts_provider 元数据加载失败（try/except
 * 返回 tts_providers:[]，success 仍 true）时，_ttsProviders 为空，原本靠注册表
 * tts_dropdown_only / editable_endpoint 驱动的判定会全部落空，导致这类 provider
 * （如 vLLM-Omni）漏进 conversation/summary/... 等 LLM 下拉、并被当成普通
 * OpenAI-compatible 服务商保存。这里用「声明了 tts_default_model / tts_default_voice
 * 但没有任何 LLM 模型字段」这个结构信号兜底，避免在前端再 re-hardcode 具体 provider key。
 */
function isStructuralTtsOnlyProvider(pk) {
    const p = _assistApiProviders[pk] || _keyBookApiProviders[pk];
    if (!p || typeof p !== 'object') return false;
    if (!p.tts_default_model && !p.tts_default_voice) return false;
    // 任意 LLM 模型字段（conversation_model / summary_model / ...）非空 → 不是纯 TTS。
    return !MODEL_TYPES.some(mt => {
        if (mt === 'tts' || mt === 'omni') return false;
        const v = p[`${mt}_model`];
        return v && String(v).trim();
    });
}

/**
 * 取某个 provider 的 TTS 元数据：优先用后端注册表（_ttsProviders），缺失时按结构信号
 * 合成一份最小元数据（tts_dropdown_only + editable_endpoint），让下拉过滤 / 字段解锁 /
 * 保存路径在元数据缺失时仍按「纯 TTS、用户自配端点」处理。合成元数据 probe_kind='none'
 * （注册表缺失时探测降级，可接受），不携带任何 provider 专属探测细节。
 */
function getTtsProviderMeta(pk) {
    if (!pk) return null;
    const meta = _ttsProviders[pk];
    if (meta) return meta;
    if (isStructuralTtsOnlyProvider(pk)) {
        const p = _assistApiProviders[pk] || _keyBookApiProviders[pk] || {};
        return {
            key: pk,
            tts_dropdown_only: true,
            editable_endpoint: true,
            default_url: '',
            default_model: p.tts_default_model || '',
            default_voice: p.tts_default_voice || '',
            url_field: 'ttsModelUrl',
            model_field: 'ttsModelId',
            voice_field: 'ttsVoiceId',
            api_key_field: 'ttsModelApiKey',
            probe_kind: p.probe_kind || 'none',
            probe_sub_type: p.probe_sub_type || '',
            probe_ws_path: p.probe_ws_path || '',
            _synthesized: true,
        };
    }
    return null;
}

/**
 * 填充所有自定义模型的服务商下拉框
 */
function getProviderInfo(providerKey) {
    if (!providerKey) return {};
    return _assistApiProviders[providerKey] || _keyBookApiProviders[providerKey] || _coreApiProviders[providerKey] || {};
}

function isProviderFlagEnabled(value) {
    return value === true || value === 1 || value === 'true' || value === '1';
}

function isProviderFlagDisabled(value) {
    return value === false || value === 0 || value === 'false' || value === '0';
}

function isTtsProviderVisibleInModelConfig(providerKey, meta = null) {
    const profile = getProviderInfo(providerKey);
    if (isProviderFlagDisabled(profile.tts_config_visible) || isProviderFlagDisabled(profile.ttsConfigVisible)) {
        return false;
    }
    if (meta && (isProviderFlagDisabled(meta.tts_config_visible) || isProviderFlagDisabled(meta.ttsConfigVisible))) {
        return false;
    }
    return true;
}

function isFixedModelProvider(providerKey) {
    const profile = getProviderInfo(providerKey);
    return isProviderFlagEnabled(profile.fixed_model) || isProviderFlagEnabled(profile.fixedModel);
}

function getProviderType(providerKey, fallback = 'openai_compatible') {
    const profile = getProviderInfo(providerKey);
    const providerType = String(profile.provider_type || profile.providerType || fallback || 'openai_compatible')
        .trim()
        .toLowerCase();
    return ['openai_compatible', 'anthropic', 'websocket', 'tts'].includes(providerType)
        ? providerType
        : 'openai_compatible';
}

function getProviderDefaultModelId(providerKey, modelType) {
    if (!providerKey || !modelType) return '';
    const profile = getProviderInfo(providerKey);
    const field = MODEL_PROVIDER_FIELD_BY_TYPE[modelType];
    if (field && profile[field]) return String(profile[field]).trim();
    if (modelType === 'gameMain' && profile.conversation_model) return String(profile.conversation_model).trim();
    if (modelType === 'gameSummary' && profile.summary_model) return String(profile.summary_model).trim();
    return '';
}

function getProviderModeSourceKey(modelType, providerMode, visited = new Set()) {
    if (!providerMode || providerMode === 'custom') return '';
    const visitKey = `${modelType}:${providerMode}`;
    if (visited.has(visitKey)) return '';
    visited.add(visitKey);
    if (providerMode === 'follow_conversation') {
        const sourceSelect = document.getElementById('conversationModelProvider');
        return getProviderModeSourceKey('conversation', sourceSelect ? sourceSelect.value : '', visited);
    }
    if (providerMode === 'follow_summary') {
        const sourceSelect = document.getElementById('summaryModelProvider');
        return getProviderModeSourceKey('summary', sourceSelect ? sourceSelect.value : '', visited);
    }
    if (providerMode === 'follow_core') {
        const coreSelect = document.getElementById('coreApiSelect');
        return coreSelect ? coreSelect.value : '';
    }
    if (providerMode === 'follow_assist') {
        const assistSelect = document.getElementById('assistApiSelect');
        return assistSelect ? assistSelect.value : '';
    }
    return providerMode;
}

function setModelIdFieldHidden(modelType, hidden) {
    const input = document.getElementById(`${modelType}ModelId`);
    if (!input) return;
    const group = input.closest('.form-group') || input.parentElement;
    if (group) group.style.display = hidden ? 'none' : '';
    input.dataset.fixedModelHidden = hidden ? 'true' : '';
}

function applyFixedModelProviderUi(modelType, providerMode) {
    const providerKey = getProviderModeSourceKey(modelType, providerMode);
    const fixedModel = isFixedModelProvider(providerKey)
        ? getProviderDefaultModelId(providerKey, modelType)
        : '';
    const input = document.getElementById(`${modelType}ModelId`);
    if (fixedModel && input) {
        input.value = fixedModel;
        input.setAttribute('readonly', 'readonly');
        setModelIdFieldHidden(modelType, true);
        return true;
    }
    setModelIdFieldHidden(modelType, false);
    return false;
}

function getResolvedCustomModelId(modelType, providerMode) {
    if (!modelType) return '';
    const providerKey = getProviderModeSourceKey(modelType, providerMode);
    const fixedModel = isFixedModelProvider(providerKey)
        ? getProviderDefaultModelId(providerKey, modelType)
        : '';
    if (fixedModel) return fixedModel;
    const input = document.getElementById(`${modelType}ModelId`);
    return input ? input.value.trim() : '';
}

function populateModelProviderDropdowns() {
    MODEL_TYPES.forEach(mt => {
        const sel = document.getElementById(`${mt}ModelProvider`);
        if (!sel) return;
        sel.innerHTML = '';

        if (mt === 'gameMain') {
            appendModelProviderOption(
                sel,
                'follow_conversation',
                'api.customModelProviderFollowConversation',
                '跟随文本对话模型'
            );
        } else if (mt === 'gameSummary') {
            appendModelProviderOption(
                sel,
                'follow_summary',
                'api.customModelProviderFollowSummary',
                '跟随摘要模型'
            );
        }

        // follow_core
        appendModelProviderOption(sel, 'follow_core', 'api.customModelProviderFollowCore', '跟随核心API');

        // follow_assist
        appendModelProviderOption(sel, 'follow_assist', 'api.customModelProviderFollowAssist', '跟随辅助API');

        // Each non-free provider from _assistApiProviders
        Object.keys(_assistApiProviders).forEach(pk => {
            if (pk === 'free') return;
            if (isProviderRestricted(pk)) return;
            // 特异 TTS provider（tts_dropdown_only）仅在 TTS 下拉里出现，不污染
            // conversation/summary/correction/emotion/vision/agent/omni 的下拉。
            // 成员由后端 tts_provider_registry 驱动；注册表元数据缺失时 getTtsProviderMeta
            // 用结构信号兜底，前端始终不硬编码 provider key。
            const _spFilter = getTtsProviderMeta(pk);
            if (mt === 'tts' && !isTtsProviderVisibleInModelConfig(pk, _spFilter)) return;
            if (_spFilter && _spFilter.tts_dropdown_only && mt !== 'tts') return;
            const pInfo = _assistApiProviders[pk];
            const opt = document.createElement('option');
            opt.value = pk;
            const translationKey = `api.assistProviderNames.${pk}`;
            if (window.t) {
                const translated = window.t(translationKey);
                opt.textContent = (translated !== translationKey) ? translated : (pInfo.name || pk);
            } else {
                opt.textContent = pInfo.name || pk;
            }
            sel.appendChild(opt);
        });

        // Registry-only TTS provider（仅在 tts_provider_registry 声明、不在
        // assist_api_providers 里，如 GPT-SoVITS）：上面的 _assistApiProviders 循环
        // 遍历不到，否则用户在下拉里永远选不到。这里补进来，且仅进 TTS 下拉。
        if (mt === 'tts') {
            Object.keys(_ttsProviders).forEach(pk => {
                if (_assistApiProviders[pk]) return; // 已在上面的循环里加过
                const meta = _ttsProviders[pk];
                if (!meta) return;
                if (!isTtsProviderVisibleInModelConfig(pk, meta)) return;
                const opt = document.createElement('option');
                opt.value = pk;
                const translationKey = `api.assistProviderNames.${pk}`;
                const translated = window.t ? window.t(translationKey) : translationKey;
                opt.textContent = (translated && translated !== translationKey) ? translated : pk;
                sel.appendChild(opt);
            });
        }

        // custom
        const optCustom = document.createElement('option');
        optCustom.value = 'custom';
        optCustom.textContent = window.t ? window.t('api.customModelProviderCustom') : '自定义';
        optCustom.setAttribute('data-i18n', 'api.customModelProviderCustom');
        sel.appendChild(optCustom);

        sel.value = getDefaultProviderForModelType(mt);

        // Attach onchange (only once — skip if already bound from a previous call)
        if (!sel.dataset.providerChangeAttached) {
            sel.addEventListener('change', function () {
                onCustomModelProviderChange(mt);
            });
            sel.dataset.providerChangeAttached = 'true';
        }
    });
}

/**
 * 当自定义模型的服务商选择变化时，自动填充 URL / Key
 * CRITICAL: omni 模型使用 core_url (WebSocket)，其他模型使用 openrouter_url (HTTPS)
 */
function onCustomModelProviderChange(modelType) {
    const sel = document.getElementById(`${modelType}ModelProvider`);
    if (!sel) return;

    syncProviderSelectDropdowns(sel);

    const previousProvider = sel.dataset.currentProvider || '';
    const provider = sel.value;
    const urlInput = document.getElementById(`${modelType}ModelUrl`);
    const keyInput = document.getElementById(`${modelType}ModelApiKey`);
    const modelIdInput = document.getElementById(`${modelType}ModelId`);
    const voiceInput = document.getElementById(`${modelType}VoiceId`);

    setModelIdFieldHidden(modelType, false);

    // Model ID is editable unless inherited or fixed by a provider preset.
    if (modelIdInput) {
        modelIdInput.removeAttribute('readonly');
    }

    if (
        modelType === 'tts'
        && previousProvider === 'vllm_omni'
        && provider !== 'vllm_omni'
        && !_isLoadingSavedConfig
        && voiceInput
    ) {
        voiceInput.value = '';
    }

    /**
     * 将 key 输入框设为 readonly 并显示管理簿提示 + 快捷跳转按钮
     */
    const setKeyReadonly = (input, value, providerKey = '') => {
        if (!input) return;
        setMaskedInput(input, value || '');
        input.setAttribute('readonly', 'readonly');
        input.placeholder = window.t ? window.t('api.keyAutoFilledFromKeyBook') : 'Key从API管理簿自动填充';
        ensureKeyBookLink(input, providerKey);
    };

    /**
     * 将 key 输入框恢复为可编辑状态
     */
    const setKeyEditable = (input) => {
        if (!input) return;
        input.removeAttribute('readonly');
        if (_isLoadingSavedConfig) return;
        // 清除残留的遮蔽状态和遮蔽值，让用户从空白开始输入
        input.dataset.realKey = '';
        input.value = '';
        // 恢复原始 placeholder
        const origPlaceholder = input.getAttribute('data-i18n-placeholder');
        if (origPlaceholder && window.t) {
            input.placeholder = window.t(origPlaceholder);
        }
        removeKeyBookLink(input);
    };

    if (provider === 'follow_conversation' || provider === 'follow_summary') {
        const sourceType = provider === 'follow_conversation' ? 'conversation' : 'summary';
        const sourceUrl = document.getElementById(`${sourceType}ModelUrl`);
        const sourceModel = document.getElementById(`${sourceType}ModelId`);
        const sourceKey = document.getElementById(`${sourceType}ModelApiKey`);
        if (urlInput) {
            urlInput.value = sourceUrl ? sourceUrl.value.trim() : '';
            urlInput.setAttribute('readonly', 'readonly');
        }
        if (modelIdInput) {
            modelIdInput.value = sourceModel ? sourceModel.value.trim() : '';
            modelIdInput.setAttribute('readonly', 'readonly');
        }
        if (keyInput) {
            setMaskedInput(keyInput, sourceKey ? getRealKey(sourceKey) : '');
            keyInput.setAttribute('readonly', 'readonly');
            keyInput.placeholder = window.t
                ? window.t('api.keyAutoFilledFromModel', 'Key 跟随已选择的模型配置')
                : 'Key 跟随已选择的模型配置';
            removeKeyBookLink(keyInput);
        }
        applyFixedModelProviderUi(modelType, provider);
        return;
    }

    if (provider === 'follow_core' || provider === 'follow_assist') {
        // Determine which provider to follow
        let sourceProviderKey;
        if (provider === 'follow_core') {
            const coreSelect = document.getElementById('coreApiSelect');
            sourceProviderKey = coreSelect ? coreSelect.value : '';
        } else {
            const assistSelect = document.getElementById('assistApiSelect');
            sourceProviderKey = assistSelect ? assistSelect.value : '';
        }

        if (sourceProviderKey && sourceProviderKey !== 'free') {
            if (modelType === 'omni') {
                const coreSelect = document.getElementById('coreApiSelect');
                const coreProviderKey = coreSelect ? coreSelect.value : '';
                const coreProfile = _coreApiProviders[coreProviderKey] || {};
                if (urlInput) {
                    urlInput.value = getProviderCoreUrl(coreProviderKey, coreProfile);
                    urlInput.setAttribute('readonly', 'readonly');
                }
                const coreBookKey = syncKeyFromBook(coreProviderKey);
                setKeyReadonly(keyInput, coreBookKey, coreProviderKey);
            } else {
                const pInfo = getProviderInfo(sourceProviderKey);
                if (urlInput) {
                    urlInput.value = getEffectiveAssistUrl(sourceProviderKey, pInfo) || getProviderCoreUrl(sourceProviderKey, pInfo);
                    urlInput.setAttribute('readonly', 'readonly');
                }
                const bookKey = getEffectiveAssistKey(sourceProviderKey);
                setKeyReadonly(
                    keyInput,
                    bookKey,
                    getEffectiveAssistProviderKey(sourceProviderKey)
                );
            }
        } else {
            // free or empty
            if (urlInput) { urlInput.value = ''; urlInput.setAttribute('readonly', 'readonly'); }
            setKeyReadonly(keyInput, '');
        }
        applyFixedModelProviderUi(modelType, provider);
    } else if (modelType === 'tts' && getTtsProviderMeta(provider) && getTtsProviderMeta(provider).editable_endpoint) {
        // 特异 TTS provider（端点可编辑，如 vLLM-Omni）：URL/Key/ModelId/Voice 全部
        // 可编辑可保存（类似 custom，但 dropdown 里有自己的名字与默认值）。分支条件由
        // tts_provider_registry 的 editable_endpoint 驱动（缺失时 getTtsProviderMeta
        // 结构信号兜底）；预填默认值优先取 api_providers.json，缺失时回退注册表 default_*。
        const _spMeta = getTtsProviderMeta(provider);
        const pInfo = getProviderInfo(provider);
        if (urlInput) {
            // 切换到该 provider 时：
            // - 若 URL 为空，或当前 URL 是从其他 provider 自动填充的 readonly 值（用户没主动编辑过），
            //   覆盖为默认 URL；
            // - 若 URL 是用户主动编辑过的地址（非 readonly），保留。
            const wasReadonly = urlInput.hasAttribute('readonly');
            if (!urlInput.value || !urlInput.value.trim() || wasReadonly) {
                urlInput.value = getProviderOpenrouterUrl(provider, pInfo) || _spMeta.default_url || '';
            }
            urlInput.removeAttribute('readonly');
        }
        if (modelIdInput && (!_isLoadingSavedConfig || !modelIdInput.value || !modelIdInput.value.trim())) {
            modelIdInput.value = pInfo.tts_default_model || _spMeta.default_model || '';
        }
        if (voiceInput && (!_isLoadingSavedConfig || !voiceInput.value || !voiceInput.value.trim())) {
            voiceInput.value = pInfo.tts_default_voice || _spMeta.default_voice || '';
        }
        setKeyEditable(keyInput);
    } else if (modelType === 'tts' && provider === 'gptsovits') {
        // GPT-SoVITS：用下方专属字段（#gptsovits-config-fields 的 URL + voice grid），
        // 标准 url/model/key/voice 不参与，故这里不动标准输入（它们会被隐藏）。
        // 选中即「启用」，无需独立开关。切到 GSV 时若已有 URL 自动拉一次声音列表。
        const gsvUrl = document.getElementById('gptsovitsApiUrl')?.value.trim();
        if (gsvUrl && !_isLoadingSavedConfig) {
            fetchGptSovitsVoices(true);
        }
    } else if (provider === 'custom') {
        // custom: remove readonly
        if (urlInput) urlInput.removeAttribute('readonly');
        if (modelIdInput) modelIdInput.removeAttribute('readonly');
        setModelIdFieldHidden(modelType, false);
        setKeyEditable(keyInput);
    } else {
        // Specific provider
        const pInfo = getProviderInfo(provider);
        const fixedModelApplied = applyFixedModelProviderUi(modelType, provider);
        if (modelIdInput && !fixedModelApplied) modelIdInput.removeAttribute('readonly');
        if (modelType === 'omni') {
            const coreProfile = _coreApiProviders[provider] || {};
            if (urlInput) {
                urlInput.value = getProviderCoreUrl(provider, coreProfile) || getProviderCoreUrl(provider, pInfo);
                urlInput.setAttribute('readonly', 'readonly');
            }
        } else {
            if (urlInput) {
                urlInput.value = getEffectiveAssistUrl(provider, pInfo, { useTokenPlan: false }) || getProviderCoreUrl(provider, pInfo);
                urlInput.setAttribute('readonly', 'readonly');
            }
        }
        const bookKey = getEffectiveAssistKey(provider, null, { useTokenPlan: false });
        setKeyReadonly(keyInput, bookKey, provider);
    }
    if (modelType === 'tts') {
        updateTtsProviderFieldVisibility(provider);
    }
    sel.dataset.currentProvider = provider;
}

/**
 * 按所选 TTS provider 切换字段可见性：选 gptsovits 时显示 GSV 专属字段（URL + voice
 * grid）、隐藏标准 url/model/key/voice；其余 provider 反之。GSV「是否启用」= 下拉是否
 * 选中 gptsovits，取代旧的独立启用开关。
 */
function updateTtsProviderFieldVisibility(provider) {
    const isGsv = (provider === 'gptsovits');
    const standardFields = document.getElementById('tts-standard-fields');
    const gsvFields = document.getElementById('gptsovits-config-fields');
    if (standardFields) standardFields.style.display = isGsv ? 'none' : '';
    if (gsvFields) gsvFields.style.display = isGsv ? 'block' : 'none';
    if (isGsv) updateGptSovitsTutorialLink();
}

/**
 * 设置 GPT-SoVITS「教程文档」按钮的跳转链接：中文（zh-*）走中文文档，其余语言走通用文档。
 * 语言可能在打开页面后才切换，故同时在 updateTtsProviderFieldVisibility 与 localechange 时调用。
 */
function updateGptSovitsTutorialLink() {
    const link = document.getElementById('gptsovitsTutorialLink');
    if (!link) return;
    const lang = (window.i18n && window.i18n.language) || document.documentElement.lang || 'zh-CN';
    const isChinese = String(lang).toLowerCase().startsWith('zh');
    link.href = isChinese
        ? 'https://docs.qq.com/aio/DQ1dDcU9rdURQTWJE?p=RLq03bnCUOGEIa8YwBS58H&client_hint=0'
        : 'https://docs.qq.com/aio/DQ1dDcU9rdURQTWJE?p=Z7zYbDaFk1FIrs4EBk7spv&client_hint=0';
}

// 语言切换时同步更新教程文档链接（中文↔其它语言走不同文档）
window.addEventListener('localechange', updateGptSovitsTutorialLink);

/**
 * 在 key 输入框旁添加"前往管理簿"快捷按钮（如果还没有）
 */
function ensureKeyBookLink(input, providerKey = '') {
    if (!input) return;
    const parent = input.parentElement;
    if (!parent) return;
    const shortcutScope = input.closest('.field-row') || parent;
    const existingLinks = Array.from(shortcutScope.querySelectorAll('.key-book-shortcut'));

    let link = existingLinks.shift() || null;
    existingLinks.forEach(extraLink => extraLink.remove());
    if (!link) {
        link = document.createElement('a');
        link.href = 'javascript:void(0)';
        link.className = 'key-book-shortcut';
        link.setAttribute('data-i18n', 'api.goToKeyBook');
        link.textContent = window.t ? window.t('api.goToKeyBook') : '前往管理簿';
        link.style.cssText = 'font-size: 0.85em; color: #40C5F1; cursor: pointer; margin-left: 8px; white-space: nowrap;';
        link.addEventListener('click', (e) => {
            e.preventDefault();
            expandAndScrollToKeyBook({ providerKey: link.dataset.providerKey || '' });
        });
    }

    link.dataset.providerKey = providerKey || '';
    link.dataset.sourceInputId = input.id || '';
    if (link.parentElement !== parent) {
        parent.appendChild(link);
    }
}

/**
 * 移除 key 输入框旁的"前往管理簿"快捷按钮
 */
function removeKeyBookLink(input) {
    if (!input) return;
    const parent = input.parentElement;
    if (!parent) return;
    const shortcutScope = input.closest('.field-row') || parent;
    shortcutScope.querySelectorAll('.key-book-shortcut').forEach(link => link.remove());
}

// ==================== 加载API服务商选项 ====================

async function loadApiProviders() {
    try {
        const response = await fetch('/api/config/api_providers', { cache: 'no-store' });
        if (response.ok) {
            const data = await response.json();
            if (data.success) {
                // Store registry and full provider info
                _apiKeyRegistry = data.api_key_registry || {};
                _coreApiProviders = data.core_api_providers_full || {};
                _assistApiProviders = data.assist_api_providers_full || {};
                _keyBookApiProviders = data.keybook_api_providers_full || {};

                // TTS provider 元数据（后端 tts_provider_registry → ui_metadata）：
                // 列表转成 key→meta 映射，供下拉过滤 / 字段解锁 / 探测 / 来源能力复用。
                _ttsProviders = {};
                if (Array.isArray(data.tts_providers)) {
                    data.tts_providers.forEach(m => {
                        if (m && m.key) _ttsProviders[m.key] = m;
                    });
                }

                // Fallback: build from array if _full not available
                if (Object.keys(_coreApiProviders).length === 0 && Array.isArray(data.core_api_providers)) {
                    data.core_api_providers.forEach(p => {
                        _coreApiProviders[p.key] = p;
                    });
                }
                if (Object.keys(_assistApiProviders).length === 0 && Array.isArray(data.assist_api_providers)) {
                    data.assist_api_providers.forEach(p => {
                        _assistApiProviders[p.key] = p;
                    });
                }
                // Build registry from providers if not provided
                if (Object.keys(_apiKeyRegistry).length === 0) {
                    const allProviders = { ..._coreApiProviders, ..._assistApiProviders, ..._keyBookApiProviders };
                    Object.keys(allProviders).forEach(pk => {
                        if (pk === 'free') return;
                        // Backend expects camelCase: assistApiKey + PascalCased provider key
                        // e.g. qwen → assistApiKeyQwen, minimax_intl → assistApiKeyMinimaxIntl
                        const defaultField = 'assistApiKey' + pk.replace(/(^|_)([a-z])/g,
                            (_, _sep, c) => c.toUpperCase());
                        _apiKeyRegistry[pk] = {
                            label: allProviders[pk].name || pk,
                            restricted: allProviders[pk].restricted || false,
                            config_field: allProviders[pk].config_field || defaultField
                        };
                    });
                }

                // 填充核心API下拉框
                const coreSelect = document.getElementById('coreApiSelect');
                if (coreSelect) {
                    coreSelect.innerHTML = ''; // 清空现有选项
                    const coreList = Array.isArray(data.core_api_providers) ? data.core_api_providers : [];
                    coreList.forEach(provider => {
                        // 如果是大陆用户，过滤掉受限的服务商
                        if (isProviderRestricted(provider.key)) {
                            console.log(`[Region] 隐藏核心API选项: ${provider.key}（大陆用户）`);
                            return; // 跳过此选项
                        }

                        const option = document.createElement('option');
                        option.value = provider.key;
                        // 使用翻译键获取显示名称
                        const translationKey = `api.coreProviderNames.${provider.key}`;
                        if (window.t) {
                            const translatedName = window.t(translationKey);
                            option.textContent = (translatedName !== translationKey) ? translatedName : provider.name;
                        } else {
                            option.textContent = provider.name;
                        }
                        coreSelect.appendChild(option);
                    });
                }

                // 填充辅助API下拉框
                const assistSelect = document.getElementById('assistApiSelect');
                if (assistSelect) {
                    assistSelect.innerHTML = ''; // 清空现有选项
                    const assistList = Array.isArray(data.assist_api_providers) ? data.assist_api_providers : [];
                    assistList.forEach(provider => {
                        const ttsMeta = getTtsProviderMeta(provider.key);
                        if (ttsMeta && ttsMeta.tts_dropdown_only) return;

                        // 如果是大陆用户，过滤掉受限的服务商
                        if (isProviderRestricted(provider.key)) {
                            console.log(`[Region] 隐藏辅助API选项: ${provider.key}（大陆用户）`);
                            return; // 跳过此选项
                        }

                        const option = document.createElement('option');
                        option.value = provider.key;
                        // 使用翻译键获取显示名称
                        const translationKey = `api.assistProviderNames.${provider.key}`;
                        if (window.t) {
                            const translatedName = window.t(translationKey);
                            // 如果翻译键存在且不是键本身，使用翻译；否则使用原始名称
                            option.textContent = (translatedName !== translationKey) ? translatedName : provider.name;
                        } else {
                            option.textContent = provider.name;
                        }
                        assistSelect.appendChild(option);
                    });
                }

                // 渲染 Key Book
                renderKeyBook(_apiKeyRegistry, _assistApiProviders);

                // 隐藏大陆用户不可用的 Key Book 输入行
                hideRestrictedKeyBookInputs();

                // 动态渲染的元素需要重新翻译
                if (window.updatePageTexts) window.updatePageTexts();

                // 填充模型服务商下拉框
                populateModelProviderDropdowns();

                syncProviderSelectDropdowns(null, { rebuild: true });

                return true;
            } else {
                console.error('加载API服务商配置失败:', data.error);
                // 加载失败时，确保下拉框为空
                clearApiProviderSelects();
                return false;
            }
        } else {
            console.error('获取API服务商配置失败，HTTP状态:', response.status);
            // 加载失败时，确保下拉框为空
            clearApiProviderSelects();
            return false;
        }
    } catch (error) {
        console.error('加载API服务商配置时出错:', error);
        // 加载失败时，确保下拉框为空
        clearApiProviderSelects();
        return false;
    }
}

async function loadCurrentApiKey() {
    // 先清空输入框和下拉框，避免显示错误的默认值
    const apiKeyInput = document.getElementById('apiKeyInput');
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');
    const assistApiKeyInput = document.getElementById('assistApiKeyInput');
    _ttsConfigDirty = false;

    if (apiKeyInput) {
        apiKeyInput.value = '';
    }
    if (coreApiSelect) {
        coreApiSelect.value = '';
    }
    if (assistApiSelect) {
        assistApiSelect.value = '';
    }
    if (assistApiKeyInput) {
        assistApiKeyInput.value = '';
    }

    syncProviderSelectDropdowns();

    try {
        const response = await fetch('/api/config/core_api');
        if (response.ok) {
            const data = await response.json();
            _resolvedProviderUrls = (data.resolvedProviderUrls && typeof data.resolvedProviderUrls === 'object')
                ? { ...data.resolvedProviderUrls }
                : {};
            // 设置API Key显示
            if (data.enableCustomApi) {
                showCurrentApiKey(window.t ? window.t('api.currentUsingCustomApi') : '当前使用：自定义API模式', '', true);
            } else if (data.api_key) {
                // 免费判定只看 core：assist=free 配付费 core 时 coreApiKey 是真实付费 Key。
                if (data.api_key === 'free-access' || data.coreApi === 'free') {
                    showCurrentApiKey(window.t ? window.t('api.currentUsingFreeVersion') : '当前使用：免费版（无需API Key）', 'free-access', true);
                } else {
                    showCurrentApiKey(window.t ? window.t('api.currentApiKey', { key: maskApiKey(data.api_key) }) : `当前API Key: ${maskApiKey(data.api_key)}`, data.api_key, true);
                }
            } else {
                showCurrentApiKey(window.t ? window.t('api.currentNoApiKey') : '当前暂未设置API Key', '', false);
            }

            // 辅助函数：设置输入框的值和占位符
            function setInputValueLocal(elementId, value, placeholder) {
                const element = document.getElementById(elementId);
                if (typeof value === 'string' && element) {
                    element.value = value;
                    if (placeholder !== undefined) {
                        element.placeholder = value || placeholder;
                    }
                }
            }

            // 设置核心API Key输入框的值（重要：必须在显示提示后设置）
            if (apiKeyInput) {
                if (data.api_key === 'free-access' || data.coreApi === 'free') {
                    // 免费版本：显示用户友好的文本
                    apiKeyInput.value = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
                } else if (data.api_key) {
                    // 有API Key时设置
                    setMaskedInput(apiKeyInput, data.api_key);
                    attachMaskBehavior(apiKeyInput);
                }
                // autoFillCoreApiKey 将在 coreApiSelect.value 设置后调用
            }
            // 设置高级设定的值（确保下拉框已加载选项）
            if (data.coreApi && coreApiSelect) {
                if (coreApiSelect.options.length > 0) {
                    // 验证选项值是否存在
                    const optionExists = Array.from(coreApiSelect.options).some(opt => opt.value === data.coreApi);
                    if (optionExists) {
                        coreApiSelect.value = data.coreApi;
                        syncProviderSelectDropdowns(coreApiSelect);
                    }
                } else {
                    // 等待选项加载完成后再设置值
                    waitForOptions(coreApiSelect, data.coreApi, {
                        maxAttempts: 20,
                        interval: 50,
                        onSuccess: () => {
                            // 选项加载并设置完成后，自动填充API Key
                            if (!data.enableCustomApi && !data.api_key) {
                                autoFillCoreApiKey(true);
                            }
                        }
                    });
                }
                // 如果选项已存在（同步路径），也需要在这里自动填充
                if (!data.enableCustomApi && !data.api_key) {
                    autoFillCoreApiKey(true);
                }
            }
            if (data.assistApi && assistApiSelect) {
                if (assistApiSelect.options.length > 0) {
                    // 验证选项值是否存在
                    const optionExists = Array.from(assistApiSelect.options).some(opt => opt.value === data.assistApi);
                    if (optionExists) {
                        assistApiSelect.value = data.assistApi;
                        syncProviderSelectDropdowns(assistApiSelect);
                    }
                } else {
                    waitForOptions(assistApiSelect, data.assistApi);
                }
            }
            const useMimoTokenPlanToggle = document.getElementById('useMimoTokenPlan');
            if (useMimoTokenPlanToggle) {
                useMimoTokenPlanToggle.checked = data.useMimoTokenPlan === true;
            }
            const mimoTokenPlanKeyInput = document.getElementById('mimoTokenPlanKeyInput');
            if (mimoTokenPlanKeyInput && data.assistApiKeyMimoTokenPlan) {
                setMaskedInput(mimoTokenPlanKeyInput, data.assistApiKeyMimoTokenPlan);
                attachMaskBehavior(mimoTokenPlanKeyInput);
            }
            updateMimoTokenPlanControls();

            // Sync the core API key into the Key Book for the selected core provider
            // so autoFillCoreApiKey() can find it later
            if (data.coreApi && data.coreApi !== 'free' && data.api_key && data.api_key !== 'free-access') {
                syncKeyToBook(data.coreApi, data.api_key);
            }

            // Load all assist API keys into Key Book inputs
            // Use api_key_registry as single source of truth for field mapping
            Object.keys(_apiKeyRegistry).forEach(providerKey => {
                if (providerKey === 'free') return;
                // 当前核心 provider 对应的管理簿位置在上面已经用 data.api_key
                // (coreApiKey, 权威值) 同步过了。这里再覆盖一次 data.assistApiKey<X>
                // 是旧 assist 用法的残留：用户曾经把 qwen 当 assist 用过，留下了
                // 一个旧 assistApiKeyQwen 字段；后来切回 qwen 当核心并换新 Key，
                // 旧字段还在但已失效。保存时 _coreApiKeyInputDirty=false 会
                // 优先用管理簿值 → coreApiKey 被悄悄 rollback 成旧 Key
                // (Codex P1 #3258747306)。data.api_key 为空时不跳过 —— 那种情况
                // step 1 没写过 keybook，仍然允许 assistApiKey<X> 提供初始值。
                if (providerKey === data.coreApi && data.api_key) return;
                const dataField = _apiKeyRegistry[providerKey].config_field;
                if (!dataField || !data.hasOwnProperty(dataField)) return;
                const val = data[dataField];
                // 服务商专属 Key 优先进入管理簿；coreApiKey 仅作为旧配置回退。
                // 这样 qwen_intl 同时作为核心/辅助时，不会用旧核心 Key 覆盖国际版 Key。
                // Only sync non-empty values; empty strings from the backend
                // usually mean "not configured" rather than "intentionally cleared".
                if (val !== '') {
                    syncKeyToBook(providerKey, val);
                }
            });

            // Set assist key input from server response data directly.
            // 不从管理簿读取，因为：
            // 1) 同服务商时管理簿被核心Key占据；
            // 2) 受限服务商（restricted）的管理簿DOM不存在。
            if (data.assistApi && data.assistApi !== 'free') {
                const assistDataField = (_apiKeyRegistry[data.assistApi] || {}).config_field;
                const assistKeyFromData = assistDataField ? (data[assistDataField] || '') : '';
                if (assistApiKeyInput) {
                    if (assistKeyFromData) {
                        setMaskedInput(assistApiKeyInput, assistKeyFromData);
                        attachMaskBehavior(assistApiKeyInput);
                    } else {
                        // 后端无辅助Key时，尝试从管理簿读取（兼容旧数据迁移）
                        const bookKey = syncKeyFromBook(data.assistApi);
                        if (bookKey) {
                            setMaskedInput(assistApiKeyInput, bookKey);
                            attachMaskBehavior(assistApiKeyInput);
                        } else if (!data.enableCustomApi) {
                            autoFillAssistApiKey(true);
                        }
                    }
                }
            }

            // 加载用户自定义API配置
            setInputValue('conversationModelUrl', data.conversationModelUrl);
            setInputValue('conversationModelId', data.conversationModelId);
            setInputValue('conversationModelApiKey', data.conversationModelApiKey);

            setInputValue('summaryModelUrl', data.summaryModelUrl);
            setInputValue('summaryModelId', data.summaryModelId);
            setInputValue('summaryModelApiKey', data.summaryModelApiKey);

            setInputValue('gameMainModelUrl', data.gameMainModelUrl);
            setInputValue('gameMainModelId', data.gameMainModelId);
            setInputValue('gameMainModelApiKey', data.gameMainModelApiKey);

            setInputValue('gameSummaryModelUrl', data.gameSummaryModelUrl);
            setInputValue('gameSummaryModelId', data.gameSummaryModelId);
            setInputValue('gameSummaryModelApiKey', data.gameSummaryModelApiKey);

            setInputValue('correctionModelUrl', data.correctionModelUrl);
            setInputValue('correctionModelId', data.correctionModelId);
            setInputValue('correctionModelApiKey', data.correctionModelApiKey);

            setInputValue('emotionModelUrl', data.emotionModelUrl);
            setInputValue('emotionModelId', data.emotionModelId);
            setInputValue('emotionModelApiKey', data.emotionModelApiKey);

            setInputValue('visionModelUrl', data.visionModelUrl);
            setInputValue('visionModelId', data.visionModelId);
            setInputValue('visionModelApiKey', data.visionModelApiKey);
            setInputValue('agentModelUrl', data.agentModelUrl);
            setInputValue('agentModelId', data.agentModelId);
            setInputValue('agentModelApiKey', data.agentModelApiKey);

            setInputValue('omniModelUrl', data.omniModelUrl);
            setInputValue('omniModelId', data.omniModelId);
            setInputValue('omniModelApiKey', data.omniModelApiKey);

            setInputValue('ttsModelUrl', data.ttsModelUrl);
            setInputValue('ttsModelId', data.ttsModelId);
            setInputValue('ttsModelApiKey', data.ttsModelApiKey);
            setInputValue('ttsVoiceId', data.ttsVoiceId);

            // 加载 GPT-SoVITS 配置：启用状态以 ttsModelProvider 下拉为准，
            // 无下拉时回落旧 gptsovitsEnabled / localhost 启发式（兼容存量）
            loadGptSovitsConfig(
                data.ttsModelUrl,
                data.ttsVoiceId,
                data.ttsModelId,
                data.ttsModelApiKey,
                data.gptsovitsEnabled,
                data.ttsModelProvider,
            );

            // 加载MCPR_TOKEN
            setInputValue('mcpTokenInput', data.mcpToken);

            // Load *ModelProvider for each model type and apply
            _isLoadingSavedConfig = true;
            MODEL_TYPES.forEach(mt => {
                const providerField = `${mt}ModelProvider`;
                const sel = document.getElementById(providerField);
                if (!sel) return;

                // GPT-SoVITS 迁到 ttsModelProvider 下拉后，「启用 GSV」= 下拉选中 gptsovits。
                // loadGptSovitsConfig 已在前面把 _loadedGptSovitsState 解析好（含旧
                // gptsovitsEnabled / legacy 嗅探）；这里据此把 tts 下拉钉到 gptsovits，
                // 覆盖「有 URL 但无 provider → custom」的旧回退，保证存量配置正确回填。
                if (mt === 'tts' && _loadedGptSovitsState === 'enabled') {
                    sel.value = 'gptsovits';
                    onCustomModelProviderChange(mt);
                    return;
                }
                if (data[providerField]) {
                    // Saved provider value exists — use it
                    const optionExists = Array.from(sel.options).some(opt => opt.value === data[providerField]);
                    if (optionExists) {
                        sel.value = data[providerField];
                    } else {
                        // Provider no longer available (removed/restricted) — preserve saved URL/Key
                        sel.value = 'custom';
                    }
                } else {
                    // No saved provider. If user has existing custom URL/Key values,
                    // treat as "custom" to avoid overwriting them with auto-fill.
                    const existingUrl = (data[`${mt}ModelUrl`] || '').trim();
                    const existingKey = (data[`${mt}ModelApiKey`] || '').trim();
                    if (existingUrl || existingKey) {
                        sel.value = 'custom';
                    }
                    // Otherwise keep the default (follow_core/follow_assist)
                }
                onCustomModelProviderChange(mt);
            });
            _isLoadingSavedConfig = false;

            // 加载自定义API启用状态
            if (typeof data.enableCustomApi === 'boolean' && document.getElementById('enableCustomApi')) {
                document.getElementById('enableCustomApi').checked = data.enableCustomApi;
                // 延迟应用状态，确保API Key已正确加载
                setTimeout(() => {
                    toggleCustomApi(true);
                }, 100);
            }

            // 加载禁用TTS状态
            if (typeof data.disableTts === 'boolean' && document.getElementById('disableTts')) {
                document.getElementById('disableTts').checked = data.disableTts;
            }

        } else {
            showCurrentApiKey(window.t ? window.t('api.getCurrentApiKeyFailed') : '获取当前API Key失败', '', false);
        }
    } catch (error) {
        console.error('loadCurrentApiKey error:', error);
        showCurrentApiKey(window.t ? window.t('api.errorGettingCurrentApiKey') : '获取当前API Key时出错', '', false);
    } finally {
        _isLoadingSavedConfig = false;
    }
}

// 全局变量存储待保存的API Key
let pendingApiKey = null;

// ==================== GPT-SoVITS v3 配置相关函数 ====================

/**
 * 从保存的 TTS 字段解析并加载 GPT-SoVITS v3 配置
 * 启用状态以 ttsModelProvider 下拉为准；无下拉时回落旧 gptsovitsEnabled / localhost 启发式（兼容存量）
 */
function loadGptSovitsConfig(ttsModelUrl, ttsVoiceId, ttsModelId = '', ttsModelApiKey = '', gptsovitsEnabled = null, ttsModelProvider = '') {
    // 检查是否是禁用但保存了配置的情况
    let isDisabledWithConfig = false;
    let savedUrl = '';
    let savedVoiceId = '';

    if (ttsVoiceId && ttsVoiceId.startsWith('__gptsovits_disabled__|')) {
        isDisabledWithConfig = true;
        const parts = ttsVoiceId.substring('__gptsovits_disabled__|'.length).split('|', 2);
        if (parts.length >= 1) savedUrl = parts[0];
        if (parts.length >= 2) savedVoiceId = parts[1];
    }

    // 启用判定与后端 snapshot 派生对偶（见 utils/config_manager.py）：ttsModelProvider
    // 一旦「显式选了某个 TTS provider」即唯一真相——选中 gptsovits 才启用、选别家就关，
    // 旧 gptsovitsEnabled / disabled sentinel 不参与。仅当未显式选择时（provider 缺失/空串，
    // 或 follow_assist/follow_core 这两个「跟随 assist/core」默认哨兵）才回落 legacy 路径：
    // 先认 __gptsovits_disabled__| sentinel，再回落显式旧 flag / localhost 启发式。
    // ⚠️ follow_* 必须当「未显式选」而非显式 provider，否则存量 GSV 用户（gptsovitsEnabled=true
    // + ttsModelProvider='follow_assist' 默认值）会被前端判成关、与后端分叉（Codex PR#1850 P1）。
    // 显式 provider 压过 sentinel：provider=gptsovits 共存旧 sentinel 时仍判启用，URL/voice
    // 从 sentinel 解出做迁移（CodeRabbit/Greptile PR#1850）。这也保证远程 GSV 的 dropdown-only
    // 用户（启发式不认远程 URL）reload 仍回填，且显式切走后残留旧 flag 不会把 GSV 兜回来。
    const provider = (ttsModelProvider || '').trim();
    const isFollowOrUnset = (provider === '' || provider === 'follow_assist' || provider === 'follow_core');
    const hasExplicitEnabledFlag = typeof gptsovitsEnabled === 'boolean';
    const isLegacyEnabled = !hasExplicitEnabledFlag
        && !isDisabledWithConfig
        && looksLikeLegacyGptSovitsConfig(ttsModelUrl, ttsModelId, ttsModelApiKey);
    let isEnabled;
    if (!isFollowOrUnset) {
        isEnabled = (provider === 'gptsovits');
    } else if (isDisabledWithConfig) {
        isEnabled = false;
    } else {
        isEnabled = hasExplicitEnabledFlag ? gptsovitsEnabled : isLegacyEnabled;
    }

    // disabled 态仅在「未显式选 + 存量 sentinel」时成立；显式选了 provider（含 gptsovits）
    // 以下拉为准，不被 sentinel 拉回 disabled。
    _loadedGptSovitsState = (isFollowOrUnset && isDisabledWithConfig)
        ? 'disabled'
        : (isEnabled ? 'enabled' : 'none');

    // GSV 迁到 ttsModelProvider 下拉后，启用状态由下拉表达（这里不再操作已删除的
    // gptsovitsEnabled 开关）。下拉值与字段可见性由随后的 provider 还原循环统一处理
    // （见 MODEL_TYPES 还原里的 _loadedGptSovitsState==='enabled' 分支）。

    // 确定要加载的配置
    const urlToLoad = isDisabledWithConfig ? savedUrl : (isEnabled ? ttsModelUrl : '');
    const voiceIdToLoad = isDisabledWithConfig ? savedVoiceId : (isEnabled ? ttsVoiceId : '');

    if (urlToLoad || voiceIdToLoad) {
        const apiUrlEl = document.getElementById('gptsovitsApiUrl');
        if (apiUrlEl && urlToLoad) apiUrlEl.value = urlToLoad;

        // 设置隐藏 input 的值（卡片高亮会在 fetchGptSovitsVoices 完成后自动匹配）
        if (voiceIdToLoad) {
            const hiddenInput = document.getElementById('gptsovitsVoiceId');
            if (hiddenInput) hiddenInput.value = voiceIdToLoad;
        }

        // 自动获取语音列表（如果有 URL 且非禁用状态）
        const autoUrl = urlToLoad || document.getElementById('gptsovitsApiUrl')?.value.trim();
        if (autoUrl && isEnabled) {
            fetchGptSovitsVoices(true);
        }
    }
}

/**
 * 选中一个 GPT-SoVITS voice 卡片
 * @param {string} voiceId - 要选中的 voice_id
 */
function selectGsvVoice(voiceId) {
    const hiddenInput = document.getElementById('gptsovitsVoiceId');
    if (hiddenInput) hiddenInput.value = voiceId;

    // 更新卡片高亮
    const grid = document.getElementById('gsv-voices-grid');
    if (!grid) return;
    grid.querySelectorAll('.gsv-voice-card').forEach(card => {
        const isSelected = card.dataset.voiceId === voiceId;
        card.classList.toggle('selected', isSelected);
        card.setAttribute('aria-checked', isSelected ? 'true' : 'false');
        card.tabIndex = isSelected ? 0 : -1;
    });
}

/**
 * 从 GPT-SoVITS v3 API 获取可用语音配置列表并渲染为卡片网格
 * @param {boolean} silent - 静默模式，不显示错误提示
 */
async function fetchGptSovitsVoices(silent = false) {
    const apiUrl = document.getElementById('gptsovitsApiUrl')?.value.trim() || 'http://127.0.0.1:9881';
    const grid = document.getElementById('gsv-voices-grid');
    const hiddenInput = document.getElementById('gptsovitsVoiceId');
    if (!grid) return;

    // 记住当前选中的值
    const currentValue = hiddenInput ? hiddenInput.value : '';

    // 显示加载状态
    grid.innerHTML = '<div class="gsv-voices-loading">' + _escHtml(window.t ? window.t('api.loadingConfig') : '正在加载...') + '</div>';

    try {
        const resp = await fetch('/api/config/gptsovits/list_voices', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_url: apiUrl })
        });
        const result = await resp.json();

        if (result.success && Array.isArray(result.voices)) {
            grid.innerHTML = '';

            if (result.voices.length === 0) {
                grid.innerHTML = '<div class="gsv-voices-empty">' + _escHtml(window.t ? window.t('api.gptsovitsNoVoices') : '-- 无可用配置 --') + '</div>';
            } else {
                let hasSelectedCard = false;
                result.voices.forEach(v => {
                    const card = document.createElement('div');
                    card.className = 'gsv-voice-card';
                    card.dataset.voiceId = v.id;
                    const isSelected = v.id === currentValue;
                    if (isSelected) card.classList.add('selected');
                    if (isSelected) hasSelectedCard = true;
                    card.setAttribute('role', 'radio');
                    card.setAttribute('aria-checked', isSelected ? 'true' : 'false');
                    card.tabIndex = isSelected ? 0 : -1;

                    // 卡片内容
                    let html = '';
                    html += '<div class="gsv-card-name">' + _escHtml(v.name || v.id) + '</div>';
                    if (v.name && v.name !== v.id) {
                        html += '<div class="gsv-card-id">' + _escHtml(v.id) + '</div>';
                    }
                    if (v.version) {
                        html += '<div class="gsv-card-version">' + _escHtml(v.version) + '</div>';
                    }
                    if (v.description) {
                        html += '<div class="gsv-card-desc" title="' + _escAttr(v.description) + '">' + _escHtml(v.description) + '</div>';
                    }
                    card.innerHTML = html;

                    card.addEventListener('click', () => selectGsvVoice(v.id));
                    card.addEventListener('keydown', (event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            selectGsvVoice(v.id);
                            return;
                        }

                        if (event.key === 'ArrowRight' || event.key === 'ArrowDown' || event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                            event.preventDefault();
                            const cards = Array.from(grid.querySelectorAll('.gsv-voice-card'));
                            const currentIndex = cards.indexOf(card);
                            if (currentIndex === -1 || cards.length === 0) return;

                            const step = (event.key === 'ArrowRight' || event.key === 'ArrowDown') ? 1 : -1;
                            const nextIndex = (currentIndex + step + cards.length) % cards.length;
                            const nextCard = cards[nextIndex];
                            if (nextCard) {
                                selectGsvVoice(nextCard.dataset.voiceId || '');
                                nextCard.focus();
                            }
                        }
                    });
                    grid.appendChild(card);
                });

                // 当没有任何已选项时，保证网格中至少一个卡片可被键盘 Tab 聚焦
                if (!hasSelectedCard) {
                    const firstCard = grid.querySelector('.gsv-voice-card');
                    if (firstCard) firstCard.tabIndex = 0;
                }
            }

            if (!silent) {
                showStatus(window.t ? window.t('api.gptsovitsVoicesLoaded', { count: result.voices.length }) : `已加载 ${result.voices.length} 个语音配置`, 'success');
            }
        } else {
            const _errMsg = (result.code && window.t) ? window.t('errors.' + result.code, result.details || {}) : result.error;
            grid.innerHTML = '<div class="gsv-voices-empty">' + _escHtml(_errMsg || (window.t ? window.t('api.gptsovitsVoicesLoadFailed') : '获取语音列表失败')) + '</div>';
            if (!silent) {
                showStatus(_errMsg || (window.t ? window.t('api.gptsovitsVoicesLoadFailed') : '获取语音列表失败'), 'error');
            }
        }
    } catch (e) {
        grid.innerHTML = '<div class="gsv-voices-empty">' + _escHtml(window.t ? window.t('api.gptsovitsVoicesLoadFailed') : '获取语音列表失败') + '</div>';
        if (!silent) {
            showStatus(window.t ? window.t('api.gptsovitsVoicesLoadFailed') : '获取语音列表失败: ' + e.message, 'error');
        }
    }
}

/** HTML escape helper */
function _escHtml(str) {
    const d = document.createElement('div');
    d.textContent = (str == null ? '' : String(str));
    return d.innerHTML;
}

/** Attribute escape helper */
function _escAttr(str) {
    const s = (str == null ? '' : String(str));
    return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * 从 GPT-SoVITS v3 配置字段组装 ttsModelUrl 和 ttsVoiceId（用于保存，不检查启用状态）
 * v3 voice_id 格式: 直接就是 voice_id 字符串
 */
function getGptSovitsConfigForSave() {
    const apiUrl = document.getElementById('gptsovitsApiUrl')?.value.trim() || '';
    const voiceId = document.getElementById('gptsovitsVoiceId')?.value || '';

    return {
        url: apiUrl || 'http://127.0.0.1:9881',
        voiceId: voiceId
    };
}

// GPT-SoVITS「是否启用」迁到 ttsModelProvider 下拉后，旧的 getGptSovitsConfig（按
// checkbox 返回 null）与 toggleGptSovitsConfig（按 checkbox 切显隐）已退役：启用状态
// 由 ttsModelProvider==='gptsovits' 表达，字段显隐由 updateTtsProviderFieldVisibility 驱动。

// ==================== 结束 GPT-SoVITS v3 配置相关函数 ====================

function updateAssistApiKeyInputAvailability() {
    const assistApiSelect = document.getElementById('assistApiSelect');
    const assistApiKeyInput = document.getElementById('assistApiKeyInput');
    if (!assistApiSelect || !assistApiKeyInput) return;

    const isFreeAssistApi = assistApiSelect.value === 'free';
    assistApiKeyInput.dataset.disabledByFreeAssist = isFreeAssistApi ? 'true' : 'false';
    assistApiKeyInput.disabled = isFreeAssistApi;
    assistApiKeyInput.readOnly = false;
    assistApiKeyInput.required = false;

    if (isFreeAssistApi) {
        const freeText = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
        assistApiKeyInput.placeholder = freeText;
        assistApiKeyInput.dataset.realKey = '';
        assistApiKeyInput.value = freeText;
        attachMaskBehavior(assistApiKeyInput);
        updateMimoTokenPlanControls();
        return;
    }

    assistApiKeyInput.placeholder = window.t ? window.t('api.assistApiKeyPlaceholder') : '留空使用管理簿对应 Key';
    if (isFreeVersionText(getRealKey(assistApiKeyInput))) {
        setMaskedInput(assistApiKeyInput, '');
    }
    updateMimoTokenPlanControls();
}

// 切换自定义API启用状态
function toggleCustomApi(skipAutoFill) {
    const enableCustomApi = document.getElementById('enableCustomApi');
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');
    const apiKeyInput = document.getElementById('apiKeyInput');

    const isCustomEnabled = enableCustomApi.checked;
    const isFreeVersion = coreApiSelect && coreApiSelect.value === 'free';

    // 禁用或启用相关控件
    // core=free 时只锁核心 API Key 输入，辅助 Key 输入由辅助服务商自身决定。
    if (coreApiSelect) coreApiSelect.disabled = false;
    if (assistApiSelect) assistApiSelect.disabled = false;
    if (apiKeyInput) apiKeyInput.disabled = isFreeVersion;
    updateAssistApiKeyInputAvailability();

    // 控制自定义API容器的折叠状态
    const customApiContainer = document.getElementById('custom-api-container');
    if (customApiContainer) {
        if (isCustomEnabled) {
            customApiContainer.style.display = 'grid';
            // 展开所有模型配置
            const modelContainers = document.querySelectorAll('.model-config-container');
            modelContainers.forEach(container => {
                container.style.display = 'block';
            });
        } else {
            customApiContainer.style.display = 'none';
            // 折叠所有模型配置
            const modelContainers = document.querySelectorAll('.model-config-container');
            modelContainers.forEach(container => {
                container.style.display = 'none';
            });
        }
    }

    // 更新提示信息
    const freeVersionHint = document.getElementById('freeVersionHint');
    if (freeVersionHint) {
        if (isCustomEnabled) {
            freeVersionHint.textContent = window.t ? window.t('api.customApiEnabledHint') : '（自定义API已启用）';
            freeVersionHint.style.color = '#ff6b35';
            freeVersionHint.style.display = 'inline';
        } else if (isFreeVersion) {
            freeVersionHint.textContent = window.t ? window.t('api.freeVersionHint') : '（免费版无需填写）';
            freeVersionHint.style.color = '#28a745';
            freeVersionHint.style.display = 'inline';
        } else {
            freeVersionHint.style.display = 'none';
        }
    }

    // 关闭自定义API时，自动填充已保存的API Key
    // 但如果是从 loadCurrentApiKey 调用（skipAutoFill=true），
    // Key 已由后端数据正确设置，跳过 autoFill 以免管理簿覆盖
    if (!isCustomEnabled && !skipAutoFill) {
        autoFillCoreApiKey(true);
        autoFillAssistApiKey(true);
        updateAssistApiRecommendation();
    }

    syncProviderSelectDropdowns();
}

// 自定义API折叠切换函数
function toggleCustomApiSection() {
    const customApiOptions = document.getElementById('custom-api-options');
    const btn = document.getElementById('custom-api-toggle-btn');
    if (customApiOptions.style.display === 'none') {
        customApiOptions.style.display = 'block';
        btn.classList.add('rotated');
    } else {
        customApiOptions.style.display = 'none';
        btn.classList.remove('rotated');
    }
}

// 清空自定义API配置
function clearCustomApi() {
    // 显示页面内确认弹窗
    document.getElementById('clear-custom-api-modal').style.display = 'flex';
}

function closeClearCustomApiModal() {
    document.getElementById('clear-custom-api-modal').style.display = 'none';
}

function confirmClearCustomApi() {
    closeClearCustomApiModal();

    // 清空所有自定义模型的 URL / Model ID / API Key
    MODEL_TYPES.forEach(mt => {
        const urlEl = document.getElementById(`${mt}ModelUrl`);
        const idEl = document.getElementById(`${mt}ModelId`);
        const keyEl = document.getElementById(`${mt}ModelApiKey`);
        if (urlEl) urlEl.value = '';
        if (idEl) idEl.value = '';
        if (keyEl) {
            keyEl.value = '';
            // 清除遮蔽状态（如果有）
            if (keyEl.dataset) {
                delete keyEl.dataset.realKey;
                delete keyEl.dataset.masked;
            }
        }
        // 重置 Provider 下拉为默认值并同步联动状态
        const providerEl = document.getElementById(`${mt}ModelProvider`);
        if (providerEl) {
            providerEl.value = getDefaultProviderForModelType(mt);
            onCustomModelProviderChange(mt);
        }
    });

    // 清空 TTS Voice ID
    const ttsVoiceIdEl = document.getElementById('ttsVoiceId');
    if (ttsVoiceIdEl) ttsVoiceIdEl.value = '';

    // GSV 启用状态由 ttsModelProvider 下拉表达；上面的 provider 还原循环已把 tts 下拉
    // 重置为 follow_core 并切回标准字段（updateTtsProviderFieldVisibility），无需再单独
    // 取消勾选已删除的 gptsovitsEnabled 开关。
    // 清空 GPT-SoVITS 隐藏字段并重置状态，防止保存时残留旧配置
    const gptsovitsApiUrlEl = document.getElementById('gptsovitsApiUrl');
    if (gptsovitsApiUrlEl) gptsovitsApiUrlEl.value = '';
    const gptsovitsVoiceIdEl = document.getElementById('gptsovitsVoiceId');
    if (gptsovitsVoiceIdEl) gptsovitsVoiceIdEl.value = '';
    _loadedGptSovitsState = 'none';
    _ttsConfigDirty = true;

    const enableCustomApi = document.getElementById('enableCustomApi');
    if (enableCustomApi && enableCustomApi.checked) {
        enableCustomApi.checked = false;
        toggleCustomApi(true);
    }

    // 同步折叠外层自定义API面板
    const customApiOptions = document.getElementById('custom-api-options');
    if (customApiOptions) customApiOptions.style.display = 'none';
    const customApiToggleBtn = document.getElementById('custom-api-toggle-btn');
    if (customApiToggleBtn) customApiToggleBtn.classList.remove('rotated');

    showStatus(window.t ? window.t('api.clearCustomApiSuccess') : '自定义API配置已清空，请点击「保存设置」按钮以保存更改', 'success');
}

// 为自定义API开关添加事件监听器
document.addEventListener('DOMContentLoaded', function () {
    const enableCustomApi = document.getElementById('enableCustomApi');
    if (enableCustomApi) {
        enableCustomApi.addEventListener('change', () => toggleCustomApi());
    }
    ['ttsModelProvider', 'ttsModelUrl', 'ttsModelId', 'ttsModelApiKey', 'ttsVoiceId'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('change', markTtsConfigDirty);
        if (el.tagName !== 'SELECT') {
            el.addEventListener('input', markTtsConfigDirty);
        }
    });

    // 拦截所有 target="_blank" 的外部链接，使用系统默认浏览器打开
    document.querySelectorAll('a[target="_blank"]').forEach(function (link) {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            var href = link.getAttribute('href');
            if (!href) return;
            if (window.electronShell && window.electronShell.openExternal) {
                window.electronShell.openExternal(href);
            } else {
                window.open(href, '_blank', 'noopener,noreferrer');
            }
        });
    });


});



async function save_button_down(e) {

    if (e && e.preventDefault) e.preventDefault();
    if (_apiSaveInProgress) return;

    const apiKeyInput = document.getElementById('apiKeyInput');

    // 获取高级设定的值
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');

    // 获取自定义API启用状态
    const enableCustomApiElement = document.getElementById('enableCustomApi');
    const enableCustomApi = enableCustomApiElement ? enableCustomApiElement.checked : false;

    // 优先从选择器获取值
    let coreApi = coreApiSelect ? coreApiSelect.value : '';
    let assistApi = assistApiSelect ? assistApiSelect.value : '';

    // 如果核心API选择器被禁用，检查是否是因为免费版本
    if (coreApiSelect && coreApiSelect.disabled && coreApi === '') {
        if (!enableCustomApi && coreApiSelect.value === 'free') {
            coreApi = 'free';
        }
    }

    // 如果辅助API选择器被禁用，检查是否是因为免费版本
    if (assistApiSelect && assistApiSelect.disabled && assistApi === '') {
        if (!enableCustomApi && coreApi === 'free') {
            assistApi = 'free';
        }
    }

    // 防御：coreApi 为空 = 服务商下拉尚未加载完成（loadCurrentApiKey 起手会先把下拉
    // 清空成 ''，再 await 后端数据异步回填）。在这个窗口内点保存（尤其是开着自定义API
    // 绕过了下方的空 Key 校验时）会把空 coreApi 写盘，后端解析时会把空值兜底成别的
    // 服务商，导致免费版被悄悄切走、key 失效。一律中止保存并提示稍候重试，绝不写空 provider。
    if (!coreApi) {
        showStatus(window.t ? window.t('api.configNotReady') : '配置尚未加载完成，请稍候重试', 'error');
        return;
    }

    // 处理API Key（优先读取真实 key）
    let apiKey = getRealKey(apiKeyInput);
    if (isFreeVersionText(apiKey)) {
        apiKey = '';
    }

    // handleCoreKeyChange 用 300ms 防抖才翻 _coreApiKeyInputDirty / syncKeyToBook,
    // 用户快速 type/paste/clear 后立刻点保存会落在窗口内：dirty 还是 false、book DOM
    // 也还是旧值。这里按 input 当前值和 book 快照对比直接判脏，绕过防抖窗口。
    if (coreApi && coreApi !== 'free' && !_coreApiKeyInputDirty && _apiKeyRegistry[coreApi]) {
        const bookSnapshot = syncKeyFromBook(coreApi);
        if (bookSnapshot !== null && bookSnapshot !== apiKey) {
            _coreApiKeyInputDirty = true;
        }
    }

    // 读取辅助API Key
    const assistKeyInput = document.getElementById('assistApiKeyInput');
    const assistKeyVal = getEffectiveAssistKey(assistApi, assistKeyInput);
    const useMimoTokenPlan = isMimoTokenPlanActive();
    const mimoTokenPlanKeyInput = document.getElementById('mimoTokenPlanKeyInput');
    const mimoTokenPlanKey = mimoTokenPlanKeyInput ? getRealKey(mimoTokenPlanKeyInput) : '';

    // Collect keys from keyBookInput_* via _apiKeyRegistry.
    // syncKeyFromBook returns null when DOM is absent (restricted/hidden provider)
    // — skip those to avoid overwriting backend values with empty string.
    const allBookKeys = {};
    Object.keys(_apiKeyRegistry).forEach(pk => {
        if (pk === 'free') return;
        const val = syncKeyFromBook(pk);
        if (val !== null) {
            allBookKeys[pk] = val; // include '' so backend can clear
        }
    });

    // 【修复】将上方主输入框的修改强制覆盖到保存 payload 中
    // 否则直接点保存时，后台的 assistApiKey[Provider] 会保留 Key Book 中的旧值
    if (coreApi && coreApi !== 'free' && _apiKeyRegistry[coreApi]) {
        if (_coreApiKeyInputDirty) {
            allBookKeys[coreApi] = isFreeVersionText(apiKey) ? '' : apiKey;
        } else if (!(coreApi in allBookKeys) && !isFreeVersionText(apiKey)) {
            allBookKeys[coreApi] = apiKey;
        }
    }
    if (assistApi && assistApi !== 'free' && _apiKeyRegistry[assistApi] && !useMimoTokenPlan) {
        allBookKeys[assistApi] = assistKeyVal;
    }

    const getVal = (id) => {
        const el = document.getElementById(id);
        return el ? el.value.trim() : '';
    };
    // API Key 字段可能被遮蔽，需要读取真实值
    const getKeyVal = (id) => {
        const el = document.getElementById(id);
        return el ? getRealKey(el) : '';
    };

    const conversationModelUrl = getVal('conversationModelUrl');
    const conversationModelId = getVal('conversationModelId');
    const conversationModelApiKey = getKeyVal('conversationModelApiKey');

    const summaryModelUrl = getVal('summaryModelUrl');
    const summaryModelId = getVal('summaryModelId');
    const summaryModelApiKey = getKeyVal('summaryModelApiKey');

    const gameMainModelUrl = getVal('gameMainModelUrl');
    const gameMainModelId = getVal('gameMainModelId');
    const gameMainModelApiKey = getKeyVal('gameMainModelApiKey');

    const gameSummaryModelUrl = getVal('gameSummaryModelUrl');
    const gameSummaryModelId = getVal('gameSummaryModelId');
    const gameSummaryModelApiKey = getKeyVal('gameSummaryModelApiKey');

    const correctionModelUrl = getVal('correctionModelUrl');
    const correctionModelId = getVal('correctionModelId');
    const correctionModelApiKey = getKeyVal('correctionModelApiKey');

    const emotionModelUrl = getVal('emotionModelUrl');
    const emotionModelId = getVal('emotionModelId');
    const emotionModelApiKey = getKeyVal('emotionModelApiKey');

    const visionModelUrl = getVal('visionModelUrl');
    const visionModelId = getVal('visionModelId');
    const visionModelApiKey = getKeyVal('visionModelApiKey');
    const agentModelUrl = getVal('agentModelUrl');
    const agentModelId = getVal('agentModelId');
    const agentModelApiKey = getKeyVal('agentModelApiKey');

    const omniModelUrl = getVal('omniModelUrl');
    const omniModelId = getVal('omniModelId');
    const omniModelApiKey = getKeyVal('omniModelApiKey');

    let ttsModelUrl = getVal('ttsModelUrl');
    const ttsModelId = getVal('ttsModelId');
    const ttsModelApiKey = getKeyVal('ttsModelApiKey');
    let ttsVoiceId = getVal('ttsVoiceId');

    // 检查 GPT-SoVITS v3 配置
    // GSV「是否启用」收口到 ttsModelProvider 下拉单一真相：选中 gptsovits 即启用。
    // gptsovitsEnabled 已退役，保存时不再写进 payload——后端 snapshot 直接从
    // ttsModelProvider 派生 GPTSOVITS_ENABLED（见 utils/config_manager.py）。这里的
    // 局部 gptsovitsEnabled 只是本函数内据下拉算出的本地标志，仅供 URL 校验 /
    // ttsModelUrl·ttsVoiceId 赋值 / ttsProvider 复用，不外发。
    const gptsovitsEnabled = (document.getElementById('ttsModelProvider')?.value || '').trim() === 'gptsovits';
    const gptsovitsConfigForSave = getGptSovitsConfigForSave();

    // 启用 GPT-SoVITS 时校验 URL 协议
    if (gptsovitsEnabled && gptsovitsConfigForSave) {
        const url = gptsovitsConfigForSave.url || '';
        if (!/^https?:\/\//.test(url)) {
            showStatus(window.t ? window.t('api.gptsovitsApiUrlRequired') : '请填写正确的 http/https API URL', 'error');
            return;
        }
    }

    if (gptsovitsEnabled && gptsovitsConfigForSave) {
        ttsModelUrl = gptsovitsConfigForSave.url;
        ttsVoiceId = gptsovitsConfigForSave.voiceId;
    }
    // 退役 __gptsovits_disabled__| 占位符：下拉切走 gptsovits 即「未选」，ttsModelUrl/
    // ttsVoiceId 直接用标准字段值，不再把旧 GSV 配置冻进 voice_id（旧前缀仍由
    // loadGptSovitsConfig 读路径兼容解析，只是不再写出）。

    const mcpToken = getVal('mcpTokenInput');

    const hasCoreBookKeyForSave = !!(
        coreApi
        && coreApi !== 'free'
        && Object.prototype.hasOwnProperty.call(allBookKeys, coreApi)
    );
    const coreBookKeyForSave = hasCoreBookKeyForSave ? allBookKeys[coreApi] : '';
    const effectiveCoreApiKeyForSave = (!_coreApiKeyInputDirty && hasCoreBookKeyForSave)
        ? coreBookKeyForSave
        : apiKey;
    // coreApiKey 只看 core 自己：assist=free 与付费 core 组合时，付费 core 仍需要真实 Key，
    // 不能被 free-access 覆盖。
    const apiKeyForSave = coreApi === 'free' ? 'free-access' : effectiveCoreApiKeyForSave;

    // 免费版和启用自定义API时不需要API Key检查
    if (!enableCustomApi && coreApi !== 'free' && !apiKeyForSave) {
        showStatus(window.t ? window.t('api.pleaseEnterApiKeyError') : '请输入API Key', 'error');
        return;
    }

    // Collect model provider selections
    const modelProviders = {};
    MODEL_TYPES.forEach(mt => {
        const sel = document.getElementById(`${mt}ModelProvider`);
        if (sel) {
            modelProviders[`${mt}ModelProvider`] = sel.value;
        }
    });
    const selectedTtsProvider = (modelProviders.ttsModelProvider || '').trim();

    // Build payload — map book keys to config field names via registry.
    // Only include providers present in allBookKeys (skips restricted/hidden ones).
    const bookPayload = {};
    Object.keys(allBookKeys).forEach(pk => {
        const field = (_apiKeyRegistry[pk] || {}).config_field;
        if (field) {
            const value = allBookKeys[pk];
            if (!Object.prototype.hasOwnProperty.call(bookPayload, field) || value || !bookPayload[field]) {
                bookPayload[field] = value;
            }
        }
    });

    const payload = {
        apiKey: apiKeyForSave, coreApi, assistApi,
        ...bookPayload,
        conversationModelUrl, conversationModelId, conversationModelApiKey,
        summaryModelUrl, summaryModelId, summaryModelApiKey,
        gameMainModelUrl, gameMainModelId, gameMainModelApiKey,
        gameSummaryModelUrl, gameSummaryModelId, gameSummaryModelApiKey,
        correctionModelUrl, correctionModelId, correctionModelApiKey,
        emotionModelUrl, emotionModelId, emotionModelApiKey,
        visionModelUrl, visionModelId, visionModelApiKey,
        agentModelUrl, agentModelId, agentModelApiKey,
        omniModelUrl, omniModelId, omniModelApiKey,
        ttsModelUrl, ttsModelId, ttsModelApiKey, ttsVoiceId,
        mcpToken, enableCustomApi,
        useMimoTokenPlan,
        assistApiKeyMimoTokenPlan: mimoTokenPlanKey,
        resolvedProviderUrls: _resolvedProviderUrls,
        ...modelProviders
    };
    if (gptsovitsEnabled) {
        payload.ttsProvider = 'gptsovits';
    } else if (selectedTtsProvider === 'mimo') {
        payload.ttsProvider = selectedTtsProvider;
    } else if (_loadedGptSovitsState !== 'none') {
        payload.ttsProvider = '';
    } else if (selectedTtsProvider) {
        payload.ttsProvider = '';
    }

    const disableTtsEl = document.getElementById('disableTts');
    if (disableTtsEl) {
        payload.disableTts = disableTtsEl.checked;
    }

    // 检查是否已有API Key，如果有则显示警告
    const currentApiKeyDiv = document.getElementById('current-api-key');
    if (currentApiKeyDiv && currentApiKeyDiv.dataset.hasKey === 'true') {
        pendingApiKey = payload;
        showWarningModal();
    } else {
        await saveApiKey(payload);
    }
}
document.getElementById('api-key-form').addEventListener('submit', save_button_down);


async function runConnectivityCheckBeforeSave(params) {
    if (typeof ConnectivityManager === 'undefined' || typeof ConnectivityManager.testAll !== 'function') {
        return null;
    }

    showStatus(window.t ? window.t('connectivity.status.testing', '正在检测API连通性...') : '正在检测API连通性...', 'info', { sticky: true });
    try {
        const summary = await ConnectivityManager.testAll();
        params.resolvedProviderUrls = _resolvedProviderUrls;
        // 后端保存前复用本轮已测 URL，避免重复请求。
        params.connectivityCheckedProviderUrls = summary && summary.resolvedProviderUrls
            ? summary.resolvedProviderUrls
            : {};
        return summary;
    } catch (error) {
        console.warn('[ConnectivityManager] 保存前自动检测失败:', error);
        params.resolvedProviderUrls = _resolvedProviderUrls;
        // 检测失败时仍显式传空对象，让后端自行重新解析。
        params.connectivityCheckedProviderUrls = {};
        return {
            total: 0,
            succeeded: 0,
            failed: 1,
            error: error && error.message ? error.message : String(error)
        };
    }
}


function refreshAutoResolvedModelUrlsForSave(params) {
    if (!params || typeof params !== 'object') return;

    const modelIdSnapshot = {};
    MODEL_TYPES.forEach(modelType => {
        const modelField = `${modelType}ModelId`;
        modelIdSnapshot[modelField] = params[modelField];
    });

    const snapshotModelId = modelType => {
        const modelField = `${modelType}ModelId`;
        return Object.prototype.hasOwnProperty.call(modelIdSnapshot, modelField)
            ? modelIdSnapshot[modelField]
            : undefined;
    };

    const resolveUrl = (modelType, providerMode, visited = new Set()) => {
        // editable_endpoint 的注册表 TTS provider（vLLM-Omni 等）端点完全由用户填写，
        // 不能被 provider profile 的 URL 覆盖；按注册表元数据豁免（缺失时结构信号兜底），
        // 不再单独硬编码 vllm_omni。
        if (!providerMode || providerMode === 'custom') return '';
        const visitKey = `${modelType}:${providerMode}`;
        if (visited.has(visitKey)) return '';
        visited.add(visitKey);
        if (modelType === 'tts') {
            const ttsMeta = getTtsProviderMeta(providerMode);
            if (ttsMeta && ttsMeta.editable_endpoint) return '';
        }

        if (providerMode === 'follow_conversation') {
            return params.conversationModelUrl || resolveUrl('conversation', params.conversationModelProvider, visited);
        }
        if (providerMode === 'follow_summary') {
            return params.summaryModelUrl || resolveUrl('summary', params.summaryModelProvider, visited);
        }

        let providerKey = providerMode;
        let scope = 'assist';
        if (providerMode === 'follow_core') {
            providerKey = params.coreApi || '';
            scope = modelType === 'omni' ? 'core' : 'assist';
        } else if (providerMode === 'follow_assist') {
            providerKey = params.assistApi || '';
            scope = 'assist';
        } else if (modelType === 'omni') {
            scope = 'core';
        }

        if (!providerKey) return '';
        if (scope === 'core') {
            return getProviderCoreUrl(providerKey, _coreApiProviders[providerKey] || {});
        }

        const assistProfile = getProviderInfo(providerKey);
        const useTokenPlan = providerMode === 'follow_assist';
        return getEffectiveAssistUrl(providerKey, assistProfile, { useTokenPlan }) || getProviderCoreUrl(providerKey, assistProfile);
    };

    const resolveModel = (modelType, providerMode, visited = new Set()) => {
        if (!providerMode || providerMode === 'custom') return null;
        const visitKey = `${modelType}:${providerMode}`;
        if (visited.has(visitKey)) return null;
        visited.add(visitKey);
        if (providerMode === 'follow_conversation') {
            const inherited = snapshotModelId('conversation');
            return inherited !== undefined && inherited !== null
                ? inherited
                : resolveModel('conversation', params.conversationModelProvider, visited);
        }
        if (providerMode === 'follow_summary') {
            const inherited = snapshotModelId('summary');
            return inherited !== undefined && inherited !== null
                ? inherited
                : resolveModel('summary', params.summaryModelProvider, visited);
        }

        let providerKey = providerMode;
        if (providerMode === 'follow_core') {
            providerKey = params.coreApi || '';
        } else if (providerMode === 'follow_assist') {
            providerKey = params.assistApi || '';
        }
        if (!providerKey || !isFixedModelProvider(providerKey)) return null;
        return getProviderDefaultModelId(providerKey, modelType) || '';
    };

    MODEL_TYPES.forEach(modelType => {
        // GSV 选中时 tts URL 由 GSV 专属字段提供，跳过自动解析。启用信号收口到
        // ttsModelProvider 下拉（gptsovitsEnabled 已不再外发）。
        if (modelType === 'tts' && (params.ttsModelProvider || '').trim() === 'gptsovits') return;

        const providerField = `${modelType}ModelProvider`;
        const urlField = `${modelType}ModelUrl`;
        const resolvedUrl = resolveUrl(modelType, params[providerField]);
        if (!resolvedUrl) return;

        params[urlField] = resolvedUrl;
        const input = document.getElementById(urlField);
        if (input && input.hasAttribute('readonly')) {
            input.value = resolvedUrl;
        }

        const modelField = `${modelType}ModelId`;
        const resolvedModel = resolveModel(modelType, params[providerField]);
        if (resolvedModel !== null && resolvedModel !== undefined) {
            params[modelField] = resolvedModel;
            const modelInput = document.getElementById(modelField);
            if (modelInput && modelInput.dataset.fixedModelHidden === 'true') {
                modelInput.value = resolvedModel;
            }
        }
    });
}


async function saveApiKey(params) {
    if (_apiSaveInProgress) return;
    const { apiKey, coreApi, assistApi, enableCustomApi } = params;

    // 统一处理免费版 API Key 的保存值。只看 core 自己：
    // assist=free 的 free-access 由后端按辅助服务商 profile 解析，不落在 coreApiKey 上。
    let finalApiKey = apiKey;
    if (coreApi === 'free') {
        finalApiKey = 'free-access';
    }

    // 确保apiKey是有效的字符串
    if (!enableCustomApi && coreApi !== 'free' && (!finalApiKey || typeof finalApiKey !== 'string')) {
        showStatus(window.t ? window.t('api.apiKeyInvalid') : 'API Key无效', 'error');
        return;
    }

    setApiSaveInProgress(true);
    try {
        await runConnectivityCheckBeforeSave(params);
        refreshAutoResolvedModelUrlsForSave(params);

        // Build the request body from params
        // Include empty strings so the backend can clear fields
        const body = {};
        body.coreApiKey = finalApiKey;
        Object.keys(params).forEach(key => {
            if (key === 'apiKey') return; // skip, we use coreApiKey
            const val = params[key];
            if (val !== undefined && val !== null) {
                body[key] = val;
            }
        });
        body.enableCustomApi = params.enableCustomApi ?? false;

        const response = await fetch('/api/config/core_api', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body)
        });

        if (response.ok) {
            const result = await response.json();
            if (result.success) {
                let statusMessage;
                if (result.sessions_ended && result.sessions_ended > 0) {
                    statusMessage = window.t ? window.t('api.saveSuccessWithReset', { count: result.sessions_ended }) : `API Key保存成功！已重置 ${result.sessions_ended} 个活跃对话，对话页面将自动刷新。`;
                } else {
                    statusMessage = window.t ? window.t('api.saveSuccessReload') : 'API Key保存成功！配置已重新加载，新配置将在下次对话时生效。';
                }
                showStatus(statusMessage, 'success');
                setMaskedInput(document.getElementById('apiKeyInput'), '');

                // 清除本地Voice ID记录
                await clearVoiceIds();
                // 通知其他页面API Key已更改
                const targetOrigin = getTargetOrigin();
                if (window.parent !== window) {
                    window.parent.postMessage({
                        type: 'api_key_changed',
                        timestamp: Date.now()
                    }, targetOrigin);
                } else {
                    // 如果是直接打开的页面，广播给所有子窗口
                    const iframes = document.querySelectorAll('iframe');
                    iframes.forEach(iframe => {
                        try {
                            iframe.contentWindow.postMessage({
                                type: 'api_key_changed',
                                timestamp: Date.now()
                            }, targetOrigin);
                        } catch (e) {
                            // 跨域iframe会抛出异常，忽略
                        }
                    });
                }
            } else {
                const errorMsg = result.error || (window.t ? window.t('common.unknownError') : '未知错误');
                showStatus(window.t ? window.t('api.saveFailed', { error: errorMsg }) : '保存失败: ' + errorMsg, 'error');
            }
        } else {
            showStatus(window.t ? window.t('api.saveNetworkError') : '保存失败，请检查网络连接', 'error');
        }

        // 无论成功还是失败，都重新加载当前API Key
        await loadCurrentApiKey();
    } catch (error) {
        showStatus(window.t ? window.t('api.saveError', { error: error.message }) : '保存时出错: ' + error.message, 'error');
        // 即使出错也尝试重新加载当前API Key
        await loadCurrentApiKey();
    } finally {
        setApiSaveInProgress(false);
    }
}

function showWarningModal() {
    document.getElementById('warning-modal').style.display = 'flex';
}

function closeWarningModal() {
    document.getElementById('warning-modal').style.display = 'none';
}

async function confirmApiKeyChange() {
    if (pendingApiKey && typeof pendingApiKey === 'object') {
        const apiKeyToSave = pendingApiKey;
        closeWarningModal();
        pendingApiKey = null;
        await saveApiKey(apiKeyToSave);
    } else {
        showStatus(window.t ? window.t('api.apiKeyInvalidRetry') : 'API Key无效，请重新输入', 'error');
        closeWarningModal();
        pendingApiKey = null;
    }
}

// Helper: 判断一个值是否表示免费版
function isFreeVersionText(value) {
    if (typeof value !== 'string') return false;
    const v = value.trim();
    if (!v) return false;
    if (v === 'free-access') return true;
    const translated = (window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key');
    if (v === translated) return true;
    return false;
}

// 根据核心API选择更新辅助API的提示和建议
function updateAssistApiRecommendation() {
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');

    if (!coreApiSelect || !assistApiSelect) return;

    const selectedCoreApi = coreApiSelect.value;

    // 控制API Key输入框和免费版提示
    const apiKeyInput = document.getElementById('apiKeyInput');
    const freeVersionHint = document.getElementById('freeVersionHint');

    // 辅助 API 与核心 API 解耦：free 与付费可双向组合，free 选项始终可选。
    // 选了 free 的辅助 API 不可填 Key，由 updateAssistApiKeyInputAvailability 锁定，
    // 后端解析时与 core=free 一样使用 free-access。
    assistApiSelect.disabled = false;
    const freeOption = assistApiSelect.querySelector('option[value="free"]');
    if (freeOption) {
        freeOption.disabled = false;
        freeOption.textContent = window.t ? window.t('api.freeVersion') : '免费版';
    }

    if (selectedCoreApi === 'free') {
        // core=free 仅锁核心 API Key，辅助 Key 输入是否可用由辅助服务商自身决定。
        if (apiKeyInput) {
            apiKeyInput.disabled = true;
            apiKeyInput.placeholder = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
            apiKeyInput.required = false;
            apiKeyInput.value = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
        }
        if (freeVersionHint) {
            freeVersionHint.style.display = 'inline';
        }

        // 用户未显式选择 assist 时默认填 'free'，保持原免费版一键到位体验。
        if (!assistApiSelect.value) {
            assistApiSelect.value = 'free';
        }
        // Directly recompute follow_assist slots instead of dispatching a change
        // event, which would re-enter updateAssistApiRecommendation() recursively.
        autoFillAssistApiKey();
        MODEL_TYPES.forEach(mt => {
            const sel = document.getElementById(`${mt}ModelProvider`);
            if (sel && sel.value === 'follow_assist') {
                onCustomModelProviderChange(mt);
            }
        });
    } else {
        if (apiKeyInput) {
            apiKeyInput.disabled = false;
            apiKeyInput.placeholder = window.t ? window.t('api.pleaseEnterApiKey') : '请输入您的API Key';
            apiKeyInput.required = true;
            if (isFreeVersionText(getRealKey(apiKeyInput))) {
                setMaskedInput(apiKeyInput, '');
            }
        }
        if (freeVersionHint) {
            freeVersionHint.style.display = 'none';
        }
    }

    updateAssistApiKeyInputAvailability();

    // Auto-fill core API key from book
    autoFillCoreApiKey();

    syncProviderSelectDropdowns();
}

// 自动填充核心API Key到核心API Key输入框
// force=true: always overwrite (used on actual core provider change)
// force=false (default): skip if user has already typed a non-empty value
function autoFillCoreApiKey(force) {
    const coreApiSelect = document.getElementById('coreApiSelect');
    const apiKeyInput = document.getElementById('apiKeyInput');

    if (!coreApiSelect || !apiKeyInput) return;

    const selectedCoreApi = coreApiSelect.value;

    if (selectedCoreApi === 'free') {
        return;
    }

    // When not forced (e.g. called from updateAssistApiRecommendation),
    // preserve any unsaved user edits in apiKeyInput.
    const currentReal = getRealKey(apiKeyInput);
    if (!force && currentReal !== '' && !isFreeVersionText(currentReal)) {
        return;
    }

    // Always sync from the book for the newly selected provider,
    // so switching providers doesn't leave the old provider's key behind.
    // Use !== null to distinguish "input not present" from "input present but empty":
    // null = restricted/hidden provider (no input), '' = user cleared the key intentionally.
    const bookKey = syncKeyFromBook(selectedCoreApi);
    // When forced (provider switch), clear input if no book key
    if (force && (bookKey === null || bookKey === '')) {
        setMaskedInput(apiKeyInput, '');
        attachMaskBehavior(apiKeyInput);
        _coreApiKeyInputDirty = false;
        return;
    }
    // Non-forced: only fill if book has a value
    if (bookKey !== null && bookKey !== '') {
        setMaskedInput(apiKeyInput, bookKey);
        attachMaskBehavior(apiKeyInput);
        _coreApiKeyInputDirty = false;
    }
}

// Auto-fill assist API key from book
// force=true: always overwrite (used on provider switch, disabling custom API, or init)
// force=false (default): skip if user has already typed a non-empty value
function autoFillAssistApiKey(force) {
    const assistApiSelect = document.getElementById('assistApiSelect');
    const assistApiKeyInput = document.getElementById('assistApiKeyInput');
    if (!assistApiSelect || !assistApiKeyInput) return;

    const selectedAssistApi = assistApiSelect.value;
    if (selectedAssistApi === 'free') {
        updateAssistApiKeyInputAvailability();
        return;
    }
    updateAssistApiKeyInputAvailability();

    if (isMimoTokenPlanActive()) {
        return;
    }

    const bookKey = syncKeyFromBook(selectedAssistApi);
    // When forced (provider switch, disabling custom API, or init), clear input if no book key
    if (force && (bookKey === null || bookKey === '')) {
        setMaskedInput(assistApiKeyInput, '');
        attachMaskBehavior(assistApiKeyInput);
        return;
    }
    // Non-forced: only fill if book has a value
    if (bookKey !== null && bookKey !== '') {
        setMaskedInput(assistApiKeyInput, bookKey);
        attachMaskBehavior(assistApiKeyInput);
    }
}

// Beacon功能 - 页面关闭时发送信号给服务器
let beaconSent = false;

function sendBeacon() {
    if (window.parent !== window) {
        return;
    }

    if (beaconSent) return;
    beaconSent = true;

    try {
        const payload = JSON.stringify({
            timestamp: Date.now(),
            action: 'shutdown'
        });

        const blob = new Blob([payload], { type: 'application/json' });
        const success = navigator.sendBeacon('/api/beacon/shutdown', blob);

        if (!success) {
            console.warn('Beacon发送失败，尝试使用fetch');
            fetch('/api/beacon/shutdown', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: payload,
                keepalive: true
            }).catch(() => { });
        }
    } catch (e) {
        // 忽略异常
    }
}

// 监听页面关闭事件（仅在直接打开时）
if (window.parent === window) {
    window.addEventListener('beforeunload', sendBeacon);
    window.addEventListener('unload', sendBeacon);
}

// Tooltip 动态定位功能
function positionTooltip(iconElement, tooltipElement) {
    const iconRect = iconElement.getBoundingClientRect();
    const tooltipRect = tooltipElement.getBoundingClientRect();

    let left = iconRect.left + iconRect.width / 2 - tooltipRect.width / 2;
    let top = iconRect.top - tooltipRect.height - 10;

    let iconCenter = iconRect.left + iconRect.width / 2;

    if (left < 20) {
        left = 20;
    }

    if (left + tooltipRect.width > window.innerWidth - 20) {
        left = window.innerWidth - tooltipRect.width - 20;
    }

    let arrowLeft = iconCenter - left;
    arrowLeft = Math.max(15, Math.min(arrowLeft, tooltipRect.width - 15));

    if (top < 20) {
        top = iconRect.bottom + 10;
        tooltipElement.setAttribute('data-position', 'bottom');
    } else {
        tooltipElement.setAttribute('data-position', 'top');
    }

    tooltipElement.style.left = left + 'px';
    tooltipElement.style.top = top + 'px';
    tooltipElement.style.setProperty('--arrow-left', arrowLeft + 'px');
}

const MODEL_CONFIG_ROW_PAIRS = Object.freeze({
    conversation: 'summary',
    summary: 'conversation',
    game: 'correction',
    correction: 'game',
    emotion: 'vision',
    vision: 'emotion',
    agent: 'omni',
    omni: 'agent',
});

function finishModelConfigCollapse(content, pairedContent, transitionId) {
    if (content.dataset.collapseTransitionId !== transitionId) return;

    delete content.dataset.collapseTransitionId;
    const expandedPair = pairedContent?.classList.contains('expanded') ? pairedContent : null;
    if (expandedPair) {
        expandedPair.classList.add('is-reflowing');
        expandedPair.getBoundingClientRect();
    }

    content.classList.remove('is-collapsing');
    content.style.removeProperty('max-height');

    if (expandedPair) {
        window.setTimeout(() => {
            expandedPair.classList.remove('is-reflowing');
        }, 260);
    }
}

// 二级折叠功能：切换模型配置的展开/折叠状态
function toggleModelConfig(modelType) {
    const content = document.getElementById(`${modelType}-model-content`);
    if (!content) return;

    const header = content.previousElementSibling;
    if (!header) return;

    const icon = header.querySelector('.toggle-icon');
    if (!icon) return;

    if (content.classList.contains('expanded')) {
        const pairedType = MODEL_CONFIG_ROW_PAIRS[modelType];
        const pairedContent = pairedType
            ? document.getElementById(`${pairedType}-model-content`)
            : null;
        const transitionId = `${Date.now()}-${Math.random()}`;

        content.dataset.collapseTransitionId = transitionId;
        content.classList.add('is-collapsing');
        content.style.maxHeight = `${content.scrollHeight}px`;
        content.getBoundingClientRect();
        content.classList.remove('expanded');
        icon.style.transform = 'rotate(0deg)';
        header.setAttribute('aria-expanded', 'false');
        content.setAttribute('aria-hidden', 'true');

        window.requestAnimationFrame(() => {
            if (content.dataset.collapseTransitionId === transitionId) {
                content.style.maxHeight = '0px';
            }
        });

        const handleTransitionEnd = event => {
            if (event.target !== content || event.propertyName !== 'max-height') return;
            content.removeEventListener('transitionend', handleTransitionEnd);
            finishModelConfigCollapse(content, pairedContent, transitionId);
        };
        content.addEventListener('transitionend', handleTransitionEnd);
        window.setTimeout(() => {
            content.removeEventListener('transitionend', handleTransitionEnd);
            finishModelConfigCollapse(content, pairedContent, transitionId);
        }, 360);
    } else {
        delete content.dataset.collapseTransitionId;
        content.classList.remove('is-collapsing');
        content.style.removeProperty('max-height');
        content.classList.add('expanded');
        icon.style.transform = 'rotate(180deg)';
        header.setAttribute('aria-expanded', 'true');
        content.setAttribute('aria-hidden', 'false');
    }
}

function navigateToCustomModelConfig(modelType) {
    const expansionPath = MODEL_CONFIG_EXPANSION_PATHS[modelType];
    if (!expansionPath) return false;

    const customApiOptions = document.getElementById('custom-api-options');
    const customApiToggleBtn = document.getElementById('custom-api-toggle-btn');
    if (customApiOptions && getComputedStyle(customApiOptions).display === 'none') {
        customApiOptions.style.display = 'block';
        if (customApiToggleBtn) customApiToggleBtn.classList.add('rotated');
    }

    const customApiContainer = document.getElementById('custom-api-container');
    if (customApiContainer && getComputedStyle(customApiContainer).display === 'none') {
        customApiContainer.style.display = 'grid';
    }

    let expandedConfig = false;
    expansionPath.forEach(configType => {
        const content = document.getElementById(`${configType}-model-content`);
        if (content && !content.classList.contains('expanded')) {
            toggleModelConfig(configType);
            expandedConfig = true;
        }
    });

    const targetType = expansionPath[expansionPath.length - 1];
    const targetContent = document.getElementById(`${targetType}-model-content`);
    const targetHeader = targetContent?.previousElementSibling;
    if (!targetHeader) return false;
    const targetPanel = targetHeader.closest(
        '.nested-model-config-container, .model-config-container'
    ) || targetHeader;

    const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    window.setTimeout(() => {
        targetPanel.scrollIntoView({
            behavior: prefersReducedMotion ? 'auto' : 'smooth',
            block: 'center'
        });
    }, expandedConfig ? 320 : 0);

    return true;
}

// 页面加载完成后初始化折叠状态
document.addEventListener('DOMContentLoaded', function () {
    // 初始化所有模型配置为折叠状态
    const modelTypes = ["conversation", 'summary', 'game', 'game-main', 'game-summary', 'correction', 'emotion', 'vision', 'agent', 'omni', 'tts', 'gptsovits'];
    modelTypes.forEach(modelType => {
        const content = document.getElementById(`${modelType}-model-content`);
        if (content) {
            const header = content.previousElementSibling;
            const icon = header?.querySelector('.toggle-icon');

            if (content && icon) {
                content.classList.remove('expanded');
                icon.style.transform = 'rotate(0deg)';
                if (header) header.setAttribute('aria-expanded', 'false');
                content.setAttribute('aria-hidden', 'true');
            }
        }
    });

    // 根据自定义API启用状态设置初始折叠状态
    const enableCustomApi = document.getElementById('enableCustomApi');
    if (enableCustomApi) {
        toggleCustomApi(true);
    }
});


// 初始化所有tooltip
function initTooltips() {
    const tooltipContainers = document.querySelectorAll('.tooltip-container');

    tooltipContainers.forEach(container => {
        const icon = container.querySelector('.tooltip-icon');
        const tooltip = container.querySelector('.tooltip-content');

        if (!icon || !tooltip) return;

        icon.addEventListener('mouseenter', function () {
            tooltip.style.visibility = 'visible';
            tooltip.style.opacity = '0';

            requestAnimationFrame(() => {
                positionTooltip(icon, tooltip);
                tooltip.style.opacity = '1';
            });
        });

        icon.addEventListener('mouseleave', function () {
            tooltip.style.opacity = '0';
            setTimeout(() => {
                if (tooltip.style.opacity === '0') {
                    tooltip.style.visibility = 'hidden';
                }
            }, 300);
        });
    });

    let resizeTimeout;
    window.addEventListener('resize', function () {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            const visibleTooltips = document.querySelectorAll('.tooltip-content[style*="visibility: visible"]');
            visibleTooltips.forEach(tooltip => {
                const container = tooltip.closest('.tooltip-container');
                if (container) {
                    const icon = container.querySelector('.tooltip-icon');
                    if (icon) {
                        positionTooltip(icon, tooltip);
                    }
                }
            });
        }, 100);
    });
}

// ==================== 连通性测试：指示灯 UI 组件 ====================

/**
 * 指示灯状态常量
 */
const LightStatus = {
    CONNECTED: 'connected',
    FAILED: 'failed',
    UNTESTED: 'untested',
    NOT_CONFIGURED: 'not_configured',
    TESTING: 'testing',
};

function getCustomModelDisplayLabel(modelType) {
    const labelMap = {
        conversation: ['api.conversationModelConfig', '文本对话模型配置'],
        summary: ['api.summaryModelConfig', '摘要模型配置'],
        gameMain: ['api.gameMainModelConfig', '小游戏主模型配置'],
        gameSummary: ['api.gameSummaryModelConfig', '小游戏摘要模型配置'],
        correction: ['api.correctionModelConfig', '纠错模型配置'],
        emotion: ['api.emotionModelConfig', '情感模型配置'],
        vision: ['api.visionModelConfig', '视觉模型配置'],
        agent: ['api.agentApiConfigTitle', 'Agent API 配置（需支持视觉功能）'],
        omni: ['api.realtimeModelConfig', '实时模型配置（Local模式）'],
        tts: ['api.ttsModelConfig', 'TTS模型配置（双工流式）'],
    };
    const [key, fallback] = labelMap[modelType] || ['', modelType];
    if (!key || !window.t) return fallback;
    const translated = window.t(key, fallback);
    return translated && translated !== key ? translated : fallback;
}

function buildConnectivityLightTitle(lightElement, statusLabel) {
    const tooltipLabel = lightElement.dataset.tooltipLabel || '';
    if (!tooltipLabel) return statusLabel;

    const modelType = lightElement.dataset.modelType || '';
    const modelIdInput = modelType ? document.getElementById(`${modelType}ModelId`) : null;
    const modelId = modelIdInput ? modelIdInput.value.trim() : '';

    const firstLine = `${tooltipLabel} ${statusLabel}`;
    if (modelId) {
        const modelIdLabel = window.t ? window.t('api.modelId', '模型ID') : '模型ID';
        return `${firstLine}\n${modelIdLabel}: ${modelId}`;
    }

    return firstLine;
}

/**
 * 创建指示灯 DOM 元素，插入到 inputElement 前方。
 * @param {HTMLElement} inputElement - 关联的输入框
 * @param {object} context - 上下文信息（用于后续 ConnectivityManager 注册）
 * @returns {HTMLElement} 创建的指示灯 <span> 元素
 */
function createIndicatorLight(inputElement, context) {
    const light = document.createElement('span');
    light.className = 'connectivity-light';
    light.dataset.status = LightStatus.NOT_CONFIGURED;
    light.title = window.t ? window.t('connectivity.status.not_configured') : '未配置';

    // 存储上下文信息，供后续 ConnectivityManager 使用
    if (context) {
        light.dataset.context = JSON.stringify(context);
        if (context.type === 'custom' && context.modelType) {
            light.dataset.modelType = context.modelType;
            light.dataset.tooltipLabel = getCustomModelDisplayLabel(context.modelType);
        }
    }

    // 将灯和 input 包在一个水平 flex 容器中，确保同行对齐
    if (inputElement && inputElement.parentNode) {
        const fieldRow = inputElement.closest('.field-row');
        const wrapper = document.createElement('div');
        wrapper.className = 'connectivity-input-row';
        inputElement.parentNode.insertBefore(wrapper, inputElement);
        wrapper.appendChild(light);
        wrapper.appendChild(inputElement);

        // 服务商配置可能先于连通性组件初始化。此时“前往管理簿”已经被
        // 插入 field-row，需要一并移入横向容器，避免它留在输入框下一行。
        const keyBookShortcut = fieldRow
            ? Array.from(fieldRow.querySelectorAll('.key-book-shortcut')).find(
                link => !link.dataset.sourceInputId || link.dataset.sourceInputId === inputElement.id
            )
            : null;
        if (keyBookShortcut) {
            wrapper.appendChild(keyBookShortcut);
        }
    }

    return light;
}

/**
 * 更新指示灯的状态（颜色、tooltip、动画）。
 * @param {HTMLElement} lightElement - 指示灯 DOM 元素
 * @param {string} status - 状态值：connected | failed | untested | not_configured | testing
 */
function updateLightStatus(lightElement, status) {
    if (!lightElement) return;

    lightElement.dataset.status = status;

    // 通过 i18n 获取 tooltip 文本
    const tooltipKey = `connectivity.status.${status}`;
    const fallbackMap = {
        [LightStatus.CONNECTED]: '已连通',
        [LightStatus.FAILED]: '连接失败',
        [LightStatus.UNTESTED]: '未测试',
        [LightStatus.NOT_CONFIGURED]: '未配置',
        [LightStatus.TESTING]: '测试中...',
    };
    const fallback = fallbackMap[status] || status;
    const statusLabel = window.t ? window.t(tooltipKey, fallback) : fallback;
    lightElement.title = buildConnectivityLightTitle(lightElement, statusLabel);
    if (lightElement.classList.contains('connectivity-summary-light')) {
        lightElement.setAttribute('aria-label', lightElement.title);
    }
}

// ==================== 连通性测试：错误信息展示 UI 组件 ====================

/**
 * 创建错误信息展示 DOM 元素，插入到 inputElement 后方。
 * @param {HTMLElement} inputElement - 关联的输入框
 * @returns {HTMLElement} 创建的错误信息 <span> 元素
 */
function createErrorMessageDisplay(inputElement) {
    const errorSpan = document.createElement('span');
    errorSpan.className = 'connectivity-error-msg';
    errorSpan.style.display = 'none';

    // 插入到 inputElement 后方
    if (inputElement && inputElement.parentNode) {
        // insertBefore(newNode, referenceNode.nextSibling) 等效于 insertAfter
        inputElement.parentNode.insertBefore(errorSpan, inputElement.nextSibling);
    }

    return errorSpan;
}

/**
 * 更新错误信息展示内容。
 * @param {HTMLElement} errorDisplayElement - 错误信息 DOM 元素
 * @param {string|null} errorCode - 错误代码（null 表示成功/重置，隐藏错误信息）
 * @param {string} errorDetail - 后端返回的原始错误描述（作为 i18n fallback）
 */
function updateErrorMessage(errorDisplayElement, errorCode, errorDetail) {
    if (!errorDisplayElement) return;

    // 成功或重置：隐藏错误信息
    if (!errorCode) {
        errorDisplayElement.textContent = '';
        errorDisplayElement.style.display = 'none';
        return;
    }

    // 尝试从 i18n 获取本地化错误信息
    const i18nKey = `connectivity.error.${errorCode}`;
    let message = '';
    if (window.t) {
        const translated = window.t(i18nKey);
        // i18next 在 key 不存在时返回 key 本身，需要判断
        message = (translated && translated !== i18nKey) ? translated : '';
    }

    // i18n key 不存在时回退到 errorDetail
    if (!message) {
        message = errorDetail || errorCode;
    }

    errorDisplayElement.textContent = message;
    errorDisplayElement.style.display = 'inline';
}

function buildConnectivityCacheId(scope, providerKey, key, url) {
    // 内置 provider 的 cacheId 故意不带 url：URL 会在第一次 testKey 成功后从
    // 候选值翻成 rememberResolvedProviderUrl 写入的 resolved_url，
    // 把 url 带进 cacheId 会让指示灯/错误面板的注册 key 凭空换一份，
    // 导致后续刷新打不到原 DOM，表现成检测灯卡住或丢状态。
    // 同一组 scope+providerKey 在 key 也是空（free 端点专用）时退到空字符串占位，
    // 也不要回退到 url，保持 cacheId 在 URL 翻新时稳定。
    if (scope && providerKey) {
        return `${scope}|${providerKey}|${key || ''}`;
    }
    return key || url || '';
}

function buildCustomConnectivityCacheId(providerType, subType, url, key, model, voiceId = '') {
    return [
        'custom',
        providerType || 'openai_compatible',
        subType || '',
        url || '',
        key || '',
        model || '',
        voiceId || '',
    ].join('|');
}

// ==================== 连通性测试：ConnectivityManager ====================

const ConnectivityManager = {
    /** Key → 测试状态映射 */
    keyStatusMap: {},

    /** Key → 错误信息映射 { error_code, error } */
    keyErrorMap: {},

    /** Key → 指示灯 DOM 元素列表映射 */
    keyLightsMap: {},

    /** Key → 错误信息 DOM 元素列表映射 */
    keyErrorDisplayMap: {},

    /** 用于取消正在进行的测试请求 */
    _abortControllers: {},

    /**
     * 解析某个 API 配置的实际 Key。
     * 处理 follow_core、follow_assist、Key Book 同步、getRealKey 等各种 Key 来源。
     *
     * @param {object} context - 上下文对象
     *   context.type: 'core' | 'assist' | 'custom'
     *   context.modelType: (仅 custom) 模型类型，如 'conversation'
     * @returns {{ key: string, url: string, providerType: string }} 解析结果
     */
    resolveEffectiveKey(context) {
        const result = { key: '', url: '', providerType: 'openai_compatible', subType: '', providerKey: '', providerScope: '', cacheId: '' };

        if (!context || !context.type) return result;

        const coreApiSelect = document.getElementById('coreApiSelect');
        const assistApiSelect = document.getElementById('assistApiSelect');
        const apiKeyInput = document.getElementById('apiKeyInput');
        const assistApiKeyInput = document.getElementById('assistApiKeyInput');

        if (context.type === 'core') {
            const coreProvider = coreApiSelect ? coreApiSelect.value : '';
            result.providerKey = coreProvider;
            result.providerScope = 'core';
            if (coreProvider === 'free') {
                // 免费版：使用预配置端点和 Key
                const coreProfile = _coreApiProviders['free'] || {};
                result.url = getProviderCoreUrl('free', coreProfile);
                result.key = 'free-access';
                result.providerType = 'websocket';
            } else {
                const coreProfile = _coreApiProviders[coreProvider] || {};
                result.url = getProviderCoreUrl(coreProvider, coreProfile);
                const inputKey = getRealKey(apiKeyInput);
                const bookKey = syncKeyFromBook(coreProvider);
                if (_coreApiKeyInputDirty && !isFreeVersionText(inputKey)) {
                    result.key = inputKey;
                } else if (bookKey !== null && bookKey !== '') {
                    result.key = bookKey;
                } else {
                    result.key = inputKey;
                }
                result.providerType = 'websocket';
            }
            result.cacheId = buildConnectivityCacheId(result.providerScope, result.providerKey, result.key, result.url);
            return result;
        }

        if (context.type === 'assist') {
            const assistProvider = assistApiSelect ? assistApiSelect.value : '';
            result.providerKey = assistProvider;
            result.providerScope = 'assist';
            if (assistProvider === 'free') {
                const assistProfile = _assistApiProviders['free'] || {};
                result.url = getProviderOpenrouterUrl('free', assistProfile);
                result.key = 'free-access';
                result.providerType = getProviderType(assistProvider);
            } else {
                const assistProfile = _assistApiProviders[assistProvider] || {};
                result.url = getEffectiveAssistUrl(assistProvider, assistProfile);
                // 优先从输入框读取，其次从 Key Book
                const inputKey = getRealKey(assistApiKeyInput);
                if (assistProvider === 'mimo' && isMimoTokenPlanActive()) {
                    result.key = getEffectiveAssistKey(assistProvider);
                } else if (inputKey && !isFreeVersionText(inputKey)) {
                    result.key = inputKey;
                } else {
                    const bookKey = syncKeyFromBook(assistProvider);
                    result.key = (bookKey !== null) ? bookKey : '';
                }
                result.providerType = getProviderType(assistProvider);
            }
            const cacheProviderKey = getEffectiveAssistProviderKey(result.providerKey);
            result.cacheId = buildConnectivityCacheId(result.providerScope, cacheProviderKey, result.key, result.url);
            return result;
        }

        if (context.type === 'custom' && context.modelType) {
            const mt = context.modelType;
            const providerSel = document.getElementById(`${mt}ModelProvider`);
            const urlInput = document.getElementById(`${mt}ModelUrl`);
            const keyInput = document.getElementById(`${mt}ModelApiKey`);
            const modelIdInput = document.getElementById(`${mt}ModelId`);

            if (!providerSel) return result;

            const provider = providerSel.value;
            result.url = urlInput ? urlInput.value.trim() : '';

            if (provider === 'follow_conversation' || provider === 'follow_summary') {
                const sourceType = provider === 'follow_conversation' ? 'conversation' : 'summary';
                const sourceResult = this.resolveEffectiveKey({ type: 'custom', modelType: sourceType });
                Object.assign(result, sourceResult);
                if (modelIdInput) {
                    const sourceModel = document.getElementById(`${sourceType}ModelId`);
                    result.model = sourceModel ? sourceModel.value.trim() : '';
                }
            } else if (provider === 'follow_core') {
                // 跟随核心 API
                const coreResult = this.resolveEffectiveKey({ type: 'core' });
                // omni 模型使用 core_url (WebSocket)，其他模型使用 openrouter_url
                if (mt === 'omni') {
                    result.key = coreResult.key;
                    result.url = coreResult.url;
                    result.providerType = 'websocket';
                    result.providerKey = coreResult.providerKey;
                    result.providerScope = 'core';
                } else {
                    // 非 omni 跟随核心时，使用核心服务商的 assist 配置
                    const coreProvider = coreApiSelect ? coreApiSelect.value : '';
                    const pInfo = _assistApiProviders[coreProvider] || _coreApiProviders[coreProvider] || {};
                    result.url = getProviderOpenrouterUrl(coreProvider, pInfo) || getProviderCoreUrl(coreProvider, pInfo);
                    result.key = coreResult.key;
                    result.providerType = getProviderType(coreProvider);
                    result.providerKey = coreProvider;
                    result.providerScope = 'assist';
                }
            } else if (provider === 'follow_assist') {
                // 跟随辅助 API
                const assistResult = this.resolveEffectiveKey({ type: 'assist' });
                result.key = assistResult.key;
                result.url = assistResult.url;
                result.providerType = assistResult.providerType;
                result.providerKey = assistResult.providerKey;
                result.providerScope = assistResult.providerScope;
            } else if (provider === 'custom') {
                // 自定义：直接从输入框读取，不设 providerKey（走自定义模式）
                result.key = keyInput ? getRealKey(keyInput) : '';
                result.providerType = (mt === 'omni') ? 'websocket' : 'openai_compatible';
                result.model = getResolvedCustomModelId(mt, provider);
            } else if (mt === 'tts' && getTtsProviderMeta(provider) && getTtsProviderMeta(provider).editable_endpoint) {
                // 端点可编辑的 TTS provider（如 vLLM-Omni）：走 Mode 2（custom 路径），
                // 不设 providerKey/providerScope → 后端用用户输入的 URL，绝不退化成「指定
                // 服务商」按 OpenAI-compatible profile 保存。具体探测协议由注册表元数据决定；
                // 注册表缺失（getTtsProviderMeta 结构兜底，probe_kind='none'）时仅做 HTTP 风格
                // custom 探测，探测降级但不会被错存成内置服务商。
                const _spProbe = getTtsProviderMeta(provider);
                if (_spProbe.probe_kind === 'ws_handshake') {
                // ws 握手探测：复用后端 _test_websocket。把 base_url 规整成 worker 实际连接的
                // ws endpoint（后缀 meta.probe_ws_path），并带 meta.probe_sub_type 让后端分流到
                // 对应的握手探测，避免发 session.update 触发 vLLM 主动断连导致连通性误判。
                // 协议细节（ws 后缀 / sub_type）由 tts_provider_registry 数据驱动，不再硬编码。
                const wsPath = _spProbe.probe_ws_path || '';
                const rawUrl = (urlInput ? urlInput.value.trim() : '').replace(/\/+$/, '');
                let wsEndpoint = '';
                if (rawUrl) {
                    let wsUrl;
                    if (rawUrl.startsWith('https://')) {
                        wsUrl = 'wss://' + rawUrl.slice('https://'.length);
                    } else if (rawUrl.startsWith('http://')) {
                        wsUrl = 'ws://' + rawUrl.slice('http://'.length);
                    } else if (rawUrl.startsWith('ws://') || rawUrl.startsWith('wss://')) {
                        wsUrl = rawUrl;
                    } else {
                        wsUrl = 'ws://' + rawUrl;
                    }
                    try {
                        // 用 URL 构造器解析（注意 ws:// 在浏览器里合法）
                        const u = new URL(wsUrl);
                        let basePath = (u.pathname || '').replace(/\/+$/, '');
                        if (basePath === '' || basePath === '/') {
                            basePath = '/v1';
                        }
                        // URL 规整幂等：若 path 已是完整 endpoint 则不重复拼接，与后端
                        // worker 的 URL 拼接保持一致，避免探测到重复后缀。
                        if (!wsPath || basePath.endsWith(wsPath)) {
                            u.pathname = basePath;
                        } else {
                            u.pathname = basePath + wsPath;
                        }
                        wsEndpoint = u.toString();
                    } catch (e) {
                        // URL 解析失败：退化为直接字符串拼接，同样做幂等检查
                        const stripped = wsUrl.replace(/\/+$/, '');
                        wsEndpoint = (!wsPath || stripped.endsWith(wsPath))
                            ? stripped
                            : stripped + wsPath;
                    }
                }
                result.url = wsEndpoint;
                result.providerType = 'websocket';
                result.subType = _spProbe.probe_sub_type || '';
                result.key = keyInput ? getRealKey(keyInput) : '';
                result.model = getResolvedCustomModelId(mt, provider);
                } else if (_spProbe.probe_kind === 'http_tts') {
                    result.url = urlInput ? urlInput.value.trim() : '';
                    result.providerType = 'tts';
                    result.subType = _spProbe.probe_sub_type || provider;
                    result.key = keyInput ? getRealKey(keyInput) : '';
                    result.model = getResolvedCustomModelId(mt, provider);
                    result.voiceId = document.getElementById('ttsVoiceId')?.value?.trim() || '';
                } else {
                    // 非 ws 的可编辑端点（结构兜底 / 未来 http 探测的 provider）：当 custom
                    // 处理——用户填的 URL/Key/Model，不绑定内置 provider profile。
                    result.url = urlInput ? urlInput.value.trim() : '';
                    result.providerType = getProviderType(provider);
                    result.key = keyInput ? getRealKey(keyInput) : '';
                    result.model = getResolvedCustomModelId(mt, provider);
                }
            } else {
                // 指定服务商：从 Key Book 读取
                result.key = getEffectiveAssistKey(provider, null, { useTokenPlan: false });
                if (mt === 'omni') {
                    const coreProfile = _coreApiProviders[provider] || {};
                    result.url = getProviderCoreUrl(provider, coreProfile);
                    result.providerType = 'websocket';
                    result.providerKey = provider;
                    result.providerScope = 'core';
                } else {
                    const pInfo = getProviderInfo(provider);
                    result.url = getEffectiveAssistUrl(provider, pInfo, { useTokenPlan: false }) || getProviderCoreUrl(provider, pInfo);
                    result.providerType = getProviderType(provider);
                    result.providerKey = provider;
                    result.providerScope = 'assist';
                }
            }

            if (result.key || result.url) {
                if (result.providerKey && result.providerScope) {
                    result.cacheId = buildConnectivityCacheId(result.providerScope, result.providerKey, result.key, result.url);
                } else {
                    result.cacheId = buildCustomConnectivityCacheId(
                        result.providerType,
                        result.subType,
                        result.url,
                        result.key,
                        result.model,
                        result.voiceId || ''
                    );
                }
            }
            return result;
        }

        result.cacheId = result.key || result.url;
        return result;
    },

    /**
     * 注册一个指示灯到某个 Key
     */
    registerLight(key, lightElement) {
        if (!key || !lightElement) return;
        if (!this.keyLightsMap[key]) {
            this.keyLightsMap[key] = [];
        }
        if (!this.keyLightsMap[key].includes(lightElement)) {
            this.keyLightsMap[key].push(lightElement);
        }
    },

    /**
     * 取消注册指示灯
     */
    unregisterLight(key, lightElement) {
        if (!key || !lightElement || !this.keyLightsMap[key]) return;
        const idx = this.keyLightsMap[key].indexOf(lightElement);
        if (idx !== -1) {
            this.keyLightsMap[key].splice(idx, 1);
        }
        if (this.keyLightsMap[key].length === 0) {
            delete this.keyLightsMap[key];
        }
    },

    /**
     * 注册一个错误信息展示元素到某个 Key
     */
    registerErrorDisplay(key, errorElement) {
        if (!key || !errorElement) return;
        if (!this.keyErrorDisplayMap[key]) {
            this.keyErrorDisplayMap[key] = [];
        }
        if (!this.keyErrorDisplayMap[key].includes(errorElement)) {
            this.keyErrorDisplayMap[key].push(errorElement);
        }
    },

    /**
     * 测试单个 Key 的连通性。
     * 调用后端 /api/config/test_connectivity 端点，前端 15 秒超时。
     *
     * @param {Object} params - 测试参数
     * @param {string} [params.provider_key] - 内置供应商 key（如 "qwen"、"openai"）
     * @param {string} [params.provider_scope] - "core" 或 "assist"
     * @param {string} [params.url] - 自定义 API 的 URL
     * @param {string} [params.api_key] - API Key
     * @param {string} [params.model] - 自定义 API 的模型名
     * @param {string} [params.provider_type] - 'openai_compatible' 或 'websocket'
     * @param {boolean} [params.is_free] - 是否免费版
     * @returns {Promise<{success: boolean, error?: string, error_code?: string}>}
     */
    async testKey(params) {
        const { provider_key, provider_scope, url, api_key: apiKey, model, voice_id: voiceId, provider_type: providerType, sub_type: subType, is_free: isFree, cache_id: cacheId } = params;
        console.log('[ConnectivityManager] testKey called:', {
            provider_key: provider_key || '(custom)',
            provider_scope: provider_scope || '(none)',
            hasUrl: !!url,
            hasKey: !!apiKey,
            model: model || '(default)',
        });
        // 取消同一 cacheId 的前一次未完成请求（使用 scoped cacheId 避免不同上下文互相干扰）
        if (cacheId && this._abortControllers[cacheId]) {
            this._abortControllers[cacheId].cancelledByNewerTest = true;
            this._abortControllers[cacheId].controller.abort();
        }

        const controller = new AbortController();
        const controllerState = { controller, cancelledByNewerTest: false };
        if (cacheId) {
            this._abortControllers[cacheId] = controllerState;
        }

        // 前端 15 秒超时
        let timedOut = false;
        const timeoutId = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, 15000);

        try {
            const cleanupRequest = () => {
                clearTimeout(timeoutId);
                // Only delete if map still points to this controller (avoid race with newer request)
                if (cacheId && this._abortControllers[cacheId] === controllerState) {
                    delete this._abortControllers[cacheId];
                }
            };
            const buildBody = (overrideUrl = '') => {
                const body = { api_key: apiKey || '' };
                if (provider_key && provider_scope) {
                    // Built-in provider mode
                    body.provider_key = provider_key;
                    body.provider_scope = provider_scope;
                    if (provider_scope === 'assist' && provider_key === 'mimo' && isMimoTokenPlanUrl(overrideUrl)) {
                        body.url = overrideUrl;
                    }
                } else {
                    // Custom API mode
                    body.url = url || '';
                    body.model = model || '';
                    body.provider_type = providerType || 'openai_compatible';
                    // 修复 PR #1764 review 第六轮：vllm_omni TTS 透传 sub_type，
                    // 让后端走 _test_vllm_omni_ws_handshake 而非 _test_websocket。
                    if (subType) {
                        body.sub_type = subType;
                    }
                    if (voiceId) {
                        body.voice_id = voiceId;
                    }
                    body.is_free = !!isFree;
                }
                return body;
            };
            const sendRequest = async (overrideUrl = '') => {
                const response = await fetch('/api/config/test_connectivity', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(buildBody(overrideUrl)),
                    signal: controller.signal
                });

                if (!response.ok) {
                    return {
                        success: false,
                        error: `HTTP ${response.status}`,
                        error_code: 'backend_unavailable',
                        resolved_url: overrideUrl || null
                    };
                }

                const data = await response.json();
                return {
                    success: !!data.success,
                    error: data.error || null,
                    error_code: data.error_code || null,
                    resolved_url: data.resolved_url || (data.success ? overrideUrl : null) || null
                };
            };
            const shouldProbeMimoTokenPlan = provider_scope === 'assist' && provider_key === 'mimo' && isMimoTokenPlanUrl(url);
            if (shouldProbeMimoTokenPlan) {
                const seenUrls = new Set();
                const candidates = [url, ...getMimoTokenPlanUrlCandidates()]
                    .map(item => String(item || '').trim())
                    .filter(item => {
                        if (!item || seenUrls.has(item)) return false;
                        seenUrls.add(item);
                        return true;
                    });
                let lastResult = null;
                for (const candidateUrl of candidates) {
                    const result = await sendRequest(candidateUrl);
                    lastResult = result;
                    if (result.success) {
                        rememberResolvedProviderUrl(provider_scope, MIMO_TOKEN_PLAN_PROVIDER_KEY, result.resolved_url || candidateUrl);
                        cleanupRequest();
                        return result;
                    }
                }
                cleanupRequest();
                return lastResult || {
                    success: false,
                    error: 'No MiMo Token Plan endpoint configured',
                    error_code: 'provider_url_missing',
                    resolved_url: null
                };
            }

            const result = await sendRequest(url);
            cleanupRequest();
            if (result.success && result.resolved_url && provider_key && provider_scope) {
                rememberResolvedProviderUrl(provider_scope, provider_key, result.resolved_url);
            }
            return result;
        } catch (err) {
            clearTimeout(timeoutId);
            if (cacheId && this._abortControllers[cacheId] === controllerState) {
                delete this._abortControllers[cacheId];
            }

            if (err.name === 'AbortError') {
                if (controllerState.cancelledByNewerTest) {
                    return {
                        success: false,
                        cancelled: true,
                        error: null,
                        error_code: null
                    };
                }
                return {
                    success: false,
                    error: timedOut ? 'Request timed out' : 'Request was cancelled',
                    error_code: 'timeout'
                };
            }
            return {
                success: false,
                error: err.message || 'Unknown error',
                error_code: 'backend_unavailable'
            };
        }
    },

    /**
     * 批量测试所有已配置的 Key（去重，相同 Key 只测试一次）。
     */
    async testAll() {
        // 收集所有需要测试的配置（以 cacheId 去重：有 key 用 key，无 key 用 URL）
        const keyConfigs = {}; // cacheId → { provider_key, provider_scope, url, api_key, model, provider_type, is_free }

        // 核心 API
        const coreSelect = document.getElementById('coreApiSelect');
        const coreIsFree = coreSelect && coreSelect.value === 'free';
        const coreResult = this.resolveEffectiveKey({ type: 'core' });
        const coreCacheId = coreResult.cacheId;
        console.log('[ConnectivityManager] testAll - core resolved:', { hasUrl: !!coreResult.url, hasKey: !!coreResult.key, providerType: coreResult.providerType });
        if (coreCacheId && !(coreResult.key && coreResult.key !== 'free-access' && isFreeVersionText(coreResult.key))) {
            if (!keyConfigs[coreCacheId]) {
                keyConfigs[coreCacheId] = {
                    provider_key: coreResult.providerKey, provider_scope: coreResult.providerScope,
                    url: coreResult.url, api_key: coreResult.key || '', provider_type: coreResult.providerType, is_free: coreIsFree
                };
            }
        }

        // 辅助 API
        const assistSelect = document.getElementById('assistApiSelect');
        const assistIsFree = assistSelect && assistSelect.value === 'free';
        const assistResult = this.resolveEffectiveKey({ type: 'assist' });
        const assistCacheId = assistResult.cacheId;
        if (assistCacheId && !keyConfigs[assistCacheId]) {
            keyConfigs[assistCacheId] = {
                provider_key: assistResult.providerKey, provider_scope: assistResult.providerScope,
                url: assistResult.url, api_key: assistResult.key || '', provider_type: assistResult.providerType, is_free: assistIsFree
            };
        }

        // 自定义 API（如果启用）
        const enableCustomApi = document.getElementById('enableCustomApi');
        if (enableCustomApi && enableCustomApi.checked) {
            CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
                const customResult = this.resolveEffectiveKey({ type: 'custom', modelType: mt });
                const customCacheId = customResult.cacheId;
                if (customCacheId && !keyConfigs[customCacheId]) {
                    const providerSel = document.getElementById(`${mt}ModelProvider`);
                    const provider = providerSel ? providerSel.value : '';
                    let isFree = false;
                    if (provider === 'follow_core' && coreSelect && coreSelect.value === 'free') isFree = true;
                    else if (provider === 'follow_assist' && assistSelect && assistSelect.value === 'free') isFree = true;
                    // For custom provider, also pass model from the input
                    const modelInput = document.getElementById(`${mt}ModelId`);
                    const model = modelInput ? modelInput.value.trim() : '';
                    keyConfigs[customCacheId] = {
                        provider_key: customResult.providerKey, provider_scope: customResult.providerScope,
                        url: customResult.url, api_key: customResult.key || '', model: model,
                        voice_id: customResult.voiceId || '',
                        provider_type: customResult.providerType, sub_type: customResult.subType || '', is_free: isFree
                    };
                }
            });
        }

        // 将所有待测试的 Key 设为 testing 状态
        console.log('[ConnectivityManager] testAll - keyConfigs to test:', Object.keys(keyConfigs).length);
        Object.keys(keyConfigs).forEach(key => {
            this.keyStatusMap[key] = LightStatus.TESTING;
            this.keyErrorMap[key] = null;
            this.syncLightsForKey(key);
            this.syncErrorDisplaysForKey(key);
        });

        const summary = {
            total: Object.keys(keyConfigs).length,
            succeeded: 0,
            failed: 0,
            resolvedProviderUrls: {},
            results: {}
        };

        // 并发测试所有唯一配置
        const testPromises = Object.entries(keyConfigs).map(async ([cacheId, config]) => {
            const result = await this.testKey({ ...config, cache_id: cacheId });
            if (result.cancelled) {
                return {
                    cacheId,
                    cancelled: true,
                    success: false,
                    error: '',
                    error_code: '',
                    provider_key: config.provider_key || '',
                    provider_scope: config.provider_scope || '',
                    resolved_url: ''
                };
            }
            if (result.success) {
                this.keyStatusMap[cacheId] = LightStatus.CONNECTED;
                this.keyErrorMap[cacheId] = null;
            } else {
                this.keyStatusMap[cacheId] = LightStatus.FAILED;
                this.keyErrorMap[cacheId] = {
                    error_code: result.error_code || 'unknown',
                    error: result.error || ''
                };
            }
            this.syncLightsForKey(cacheId);
            this.syncErrorDisplaysForKey(cacheId);
            return {
                cacheId,
                success: !!result.success,
                error: result.error || '',
                error_code: result.error_code || '',
                provider_key: config.provider_key || '',
                provider_scope: config.provider_scope || '',
                resolved_url: result.resolved_url || ''
            };
        });

        const settledResults = await Promise.allSettled(testPromises);
        settledResults.forEach(item => {
            if (item.status === 'fulfilled') {
                const value = item.value;
                summary.results[value.cacheId] = value;
                if (value.cancelled) {
                    return;
                } else if (value.success) {
                    summary.succeeded += 1;
                    if (value.provider_key && value.provider_scope && value.resolved_url) {
                        summary.resolvedProviderUrls[`${value.provider_scope}:${value.provider_key}`] = value.resolved_url;
                    }
                } else {
                    summary.failed += 1;
                }
            } else {
                summary.failed += 1;
            }
        });
        return summary;
    },

    /**
     * 更新所有共享同一 Key 的指示灯状态。
     */
    syncLightsForKey(key) {
        if (!key) return;
        const status = this.keyStatusMap[key] || LightStatus.NOT_CONFIGURED;
        const lights = this.keyLightsMap[key] || [];
        console.log(`[ConnectivityManager] syncLightsForKey: status=${status}, lights=${lights.length}`);
        lights.forEach(light => {
            updateLightStatus(light, status);
        });
    },

    /**
     * 更新所有共享同一 Key 的错误信息展示。
     */
    syncErrorDisplaysForKey(key) {
        if (!key) return;
        const errorInfo = this.keyErrorMap[key];
        const displays = this.keyErrorDisplayMap[key] || [];
        displays.forEach(display => {
            if (errorInfo) {
                updateErrorMessage(display, errorInfo.error_code, errorInfo.error);
            } else {
                updateErrorMessage(display, null, '');
            }
        });
    },

    /**
     * 当 Key 被修改时重置状态为 untested，清除错误信息。
     */
    onKeyChanged(key) {
        if (!key) return;
        this.keyStatusMap[key] = LightStatus.UNTESTED;
        this.keyErrorMap[key] = null;
        this.syncLightsForKey(key);
        this.syncErrorDisplaysForKey(key);
    },

    /**
     * 仅测试自定义 API 的连通性（不包含核心和辅助 API）。
     * 由自定义 API 区域的测试按钮调用。
     */
    async testCustomOnly() {
        const keyConfigs = {}; // cacheId → { provider_key, provider_scope, url, api_key, model, provider_type, is_free }

        const enableCustomApi = document.getElementById('enableCustomApi');
        const coreSelect = document.getElementById('coreApiSelect');
        const assistSelect = document.getElementById('assistApiSelect');

        if (enableCustomApi && enableCustomApi.checked) {
            CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
                const customResult = this.resolveEffectiveKey({ type: 'custom', modelType: mt });
                const cacheId = customResult.cacheId;
                if (cacheId && !keyConfigs[cacheId]) {
                    const providerSel = document.getElementById(`${mt}ModelProvider`);
                    const provider = providerSel ? providerSel.value : '';
                    let isFree = false;
                    if (provider === 'follow_core' && coreSelect && coreSelect.value === 'free') {
                        isFree = true;
                    } else if (provider === 'follow_assist' && assistSelect && assistSelect.value === 'free') {
                        isFree = true;
                    }
                    const modelInput = document.getElementById(`${mt}ModelId`);
                    const model = modelInput ? modelInput.value.trim() : '';
                    keyConfigs[cacheId] = {
                        provider_key: customResult.providerKey, provider_scope: customResult.providerScope,
                        url: customResult.url, api_key: customResult.key || '', model: model,
                        voice_id: customResult.voiceId || '',
                        provider_type: customResult.providerType, sub_type: customResult.subType || '', is_free: isFree
                    };
                }
            });
        }

        // 将所有待测试的配置设为 testing 状态
        Object.keys(keyConfigs).forEach(cacheId => {
            this.keyStatusMap[cacheId] = LightStatus.TESTING;
            this.keyErrorMap[cacheId] = null;
            this.syncLightsForKey(cacheId);
            this.syncErrorDisplaysForKey(cacheId);
        });

        // 并发测试
        const testPromises = Object.entries(keyConfigs).map(async ([cacheId, config]) => {
            const result = await this.testKey({ ...config, cache_id: cacheId });
            if (result.cancelled) {
                return;
            }
            if (result.success) {
                this.keyStatusMap[cacheId] = LightStatus.CONNECTED;
                this.keyErrorMap[cacheId] = null;
            } else {
                this.keyStatusMap[cacheId] = LightStatus.FAILED;
                this.keyErrorMap[cacheId] = {
                    error_code: result.error_code || 'unknown',
                    error: result.error || ''
                };
            }
            this.syncLightsForKey(cacheId);
            this.syncErrorDisplaysForKey(cacheId);
        });

        await Promise.allSettled(testPromises);
    },

    /**
     * 页面加载时的自动测试。
     */
    async autoTestOnLoad() {
        console.log('[ConnectivityManager] autoTestOnLoad called');
        await this.testAll();
        console.log('[ConnectivityManager] autoTestOnLoad finished');
    },
};

// ==================== 连通性测试：集成初始化 ====================

/**
 * 初始化所有连通性指示灯、错误信息展示、测试按钮和事件绑定。
 * 在 initializePage() 中调用，负责 Tasks 6.1, 6.2, 6.3, 7.1, 7.2, 8.1, 8.2。
 */
function initConnectivityLights() {
    // Prevent duplicate initialization (race condition guard)
    if (window._connectivityLightsInitialized) return;
    window._connectivityLightsInitialized = true;

    const apiKeyInput = document.getElementById('apiKeyInput');
    const assistApiKeyInput = document.getElementById('assistApiKeyInput');
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');
    const enableCustomApiCheckbox = document.getElementById('enableCustomApi');
    const customApiContainer = document.getElementById('custom-api-container');

    // 用于存储所有创建的指示灯和错误展示元素的引用，方便事件处理
    const lightRefs = {
        core: { light: null, errorDisplay: null },
        assist: { light: null, errorDisplay: null },
        custom: {} // { [modelType]: { light, errorDisplay } }
    };

    // 简单的 debounce 工具函数
    function debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    /**
     * 为指定的 light 和 errorDisplay 注册到 ConnectivityManager，
     * 根据当前 key 设置初始状态。
     */
    function registerAndSetInitialStatus(light, errorDisplay, context) {
        const resolved = ConnectivityManager.resolveEffectiveKey(context);
        const key = resolved.key;
        const url = resolved.url;

        // 用于注册和缓存的标识：使用带作用域前缀的 cacheId，避免不同测试上下文互相污染
        const cacheId = resolved.cacheId;

        if (!cacheId || (key && key !== 'free-access' && isFreeVersionText(key))) {
            // 无 key 且无 URL → 灰色（真正的未配置）
            updateLightStatus(light, LightStatus.NOT_CONFIGURED);
            updateErrorMessage(errorDisplay, null, '');
        } else {
            // 有 key 或有 URL → 检查缓存，有缓存用缓存，无缓存显示黄色
            const cachedStatus = ConnectivityManager.keyStatusMap[cacheId];
            if (cachedStatus) {
                updateLightStatus(light, cachedStatus);
                const errorInfo = ConnectivityManager.keyErrorMap[cacheId];
                if (errorInfo) {
                    updateErrorMessage(errorDisplay, errorInfo.error_code, errorInfo.error);
                } else {
                    updateErrorMessage(errorDisplay, null, '');
                }
            } else {
                updateLightStatus(light, LightStatus.UNTESTED);
                ConnectivityManager.keyStatusMap[cacheId] = LightStatus.UNTESTED;
                updateErrorMessage(errorDisplay, null, '');
            }
            ConnectivityManager.registerLight(cacheId, light);
            ConnectivityManager.registerErrorDisplay(cacheId, errorDisplay);
        }

        return cacheId;
    }

    /**
     * 重新注册一个 light/errorDisplay 到新的 key（取消旧注册）。
     * 可选地同时重新注册一个 summaryLight。
     * 返回新的 key。
     */
    function reRegister(light, errorDisplay, context, oldKey, summaryLight) {
        // 取消旧注册
        if (oldKey) {
            ConnectivityManager.unregisterLight(oldKey, light);
            if (summaryLight) {
                ConnectivityManager.unregisterLight(oldKey, summaryLight);
            }
            // unregister error display
            const displays = ConnectivityManager.keyErrorDisplayMap[oldKey];
            if (displays) {
                const idx = displays.indexOf(errorDisplay);
                if (idx !== -1) displays.splice(idx, 1);
                if (displays.length === 0) delete ConnectivityManager.keyErrorDisplayMap[oldKey];
            }
        }
        const newKey = registerAndSetInitialStatus(light, errorDisplay, context);
        // Re-register summary light to the new key
        if (summaryLight && newKey) {
            ConnectivityManager.registerLight(newKey, summaryLight);
            summaryLight.dataset.status = light.dataset.status;
        } else if (summaryLight) {
            summaryLight.dataset.status = LightStatus.NOT_CONFIGURED;
        }
        return newKey;
    }

    // ===== Task 6.1: Core API indicator light and error display =====
    let coreCurrentKey = '';
    if (apiKeyInput) {
        const coreLight = createIndicatorLight(apiKeyInput, { type: 'core' });
        const coreErrorDisplay = createErrorMessageDisplay(apiKeyInput);
        lightRefs.core.light = coreLight;
        lightRefs.core.errorDisplay = coreErrorDisplay;
        coreCurrentKey = registerAndSetInitialStatus(coreLight, coreErrorDisplay, { type: 'core' });

        // 核心 API 手动测试按钮
        const coreTestBtn = document.createElement('button');
        coreTestBtn.type = 'button';
        coreTestBtn.className = 'connectivity-mini-test-btn';
        coreTestBtn.textContent = window.t ? window.t('connectivity.testCore', '测试') : '测试';
        coreTestBtn.title = window.t ? window.t('connectivity.testCoreTooltip', '测试核心 API 连通性') : '测试核心 API 连通性';
        coreTestBtn.addEventListener('click', async () => {
            coreTestBtn.disabled = true;
            coreTestBtn.classList.add('testing');
            const resolved = ConnectivityManager.resolveEffectiveKey({ type: 'core' });
            if (resolved.cacheId) {
                const coreSelect = document.getElementById('coreApiSelect');
                const isFree = coreSelect && coreSelect.value === 'free';
                ConnectivityManager.keyStatusMap[resolved.cacheId] = LightStatus.TESTING;
                ConnectivityManager.keyErrorMap[resolved.cacheId] = null;
                ConnectivityManager.syncLightsForKey(resolved.cacheId);
                ConnectivityManager.syncErrorDisplaysForKey(resolved.cacheId);
                const result = await ConnectivityManager.testKey({
                    provider_key: resolved.providerKey, provider_scope: resolved.providerScope,
                    url: resolved.url, api_key: resolved.key,
                    model: resolved.model || '',
                    voice_id: resolved.voiceId || '',
                    provider_type: resolved.providerType,
                    sub_type: resolved.subType || '',
                    is_free: isFree,
                    cache_id: resolved.cacheId
                });
                if (result.cancelled) {
                    coreTestBtn.classList.remove('testing');
                    coreTestBtn.disabled = false;
                    return;
                } else if (result.success) {
                    ConnectivityManager.keyStatusMap[resolved.cacheId] = LightStatus.CONNECTED;
                    ConnectivityManager.keyErrorMap[resolved.cacheId] = null;
                } else {
                    ConnectivityManager.keyStatusMap[resolved.cacheId] = LightStatus.FAILED;
                    ConnectivityManager.keyErrorMap[resolved.cacheId] = { error_code: result.error_code || 'unknown', error: result.error || '' };
                }
                ConnectivityManager.syncLightsForKey(resolved.cacheId);
                ConnectivityManager.syncErrorDisplaysForKey(resolved.cacheId);
            }
            coreTestBtn.classList.remove('testing');
            coreTestBtn.disabled = false;
        });
        // 插入到 connectivity-input-row 内部，输入框后面（同一行）
        const coreInputRow = apiKeyInput.closest('.connectivity-input-row');
        if (coreInputRow) {
            coreInputRow.appendChild(coreTestBtn);
        }
    }

    // ===== Task 6.2: Assist API indicator light and error display =====
    let assistCurrentKey = '';
    if (assistApiKeyInput) {
        const assistLight = createIndicatorLight(assistApiKeyInput, { type: 'assist' });
        const assistErrorDisplay = createErrorMessageDisplay(assistApiKeyInput);
        lightRefs.assist.light = assistLight;
        lightRefs.assist.errorDisplay = assistErrorDisplay;
        assistCurrentKey = registerAndSetInitialStatus(assistLight, assistErrorDisplay, { type: 'assist' });

        // 辅助 API 手动测试按钮
        const assistTestBtn = document.createElement('button');
        assistTestBtn.type = 'button';
        assistTestBtn.className = 'connectivity-mini-test-btn';
        assistTestBtn.textContent = window.t ? window.t('connectivity.testAssist', '测试') : '测试';
        assistTestBtn.title = window.t ? window.t('connectivity.testAssistTooltip', '测试辅助 API 连通性') : '测试辅助 API 连通性';
        assistTestBtn.addEventListener('click', async () => {
            assistTestBtn.disabled = true;
            assistTestBtn.classList.add('testing');
            const resolved = ConnectivityManager.resolveEffectiveKey({ type: 'assist' });
            if (resolved.cacheId) {
                const assistSelect = document.getElementById('assistApiSelect');
                const isFree = assistSelect && assistSelect.value === 'free';
                ConnectivityManager.keyStatusMap[resolved.cacheId] = LightStatus.TESTING;
                ConnectivityManager.keyErrorMap[resolved.cacheId] = null;
                ConnectivityManager.syncLightsForKey(resolved.cacheId);
                ConnectivityManager.syncErrorDisplaysForKey(resolved.cacheId);
                const result = await ConnectivityManager.testKey({
                    provider_key: resolved.providerKey, provider_scope: resolved.providerScope,
                    url: resolved.url, api_key: resolved.key,
                    model: resolved.model || '',
                    voice_id: resolved.voiceId || '',
                    provider_type: resolved.providerType,
                    sub_type: resolved.subType || '',
                    is_free: isFree,
                    cache_id: resolved.cacheId
                });
                if (result.cancelled) {
                    assistTestBtn.classList.remove('testing');
                    assistTestBtn.disabled = false;
                    return;
                } else if (result.success) {
                    ConnectivityManager.keyStatusMap[resolved.cacheId] = LightStatus.CONNECTED;
                    ConnectivityManager.keyErrorMap[resolved.cacheId] = null;
                } else {
                    ConnectivityManager.keyStatusMap[resolved.cacheId] = LightStatus.FAILED;
                    ConnectivityManager.keyErrorMap[resolved.cacheId] = { error_code: result.error_code || 'unknown', error: result.error || '' };
                }
                ConnectivityManager.syncLightsForKey(resolved.cacheId);
                ConnectivityManager.syncErrorDisplaysForKey(resolved.cacheId);
            }
            assistTestBtn.classList.remove('testing');
            assistTestBtn.disabled = false;
        });
        // 插入到 connectivity-input-row 内部，输入框后面（同一行）
        const assistInputRow = assistApiKeyInput.closest('.connectivity-input-row');
        if (assistInputRow) {
            assistInputRow.appendChild(assistTestBtn);
        }
    }

    // ===== Task 7.1: Custom API test button =====
    let testButton = null;
    if (customApiContainer) {
        testButton = document.createElement('button');
        testButton.type = 'button';
        testButton.className = 'connectivity-test-btn';
        testButton.id = 'customApiTestBtn';

        // 创建按钮内的小指示灯图标
        const btnLight = document.createElement('span');
        btnLight.className = 'connectivity-light';
        btnLight.dataset.status = LightStatus.NOT_CONFIGURED;
        btnLight.style.marginRight = '6px';
        testButton.appendChild(btnLight);

        const btnText = document.createElement('span');
        btnText.textContent = window.t ? window.t('connectivity.testCustom', '测试自定义 API 连通性') : '测试自定义 API 连通性';
        btnText.setAttribute('data-i18n', 'connectivity.testCustom');
        testButton.appendChild(btnText);

        // 插入到 custom-api-container 顶部
        customApiContainer.insertBefore(testButton, customApiContainer.firstChild);

        // ===== Issue 3: Summary lights row next to test button =====
        const summaryRow = document.createElement('span');
        summaryRow.className = 'connectivity-summary-row';
        summaryRow.id = 'customApiSummaryLights';

        CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
            const summaryLight = document.createElement('button');
            summaryLight.type = 'button';
            summaryLight.className = 'connectivity-summary-light';
            summaryLight.dataset.status = LightStatus.NOT_CONFIGURED;
            summaryLight.dataset.modelType = mt;
            summaryLight.dataset.tooltipLabel = getCustomModelDisplayLabel(mt);
            const expansionPath = MODEL_CONFIG_EXPANSION_PATHS[mt];
            const targetType = expansionPath?.[expansionPath.length - 1];
            if (targetType) {
                summaryLight.dataset.targetContentId = `${targetType}-model-content`;
            }
            updateLightStatus(summaryLight, LightStatus.NOT_CONFIGURED);
            summaryLight.addEventListener('click', () => navigateToCustomModelConfig(mt));
            summaryRow.appendChild(summaryLight);
        });

        // Wrap button and summary lights in a container
        const btnWrapper = document.createElement('div');
        btnWrapper.className = 'connectivity-test-btn-wrapper';
        btnWrapper.style.display = 'flex';

        // Move button into wrapper (replace its position)
        testButton.style.marginBottom = '0';
        customApiContainer.removeChild(testButton);
        btnWrapper.appendChild(testButton);
        btnWrapper.appendChild(summaryRow);
        customApiContainer.insertBefore(btnWrapper, customApiContainer.firstChild);

        // 根据 enableCustomApi 状态显示/隐藏
        if (enableCustomApiCheckbox) {
            btnWrapper.style.display = enableCustomApiCheckbox.checked ? 'flex' : 'none';
        }

        testButton.addEventListener('click', async () => {
            testButton.disabled = true;
            try {
                await ConnectivityManager.testCustomOnly();
            } finally {
                testButton.disabled = false;
            }
        });
    }

    // ===== Task 7.2: Custom model indicator lights =====
    const customCurrentKeys = {}; // { [modelType]: currentKey }
    function reRegisterModelSlot(modelType) {
        if (!lightRefs.custom[modelType]) return;
        const oldKey = customCurrentKeys[modelType];
        customCurrentKeys[modelType] = reRegister(
            lightRefs.custom[modelType].light,
            lightRefs.custom[modelType].errorDisplay,
            { type: 'custom', modelType },
            oldKey,
            lightRefs.custom[modelType].summaryLight
        );
    }

    function syncModelFollowers(sourceType) {
        const followerType = sourceType === 'conversation' ? 'gameMain' : 'gameSummary';
        const providerSel = document.getElementById(`${followerType}ModelProvider`);
        const expectedProvider = sourceType === 'conversation' ? 'follow_conversation' : 'follow_summary';
        if (!providerSel || providerSel.value !== expectedProvider) return;
        onCustomModelProviderChange(followerType);
        reRegisterModelSlot(followerType);
    }

    function refreshFollowModelSlot(modelType) {
        onCustomModelProviderChange(modelType);
        reRegisterModelSlot(modelType);
    }

    CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
        const keyInput = document.getElementById(`${mt}ModelApiKey`);
        if (!keyInput) return;

        const light = createIndicatorLight(keyInput, { type: 'custom', modelType: mt });
        const errorDisplay = createErrorMessageDisplay(keyInput);
        lightRefs.custom[mt] = { light, errorDisplay };

        const key = registerAndSetInitialStatus(light, errorDisplay, { type: 'custom', modelType: mt });
        customCurrentKeys[mt] = key;

        // Also register the corresponding summary light for the same key
        const summaryLight = document.querySelector(`.connectivity-summary-light[data-model-type="${mt}"]`);
        if (summaryLight && key) {
            ConnectivityManager.registerLight(key, summaryLight);
            // Set initial status to match the main light
            summaryLight.dataset.status = light.dataset.status;
            summaryLight.dataset.tooltipLabel = getCustomModelDisplayLabel(mt);
            updateLightStatus(summaryLight, light.dataset.status || LightStatus.NOT_CONFIGURED);
        }
        if (summaryLight) {
            lightRefs.custom[mt].summaryLight = summaryLight;
        }
    });

    // ===== Task 7.1 (continued): Show/hide test button on enableCustomApi change =====
    if (enableCustomApiCheckbox && testButton) {
        const wrapper = document.getElementById('customApiTestBtn')?.closest('.connectivity-test-btn-wrapper');
        enableCustomApiCheckbox.addEventListener('change', () => {
            if (wrapper) {
                wrapper.style.display = enableCustomApiCheckbox.checked ? 'flex' : 'none';
            }
        });
    }

    // ===== Task 8.1: Key modification event binding =====

    // Helper: cascade reset for all lights sharing the same key
    function cascadeResetForKey(key) {
        if (!key) return;
        ConnectivityManager.onKeyChanged(key);
    }

    // Core API key input change
    if (apiKeyInput) {
        const handleCoreKeyChange = debounce(() => {
            if (!_isLoadingSavedConfig) {
                _coreApiKeyInputDirty = true;
                const providerKey = coreApiSelect ? coreApiSelect.value : '';
                if (providerKey && providerKey !== 'free') {
                    syncKeyToBook(providerKey, getRealKey(apiKeyInput), apiKeyInput);
                }
            }
            const oldKey = coreCurrentKey;
            coreCurrentKey = reRegister(
                lightRefs.core.light, lightRefs.core.errorDisplay,
                { type: 'core' }, oldKey
            );
            // Cascade: reset all lights that shared the OLD key (they lost their source)
            if (oldKey && oldKey !== coreCurrentKey) {
                cascadeResetForKey(oldKey);
            }
            // 新 key 不需要 cascadeReset — reRegister 已经从缓存正确恢复了状态
            // Re-register custom models that follow_core so they track the new key
            CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
                const providerSel = document.getElementById(`${mt}ModelProvider`);
                if (providerSel && providerSel.value === 'follow_core' && lightRefs.custom[mt]) {
                    refreshFollowModelSlot(mt);
                }
            });
            syncModelFollowers('conversation');
            syncModelFollowers('summary');
        }, 300);

        apiKeyInput.addEventListener('input', handleCoreKeyChange);
        apiKeyInput.addEventListener('change', handleCoreKeyChange);
    }

    // Assist API key input change
    if (assistApiKeyInput) {
        const handleAssistKeyChange = debounce(() => {
            const oldKey = assistCurrentKey;
            assistCurrentKey = reRegister(
                lightRefs.assist.light, lightRefs.assist.errorDisplay,
                { type: 'assist' }, oldKey
            );
            if (oldKey && oldKey !== assistCurrentKey) {
                cascadeResetForKey(oldKey);
            }
            // 新 key 不需要 cascadeReset — reRegister 已经从缓存正确恢复了状态
            // Re-register custom models that follow_assist so they track the new key
            CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
                const providerSel = document.getElementById(`${mt}ModelProvider`);
                if (providerSel && providerSel.value === 'follow_assist' && lightRefs.custom[mt]) {
                    refreshFollowModelSlot(mt);
                }
            });
            syncModelFollowers('conversation');
            syncModelFollowers('summary');
        }, 300);

        assistApiKeyInput.addEventListener('input', handleAssistKeyChange);
        assistApiKeyInput.addEventListener('change', handleAssistKeyChange);
        const mimoTokenPlanKeyInput = document.getElementById('mimoTokenPlanKeyInput');
        if (mimoTokenPlanKeyInput) {
            mimoTokenPlanKeyInput.addEventListener('input', handleAssistKeyChange);
            mimoTokenPlanKeyInput.addEventListener('change', handleAssistKeyChange);
            attachMaskBehavior(mimoTokenPlanKeyInput);
        }
    }

    // Custom model key input changes
    CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
        const keyInput = document.getElementById(`${mt}ModelApiKey`);
        if (!keyInput || !lightRefs.custom[mt]) return;

        const handleCustomKeyChange = debounce(() => {
            const oldKey = customCurrentKeys[mt];
            customCurrentKeys[mt] = reRegister(
                lightRefs.custom[mt].light, lightRefs.custom[mt].errorDisplay,
                { type: 'custom', modelType: mt }, oldKey,
                lightRefs.custom[mt].summaryLight
            );
            if (oldKey && oldKey !== customCurrentKeys[mt]) {
                cascadeResetForKey(oldKey);
            }
            if (mt === 'conversation' || mt === 'summary') {
                syncModelFollowers(mt);
            }
            // 新 key 不需要 cascadeReset — reRegister 已经从缓存正确恢复了状态
        }, 300);

        keyInput.addEventListener('input', handleCustomKeyChange);
        keyInput.addEventListener('change', handleCustomKeyChange);
    });

    // Custom model URL / model ID changes can affect connectivity cache even when Key is unchanged.
    CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
        ['Url', 'Id'].forEach(suffix => {
            const input = document.getElementById(`${mt}Model${suffix}`);
            if (!input || !lightRefs.custom[mt]) return;

            const handleCustomEndpointChange = debounce(() => {
                reRegisterModelSlot(mt);
                if (mt === 'conversation' || mt === 'summary') {
                    syncModelFollowers(mt);
                }
            }, 300);

            input.addEventListener('input', handleCustomEndpointChange);
            input.addEventListener('change', handleCustomEndpointChange);
        });
    });

    // ===== Task 8.2: Provider switch event binding =====

    // Core API provider change
    if (coreApiSelect) {
        coreApiSelect.addEventListener('change', () => {
            const oldKey = coreCurrentKey;
            coreCurrentKey = reRegister(
                lightRefs.core.light, lightRefs.core.errorDisplay,
                { type: 'core' }, oldKey
            );
            // 核心切换可能导致辅助被强制改变（如切到免费版时辅助锁定为免费版）
            // 需要重新注册辅助灯
            if (lightRefs.assist.light) {
                const oldAssistKey = assistCurrentKey;
                assistCurrentKey = reRegister(
                    lightRefs.assist.light, lightRefs.assist.errorDisplay,
                    { type: 'assist' }, oldAssistKey
                );
            }
            // Also re-register custom models that follow_core or follow_assist
            CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
                const providerSel = document.getElementById(`${mt}ModelProvider`);
                if (providerSel && (providerSel.value === 'follow_core' || providerSel.value === 'follow_assist') && lightRefs.custom[mt]) {
                    refreshFollowModelSlot(mt);
                }
            });
            syncModelFollowers('conversation');
            syncModelFollowers('summary');
        });
    }

    // Assist API provider change
    if (assistApiSelect) {
        assistApiSelect.addEventListener('change', () => {
            updateMimoTokenPlanControls();
            const oldKey = assistCurrentKey;
            assistCurrentKey = reRegister(
                lightRefs.assist.light, lightRefs.assist.errorDisplay,
                { type: 'assist' }, oldKey
            );
            // Also re-register custom models that follow_assist
            CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
                const providerSel = document.getElementById(`${mt}ModelProvider`);
                if (providerSel && providerSel.value === 'follow_assist' && lightRefs.custom[mt]) {
                    refreshFollowModelSlot(mt);
                }
            });
            syncModelFollowers('conversation');
            syncModelFollowers('summary');
        });
    }

    const useMimoTokenPlanToggle = document.getElementById('useMimoTokenPlan');
    if (useMimoTokenPlanToggle) {
        useMimoTokenPlanToggle.addEventListener('change', () => {
            updateMimoTokenPlanControls();
            const oldKey = assistCurrentKey;
            assistCurrentKey = reRegister(
                lightRefs.assist.light, lightRefs.assist.errorDisplay,
                { type: 'assist' }, oldKey
            );
            if (oldKey && oldKey !== assistCurrentKey) {
                cascadeResetForKey(oldKey);
            }
            CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
                const providerSel = document.getElementById(`${mt}ModelProvider`);
                if (providerSel && providerSel.value === 'follow_assist' && lightRefs.custom[mt]) {
                    refreshFollowModelSlot(mt);
                }
            });
            syncModelFollowers('conversation');
            syncModelFollowers('summary');
        });
    }

    // Custom model provider dropdown changes
    CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
        const providerSel = document.getElementById(`${mt}ModelProvider`);
        if (!providerSel || !lightRefs.custom[mt]) return;

        providerSel.addEventListener('change', () => {
            reRegisterModelSlot(mt);
            if (mt === 'conversation' || mt === 'summary') {
                syncModelFollowers(mt);
            }
        });
    });

    // ===== GPT-SoVITS connectivity test button =====
    const gsvUrlInput = document.getElementById('gptsovitsApiUrl');
    if (gsvUrlInput) {
        const gsvLight = document.createElement('span');
        gsvLight.className = 'connectivity-light';
        gsvLight.dataset.status = LightStatus.NOT_CONFIGURED;
        gsvLight.title = 'GPT-SoVITS';

        const gsvTestBtn = document.createElement('button');
        gsvTestBtn.type = 'button';
        gsvTestBtn.className = 'connectivity-mini-test-btn';
        gsvTestBtn.textContent = window.t ? window.t('connectivity.testCore', '测试') : '测试';
        gsvTestBtn.title = window.t ? window.t('connectivity.gsvTestTooltip', 'GPT-SoVITS 连通性测试') : 'GPT-SoVITS 连通性测试';

        // Wrap input with light and button
        const gsvRow = gsvUrlInput.closest('.field-row');
        if (gsvRow) {
            const wrapper = document.createElement('div');
            wrapper.className = 'connectivity-input-row';
            gsvUrlInput.parentNode.insertBefore(wrapper, gsvUrlInput);
            wrapper.appendChild(gsvLight);
            wrapper.appendChild(gsvUrlInput);
            wrapper.appendChild(gsvTestBtn);
        }

        // Update light based on URL presence and changes
        let gsvLastUrl = gsvUrlInput.value.trim();
        const gsvErrorMsg = document.createElement('span');
        gsvErrorMsg.className = 'connectivity-error-msg';
        gsvErrorMsg.style.display = 'none';
        if (gsvRow) {
            gsvRow.appendChild(gsvErrorMsg);
        }

        const updateGsvLightStatus = () => {
            const url = gsvUrlInput.value.trim();
            if (!url) {
                updateLightStatus(gsvLight, LightStatus.NOT_CONFIGURED);
                updateErrorMessage(gsvErrorMsg, null, '');
            } else if (url !== gsvLastUrl) {
                // URL changed — reset to untested (same behavior as key change cascade)
                updateLightStatus(gsvLight, LightStatus.UNTESTED);
                updateErrorMessage(gsvErrorMsg, null, '');
            } else if (gsvLight.dataset.status === LightStatus.NOT_CONFIGURED) {
                updateLightStatus(gsvLight, LightStatus.UNTESTED);
            }
            gsvLastUrl = url;
        };
        updateGsvLightStatus();
        gsvUrlInput.addEventListener('input', () => {
            updateGsvLightStatus();
        });

        // Test button click: full WebSocket round-trip (init → ready → send text → receive response)
        gsvTestBtn.addEventListener('click', async () => {
            const url = gsvUrlInput.value.trim() || 'http://127.0.0.1:9881';
            const voiceIdInput = document.getElementById('gptsovitsVoiceId');
            const voiceId = voiceIdInput ? voiceIdInput.value.trim() : '_default';
            const testText = window.t ? window.t('connectivity.gsvTestText', 'GSV连通性测试') : 'GSV连通性测试';

            gsvTestBtn.disabled = true;
            gsvTestBtn.classList.add('testing');
            updateLightStatus(gsvLight, LightStatus.TESTING);
            updateErrorMessage(gsvErrorMsg, null, '');

            try {
                const resp = await fetch('/api/config/gptsovits/test_connectivity', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_url: url, voice_id: voiceId, test_text: testText }),
                    signal: AbortSignal.timeout(15000),
                });
                const result = await resp.json();
                if (result.success) {
                    updateLightStatus(gsvLight, LightStatus.CONNECTED);
                    updateErrorMessage(gsvErrorMsg, null, '');
                    // --- 以下为编写连通测试时使用的音频播放验证代码，已确认可行（2026-04-22） ---
                    // --- 保留供后续调试使用，正常运行时不启用 ---
                    // if (result.audio_data && result.sample_rate) {
                    //     try {
                    //         const pcmBytes = Uint8Array.from(atob(result.audio_data), c => c.charCodeAt(0));
                    //         const samples = new Int16Array(pcmBytes.buffer);
                    //         const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    //         const buffer = audioCtx.createBuffer(1, samples.length, result.sample_rate);
                    //         const channelData = buffer.getChannelData(0);
                    //         for (let i = 0; i < samples.length; i++) {
                    //             channelData[i] = samples[i] / 32768;
                    //         }
                    //         const source = audioCtx.createBufferSource();
                    //         source.buffer = buffer;
                    //         source.connect(audioCtx.destination);
                    //         source.start();
                    //         console.log(`[GSV Test] Playing ${result.audio_length_ms}ms audio at ${result.sample_rate}Hz`);
                    //     } catch (audioErr) {
                    //         console.warn('[GSV Test] Audio playback failed:', audioErr);
                    //     }
                    // }
                } else {
                    updateLightStatus(gsvLight, LightStatus.FAILED);
                    updateErrorMessage(gsvErrorMsg, result.error_code || 'unknown', result.error || '');
                    console.error('[GSV Test] Failed:', result.error || result.error_code || 'unknown');
                }
            } catch (err) {
                updateLightStatus(gsvLight, LightStatus.FAILED);
                updateErrorMessage(
                    gsvErrorMsg,
                    err.name === 'TimeoutError' ? 'timeout' : 'unknown',
                    err.message || ''
                );
                console.error('[GSV Test] Error:', err);
            }

            gsvTestBtn.classList.remove('testing');
            gsvTestBtn.disabled = false;
        });
    }
}

// 等待 i18n 初始化完成
async function waitForI18n(timeout = 3000) {
    const startTime = Date.now();
    while (!window.t && Date.now() - startTime < timeout) {
        await new Promise(resolve => setTimeout(resolve, 50));
    }
    return !!window.t;
}

// 页面初始化函数 - 先加载配置再显示UI
async function initializePage() {
    if (window.apiKeySettingsInitialized) {
        return;
    }

    try {
        const loadingOverlay = document.getElementById('loading-overlay');

        if (loadingOverlay) {
            loadingOverlay.style.display = 'flex';
        }

        initProviderSelectDropdowns();

        await waitForI18n();

        isMainlandChinaUser = await checkMainlandChinaUser();
        console.log(`[Region] 用户区域检测完成: isMainlandChinaUser = ${isMainlandChinaUser}`);

        const providersLoaded = await loadApiProviders();

        if (!providersLoaded) {
            throw new Error(window.t ? window.t('api.loadProvidersFailed') : '加载API服务商选项失败');
        }

        await loadCurrentApiKey();

        const UI_SETTLE_DELAY = 300;
        await new Promise(resolve => setTimeout(resolve, UI_SETTLE_DELAY));

        initTooltips();

        const coreApiSelect = document.getElementById('coreApiSelect');
        const apiKeyInput = document.getElementById('apiKeyInput');
        const freeVersionHint = document.getElementById('freeVersionHint');

        if (coreApiSelect && apiKeyInput && freeVersionHint) {
            const selectedCoreApi = coreApiSelect.value;

            if (selectedCoreApi === 'free') {
                apiKeyInput.disabled = true;
                apiKeyInput.placeholder = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
                apiKeyInput.required = false;
                apiKeyInput.value = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
                freeVersionHint.style.display = 'inline';
            } else {
                apiKeyInput.disabled = false;
                apiKeyInput.placeholder = window.t ? window.t('api.pleaseEnterApiKey') : '请输入您的API Key';
                apiKeyInput.required = true;
                if (isFreeVersionText(getRealKey(apiKeyInput))) {
                    setMaskedInput(apiKeyInput, '');
                }
                freeVersionHint.style.display = 'none';
            }
            updateAssistApiKeyInputAvailability();

            updateAssistApiRecommendation();
            autoFillCoreApiKey(true);
            // 不再调用 autoFillAssistApiKey(true)，因为 loadCurrentApiKey()
            // 已从后端数据直接设置辅助API Key，此处再次从管理簿读取会覆盖正确值
            // （同服务商时管理簿存的是核心Key，受限服务商时管理簿DOM不存在）
            syncProviderSelectDropdowns();
        }

        // CRITICAL: Core/Assist selector change handlers that recompute follow-provider model slots
        if (coreApiSelect) {
            coreApiSelect.addEventListener('change', function () {
                updateAssistApiRecommendation();
                autoFillCoreApiKey(true);
                // Recompute all follow_core model slots
                MODEL_TYPES.forEach(mt => {
                    const sel = document.getElementById(`${mt}ModelProvider`);
                    if (sel && sel.value === 'follow_core') {
                        onCustomModelProviderChange(mt);
                    }
                });
            });
        }

        const assistApiSelect = document.getElementById('assistApiSelect');
        if (assistApiSelect) {
            assistApiSelect.addEventListener('change', function () {
                updateMimoTokenPlanControls();
                updateAssistApiRecommendation();
                autoFillAssistApiKey(true);
                // Recompute all follow_assist model slots
                MODEL_TYPES.forEach(mt => {
                    const sel = document.getElementById(`${mt}ModelProvider`);
                    if (sel && sel.value === 'follow_assist') {
                        onCustomModelProviderChange(mt);
                    }
                });
            });
        }

        updateAssistApiRecommendation();

        // 监听语言切换事件，更新下拉选项（保留用户未保存的输入）
        window.addEventListener('localechange', async () => {
            // Capture current state before DOM is rebuilt
            const selectedCoreApi = coreApiSelect ? coreApiSelect.value : '';
            const selectedAssistApi = assistApiSelect ? assistApiSelect.value : '';

            // Snapshot Key Book input values（读取真实 key，避免存遮蔽值）
            const keyBookSnapshot = {};
            const bookContainer = document.getElementById('key-book-inputs');
            if (bookContainer) {
                bookContainer.querySelectorAll('input[data-provider-key]').forEach(input => {
                    keyBookSnapshot[input.dataset.providerKey] = getRealKey(input);
                });
            }

            // Snapshot model provider select values
            const modelProviderSnapshot = {};
            MODEL_TYPES.forEach(mt => {
                const sel = document.getElementById(`${mt}ModelProvider`);
                if (sel) modelProviderSnapshot[mt] = sel.value;
            });

            await loadApiProviders();

            // Restore core/assist selects
            if (coreApiSelect && selectedCoreApi) {
                coreApiSelect.value = selectedCoreApi;
            }
            if (assistApiSelect && selectedAssistApi) {
                assistApiSelect.value = selectedAssistApi;
            }

            syncProviderSelectDropdowns();

            // Restore Key Book input values
            Object.keys(keyBookSnapshot).forEach(providerKey => {
                syncKeyToBook(providerKey, keyBookSnapshot[providerKey]);
            });

            // Restore model provider select values and replay derived state
            MODEL_TYPES.forEach(mt => {
                const sel = document.getElementById(`${mt}ModelProvider`);
                if (sel && modelProviderSnapshot[mt] !== undefined) {
                    sel.value = modelProviderSnapshot[mt];
                    onCustomModelProviderChange(mt);
                }
            });
        });

        if (loadingOverlay) {
            loadingOverlay.style.display = 'none';
        }

        // Task 6.1, 6.2, 7.1, 7.2, 8.1, 8.2: Initialize connectivity lights, buttons, and event bindings
        initConnectivityLights();

        window.apiKeySettingsInitialized = true;

        setTimeout(() => {
            toggleCustomApi(true);
        }, 0);

        if (shouldFocusKeyBookFromLocation()) {
            setTimeout(() => {
                expandAndScrollToKeyBook();
            }, 80);
        }

        // Task 6.3: Auto-test removed per maintainer feedback (Wehos).
        // Manual test buttons are sufficient; auto-test on page load could
        // consume tokens without user consent and /models doesn't reliably
        // verify key validity across all providers.
        // Future: consider auto-test opt-in via user preference.

    } catch (error) {
        console.error('页面初始化失败:', error);

        showStatus(window.t ? window.t('api.loadConfigFailed') : '加载配置失败，请刷新页面重试', 'error');

        const loadingOverlay = document.getElementById('loading-overlay');

        if (loadingOverlay) {
            loadingOverlay.style.display = 'none';
        }
    }
}

// 页面加载完成后开始初始化
document.addEventListener('DOMContentLoaded', initializePage);

window.addEventListener('message', event => {
    if (event.origin !== window.location.origin) return;
    if (event.data && event.data.type === 'focus_api_key_book') {
        expandAndScrollToKeyBook();
    }
});

// 兼容性：防止在某些情况下DOMContentLoaded不触发
window.addEventListener('load', () => {
    if (!window.apiKeySettingsInitialized) {
        initializePage();
    }
    // Electron白屏修复：强制重绘
    if (document.body) {
        void document.body.offsetHeight;
    }
});

// 立即执行一次白屏修复（针对Electron）
(function () {
    const fixWhiteScreen = () => {
        if (document.body) {
            void document.body.offsetHeight;
        }
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fixWhiteScreen);
    } else {
        fixWhiteScreen();
    }
})();

// 关闭API Key设置页面
function closeApiKeySettings() {
    closeSettingsPage();
}

// 统一的页面关闭函数
function closeSettingsPage() {
    if (window.opener) {
        window.close();
    } else if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'close_api_key_settings' }, getTargetOrigin());
    } else {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            window.close();
            setTimeout(() => {
                if (!window.closed) {
                    window.location.href = '/';
                }
            }, 100);
        }
    }
}
