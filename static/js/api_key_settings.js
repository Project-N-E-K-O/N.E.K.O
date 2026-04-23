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
// 核心API服务商完整信息（从后端加载）
let _coreApiProviders = {};
// 所有模型类型
const MODEL_TYPES = ['conversation', 'summary', 'correction', 'emotion', 'vision', 'agent', 'omni', 'tts'];
// Model types that support connectivity testing.
// All model types including TTS are testable — TTS follows the same
// provider resolution logic (follow_core/follow_assist/custom).
// Future: GPT-SoVITS custom TTS may need dedicated WebSocket test path.
const CONNECTIVITY_TESTABLE_TYPES = MODEL_TYPES;
// 当前加载到页面中的 GPT-SoVITS 状态：none | enabled | disabled
let _loadedGptSovitsState = 'none';
// 上方普通 TTS 配置是否被用户在本页改动过
let _ttsConfigDirty = false;

function markTtsConfigDirty() {
    if (_isLoadingSavedConfig) return;
    _ttsConfigDirty = true;
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

function showStatus(message, type = 'info') {
    const statusDiv = document.getElementById('status');
    if (!statusDiv) {
        console.warn('[API Key Settings] status element not found');
        return;
    }

    statusDiv.textContent = message;
    statusDiv.className = `status ${type}`;
    statusDiv.style.display = 'block';

    if (type === 'success') {
        setTimeout(() => {
            statusDiv.style.display = 'none';
        }, 3000);
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
function syncKeyToBook(providerKey, keyValue) {
    const input = document.getElementById(`keyBookInput_${providerKey}`);
    if (input) {
        setMaskedInput(input, keyValue || '');
        attachMaskBehavior(input);
    }
}

// ==================== Model Provider Dropdowns ====================

/**
 * 填充所有自定义模型的服务商下拉框
 */
function populateModelProviderDropdowns() {
    MODEL_TYPES.forEach(mt => {
        const sel = document.getElementById(`${mt}ModelProvider`);
        if (!sel) return;
        sel.innerHTML = '';

        // follow_core
        const optCore = document.createElement('option');
        optCore.value = 'follow_core';
        optCore.textContent = window.t ? window.t('api.customModelProviderFollowCore') : '跟随核心API';
        optCore.setAttribute('data-i18n', 'api.customModelProviderFollowCore');
        sel.appendChild(optCore);

        // follow_assist
        const optAssist = document.createElement('option');
        optAssist.value = 'follow_assist';
        optAssist.textContent = window.t ? window.t('api.customModelProviderFollowAssist') : '跟随辅助API';
        optAssist.setAttribute('data-i18n', 'api.customModelProviderFollowAssist');
        sel.appendChild(optAssist);

        // Each non-free provider from _assistApiProviders
        Object.keys(_assistApiProviders).forEach(pk => {
            if (pk === 'free') return;
            if (isProviderRestricted(pk)) return;
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

        // custom
        const optCustom = document.createElement('option');
        optCustom.value = 'custom';
        optCustom.textContent = window.t ? window.t('api.customModelProviderCustom') : '自定义';
        optCustom.setAttribute('data-i18n', 'api.customModelProviderCustom');
        sel.appendChild(optCustom);

        // Default: omni → follow_core, others → follow_assist
        sel.value = (mt === 'omni') ? 'follow_core' : 'follow_assist';

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

    const provider = sel.value;
    const urlInput = document.getElementById(`${modelType}ModelUrl`);
    const keyInput = document.getElementById(`${modelType}ModelApiKey`);
    const modelIdInput = document.getElementById(`${modelType}ModelId`);

    // Model ID is NEVER readonly
    if (modelIdInput) {
        modelIdInput.removeAttribute('readonly');
    }

    /**
     * 将 key 输入框设为 readonly 并显示管理簿提示 + 快捷跳转按钮
     */
    const setKeyReadonly = (input, value) => {
        if (!input) return;
        setMaskedInput(input, value || '');
        input.setAttribute('readonly', 'readonly');
        input.placeholder = window.t ? window.t('api.keyAutoFilledFromKeyBook') : 'Key从API管理簿自动填充';
        ensureKeyBookLink(input);
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
                    urlInput.value = coreProfile.core_url || '';
                    urlInput.setAttribute('readonly', 'readonly');
                }
                const coreBookKey = syncKeyFromBook(coreProviderKey);
                setKeyReadonly(keyInput, coreBookKey);
            } else {
                const pInfo = _assistApiProviders[sourceProviderKey] || _coreApiProviders[sourceProviderKey] || {};
                if (urlInput) {
                    urlInput.value = pInfo.openrouter_url || pInfo.core_url || '';
                    urlInput.setAttribute('readonly', 'readonly');
                }
                const bookKey = syncKeyFromBook(sourceProviderKey);
                setKeyReadonly(keyInput, bookKey);
            }
        } else {
            // free or empty
            if (urlInput) { urlInput.value = ''; urlInput.setAttribute('readonly', 'readonly'); }
            setKeyReadonly(keyInput, '');
        }
    } else if (provider === 'custom') {
        // custom: remove readonly
        if (urlInput) urlInput.removeAttribute('readonly');
        setKeyEditable(keyInput);
    } else {
        // Specific provider
        const pInfo = _assistApiProviders[provider] || _coreApiProviders[provider] || {};
        if (modelType === 'omni') {
            const coreProfile = _coreApiProviders[provider] || {};
            if (urlInput) {
                urlInput.value = coreProfile.core_url || pInfo.core_url || '';
                urlInput.setAttribute('readonly', 'readonly');
            }
        } else {
            if (urlInput) {
                urlInput.value = pInfo.openrouter_url || pInfo.core_url || '';
                urlInput.setAttribute('readonly', 'readonly');
            }
        }
        const bookKey = syncKeyFromBook(provider);
        setKeyReadonly(keyInput, bookKey);
    }
}

/**
 * 在 key 输入框旁添加"前往管理簿"快捷按钮（如果还没有）
 */
function ensureKeyBookLink(input) {
    if (!input) return;
    const parent = input.parentElement;
    if (!parent) return;
    if (parent.querySelector('.key-book-shortcut')) return;

    const link = document.createElement('a');
    link.href = 'javascript:void(0)';
    link.className = 'key-book-shortcut';
    link.setAttribute('data-i18n', 'api.goToKeyBook');
    link.textContent = window.t ? window.t('api.goToKeyBook') : '前往管理簿';
    link.style.cssText = 'font-size: 0.85em; color: #40C5F1; cursor: pointer; margin-left: 8px; white-space: nowrap;';
    link.addEventListener('click', (e) => {
        e.preventDefault();
        // 展开 Key Book 区域并滚动到它
        const options = document.getElementById('key-book-options');
        const btn = document.getElementById('key-book-toggle-btn');
        if (options && options.style.display === 'none') {
            options.style.display = 'block';
            if (btn) btn.classList.add('rotated');
        }
        const section = document.getElementById('key-book-section');
        if (section) section.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    parent.appendChild(link);
}

/**
 * 移除 key 输入框旁的"前往管理簿"快捷按钮
 */
function removeKeyBookLink(input) {
    if (!input) return;
    const parent = input.parentElement;
    if (!parent) return;
    const link = parent.querySelector('.key-book-shortcut');
    if (link) link.remove();
}

// ==================== 加载API服务商选项 ====================

async function loadApiProviders() {
    try {
        const response = await fetch('/api/config/api_providers');
        if (response.ok) {
            const data = await response.json();
            if (data.success) {
                // Store registry and full provider info
                _apiKeyRegistry = data.api_key_registry || {};
                _coreApiProviders = data.core_api_providers_full || {};
                _assistApiProviders = data.assist_api_providers_full || {};

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
                    const allProviders = { ..._coreApiProviders, ..._assistApiProviders };
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
            // 设置API Key显示
            if (data.enableCustomApi) {
                showCurrentApiKey(window.t ? window.t('api.currentUsingCustomApi') : '当前使用：自定义API模式', '', true);
            } else if (data.api_key) {
                if (data.api_key === 'free-access' || data.coreApi === 'free' || data.assistApi === 'free') {
                    showCurrentApiKey(window.t ? window.t('api.currentUsingFreeVersion') : '当前使用：免费版（无需API Key）', 'free-access', true);
                } else {
                    showCurrentApiKey(window.t ? window.t('api.currentApiKey', { key: maskApiKey(data.api_key) }) : `当前API Key: ${maskApiKey(data.api_key)}`, data.api_key, true);
                }
            } else {
                showCurrentApiKey(window.t ? window.t('api.currentNoApiKey') : '当前暂未设置API Key', '', false);
            }

            // 辅助函数：设置输入框的值和占位符
            function setInputValue(elementId, value, placeholder) {
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
                if (data.api_key === 'free-access' || data.coreApi === 'free' || data.assistApi === 'free') {
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

            // Sync the core API key into the Key Book for the selected core provider
            // so autoFillCoreApiKey() can find it later
            if (data.coreApi && data.coreApi !== 'free' && data.api_key && data.api_key !== 'free-access') {
                syncKeyToBook(data.coreApi, data.api_key);
            }

            // Load all assist API keys into Key Book inputs
            // Use api_key_registry as single source of truth for field mapping
            Object.keys(_apiKeyRegistry).forEach(providerKey => {
                if (providerKey === 'free') return;
                const dataField = _apiKeyRegistry[providerKey].config_field;
                if (!dataField || !data.hasOwnProperty(dataField)) return;
                const val = data[dataField];
                // 当前核心API服务商的Key已由上方 data.api_key 写入管理簿，
                // 此处跳过以免 assistApiKey* 字段的旧值覆盖核心Key
                if (providerKey === data.coreApi && data.api_key) return;
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

            // 加载 GPT-SoVITS 配置（优先使用显式启用状态，兼容旧配置）
            loadGptSovitsConfig(
                data.ttsModelUrl,
                data.ttsVoiceId,
                data.ttsModelId,
                data.ttsModelApiKey,
                data.gptsovitsEnabled,
            );

            // 加载MCPR_TOKEN
            setInputValue('mcpTokenInput', data.mcpToken);

            // Load *ModelProvider for each model type and apply
            _isLoadingSavedConfig = true;
            MODEL_TYPES.forEach(mt => {
                const providerField = `${mt}ModelProvider`;
                const sel = document.getElementById(providerField);
                if (!sel) return;

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
 * 优先使用显式 gptsovitsEnabled，旧配置再做有限兼容判断
 */
function loadGptSovitsConfig(ttsModelUrl, ttsVoiceId, ttsModelId = '', ttsModelApiKey = '', gptsovitsEnabled = null) {
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

    const hasExplicitEnabledFlag = typeof gptsovitsEnabled === 'boolean';
    const isLegacyEnabled = !hasExplicitEnabledFlag
        && !isDisabledWithConfig
        && looksLikeLegacyGptSovitsConfig(ttsModelUrl, ttsModelId, ttsModelApiKey);
    const isEnabled = !isDisabledWithConfig && (hasExplicitEnabledFlag ? gptsovitsEnabled : isLegacyEnabled);

    _loadedGptSovitsState = isDisabledWithConfig ? 'disabled' : (isEnabled ? 'enabled' : 'none');

    // 设置启用开关状态
    const enabledCheckbox = document.getElementById('gptsovitsEnabled');
    if (enabledCheckbox) {
        enabledCheckbox.checked = isEnabled;
    }
    toggleGptSovitsConfig();

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

/**
 * 从 GPT-SoVITS v3 配置字段组装 ttsModelUrl 和 ttsVoiceId
 * 返回 { url, voiceId } 或 null（如果未启用）
 */
function getGptSovitsConfig() {
    const enabled = document.getElementById('gptsovitsEnabled')?.checked;
    if (!enabled) return null;

    const config = getGptSovitsConfigForSave();
    if (config && config.url.startsWith('http')) return config;
    return null;
}

/**
 * 切换 GPT-SoVITS 配置区域的显示/隐藏
 */
function toggleGptSovitsConfig() {
    const enabled = document.getElementById('gptsovitsEnabled')?.checked;
    const configFields = document.getElementById('gptsovits-config-fields');
    if (configFields) {
        configFields.style.display = enabled ? 'block' : 'none';
    }
}

// ==================== 结束 GPT-SoVITS v3 配置相关函数 ====================

// 切换自定义API启用状态
function toggleCustomApi(skipAutoFill) {
    const enableCustomApi = document.getElementById('enableCustomApi');
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');
    const apiKeyInput = document.getElementById('apiKeyInput');

    const isCustomEnabled = enableCustomApi.checked;
    const isFreeVersion = coreApiSelect && coreApiSelect.value === 'free';

    // 禁用或启用相关控件
    const assistApiKeyInput = document.getElementById('assistApiKeyInput');
    if (isFreeVersion) {
        if (assistApiSelect) assistApiSelect.disabled = true;
        if (apiKeyInput) apiKeyInput.disabled = true;
        if (assistApiKeyInput) assistApiKeyInput.disabled = true;
        if (coreApiSelect) coreApiSelect.disabled = false;
    } else {
        if (coreApiSelect) coreApiSelect.disabled = false;
        if (assistApiSelect) assistApiSelect.disabled = false;
        if (apiKeyInput) apiKeyInput.disabled = false;
        if (assistApiKeyInput) assistApiKeyInput.disabled = false;
    }

    // 控制自定义API容器的折叠状态
    const customApiContainer = document.getElementById('custom-api-container');
    if (customApiContainer) {
        if (isCustomEnabled) {
            customApiContainer.style.display = 'block';
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
        // 重置 Provider 下拉为默认值（跟随核心API）并同步联动状态
        const providerEl = document.getElementById(`${mt}ModelProvider`);
        if (providerEl) {
            providerEl.value = 'follow_core';
            onCustomModelProviderChange(mt);
        }
    });

    // 清空 TTS Voice ID
    const ttsVoiceIdEl = document.getElementById('ttsVoiceId');
    if (ttsVoiceIdEl) ttsVoiceIdEl.value = '';

    // 取消勾选 GPT-SoVITS
    const gptsovitsEnabled = document.getElementById('gptsovitsEnabled');
    if (gptsovitsEnabled && gptsovitsEnabled.checked) {
        gptsovitsEnabled.checked = false;
        toggleGptSovitsConfig();
    }
    // 清空 GPT-SoVITS 隐藏字段并重置状态，防止保存时残留旧配置
    const gptsovitsApiUrlEl = document.getElementById('gptsovitsApiUrl');
    if (gptsovitsApiUrlEl) gptsovitsApiUrlEl.value = '';
    const gptsovitsVoiceIdEl = document.getElementById('gptsovitsVoiceId');
    if (gptsovitsVoiceIdEl) gptsovitsVoiceIdEl.value = '';
    _loadedGptSovitsState = 'none';
    _ttsConfigDirty = true;

    // 取消勾选自定义API开关（skipAutoFill=true 避免覆盖未保存的核心/辅助API输入）
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

    e.preventDefault();

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

    // 处理API Key（优先读取真实 key）
    let apiKey = getRealKey(apiKeyInput);
    if (isFreeVersionText(apiKey)) {
        apiKey = '';
    }

    // 读取辅助API Key
    const assistKeyInput = document.getElementById('assistApiKeyInput');
    const assistKeyVal = getRealKey(assistKeyInput);

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

    // 用输入框中的值覆盖管理簿中对应服务商的Key（不直接修改管理簿DOM，
    // 避免二次确认取消时管理簿已被污染）
    if (coreApi && coreApi !== 'free' && apiKey) {
        allBookKeys[coreApi] = apiKey;
    }
    if (assistApi && assistApi !== 'free' && assistKeyVal) {
        allBookKeys[assistApi] = assistKeyVal;
    }

    // 获取用户自定义API配置
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
    const gptsovitsEnabled = document.getElementById('gptsovitsEnabled')?.checked;
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
    } else if (!gptsovitsEnabled && _loadedGptSovitsState !== 'none' && !_ttsConfigDirty) {
        if (gptsovitsConfigForSave) {
            ttsVoiceId = `__gptsovits_disabled__|${gptsovitsConfigForSave.url}|${gptsovitsConfigForSave.voiceId}`;
        }
        ttsModelUrl = '';
    }

    const mcpToken = getVal('mcpTokenInput');

    const apiKeyForSave = (coreApi === 'free' || assistApi === 'free') ? 'free-access' : apiKey;

    // 免费版和启用自定义API时不需要API Key检查
    if (!enableCustomApi && coreApi !== 'free' && assistApi !== 'free' && !apiKey) {
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

    // Build payload — map book keys to config field names via registry.
    // Only include providers present in allBookKeys (skips restricted/hidden ones).
    const bookPayload = {};
    Object.keys(allBookKeys).forEach(pk => {
        const field = (_apiKeyRegistry[pk] || {}).config_field;
        if (field) {
            bookPayload[field] = allBookKeys[pk];
        }
    });

    const payload = {
        apiKey: apiKeyForSave, coreApi, assistApi,
        ...bookPayload,
        conversationModelUrl, conversationModelId, conversationModelApiKey,
        summaryModelUrl, summaryModelId, summaryModelApiKey,
        correctionModelUrl, correctionModelId, correctionModelApiKey,
        emotionModelUrl, emotionModelId, emotionModelApiKey,
        visionModelUrl, visionModelId, visionModelApiKey,
        agentModelUrl, agentModelId, agentModelApiKey,
        omniModelUrl, omniModelId, omniModelApiKey,
        ttsModelUrl, ttsModelId, ttsModelApiKey, ttsVoiceId,
        mcpToken, enableCustomApi, gptsovitsEnabled,
        ...modelProviders
    };

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


async function saveApiKey(params) {
    const { apiKey, coreApi, assistApi, enableCustomApi } = params;

    // 统一处理免费版 API Key 的保存值
    let finalApiKey = apiKey;
    if (coreApi === 'free' || assistApi === 'free') {
        finalApiKey = 'free-access';
    }

    // 确保apiKey是有效的字符串
    if (!enableCustomApi && coreApi !== 'free' && assistApi !== 'free' && (!finalApiKey || typeof finalApiKey !== 'string')) {
        showStatus(window.t ? window.t('api.apiKeyInvalid') : 'API Key无效', 'error');
        return;
    }

    try {
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

    const assistApiKeyInput = document.getElementById('assistApiKeyInput');

    if (selectedCoreApi === 'free') {
        if (apiKeyInput) {
            apiKeyInput.disabled = true;
            apiKeyInput.placeholder = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
            apiKeyInput.required = false;
            apiKeyInput.value = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
        }
        if (assistApiKeyInput) {
            assistApiKeyInput.disabled = true;
            assistApiKeyInput.value = '';
        }
        if (freeVersionHint) {
            freeVersionHint.style.display = 'inline';
        }

        // 禁用辅助API选择框，强制为免费版
        assistApiSelect.disabled = true;
        assistApiSelect.value = 'free';
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
        if (assistApiKeyInput) {
            assistApiKeyInput.disabled = false;
        }
        if (freeVersionHint) {
            freeVersionHint.style.display = 'none';
        }

        // 启用辅助API选择框
        assistApiSelect.disabled = false;
        const freeOption = assistApiSelect.querySelector('option[value="free"]');
        if (freeOption) {
            freeOption.disabled = true;
            freeOption.textContent = window.t ? window.t('api.freeVersionOnlyWhenCoreFree') : '免费版（仅核心API为免费版时可用）';
        }
        // If assist is still stuck on 'free' (now disabled), switch to a valid provider
        if (assistApiSelect.value === 'free') {
            // Prefer qwen as default, otherwise pick first non-free enabled option
            const qwenOpt = assistApiSelect.querySelector('option[value="qwen"]');
            if (qwenOpt && !qwenOpt.disabled) {
                assistApiSelect.value = 'qwen';
            } else {
                const validOpt = Array.from(assistApiSelect.options).find(o => !o.disabled && o.value !== 'free');
                if (validOpt) assistApiSelect.value = validOpt.value;
            }
            autoFillAssistApiKey(true);
            // Directly recompute follow_assist slots (avoid redundant handler call)
            MODEL_TYPES.forEach(mt => {
                const sel = document.getElementById(`${mt}ModelProvider`);
                if (sel && sel.value === 'follow_assist') {
                    onCustomModelProviderChange(mt);
                }
            });
        }
    }

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
        return;
    }
    // Non-forced: only fill if book has a value
    if (bookKey !== null && bookKey !== '') {
        setMaskedInput(apiKeyInput, bookKey);
        attachMaskBehavior(apiKeyInput);
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
        setMaskedInput(assistApiKeyInput, '');
        attachMaskBehavior(assistApiKeyInput);
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

// 二级折叠功能：切换模型配置的展开/折叠状态
function toggleModelConfig(modelType) {
    const content = document.getElementById(`${modelType}-model-content`);
    if (!content) return;

    const header = content.previousElementSibling;
    if (!header) return;

    const icon = header.querySelector('.toggle-icon');
    if (!icon) return;

    if (content.classList.contains('expanded')) {
        content.classList.remove('expanded');
        icon.style.transform = 'rotate(0deg)';
        header.setAttribute('aria-expanded', 'false');
        content.setAttribute('aria-hidden', 'true');
    } else {
        content.classList.add('expanded');
        icon.style.transform = 'rotate(180deg)';
        header.setAttribute('aria-expanded', 'true');
        content.setAttribute('aria-hidden', 'false');
    }
}

// 页面加载完成后初始化折叠状态
document.addEventListener('DOMContentLoaded', function () {
    // 初始化所有模型配置为折叠状态
    const modelTypes = ["conversation", 'summary', 'correction', 'emotion', 'vision', 'agent', 'omni', 'tts', 'gptsovits'];
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
        toggleCustomApi();
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
    }

    // 将灯和 input 包在一个水平 flex 容器中，确保同行对齐
    if (inputElement && inputElement.parentNode) {
        const wrapper = document.createElement('div');
        wrapper.className = 'connectivity-input-row';
        inputElement.parentNode.insertBefore(wrapper, inputElement);
        wrapper.appendChild(light);
        wrapper.appendChild(inputElement);
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
    lightElement.title = window.t ? window.t(tooltipKey, fallback) : fallback;
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
        const result = { key: '', url: '', providerType: 'openai_compatible', providerKey: '', providerScope: '' };

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
                result.url = coreProfile.core_url || '';
                result.key = 'free-access';
                result.providerType = 'websocket';
            } else {
                const coreProfile = _coreApiProviders[coreProvider] || {};
                result.url = coreProfile.core_url || '';
                result.key = getRealKey(apiKeyInput);
                result.providerType = 'websocket';
            }
            return result;
        }

        if (context.type === 'assist') {
            const assistProvider = assistApiSelect ? assistApiSelect.value : '';
            result.providerKey = assistProvider;
            result.providerScope = 'assist';
            if (assistProvider === 'free') {
                const assistProfile = _assistApiProviders['free'] || {};
                result.url = assistProfile.openrouter_url || '';
                result.key = 'free-access';
                result.providerType = 'openai_compatible';
            } else {
                const assistProfile = _assistApiProviders[assistProvider] || {};
                result.url = assistProfile.openrouter_url || '';
                // 优先从输入框读取，其次从 Key Book
                const inputKey = getRealKey(assistApiKeyInput);
                if (inputKey && !isFreeVersionText(inputKey)) {
                    result.key = inputKey;
                } else {
                    const bookKey = syncKeyFromBook(assistProvider);
                    result.key = (bookKey !== null) ? bookKey : '';
                }
                result.providerType = 'openai_compatible';
            }
            return result;
        }

        if (context.type === 'custom' && context.modelType) {
            const mt = context.modelType;
            const providerSel = document.getElementById(`${mt}ModelProvider`);
            const urlInput = document.getElementById(`${mt}ModelUrl`);
            const keyInput = document.getElementById(`${mt}ModelApiKey`);

            if (!providerSel) return result;

            const provider = providerSel.value;
            result.url = urlInput ? urlInput.value.trim() : '';

            if (provider === 'follow_core') {
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
                    result.url = pInfo.openrouter_url || pInfo.core_url || '';
                    result.key = coreResult.key;
                    result.providerType = 'openai_compatible';
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
            } else {
                // 指定服务商：从 Key Book 读取
                const bookKey = syncKeyFromBook(provider);
                result.key = (bookKey !== null) ? bookKey : '';
                if (mt === 'omni') {
                    const coreProfile = _coreApiProviders[provider] || {};
                    result.url = coreProfile.core_url || '';
                    result.providerType = 'websocket';
                    result.providerKey = provider;
                    result.providerScope = 'core';
                } else {
                    const pInfo = _assistApiProviders[provider] || _coreApiProviders[provider] || {};
                    result.url = pInfo.openrouter_url || pInfo.core_url || '';
                    result.providerType = 'openai_compatible';
                    result.providerKey = provider;
                    result.providerScope = 'assist';
                }
            }

            return result;
        }

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
        const { provider_key, provider_scope, url, api_key: apiKey, model, provider_type: providerType, is_free: isFree } = params;
        console.log('[ConnectivityManager] testKey called:', {
            provider_key: provider_key || '(custom)',
            provider_scope: provider_scope || '(none)',
            hasUrl: !!url,
            hasKey: !!apiKey,
            model: model || '(default)',
        });
        // 取消同一 Key 的前一次未完成请求
        const cacheKey = `${provider_key || url}|${apiKey || ''}`;
        if (this._abortControllers[cacheKey]) {
            this._abortControllers[cacheKey].abort();
        }

        const controller = new AbortController();
        this._abortControllers[cacheKey] = controller;

        // 前端 15 秒超时
        const timeoutId = setTimeout(() => controller.abort(), 15000);

        try {
            // Build request body based on mode
            const body = { api_key: apiKey || '' };
            if (provider_key && provider_scope) {
                // Built-in provider mode
                body.provider_key = provider_key;
                body.provider_scope = provider_scope;
            } else {
                // Custom API mode
                body.url = url || '';
                body.model = model || '';
                body.provider_type = providerType || 'openai_compatible';
                body.is_free = !!isFree;
            }

            const response = await fetch('/api/config/test_connectivity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal: controller.signal
            });

            clearTimeout(timeoutId);
            // Only delete if map still points to this controller (avoid race with newer request)
            if (this._abortControllers[cacheKey] === controller) {
                delete this._abortControllers[cacheKey];
            }

            if (!response.ok) {
                return {
                    success: false,
                    error: `HTTP ${response.status}`,
                    error_code: 'backend_unavailable'
                };
            }

            const data = await response.json();
            return {
                success: !!data.success,
                error: data.error || null,
                error_code: data.error_code || null
            };
        } catch (err) {
            clearTimeout(timeoutId);
            if (this._abortControllers[cacheKey] === controller) {
                delete this._abortControllers[cacheKey];
            }

            if (err.name === 'AbortError') {
                return {
                    success: false,
                    error: 'Request timed out or was cancelled',
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
        const coreCacheId = coreResult.key || coreResult.url;
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
        const assistCacheId = assistResult.key || assistResult.url;
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
                const customCacheId = customResult.key || customResult.url;
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
                        provider_type: customResult.providerType, is_free: isFree
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

        // 并发测试所有唯一配置
        const testPromises = Object.entries(keyConfigs).map(async ([cacheId, config]) => {
            const result = await this.testKey(config);
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
                const cacheId = customResult.key || customResult.url;
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
                        provider_type: customResult.providerType, is_free: isFree
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
            const result = await this.testKey(config);
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

        // 用于注册和缓存的标识：优先用 key，无 key 时用 URL（本地服务场景）
        const cacheId = key || url;

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
            if (resolved.key) {
                const coreSelect = document.getElementById('coreApiSelect');
                const isFree = coreSelect && coreSelect.value === 'free';
                ConnectivityManager.keyStatusMap[resolved.key] = LightStatus.TESTING;
                ConnectivityManager.keyErrorMap[resolved.key] = null;
                ConnectivityManager.syncLightsForKey(resolved.key);
                ConnectivityManager.syncErrorDisplaysForKey(resolved.key);
                const result = await ConnectivityManager.testKey({
                    provider_key: resolved.providerKey, provider_scope: resolved.providerScope,
                    url: resolved.url, api_key: resolved.key, provider_type: resolved.providerType, is_free: isFree
                });
                if (result.success) {
                    ConnectivityManager.keyStatusMap[resolved.key] = LightStatus.CONNECTED;
                    ConnectivityManager.keyErrorMap[resolved.key] = null;
                } else {
                    ConnectivityManager.keyStatusMap[resolved.key] = LightStatus.FAILED;
                    ConnectivityManager.keyErrorMap[resolved.key] = { error_code: result.error_code || 'unknown', error: result.error || '' };
                }
                ConnectivityManager.syncLightsForKey(resolved.key);
                ConnectivityManager.syncErrorDisplaysForKey(resolved.key);
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
            if (resolved.key) {
                const assistSelect = document.getElementById('assistApiSelect');
                const isFree = assistSelect && assistSelect.value === 'free';
                ConnectivityManager.keyStatusMap[resolved.key] = LightStatus.TESTING;
                ConnectivityManager.keyErrorMap[resolved.key] = null;
                ConnectivityManager.syncLightsForKey(resolved.key);
                ConnectivityManager.syncErrorDisplaysForKey(resolved.key);
                const result = await ConnectivityManager.testKey({
                    provider_key: resolved.providerKey, provider_scope: resolved.providerScope,
                    url: resolved.url, api_key: resolved.key, provider_type: resolved.providerType, is_free: isFree
                });
                if (result.success) {
                    ConnectivityManager.keyStatusMap[resolved.key] = LightStatus.CONNECTED;
                    ConnectivityManager.keyErrorMap[resolved.key] = null;
                } else {
                    ConnectivityManager.keyStatusMap[resolved.key] = LightStatus.FAILED;
                    ConnectivityManager.keyErrorMap[resolved.key] = { error_code: result.error_code || 'unknown', error: result.error || '' };
                }
                ConnectivityManager.syncLightsForKey(resolved.key);
                ConnectivityManager.syncErrorDisplaysForKey(resolved.key);
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
            const summaryLight = document.createElement('span');
            summaryLight.className = 'connectivity-summary-light';
            summaryLight.dataset.status = LightStatus.NOT_CONFIGURED;
            summaryLight.dataset.modelType = mt;
            const modelLabel = window.t ? window.t(`model.${mt}`, mt) : mt;
            summaryLight.title = modelLabel;
            summaryRow.appendChild(summaryLight);
        });

        // Wrap button and summary lights in a container
        const btnWrapper = document.createElement('div');
        btnWrapper.className = 'connectivity-test-btn-wrapper';
        btnWrapper.style.display = 'flex';
        btnWrapper.style.alignItems = 'center';
        btnWrapper.style.marginBottom = '12px';

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
            summaryLight.title = (window.t ? window.t(`model.${mt}`, mt) : mt) + ' - ' + (light.title || '');
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
                    const oldCustomKey = customCurrentKeys[mt];
                    customCurrentKeys[mt] = reRegister(
                        lightRefs.custom[mt].light, lightRefs.custom[mt].errorDisplay,
                        { type: 'custom', modelType: mt }, oldCustomKey,
                        lightRefs.custom[mt].summaryLight
                    );
                }
            });
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
                    const oldCustomKey = customCurrentKeys[mt];
                    customCurrentKeys[mt] = reRegister(
                        lightRefs.custom[mt].light, lightRefs.custom[mt].errorDisplay,
                        { type: 'custom', modelType: mt }, oldCustomKey,
                        lightRefs.custom[mt].summaryLight
                    );
                }
            });
        }, 300);

        assistApiKeyInput.addEventListener('input', handleAssistKeyChange);
        assistApiKeyInput.addEventListener('change', handleAssistKeyChange);
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
            // 新 key 不需要 cascadeReset — reRegister 已经从缓存正确恢复了状态
        }, 300);

        keyInput.addEventListener('input', handleCustomKeyChange);
        keyInput.addEventListener('change', handleCustomKeyChange);
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
                    const oldCustomKey = customCurrentKeys[mt];
                    customCurrentKeys[mt] = reRegister(
                        lightRefs.custom[mt].light, lightRefs.custom[mt].errorDisplay,
                        { type: 'custom', modelType: mt }, oldCustomKey,
                        lightRefs.custom[mt].summaryLight
                    );
                }
            });
        });
    }

    // Assist API provider change
    if (assistApiSelect) {
        assistApiSelect.addEventListener('change', () => {
            const oldKey = assistCurrentKey;
            assistCurrentKey = reRegister(
                lightRefs.assist.light, lightRefs.assist.errorDisplay,
                { type: 'assist' }, oldKey
            );
            // Also re-register custom models that follow_assist
            CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
                const providerSel = document.getElementById(`${mt}ModelProvider`);
                if (providerSel && providerSel.value === 'follow_assist' && lightRefs.custom[mt]) {
                    const oldCustomKey = customCurrentKeys[mt];
                    customCurrentKeys[mt] = reRegister(
                        lightRefs.custom[mt].light, lightRefs.custom[mt].errorDisplay,
                        { type: 'custom', modelType: mt }, oldCustomKey,
                        lightRefs.custom[mt].summaryLight
                    );
                }
            });
        });
    }

    // Custom model provider dropdown changes
    CONNECTIVITY_TESTABLE_TYPES.forEach(mt => {
        const providerSel = document.getElementById(`${mt}ModelProvider`);
        if (!providerSel || !lightRefs.custom[mt]) return;

        providerSel.addEventListener('change', () => {
            const oldKey = customCurrentKeys[mt];
            customCurrentKeys[mt] = reRegister(
                lightRefs.custom[mt].light, lightRefs.custom[mt].errorDisplay,
                { type: 'custom', modelType: mt }, oldKey,
                lightRefs.custom[mt].summaryLight
            );
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

            const assistApiKeyInputInit = document.getElementById('assistApiKeyInput');
            if (selectedCoreApi === 'free') {
                apiKeyInput.disabled = true;
                apiKeyInput.placeholder = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
                apiKeyInput.required = false;
                apiKeyInput.value = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
                if (assistApiKeyInputInit) assistApiKeyInputInit.disabled = true;
                freeVersionHint.style.display = 'inline';
            } else {
                apiKeyInput.disabled = false;
                apiKeyInput.placeholder = window.t ? window.t('api.pleaseEnterApiKey') : '请输入您的API Key';
                apiKeyInput.required = true;
                if (isFreeVersionText(getRealKey(apiKeyInput))) {
                    setMaskedInput(apiKeyInput, '');
                }
                if (assistApiKeyInputInit) assistApiKeyInputInit.disabled = false;
                freeVersionHint.style.display = 'none';
            }

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
