/**
 * API 密钥设置模块
 * 负责处理 API 密钥的存储、验证和显示
 * 包含对中国大陆用户的特殊处理
 */
// 全局变量：是否为中国大陆用户
let isMainlandChinaUser = false;

// API Key 管理簿注册表（从后端加载）
let _apiKeyRegistry = {};
// 辅助API服务商完整信息（从后端加载）
let _assistApiProviders = {};
// 核心API服务商完整信息（从后端加载）
let _coreApiProviders = {};
// 所有模型类型
const MODEL_TYPES = ['conversation', 'summary', 'correction', 'emotion', 'vision', 'agent', 'omni', 'tts'];

/**
 * 遮蔽 API Key：只显示前6位和后6位，中间用 *** 替代。
 * 短于14位的 key 原样返回（不够遮蔽）。
 */
function maskApiKey(key) {
    if (!key || typeof key !== 'string') return key;
    if (key.length < 14) return key;
    return key.slice(0, 6) + '***' + key.slice(-6);
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
 * 读取 input 中存储的真实 key（优先 dataset，否则 value）。
 */
function getRealKey(input) {
    if (!input) return '';
    // 如果用户直接编辑了（没有 realKey 或 focus 中），使用 value
    if (input.dataset.realKey !== undefined && input.dataset.realKey !== '') {
        return input.dataset.realKey;
    }
    return input.value.trim();
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
}

// 等待下拉选项加载完成再设置值，避免单次 setTimeout 竞态
function waitForOptions(select, targetValue, { maxAttempts = 20, interval = 50 } = {}) {
    if (!select || !targetValue) return;

    let attempts = 0;
    const checkAndSet = () => {
        if (select.options.length > 0) {
            const optionExists = Array.from(select.options).some(opt => opt.value === targetValue);
            if (optionExists) {
                select.value = targetValue;
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
                            restricted: (pk === 'openai' || pk === 'gemini'),
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
            if (apiKeyInput && data.api_key) {
                if (data.api_key === 'free-access' || data.coreApi === 'free' || data.assistApi === 'free') {
                    // 免费版本：显示用户友好的文本
                    apiKeyInput.value = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
                } else {
                    setMaskedInput(apiKeyInput, data.api_key);
                    attachMaskBehavior(apiKeyInput);
                }
            }
            // 设置高级设定的值（确保下拉框已加载选项）
            if (data.coreApi && coreApiSelect) {
                if (coreApiSelect.options.length > 0) {
                    // 验证选项值是否存在
                    const optionExists = Array.from(coreApiSelect.options).some(opt => opt.value === data.coreApi);
                    if (optionExists) {
                        coreApiSelect.value = data.coreApi;
                    }
                } else {
                    waitForOptions(coreApiSelect, data.coreApi);
                }
            }
            if (data.assistApi && assistApiSelect) {
                if (assistApiSelect.options.length > 0) {
                    // 验证选项值是否存在
                    const optionExists = Array.from(assistApiSelect.options).some(opt => opt.value === data.assistApi);
                    if (optionExists) {
                        assistApiSelect.value = data.assistApi;
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
                // Skip empty-string values for the current core provider so we
                // don't overwrite the valid key synced from data.api_key above.
                if (val === '' && providerKey === data.coreApi) return;
                // Only sync non-empty values; empty strings from the backend
                // usually mean "not configured" rather than "intentionally cleared".
                if (val !== '') {
                    syncKeyToBook(providerKey, val);
                }
            });

            // Set assist key input from selected assist provider's book input
            if (data.assistApi && data.assistApi !== 'free') {
                const assistBookKey = syncKeyFromBook(data.assistApi);
                if (assistApiKeyInput && assistBookKey) {
                    setMaskedInput(assistApiKeyInput, assistBookKey);
                    attachMaskBehavior(assistApiKeyInput);
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

            // 加载 GPT-SoVITS 配置（从 ttsModelUrl 和 ttsVoiceId 解析）
            loadGptSovitsConfig(data.ttsModelUrl, data.ttsVoiceId);

            // 加载MCPR_TOKEN
            setInputValue('mcpTokenInput', data.mcpToken);

            // Load *ModelProvider for each model type and apply
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

            // 加载自定义API启用状态
            if (typeof data.enableCustomApi === 'boolean' && document.getElementById('enableCustomApi')) {
                document.getElementById('enableCustomApi').checked = data.enableCustomApi;
                // 延迟应用状态，确保API Key已正确加载
                setTimeout(() => {
                    toggleCustomApi();
                }, 100);
            }
        } else {
            showCurrentApiKey(window.t ? window.t('api.getCurrentApiKeyFailed') : '获取当前API Key失败', '', false);
        }
    } catch (error) {
        console.error('loadCurrentApiKey error:', error);
        showCurrentApiKey(window.t ? window.t('api.errorGettingCurrentApiKey') : '获取当前API Key时出错', '', false);
    }
}

// 全局变量存储待保存的API Key
let pendingApiKey = null;

// ==================== GPT-SoVITS v3 配置相关函数 ====================

/**
 * 从 ttsModelUrl 和 ttsVoiceId 解析并加载 GPT-SoVITS v3 配置
 * v3 voice_id 格式: "voice_id" 或 "voice_id|高级参数JSON"
 * 特殊格式：__gptsovits_disabled__|url|voiceId 表示禁用但保存了配置
 */
function loadGptSovitsConfig(ttsModelUrl, ttsVoiceId) {
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

    // 检查是否是 GPT-SoVITS 配置（HTTP URL）
    const isGptSovits = ttsModelUrl && (ttsModelUrl.startsWith('http://') || ttsModelUrl.startsWith('https://'));

    // 设置启用开关状态
    const enabledCheckbox = document.getElementById('gptsovitsEnabled');
    if (enabledCheckbox) {
        enabledCheckbox.checked = isGptSovits && !isDisabledWithConfig;
    }
    toggleGptSovitsConfig();

    // 确定要加载的配置
    const urlToLoad = isGptSovits ? ttsModelUrl : (isDisabledWithConfig ? savedUrl : '');
    const voiceIdToLoad = isGptSovits ? ttsVoiceId : (isDisabledWithConfig ? savedVoiceId : '');

    if (urlToLoad || voiceIdToLoad) {
        const apiUrlEl = document.getElementById('gptsovitsApiUrl');
        if (apiUrlEl && urlToLoad) apiUrlEl.value = urlToLoad;

        // 设置隐藏 input 的值（卡片高亮会在 fetchGptSovitsVoices 完成后自动匹配）
        if (voiceIdToLoad) {
            const hiddenInput = document.getElementById('gptsovitsVoiceId');
            if (hiddenInput) hiddenInput.value = voiceIdToLoad;
        }

        // 自动获取语音列表（如果有 URL）
        const autoUrl = urlToLoad || document.getElementById('gptsovitsApiUrl')?.value.trim();
        if (autoUrl) {
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
function toggleCustomApi() {
    const enableCustomApi = document.getElementById('enableCustomApi');
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');
    const apiKeyInput = document.getElementById('apiKeyInput');

    const isCustomEnabled = enableCustomApi.checked;
    const isFreeVersion = coreApiSelect && coreApiSelect.value === 'free';

    // 禁用或启用相关控件
    if (isFreeVersion) {
        if (assistApiSelect) assistApiSelect.disabled = true;
        if (apiKeyInput) apiKeyInput.disabled = true;
        if (coreApiSelect) coreApiSelect.disabled = false;
    } else {
        if (coreApiSelect) coreApiSelect.disabled = false;
        if (assistApiSelect) assistApiSelect.disabled = false;
        if (apiKeyInput) apiKeyInput.disabled = false;
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

// 为自定义API开关添加事件监听器
document.addEventListener('DOMContentLoaded', function () {
    const enableCustomApi = document.getElementById('enableCustomApi');
    if (enableCustomApi) {
        enableCustomApi.addEventListener('change', toggleCustomApi);
    }

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

    // Sync core key to book
    if (coreApi && coreApi !== 'free') {
        syncKeyToBook(coreApi, apiKey);
    }

    // Sync assist key to book (including empty string to clear)
    const assistKeyInput = document.getElementById('assistApiKeyInput');
    const assistKeyVal = getRealKey(assistKeyInput);
    if (assistApi && assistApi !== 'free') {
        syncKeyToBook(assistApi, assistKeyVal);
    }

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

    // 获取用户自定义API配置
    const getVal = (id) => {
        const el = document.getElementById(id);
        return el ? el.value.trim() : '';
    };

    const conversationModelUrl = getVal('conversationModelUrl');
    const conversationModelId = getVal('conversationModelId');
    const conversationModelApiKey = getVal('conversationModelApiKey');

    const summaryModelUrl = getVal('summaryModelUrl');
    const summaryModelId = getVal('summaryModelId');
    const summaryModelApiKey = getVal('summaryModelApiKey');

    const correctionModelUrl = getVal('correctionModelUrl');
    const correctionModelId = getVal('correctionModelId');
    const correctionModelApiKey = getVal('correctionModelApiKey');

    const emotionModelUrl = getVal('emotionModelUrl');
    const emotionModelId = getVal('emotionModelId');
    const emotionModelApiKey = getVal('emotionModelApiKey');

    const visionModelUrl = getVal('visionModelUrl');
    const visionModelId = getVal('visionModelId');
    const visionModelApiKey = getVal('visionModelApiKey');
    const agentModelUrl = getVal('agentModelUrl');
    const agentModelId = getVal('agentModelId');
    const agentModelApiKey = getVal('agentModelApiKey');

    const omniModelUrl = getVal('omniModelUrl');
    const omniModelId = getVal('omniModelId');
    const omniModelApiKey = getVal('omniModelApiKey');

    let ttsModelUrl = getVal('ttsModelUrl');
    const ttsModelId = getVal('ttsModelId');
    const ttsModelApiKey = getVal('ttsModelApiKey');
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
    } else if (!gptsovitsEnabled) {
        if (ttsModelUrl && (ttsModelUrl.startsWith('http://') || ttsModelUrl.startsWith('https://'))) {
            if (gptsovitsConfigForSave) {
                ttsVoiceId = `__gptsovits_disabled__|${gptsovitsConfigForSave.url}|${gptsovitsConfigForSave.voiceId}`;
            }
            ttsModelUrl = '';
        }
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
        mcpToken, enableCustomApi,
        ...modelProviders
    };

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

    if (selectedCoreApi === 'free') {
        if (apiKeyInput) {
            apiKeyInput.disabled = true;
            apiKeyInput.placeholder = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
            apiKeyInput.required = false;
            apiKeyInput.value = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
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
            autoFillAssistApiKey();
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
    if (bookKey !== null) {
        setMaskedInput(apiKeyInput, bookKey);
        attachMaskBehavior(apiKeyInput);
        return;
    }

    // No book input for this provider (restricted/hidden) — clear old value first
    setMaskedInput(apiKeyInput, '');

    // Fallback: use saved key from dataset (if available and not free)
    const currentApiKeyDiv = document.getElementById('current-api-key');
    if (currentApiKeyDiv && currentApiKeyDiv.dataset.hasKey === 'true') {
        const savedKey = currentApiKeyDiv.dataset.apiKey;
        if (savedKey && savedKey !== 'free-access') {
            setMaskedInput(apiKeyInput, savedKey);
            attachMaskBehavior(apiKeyInput);
        }
    }
}

// Auto-fill assist API key from book
function autoFillAssistApiKey() {
    const assistApiSelect = document.getElementById('assistApiSelect');
    const assistApiKeyInput = document.getElementById('assistApiKeyInput');
    if (!assistApiSelect || !assistApiKeyInput) return;

    const selectedAssistApi = assistApiSelect.value;
    if (selectedAssistApi === 'free') {
        setMaskedInput(assistApiKeyInput, '');
        return;
    }

    const bookKey = syncKeyFromBook(selectedAssistApi);
    setMaskedInput(assistApiKeyInput, bookKey || '');
    attachMaskBehavior(assistApiKeyInput);
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

            updateAssistApiRecommendation();
            autoFillCoreApiKey(true);
            autoFillAssistApiKey();
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
                autoFillAssistApiKey();
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

            // Snapshot Key Book input values
            const keyBookSnapshot = {};
            const bookContainer = document.getElementById('key-book-inputs');
            if (bookContainer) {
                bookContainer.querySelectorAll('input[data-provider-key]').forEach(input => {
                    keyBookSnapshot[input.dataset.providerKey] = input.value;
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

            // Restore Key Book input values
            Object.keys(keyBookSnapshot).forEach(providerKey => {
                syncKeyToBook(providerKey, keyBookSnapshot[providerKey]);
            });

            // Restore model provider select values
            MODEL_TYPES.forEach(mt => {
                const sel = document.getElementById(`${mt}ModelProvider`);
                if (sel && modelProviderSnapshot[mt] !== undefined) {
                    sel.value = modelProviderSnapshot[mt];
                }
            });
        });

        if (loadingOverlay) {
            loadingOverlay.style.display = 'none';
        }

        window.apiKeySettingsInitialized = true;

        setTimeout(() => {
            toggleCustomApi();
        }, 0);

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
