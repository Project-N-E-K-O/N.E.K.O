
// 全局变量：是否为中国大陆用户
let isMainlandChinaUser = false;

// 需要对大陆用户隐藏的 API 服务商
const RESTRICTED_PROVIDERS = ['openai', 'gemini'];

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

// 隐藏大陆用户不可用的辅助 API Key 输入框
function hideRestrictedAssistApiKeyInputs() {
    if (!isMainlandChinaUser) return;

    // 隐藏 OpenAI 和 Gemini 辅助 API Key 输入框
    const restrictedInputIds = ['assistApiKeyInputOpenai', 'assistApiKeyInputGemini'];
    restrictedInputIds.forEach(inputId => {
        const inputRow = document.getElementById(inputId)?.closest('.field-row');
        if (inputRow) {
            inputRow.style.display = 'none';
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

// 加载API服务商选项
async function loadApiProviders() {
    try {
        const response = await fetch('/api/config/api_providers');
        if (response.ok) {
            const data = await response.json();
            if (data.success) {
                // 填充核心API下拉框
                const coreSelect = document.getElementById('coreApiSelect');
                if (coreSelect) {
                    coreSelect.innerHTML = ''; // 清空现有选项
                    const coreList = Array.isArray(data.core_api_providers) ? data.core_api_providers : [];
                    coreList.forEach(provider => {
                        // 如果是大陆用户，过滤掉受限的服务商
                        if (isMainlandChinaUser && RESTRICTED_PROVIDERS.includes(provider.key)) {
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
                        if (isMainlandChinaUser && RESTRICTED_PROVIDERS.includes(provider.key)) {
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

                // 隐藏大陆用户不可用的辅助 API Key 输入框
                hideRestrictedAssistApiKeyInputs();

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

    if (apiKeyInput) {
        apiKeyInput.value = '';
    }
    if (coreApiSelect) {
        coreApiSelect.value = '';
    }
    if (assistApiSelect) {
        assistApiSelect.value = '';
    }

    try {
        const response = await fetch('/api/config/core_api');
        if (response.ok) {
            const data = await response.json();
            // 设置API Key显示
            if (data.enableCustomApi) {
                showCurrentApiKey(window.t ? window.t('api.currentUsingCustomApi') : '🔧 当前使用：自定义API模式', '', true);
            } else if (data.api_key) {
                if (data.api_key === 'free-access' || data.coreApi === 'free' || data.assistApi === 'free') {
                    showCurrentApiKey(window.t ? window.t('api.currentUsingFreeVersion') : '当前使用：免费版（无需API Key）', 'free-access', true);
                } else {
                    showCurrentApiKey(window.t ? window.t('api.currentApiKey', { key: data.api_key }) : `当前API Key: ${data.api_key}`, data.api_key, true);
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
                    apiKeyInput.value = data.api_key;
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
            const assistApiKeyPlaceholder = window.t ? window.t('api.assistApiKeyPlaceholder') : '可选，默认为核心API Key';
            setInputValue('assistApiKeyInputQwen', data.assistApiKeyQwen, assistApiKeyPlaceholder);
            setInputValue('assistApiKeyInputOpenai', data.assistApiKeyOpenai, assistApiKeyPlaceholder);
            setInputValue('assistApiKeyInputGlm', data.assistApiKeyGlm, assistApiKeyPlaceholder);
            setInputValue('assistApiKeyInputStep', data.assistApiKeyStep, assistApiKeyPlaceholder);
            setInputValue('assistApiKeyInputSilicon', data.assistApiKeySilicon, assistApiKeyPlaceholder);
            setInputValue('assistApiKeyInputGemini', data.assistApiKeyGemini, assistApiKeyPlaceholder);
            setInputValue('assistApiKeyInputKimi', data.assistApiKeyKimi, assistApiKeyPlaceholder);

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
    grid.innerHTML = '<div class="gsv-voices-loading">⏳ ' + _escHtml(window.t ? window.t('api.loadingConfig') : '正在加载...') + '</div>';

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
            grid.innerHTML = '<div class="gsv-voices-empty">' + _escHtml(result.error || (window.t ? window.t('api.gptsovitsVoicesLoadFailed') : '获取语音列表失败')) + '</div>';
            if (!silent) {
                showStatus(result.error || (window.t ? window.t('api.gptsovitsVoicesLoadFailed') : '获取语音列表失败'), 'error');
            }
        }
    } catch (e) {
        grid.innerHTML = '<div class="gsv-voices-empty">❌ ' + _escHtml(window.t ? window.t('api.gptsovitsVoicesLoadFailed') : '获取语音列表失败') + '</div>';
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
    // 自定义API模式：不影响其他控件
    // 免费版本：只禁用API Key输入框和辅助API选择框，核心API选择框保持可用
    if (isFreeVersion) {
        // 免费版本：只禁用API Key输入框和辅助API选择框
        if (assistApiSelect) assistApiSelect.disabled = true;
        if (apiKeyInput) apiKeyInput.disabled = true;

        // 核心API选择框保持可用，以便用户可以切换回付费版本
        if (coreApiSelect) coreApiSelect.disabled = false;

        // 辅助API Key输入框保持可用，允许保存额外Key
        setAssistApiInputsDisabled(false);
    } else {
        // 付费版本：启用所有控件
        if (coreApiSelect) coreApiSelect.disabled = false;
        if (assistApiSelect) assistApiSelect.disabled = false;
        if (apiKeyInput) apiKeyInput.disabled = false;

        // 启用所有辅助API Key输入框（统一处理）
        setAssistApiInputsDisabled(false);
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
            // 自定义 API 已启用，显示对应提示（优先级最高）
            freeVersionHint.textContent = window.t ? window.t('api.customApiEnabledHint') : '（自定义API已启用）';
            freeVersionHint.style.color = '#ff6b35';
            freeVersionHint.style.display = 'inline';
        } else if (isFreeVersion) {
            // 仅当核心 API 真正为免费版时显示免费提示
            freeVersionHint.textContent = window.t ? window.t('api.freeVersionHint') : '（免费版无需填写）';
            freeVersionHint.style.color = '#28a745';
            freeVersionHint.style.display = 'inline';
        } else {
            // 其他情况隐藏提示，避免误导用户
            freeVersionHint.style.display = 'none';
        }
    }

    // 更新高级选项的提示
    const advancedTips = document.querySelector('#advanced-options > div:first-child');
    if (advancedTips) {
        if (isCustomEnabled) {
            advancedTips.innerHTML = `<strong>${window.t ? window.t('api.customApiEnabled') : ' 配置状态：'}</strong><br>• <strong>${window.t ? window.t('api.customApiEnabledDesc') : '自定义API已启用'}</strong><br>• ${window.t ? window.t('api.customApiEnabledNote') : '请在下方的自定义API配置中设置各功能模块的API'}`;
            advancedTips.style.background = '#e7f3ff';
            advancedTips.style.borderColor = '#b3d9ff';
            advancedTips.style.color = '#40C5F1';
            advancedTips.style.lineHeight = '1.6';
        } else {
            advancedTips.innerHTML = `<strong>${window.t ? window.t('api.configSuggestionFull') : '配置建议：'}</strong><br>• <strong>${window.t ? window.t('api.freeVersion') : '免费版'}</strong>：${window.t ? window.t('api.freeVersionSuggestionFull') : '完全免费，无需API Key，适合新手体验（不支持自定义语音、Agent模式和视频对话）'}<br>• <strong>${window.t ? window.t('api.coreApiProvider') : '核心API'}</strong>：${window.t ? window.t('api.coreApiSuggestionFull') : '负责对话功能，建议根据预算和需求选择'}<br>• <strong>${window.t ? window.t('api.assistApiProvider') : '辅助API'}</strong>：${window.t ? window.t('api.assistApiSuggestionFull') : '负责记忆管理和自定义语音，只有阿里支持自定义语音'}`;
            advancedTips.style.background = '#e7f3ff';
            advancedTips.style.borderColor = '#b3d9ff';
            advancedTips.style.color = '#40C5F1';
            advancedTips.style.lineHeight = '1.6';
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
});

document.getElementById('api-key-form').addEventListener('submit', async function (e) {
    e.preventDefault();

    const apiKeyInput = document.getElementById('apiKeyInput');

    // 获取高级设定的值
    // 即使选择器被禁用，也要确保能正确获取当前选择的值
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');

    // 获取自定义API启用状态（用于推断逻辑，优先判断非自定义模式）
    const enableCustomApiElement = document.getElementById('enableCustomApi');
    const enableCustomApi = enableCustomApiElement ? enableCustomApiElement.checked : false;

    // 优先从选择器获取值，如果选择器被禁用或值为空，则从当前显示状态推断
    let coreApi = coreApiSelect ? coreApiSelect.value : '';
    let assistApi = assistApiSelect ? assistApiSelect.value : '';

    // 如果核心API选择器被禁用，检查是否是因为免费版本
    if (coreApiSelect && coreApiSelect.disabled && coreApi === '') {
        // 仅在非自定义API模式下，根据 select 的实际值判断是否为免费版
        if (!enableCustomApi && coreApiSelect.value === 'free') {
            coreApi = 'free';
        }
    }

    // 如果辅助API选择器被禁用，检查是否是因为免费版本
    if (assistApiSelect && assistApiSelect.disabled && assistApi === '') {
        // 仅在非自定义API模式下，如果核心 API 已确定为 free，则辅助 API 也强制为 'free'
        if (!enableCustomApi && coreApi === 'free') {
            assistApi = 'free';
        }
    }

    // 处理API Key：读取用户输入并去除免费版展示文本
    let apiKey = apiKeyInput.value ? apiKeyInput.value.trim() : '';
    if (isFreeVersionText(apiKey)) {
        apiKey = '';
    }
    const assistApiKeyQwen = document.getElementById('assistApiKeyInputQwen') ? document.getElementById('assistApiKeyInputQwen').value.trim() : '';
    const assistApiKeyOpenai = document.getElementById('assistApiKeyInputOpenai') ? document.getElementById('assistApiKeyInputOpenai').value.trim() : '';
    const assistApiKeyGlm = document.getElementById('assistApiKeyInputGlm') ? document.getElementById('assistApiKeyInputGlm').value.trim() : '';
    const assistApiKeyStep = document.getElementById('assistApiKeyInputStep') ? document.getElementById('assistApiKeyInputStep').value.trim() : '';
    const assistApiKeySilicon = document.getElementById('assistApiKeyInputSilicon') ? document.getElementById('assistApiKeyInputSilicon').value.trim() : '';
    const assistApiKeyGemini = document.getElementById('assistApiKeyInputGemini') ? document.getElementById('assistApiKeyInputGemini').value.trim() : '';
    const assistApiKeyKimi = document.getElementById('assistApiKeyInputKimi') ? document.getElementById('assistApiKeyInputKimi').value.trim() : '';

    // 获取用户自定义API配置
    const conversationModelUrl = document.getElementById('conversationModelUrl') ? document.getElementById('conversationModelUrl').value.trim() : '';
    const conversationModelId = document.getElementById('conversationModelId') ? document.getElementById('conversationModelId').value.trim() : '';
    const conversationModelApiKey = document.getElementById('conversationModelApiKey') ? document.getElementById('conversationModelApiKey').value.trim() : '';

    const summaryModelUrl = document.getElementById('summaryModelUrl') ? document.getElementById('summaryModelUrl').value.trim() : '';
    const summaryModelId = document.getElementById('summaryModelId') ? document.getElementById('summaryModelId').value.trim() : '';
    const summaryModelApiKey = document.getElementById('summaryModelApiKey') ? document.getElementById('summaryModelApiKey').value.trim() : '';

    const correctionModelUrl = document.getElementById('correctionModelUrl') ? document.getElementById('correctionModelUrl').value.trim() : '';
    const correctionModelId = document.getElementById('correctionModelId') ? document.getElementById('correctionModelId').value.trim() : '';
    const correctionModelApiKey = document.getElementById('correctionModelApiKey') ? document.getElementById('correctionModelApiKey').value.trim() : '';

    const emotionModelUrl = document.getElementById('emotionModelUrl') ? document.getElementById('emotionModelUrl').value.trim() : '';
    const emotionModelId = document.getElementById('emotionModelId') ? document.getElementById('emotionModelId').value.trim() : '';
    const emotionModelApiKey = document.getElementById('emotionModelApiKey') ? document.getElementById('emotionModelApiKey').value.trim() : '';

    const visionModelUrl = document.getElementById('visionModelUrl') ? document.getElementById('visionModelUrl').value.trim() : '';
    const visionModelId = document.getElementById('visionModelId') ? document.getElementById('visionModelId').value.trim() : '';
    const visionModelApiKey = document.getElementById('visionModelApiKey') ? document.getElementById('visionModelApiKey').value.trim() : '';
    const agentModelUrl = document.getElementById('agentModelUrl') ? document.getElementById('agentModelUrl').value.trim() : '';
    const agentModelId = document.getElementById('agentModelId') ? document.getElementById('agentModelId').value.trim() : '';
    const agentModelApiKey = document.getElementById('agentModelApiKey') ? document.getElementById('agentModelApiKey').value.trim() : '';

    const omniModelUrl = document.getElementById('omniModelUrl') ? document.getElementById('omniModelUrl').value.trim() : '';
    const omniModelId = document.getElementById('omniModelId') ? document.getElementById('omniModelId').value.trim() : '';
    const omniModelApiKey = document.getElementById('omniModelApiKey') ? document.getElementById('omniModelApiKey').value.trim() : '';

    let ttsModelUrl = document.getElementById('ttsModelUrl') ? document.getElementById('ttsModelUrl').value.trim() : '';
    const ttsModelId = document.getElementById('ttsModelId') ? document.getElementById('ttsModelId').value.trim() : '';
    const ttsModelApiKey = document.getElementById('ttsModelApiKey') ? document.getElementById('ttsModelApiKey').value.trim() : '';
    let ttsVoiceId = document.getElementById('ttsVoiceId') ? document.getElementById('ttsVoiceId').value.trim() : '';

    // 检查 GPT-SoVITS v3 配置
    const gptsovitsEnabled = document.getElementById('gptsovitsEnabled')?.checked;
    // 始终获取 GPT-SoVITS 配置用于保存（即使禁用也保存配置以便下次启用时恢复）
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
        // GPT-SoVITS 启用，使用其配置
        ttsModelUrl = gptsovitsConfigForSave.url;
        ttsVoiceId = gptsovitsConfigForSave.voiceId;
    } else if (!gptsovitsEnabled) {
        // GPT-SoVITS 禁用
        // 如果当前 ttsModelUrl 是 HTTP URL（GPT-SoVITS 格式），需要特殊处理
        if (ttsModelUrl && (ttsModelUrl.startsWith('http://') || ttsModelUrl.startsWith('https://'))) {
            // 保存 GPT-SoVITS 配置到特殊标记，但清空实际使用的 URL
            // 格式：在 voiceId 中添加 __gptsovits_disabled__ 前缀保存配置
            if (gptsovitsConfigForSave) {
                ttsVoiceId = `__gptsovits_disabled__|${gptsovitsConfigForSave.url}|${gptsovitsConfigForSave.voiceId}`;
            }
            ttsModelUrl = '';
        }
    }

    const mcpToken = document.getElementById('mcpTokenInput') ? document.getElementById('mcpTokenInput').value.trim() : '';

    const apiKeyForSave = (coreApi === 'free' || assistApi === 'free') ? 'free-access' : apiKey;

    // 免费版和启用自定义API时不需要API Key检查
    if (!enableCustomApi && coreApi !== 'free' && assistApi !== 'free' && !apiKey) {
        showStatus(window.t ? window.t('api.pleaseEnterApiKeyError') : '请输入API Key', 'error');
        return;
    }

    // 检查是否已有API Key，如果有则显示警告
    const currentApiKeyDiv = document.getElementById('current-api-key');
    if (currentApiKeyDiv && currentApiKeyDiv.dataset.hasKey === 'true') {
        // 已有API Key，显示警告弹窗
        pendingApiKey = {
            apiKey: apiKeyForSave, coreApi, assistApi,
            assistApiKeyQwen, assistApiKeyOpenai, assistApiKeyGlm, assistApiKeyStep, assistApiKeySilicon, assistApiKeyGemini, assistApiKeyKimi,
            conversationModelUrl, conversationModelId, conversationModelApiKey,
            summaryModelUrl, summaryModelId, summaryModelApiKey,
            correctionModelUrl, correctionModelId, correctionModelApiKey,
            emotionModelUrl, emotionModelId, emotionModelApiKey,
            visionModelUrl, visionModelId, visionModelApiKey,
            agentModelUrl, agentModelId, agentModelApiKey,
            omniModelUrl, omniModelId, omniModelApiKey,
            ttsModelUrl, ttsModelId, ttsModelApiKey, ttsVoiceId,
            mcpToken, enableCustomApi
        };
        showWarningModal();
    } else {
        // 没有现有API Key，直接保存
        await saveApiKey({
            apiKey: apiKeyForSave, coreApi, assistApi,
            assistApiKeyQwen, assistApiKeyOpenai, assistApiKeyGlm, assistApiKeyStep, assistApiKeySilicon, assistApiKeyGemini, assistApiKeyKimi,
            conversationModelUrl, conversationModelId, conversationModelApiKey,
            summaryModelUrl, summaryModelId, summaryModelApiKey,
            correctionModelUrl, correctionModelId, correctionModelApiKey,
            emotionModelUrl, emotionModelId, emotionModelApiKey,
            visionModelUrl, visionModelId, visionModelApiKey,
            agentModelUrl, agentModelId, agentModelApiKey,
            omniModelUrl, omniModelId, omniModelApiKey,
            ttsModelUrl, ttsModelId, ttsModelApiKey, ttsVoiceId,
            mcpToken, enableCustomApi
        });
    }
});

async function saveApiKey({ apiKey, coreApi, assistApi, assistApiKeyQwen, assistApiKeyOpenai, assistApiKeyGlm, assistApiKeyStep, assistApiKeySilicon, assistApiKeyGemini, assistApiKeyKimi, conversationModelUrl, conversationModelId, conversationModelApiKey, summaryModelUrl, summaryModelId, summaryModelApiKey, correctionModelUrl, correctionModelId, correctionModelApiKey, emotionModelUrl, emotionModelId, emotionModelApiKey, visionModelUrl, visionModelId, visionModelApiKey, agentModelUrl, agentModelId, agentModelApiKey, omniModelUrl, omniModelId, omniModelApiKey, ttsModelUrl, ttsModelId, ttsModelApiKey, ttsVoiceId, mcpToken, enableCustomApi }) {
    // 统一处理免费版 API Key 的保存值：如果核心或辅助 API 为 free，则保存值应为 'free-access'
    if (coreApi === 'free' || assistApi === 'free') {
        // 无论用户在 UI 中看到的是翻译文本或空值，保存时都使用 'free-access'
        apiKey = 'free-access';
    }

    // 确保apiKey是有效的字符串（启用自定义API或免费版时不需要API Key）
    if (!enableCustomApi && coreApi !== 'free' && assistApi !== 'free' && (!apiKey || typeof apiKey !== 'string')) {
        showStatus(window.t ? window.t('api.apiKeyInvalid') : 'API Key无效', 'error');
        return;
    }

    try {
        const response = await fetch('/api/config/core_api', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                coreApiKey: apiKey,
                coreApi: coreApi || undefined,
                assistApi: assistApi || undefined,
                assistApiKeyQwen: assistApiKeyQwen || undefined,
                assistApiKeyOpenai: assistApiKeyOpenai || undefined,
                assistApiKeyGlm: assistApiKeyGlm || undefined,
                assistApiKeyStep: assistApiKeyStep || undefined,
                assistApiKeySilicon: assistApiKeySilicon || undefined,
                assistApiKeyGemini: assistApiKeyGemini || undefined,
                assistApiKeyKimi: assistApiKeyKimi || undefined,
                conversationModelUrl: conversationModelUrl || undefined,
                conversationModelId: conversationModelId || undefined,
                conversationModelApiKey: conversationModelApiKey || undefined,
                summaryModelUrl: summaryModelUrl || undefined,
                summaryModelId: summaryModelId || undefined,
                summaryModelApiKey: summaryModelApiKey || undefined,
                correctionModelUrl: correctionModelUrl || undefined,
                correctionModelId: correctionModelId || undefined,
                correctionModelApiKey: correctionModelApiKey || undefined,
                emotionModelUrl: emotionModelUrl || undefined,
                emotionModelId: emotionModelId || undefined,
                emotionModelApiKey: emotionModelApiKey || undefined,
                visionModelUrl: visionModelUrl || undefined,
                visionModelId: visionModelId || undefined,
                visionModelApiKey: visionModelApiKey || undefined,
                agentModelUrl: agentModelUrl || undefined,
                agentModelId: agentModelId || undefined,
                agentModelApiKey: agentModelApiKey || undefined,
                omniModelUrl: omniModelUrl || undefined,
                omniModelId: omniModelId || undefined,
                omniModelApiKey: omniModelApiKey || undefined,
                ttsModelUrl: ttsModelUrl || undefined,
                ttsModelId: ttsModelId || undefined,
                ttsModelApiKey: ttsModelApiKey || undefined,
                ttsVoiceId: ttsVoiceId || undefined,
                mcpToken: mcpToken || undefined,
                enableCustomApi: enableCustomApi || false
            })
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
                document.getElementById('apiKeyInput').value = '';

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
    // 不在这里清空 pendingApiKey，让调用者决定何时清空
}

async function confirmApiKeyChange() {
    if (pendingApiKey && typeof pendingApiKey === 'object') {
        const apiKeyToSave = pendingApiKey; // 保存当前值
        closeWarningModal();
        pendingApiKey = null; // 清空全局变量
        await saveApiKey(apiKeyToSave); // 使用保存的值
    } else {
        showStatus(window.t ? window.t('api.apiKeyInvalidRetry') : 'API Key无效，请重新输入', 'error');
        closeWarningModal();
        pendingApiKey = null; // 清空全局变量
    }
}

function toggleAdvancedOptions() {
    const adv = document.getElementById('advanced-options');
    const btn = document.getElementById('advanced-toggle-btn');
    if (adv.style.display === 'none') {
        adv.style.display = 'block';
        btn.classList.add('rotated');
    } else {
        adv.style.display = 'none';
        btn.classList.remove('rotated');
    }
}

// Helper: 判断一个值是否表示免费版（支持存储值 'free-access' 和当前语言的翻译文本）
function isFreeVersionText(value) {
    if (typeof value !== 'string') return false;
    const v = value.trim();
    if (!v) return false;
    // 存储层标记
    if (v === 'free-access') return true;
    // UI 展示的翻译文本
    const translated = (window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key');
    if (v === translated) return true;
    return false;
}

// 统一禁用/启用所有辅助API Key输入框
function setAssistApiInputsDisabled(disabled) {
    const assistApiKeyInputs = [
        'assistApiKeyInputQwen', 'assistApiKeyInputOpenai', 'assistApiKeyInputGlm',
        'assistApiKeyInputStep', 'assistApiKeyInputSilicon', 'assistApiKeyInputGemini',
        'assistApiKeyInputKimi'
    ];
    assistApiKeyInputs.forEach(id => {
        const input = document.getElementById(id);
        if (!input) return;
        input.disabled = !!disabled;
        // 启用时清理表示免费版的占位值
        if (!disabled && isFreeVersionText(input.value)) {
            input.value = '';
        }
    });
}

// 根据核心API选择更新辅助API的提示和建议
function updateAssistApiRecommendation() {
    const coreApiSelect = document.getElementById('coreApiSelect');
    const assistApiSelect = document.getElementById('assistApiSelect');

    if (!coreApiSelect || !assistApiSelect) return;

    const selectedCoreApi = coreApiSelect.value;
    const selectedAssistApi = assistApiSelect.value;
    let recommendation = '';

    // 控制API Key输入框和免费版提示
    const apiKeyInput = document.getElementById('apiKeyInput');
    const freeVersionHint = document.getElementById('freeVersionHint');

    if (selectedCoreApi === 'free') {
        // 核心API选择免费版时，自动屏蔽辅助API选择，强制使用免费版
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

        // 辅助API输入框保持可用，允许用户填写备用Key
        setAssistApiInputsDisabled(false);

        recommendation = window.t ? window.t('api.freeVersionConfig') : '免费版配置：支持语音对话、文本对话和记忆管理，不支持自定义语音、Agent模式和视频对话';
    } else {
        // 核心API不是免费版
        if (apiKeyInput) {
            apiKeyInput.disabled = false;
            apiKeyInput.placeholder = window.t ? window.t('api.pleaseEnterApiKey') : '请输入您的API Key';
            apiKeyInput.required = true;
            if (isFreeVersionText(apiKeyInput.value)) {
                apiKeyInput.value = '';
            }
        }
        if (freeVersionHint) {
            freeVersionHint.style.display = 'none';
        }

        // 启用辅助API选择框，但禁用免费版选项
        assistApiSelect.disabled = false;
        const freeOption = assistApiSelect.querySelector('option[value="free"]');
        if (freeOption) {
            freeOption.disabled = true;
            freeOption.textContent = window.t ? window.t('api.freeVersionOnlyWhenCoreFree') : '免费版（仅核心API为免费版时可用）';
        }

        // 启用所有辅助API输入框（统一处理，启用时清理显示为免费版的占位值）
        setAssistApiInputsDisabled(false);

        // 辅助API Key输入框映射
        const assistApiKeyInputMap = {
            'qwen': 'assistApiKeyInputQwen',
            'openai': 'assistApiKeyInputOpenai',
            'glm': 'assistApiKeyInputGlm',
            'step': 'assistApiKeyInputStep',
            'silicon': 'assistApiKeyInputSilicon',
            'gemini': 'assistApiKeyInputGemini',
            'kimi': 'assistApiKeyInputKimi'
        };

        // 检查辅助API是否有对应的API Key
        function hasAssistApiKey(assistApi) {
            if (assistApi === 'free') return false;
            const inputId = assistApiKeyInputMap[assistApi];
            if (!inputId) return false;
            const input = document.getElementById(inputId);
            return input && input.value && input.value.trim() !== '';
        }

        // 如果当前 assist 是免费版或没有对应的 Key，自动跟随 core
        let newAssistApi = selectedAssistApi;
        if (selectedAssistApi === 'free' || !hasAssistApiKey(selectedAssistApi)) {
            // 检查 core API 是否在 assist 选项中可用
            const coreOption = assistApiSelect.querySelector(`option[value="${selectedCoreApi}"]`);
            if (coreOption && !coreOption.disabled) {
                newAssistApi = selectedCoreApi;
                if (selectedAssistApi !== 'free') {
                    console.log(`[API Settings] 辅助API ${selectedAssistApi} 没有Key，自动跟随核心API: ${selectedCoreApi}`);
                }
            } else {
                // core 不在 assist 选项中，默认使用 qwen
                newAssistApi = 'qwen';
            }
        }

        if (newAssistApi !== selectedAssistApi) {
            assistApiSelect.value = newAssistApi;
        }

        switch (selectedCoreApi) {
            case 'qwen':
                recommendation = window.t ? window.t('api.qwenRecommendation') : '阿里作为核心API时，建议辅助API也选择阿里以获得最佳的自定义语音体验';
                break;
            case 'glm':
                recommendation = window.t ? window.t('api.glmRecommendation') : '智谱作为核心API时，建议辅助API选择阿里以支持自定义语音功能';
                break;
            case 'openai':
                recommendation = window.t ? window.t('api.openaiRecommendation') : 'OpenAI作为核心API时，建议辅助API选择阿里以支持自定义语音功能';
                break;
            case 'step':
                recommendation = window.t ? window.t('api.stepRecommendation') : '阶跃星辰作为核心API时，建议辅助API选择阿里以支持自定义语音功能';
                break;
            case 'gemini':
                recommendation = window.t ? window.t('api.geminiRecommendation') : 'Gemini作为核心API时，建议辅助API选择阿里以支持自定义语音功能';
                break;
        }
    }

    // 更新辅助API选择框的提示
    const assistApiTooltip = assistApiSelect.parentElement.querySelector('label .tooltip-content');
    if (assistApiTooltip) {
        assistApiTooltip.innerHTML = `
            <strong>${window.t ? window.t('api.assistApiTitle') : '辅助API负责记忆管理和自定义语音：'}</strong><br>
            • <span>${window.t ? window.t('api.freeVersionAssist') : '免费版：完全免费，无需API Key，但不支持自定义语音'}</span><br>
            • <span>${window.t ? window.t('api.aliAssist') : '阿里：推荐选择，支持自定义语音'}</span><br>
            • <span>${window.t ? window.t('api.glmAssist') : '智谱：支持Agent模式'}</span><br>
            • <span>${window.t ? window.t('api.stepAssist') : '阶跃星辰：价格相对便宜'}</span><br>
            • <span>${window.t ? window.t('api.siliconAssist') : '硅基流动：性价比高'}</span><br>
            • <span>${window.t ? window.t('api.openaiAssist') : 'OpenAI：记忆管理能力强'}</span><br>
            • <span>${window.t ? window.t('api.geminiAssist') : 'Gemini：智能和性价比极高，但国内版不支持'}</span><br>
            • <span>${window.t ? window.t('api.kimiAssist') : 'Kimi：国内可用，支持长上下文和视觉'}</span><br>
            <strong>${window.t ? window.t('api.assistApiNote') : '注意：只有阿里支持自定义语音功能'}</strong><br>
            <strong>${window.t ? window.t('api.currentSuggestion') : '当前建议：'}</strong>${recommendation}
        `;
    }

    // 调用自动填充核心API Key的函数
    autoFillCoreApiKey();
}

// 自动填充核心API Key到核心API Key输入框
function autoFillCoreApiKey() {
    const coreApiSelect = document.getElementById('coreApiSelect');
    const apiKeyInput = document.getElementById('apiKeyInput');

    if (!coreApiSelect || !apiKeyInput) return;

    const selectedCoreApi = coreApiSelect.value;

    // 如果选择的是免费版，不需要填充
    if (selectedCoreApi === 'free') {
        return;
    }

    // 获取当前核心API Key输入框的值
    const currentApiKey = apiKeyInput.value.trim();

    // 如果核心API Key输入框为空，尝试自动填充
    if (!currentApiKey || isFreeVersionText(currentApiKey)) {
        let sourceApiKey = '';

        // 策略1：从 current-api-key 的 dataset 获取
        const currentApiKeyDiv = document.getElementById('current-api-key');
        if (currentApiKeyDiv && currentApiKeyDiv.dataset.hasKey === 'true') {
            const savedKey = currentApiKeyDiv.dataset.apiKey;
            if (savedKey && savedKey !== 'free-access') {
                sourceApiKey = savedKey;
            }
        }

        // 如果找到了有效的API Key，自动填充到核心API Key输入框
        if (sourceApiKey) {
            apiKeyInput.value = sourceApiKey;

            // 显示提示信息
            const autoFillMsg = window.t ? window.t('api.autoFillCoreApiKey') : '已自动填充核心API Key';
            showStatus(autoFillMsg, 'info');
            setTimeout(() => {
                const statusDiv = document.getElementById('status');
                if (statusDiv && statusDiv.textContent.includes(autoFillMsg)) {
                    statusDiv.style.display = 'none';
                }
            }, 2000);
        }
    }
}

// Beacon功能 - 页面关闭时发送信号给服务器（仅在直接打开时发送，iframe中不发送）
let beaconSent = false;

function sendBeacon() {
    // 如果在iframe中，不发送beacon
    if (window.parent !== window) {
        return;
    }

    if (beaconSent) return; // 防止重复发送
    beaconSent = true;

    try {
        // 构建JSON payload
        const payload = JSON.stringify({
            timestamp: Date.now(),
            action: 'shutdown'
        });

        // 使用navigator.sendBeacon确保信号不被拦截，传入Blob以设置正确的Content-Type
        const blob = new Blob([payload], { type: 'application/json' });
        const success = navigator.sendBeacon('/api/beacon/shutdown', blob);

        if (!success) {
            console.warn('Beacon发送失败，尝试使用fetch');
            // 备用方案：使用fetch，复用相同的payload
            fetch('/api/beacon/shutdown', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: payload,
                keepalive: true // 确保请求在页面关闭时仍能发送
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

    // 计算tooltip的初始位置（在图标上方居中）
    let left = iconRect.left + iconRect.width / 2 - tooltipRect.width / 2;
    let top = iconRect.top - tooltipRect.height - 10; // 10px间距

    // 计算图标中心相对于tooltip左边的位置
    let iconCenter = iconRect.left + iconRect.width / 2;

    // 检查左边界
    if (left < 20) {
        left = 20;
    }

    // 检查右边界
    if (left + tooltipRect.width > window.innerWidth - 20) {
        left = window.innerWidth - tooltipRect.width - 20;
    }

    // 计算箭头位置（相对于tooltip）
    let arrowLeft = iconCenter - left;
    // 限制箭头位置在tooltip范围内
    arrowLeft = Math.max(15, Math.min(arrowLeft, tooltipRect.width - 15));

    // 检查上边界（如果上方空间不足，显示在下方）
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
        // 折叠
        content.classList.remove('expanded');
        icon.style.transform = 'rotate(0deg)';
        header.setAttribute('aria-expanded', 'false');
        content.setAttribute('aria-hidden', 'true');
    } else {
        // 展开
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
        toggleCustomApi(); // 调用一次以设置初始状态
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
            // 先让tooltip可见但保持透明，以便计算尺寸
            tooltip.style.visibility = 'visible';
            tooltip.style.opacity = '0';

            // 使用requestAnimationFrame确保DOM已更新
            requestAnimationFrame(() => {
                positionTooltip(icon, tooltip);
                // 再设置透明度，产生淡入效果
                tooltip.style.opacity = '1';
            });
        });

        icon.addEventListener('mouseleave', function () {
            tooltip.style.opacity = '0';
            // 等待transition完成后再隐藏
            setTimeout(() => {
                if (tooltip.style.opacity === '0') {
                    tooltip.style.visibility = 'hidden';
                }
            }, 300);
        });
    });

    // 窗口大小改变时重新定位
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
    // 防止重复初始化
    if (window.apiKeySettingsInitialized) {
        return;
    }

    try {
        // 显示加载遮罩（半透明覆盖在原有UI上）
        const loadingOverlay = document.getElementById('loading-overlay');

        if (loadingOverlay) {
            loadingOverlay.style.display = 'flex';
        }

        // 等待 i18n 初始化完成
        await waitForI18n();

        // 检测用户是否为中国大陆用户（用于过滤 OpenAI/Gemini 选项）
        isMainlandChinaUser = await checkMainlandChinaUser();
        console.log(`[Region] 用户区域检测完成: isMainlandChinaUser = ${isMainlandChinaUser}`);

        // 第一步：加载API服务商选项（会根据 isMainlandChinaUser 过滤选项）
        const providersLoaded = await loadApiProviders();

        if (!providersLoaded) {
            throw new Error(window.t ? window.t('api.loadProvidersFailed') : '加载API服务商选项失败');
        }

        // 第二步：加载当前API配置
        await loadCurrentApiKey();

        // 第三步：等待所有配置加载完成，然后初始化UI状态
        const UI_SETTLE_DELAY = 300; // 等待 DOM 变更和下拉渲染稳定
        await new Promise(resolve => setTimeout(resolve, UI_SETTLE_DELAY));

        // 初始化tooltips
        initTooltips();

        // 确保API输入框状态与当前配置一致
        const coreApiSelect = document.getElementById('coreApiSelect');
        const apiKeyInput = document.getElementById('apiKeyInput');
        const freeVersionHint = document.getElementById('freeVersionHint');

        if (coreApiSelect && apiKeyInput && freeVersionHint) {
            const selectedCoreApi = coreApiSelect.value;

            // 重新确认API输入框状态是否与当前配置一致
            if (selectedCoreApi === 'free') {
                // 如果是免费版，确保输入框被禁用
                apiKeyInput.disabled = true;
                apiKeyInput.placeholder = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
                apiKeyInput.required = false;
                apiKeyInput.value = window.t ? window.t('api.freeVersionNoApiKey') : '免费版无需API Key';
                freeVersionHint.style.display = 'inline';
            } else {
                // 如果不是免费版，确保输入框可用
                apiKeyInput.disabled = false;
                apiKeyInput.placeholder = window.t ? window.t('api.pleaseEnterApiKey') : '请输入您的API Key';
                apiKeyInput.required = true;
                if (isFreeVersionText(apiKeyInput.value)) {
                    apiKeyInput.value = '';
                }
                freeVersionHint.style.display = 'none';
            }

            // 强制更新辅助API推荐和锁定状态
            updateAssistApiRecommendation();

            // 页面加载完成后立即尝试自动填充核心API Key
            autoFillCoreApiKey();
        }

        // 添加核心API和辅助API选择变化的事件监听器
        if (coreApiSelect) {
            coreApiSelect.addEventListener('change', function () {
                updateAssistApiRecommendation();
                autoFillCoreApiKey();
            });
        }

        const assistApiSelect = document.getElementById('assistApiSelect');
        if (assistApiSelect) {
            assistApiSelect.addEventListener('change', function () {
                updateAssistApiRecommendation();
                autoFillCoreApiKey();
            });
        }

        // 初始化时也更新一次建议
        updateAssistApiRecommendation();

        // 监听语言切换事件，更新下拉选项
        window.addEventListener('localechange', async () => {
            // 保存当前选中的值
            const selectedCoreApi = coreApiSelect ? coreApiSelect.value : '';
            const selectedAssistApi = assistApiSelect ? assistApiSelect.value : '';

            // 重新加载下拉选项（会使用新的语言）
            await loadApiProviders();

            // 恢复之前选中的值
            if (coreApiSelect && selectedCoreApi) {
                coreApiSelect.value = selectedCoreApi;
            }
            if (assistApiSelect && selectedAssistApi) {
                assistApiSelect.value = selectedAssistApi;
            }
        });

        // 所有配置加载完成，隐藏加载遮罩
        if (loadingOverlay) {
            loadingOverlay.style.display = 'none';
        }


        // 标记页面已初始化完成，防止重复执行
        window.apiKeySettingsInitialized = true;

        // 页面初始化完成后立即应用自定义API状态，确保显示正确的禁用状态
        setTimeout(() => {
            toggleCustomApi();
        }, 0);

    } catch (error) {
        console.error('页面初始化失败:', error);

        // 显示错误信息
        showStatus(window.t ? window.t('api.loadConfigFailed') : '加载配置失败，请刷新页面重试', 'error');

        // 隐藏加载遮罩（即使有错误也要显示UI）
        const loadingOverlay = document.getElementById('loading-overlay');

        if (loadingOverlay) {
            loadingOverlay.style.display = 'none';
        }
    }
}

// 页面加载完成后开始初始化
document.addEventListener('DOMContentLoaded', initializePage);

// 兼容性：防止在某些情况下DOMContentLoaded不触发（如样式表阻塞），添加load作为后备
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
        // 如果是通过 window.open() 打开的，直接关闭
        window.close();
    } else if (window.parent && window.parent !== window) {
        // 如果在 iframe 中，通知父窗口关闭
        window.parent.postMessage({ type: 'close_api_key_settings' }, getTargetOrigin());
    } else {
        // 否则尝试关闭窗口
        // 注意：如果是用户直接访问的页面，浏览器可能不允许关闭
        // 在这种情况下，可以尝试返回上一页或显示提示
        if (window.history.length > 1) {
            window.history.back();
        } else {
            window.close();
            // 如果 window.close() 失败（页面仍然存在），可以显示提示
            setTimeout(() => {
                if (!window.closed) {
                    // 窗口未能关闭，返回主页
                    window.location.href = '/';
                }
            }, 100);
        }
    }
}

