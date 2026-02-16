/**
 * VRM 情感映射管理器 - JavaScript 模块
 */

(function() {
    'use strict';

    // DOM 元素
    const modelSelect = document.getElementById('model-select');
    const emotionConfig = document.getElementById('emotion-config');
    const saveBtn = document.getElementById('save-btn');
    const resetBtn = document.getElementById('reset-btn');
    const statusMessage = document.getElementById('status-message');
    const previewButtons = document.getElementById('preview-buttons');

    // 状态变量
    const emotions = ['neutral', 'happy', 'relaxed', 'sad', 'angry', 'surprised'];
    let currentModelInfo = null;
    let availableExpressions = [];

    // i18n 辅助函数
    function t(key, paramsOrFallback, fallback) {
        if (typeof i18next !== 'undefined' && i18next.isInitialized) {
            return i18next.t(key, paramsOrFallback);
        }
        // Fallback: 返回 fallback 或 key 的最后部分
        if (typeof paramsOrFallback === 'string') {
            return paramsOrFallback;
        }
        return fallback || key.split('.').pop();
    }

    // Default mood map (fallback)
    const defaultMoodMap = {
        'neutral': ['neutral'],
        'happy': ['happy', 'joy', 'fun', 'smile', 'joy_01'],
        'relaxed': ['relaxed', 'joy', 'fun', 'content'],
        'sad': ['sad', 'sorrow', 'grief'],
        'angry': ['angry', 'anger'],
        'surprised': ['surprised', 'surprise', 'shock', 'e', 'o']
    };

    // 显示状态消息
    function showStatus(message, type = 'info') {
        statusMessage.textContent = message;
        statusMessage.className = `status-message status-${type}`;
        statusMessage.style.display = 'block';

        setTimeout(() => {
            statusMessage.style.display = 'none';
        }, 3000);
    }

    // 加载模型列表
    async function loadModelList() {
        try {
            const response = await fetch('/api/model/vrm/models');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            const data = await response.json();

            if (data.success && Array.isArray(data.models) && data.models.length > 0) {
                modelSelect.innerHTML = `<option value="">${t('vrmEmotionManager.pleaseSelectModel', '请选择模型')}</option>`;

                data.models.forEach(model => {
                    const option = document.createElement('option');
                    option.value = model.name;
                    const locationText = model.location === 'user' ?
                        t('common.user', '用户') : t('common.project', '项目');
                    option.textContent = `${model.name} (${locationText})`;
                    option.dataset.info = JSON.stringify(model);
                    modelSelect.appendChild(option);
                });
            } else {
                modelSelect.innerHTML = `<option value="">${t('vrmEmotionManager.noModelsFound', '没有找到可用的VRM模型')}</option>`;
                showStatus(t('vrmEmotionManager.noModelsFound', '没有找到可用的VRM模型，请先上传模型'), 'warning');
            }
        } catch (error) {
            console.error('加载模型列表失败:', error);
            showStatus(t('vrmEmotionManager.loadModelListFailed', '加载模型列表失败') + ': ' + error.message, 'error');
        }
    }

    // 从父窗口获取实际模型表情列表
    function getExpressionsFromParentWindow() {
        return new Promise((resolve) => {
            // 检查父窗口是否有 vrmManager
            if (window.opener && !window.opener.closed && window.opener.vrmManager && window.opener.vrmManager.expression) {
                const expressions = window.opener.vrmManager.expression.getExpressionList();
                if (expressions && expressions.length > 0) {
                    console.log('[VRM Emotion] 从父窗口获取到表情列表:', expressions.length, '个');
                    resolve(expressions);
                    return;
                }
            }

            // 尝试通过 postMessage 获取
            if (window.opener && !window.opener.closed) {
                const messageHandler = (event) => {
                    // 安全检查：验证消息来源
                    if (event.origin !== window.location.origin) {
                        return;
                    }
                    if (event.data && event.data.type === 'vrm-expressions-response') {
                        window.removeEventListener('message', messageHandler);
                        if (event.data.expressions && event.data.expressions.length > 0) {
                            console.log('[VRM Emotion] 通过 postMessage 获取到表情列表:', event.data.expressions.length, '个');
                            resolve(event.data.expressions);
                        } else {
                            resolve(null);
                        }
                    }
                };
                window.addEventListener('message', messageHandler);

                // 发送请求（使用明确的 targetOrigin）
                window.opener.postMessage({ type: 'vrm-get-expressions' }, window.location.origin);

                // 3秒超时
                setTimeout(() => {
                    window.removeEventListener('message', messageHandler);
                    resolve(null);
                }, 3000);
            } else {
                resolve(null);
            }
        });
    }

    // 加载模型表情列表
    async function loadModelExpressions(modelName, modelInfo) {
        // 优先从父窗口获取实际模型表情列表
        let expressionsFromParent = null;
        try {
            expressionsFromParent = await getExpressionsFromParentWindow();
        } catch (e) {
            console.warn('[VRM Emotion] 从父窗口获取表情列表失败:', e);
        }

        if (expressionsFromParent && expressionsFromParent.length > 0) {
            // 添加 neutral 到列表（如果不存在）
            if (!expressionsFromParent.includes('neutral')) {
                expressionsFromParent.unshift('neutral');
            }
            availableExpressions = expressionsFromParent;
            populateSelects();
            populatePreviewButtons();
            emotionConfig.style.display = 'block';
            showStatus(t('vrmEmotionManager.expressionsLoadedFromModel', '已从当前模型加载表情列表'), 'success');
            return;
        }

        // 回退到后端 API
        try {
            // 尝试从后端获取模型的表情列表
            const response = await fetch(`/api/model/vrm/expressions/${encodeURIComponent(modelName)}`);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();

            if (data.success) {
                availableExpressions = data.expressions || [];
                populateSelects();
                populatePreviewButtons();
                emotionConfig.style.display = 'block';
                showStatus(t('vrmEmotionManager.useGenericExpressions', '使用通用表情列表（请先在主页面加载模型）'), 'info');
            } else {
                showStatus(t('vrmEmotionManager.loadExpressionsFailed', '加载模型表情失败') + ': ' + (data.error || t('common.unknownError', '未知错误')), 'error');
            }
        } catch (error) {
            console.error('加载模型表情失败:', error);
            // 如果API不可用，使用默认表情列表
            availableExpressions = [
                'neutral', 'happy', 'joy', 'fun', 'relaxed',
                'sad', 'angry', 'surprised', 'blink', 'blink_l', 'blink_r'
            ];
            populateSelects();
            populatePreviewButtons();
            emotionConfig.style.display = 'block';
            showStatus(t('vrmEmotionManager.useDefaultExpressions', '使用默认表情列表（请先在主页面加载模型）'), 'info');
        }
    }

    // 填充预览按钮
    function populatePreviewButtons() {
        previewButtons.innerHTML = '';

        // 过滤掉口型和视线相关的表情
        const excludeKeywords = ['aa', 'ih', 'ou', 'ee', 'oh', 'look'];
        const filteredExpressions = availableExpressions.filter(name => {
            const lowerName = name.toLowerCase();
            return !excludeKeywords.some(keyword => lowerName.includes(keyword));
        });

        if (filteredExpressions.length === 0) {
            previewButtons.innerHTML = `<span style="color: var(--color-text-muted);">${t('vrmEmotionManager.noExpressionsFound', '没有可用的表情')}</span>`;
            return;
        }

        filteredExpressions.forEach(exprName => {
            const btn = document.createElement('button');
            btn.className = 'preview-btn';
            btn.textContent = exprName;
            btn.dataset.expression = exprName;

            btn.addEventListener('click', () => {
                // 移除其他按钮的playing状态
                document.querySelectorAll('.preview-btn').forEach(b => b.classList.remove('playing'));
                btn.classList.add('playing');

                // 发送表情预览事件
                if (window.opener && window.opener.vrmManager) {
                    window.opener.vrmManager.expression.setBaseExpression(exprName);
                } else {
                    // 通过 postMessage 通知父窗口
                    window.opener?.postMessage({
                        type: 'vrm-preview-expression',
                        expression: exprName
                    }, '*');
                }

                // 3秒后取消playing状态
                setTimeout(() => {
                    btn.classList.remove('playing');
                }, 3000);
            });

            previewButtons.appendChild(btn);
        });
    }

    // 切换下拉菜单
    function toggleDropdown(event) {
        const multiselect = event.currentTarget.closest('.custom-multiselect');
        const header = multiselect.querySelector('.multiselect-header');
        const wasActive = multiselect.classList.contains('active');

        // 关闭所有其他下拉菜单
        document.querySelectorAll('.custom-multiselect').forEach(ms => {
            ms.classList.remove('active');
            const h = ms.querySelector('.multiselect-header');
            if (h) h.setAttribute('aria-expanded', 'false');
        });

        if (!wasActive) {
            multiselect.classList.add('active');
            if (header) header.setAttribute('aria-expanded', 'true');
        }

        event.stopPropagation();
    }

    // 点击外部关闭下拉菜单
    window.addEventListener('click', () => {
        document.querySelectorAll('.custom-multiselect').forEach(ms => {
            ms.classList.remove('active');
            const h = ms.querySelector('.multiselect-header');
            if (h) h.setAttribute('aria-expanded', 'false');
        });
    });

    // 更新头部显示
    function updateMultiselectHeader(multiselect) {
        const checkboxes = multiselect.querySelectorAll('input[type="checkbox"]:checked');
        const headerContainer = multiselect.querySelector('.selected-text');

        headerContainer.innerHTML = '';

        if (checkboxes.length === 0) {
            headerContainer.textContent = t('vrmEmotionManager.selectExpression', '选择表情');
        } else {
            checkboxes.forEach(cb => {
                const label = cb.closest('.multiselect-item').querySelector('span').textContent;
                const tag = document.createElement('span');
                tag.className = 'selected-tag';
                tag.textContent = label;
                headerContainer.appendChild(tag);
            });
        }
    }

    // 填充下拉菜单
    function populateSelects() {
        emotions.forEach(emotion => {
            const expressionContainer = document.querySelector(`.emotion-expression-select[data-emotion="${emotion}"] .multiselect-options`);

            if (expressionContainer) {
                expressionContainer.innerHTML = '';
                expressionContainer.onclick = (e) => e.stopPropagation();

                availableExpressions.forEach(expression => {
                    const item = document.createElement('div');
                    item.className = 'multiselect-item';

                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.value = expression;

                    const span = document.createElement('span');
                    span.textContent = expression;

                    item.appendChild(checkbox);
                    item.appendChild(span);

                    item.addEventListener('click', (e) => {
                        if (e.target.tagName !== 'INPUT') {
                            checkbox.checked = !checkbox.checked;
                        }
                        updateMultiselectHeader(expressionContainer.closest('.custom-multiselect'));
                        e.stopPropagation();
                    });
                    expressionContainer.appendChild(item);
                });

                updateMultiselectHeader(expressionContainer.closest('.custom-multiselect'));

                const header = expressionContainer.closest('.custom-multiselect').querySelector('.multiselect-header');
                header.onclick = toggleDropdown;
                header.onkeydown = (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        toggleDropdown(e);
                    }
                };
            }
        });
    }

    // 加载情感映射配置
    async function loadEmotionMapping(modelName) {
        try {
            const response = await fetch(`/api/model/vrm/emotion_mapping/${encodeURIComponent(modelName)}`);

            if (!response.ok) {
                console.error(`加载情感映射配置失败: HTTP ${response.status}`, await response.text().catch(() => ''));
                applyDefaultConfig();
                showStatus(t('vrmEmotionManager.configLoadFailed', '配置加载失败'), 'error');
                return;
            }

            const data = await response.json();

            if (data.success && data.config) {
                const config = data.config;

                emotions.forEach(emotion => {
                    const expressionMS = document.querySelector(`.emotion-expression-select[data-emotion="${emotion}"]`);

                    if (expressionMS) {
                        expressionMS.querySelectorAll('input').forEach(cb => { cb.checked = false; });
                        updateMultiselectHeader(expressionMS);
                    }

                    if (config[emotion]) {
                        const files = Array.isArray(config[emotion]) ? config[emotion] : [config[emotion]];
                        if (expressionMS) {
                            files.forEach(file => {
                                const cb = expressionMS.querySelector(`input[value="${CSS.escape(file)}"]`);
                                if (cb) cb.checked = true;
                            });
                            updateMultiselectHeader(expressionMS);
                        }
                    }
                });

                showStatus(t('vrmEmotionManager.configLoadSuccess', '配置加载成功'), 'success');
            } else {
                // 没有保存的配置，使用默认值
                applyDefaultConfig();
                showStatus(t('vrmEmotionManager.configUseDefault', '使用默认配置'), 'info');
            }
        } catch (error) {
            console.error('加载情感映射配置失败:', error);
            applyDefaultConfig();
        }
    }

    // 应用默认配置
    function applyDefaultConfig() {
        emotions.forEach(emotion => {
            const expressionMS = document.querySelector(`.emotion-expression-select[data-emotion="${emotion}"]`);

            if (expressionMS) {
                expressionMS.querySelectorAll('input').forEach(cb => { cb.checked = false; });

                const defaults = defaultMoodMap[emotion] || [];
                defaults.forEach(expr => {
                    const cb = expressionMS.querySelector(`input[value="${CSS.escape(expr)}"]`);
                    if (cb) cb.checked = true;
                });

                updateMultiselectHeader(expressionMS);
            }
        });
    }

    // 保存情感映射配置
    async function saveEmotionMapping() {
        if (!currentModelInfo) {
            showStatus(t('vrmEmotionManager.pleaseSelectModelFirst', '请先选择模型'), 'error');
            return;
        }

        const config = {};

        emotions.forEach(emotion => {
            const expressionMS = document.querySelector(`.emotion-expression-select[data-emotion="${emotion}"]`);

            if (expressionMS) {
                const selected = Array.from(expressionMS.querySelectorAll('input:checked')).map(cb => cb.value);
                if (selected.length > 0) config[emotion] = selected;
            }
        });

        try {
            const response = await fetch(`/api/model/vrm/emotion_mapping/${encodeURIComponent(currentModelInfo.name)}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(config)
            });

            if (!response.ok) {
                console.error(`保存情感映射配置失败: HTTP ${response.status}`, await response.text().catch(() => ''));
                showStatus(t('vrmEmotionManager.saveFailed', '保存失败') + `: HTTP ${response.status}`, 'error');
                return;
            }

            const data = await response.json();

            if (data.success) {
                showStatus(t('vrmEmotionManager.configSaveSuccess', '配置保存成功！'), 'success');
            } else {
                showStatus(t('vrmEmotionManager.saveFailed', '保存失败') + ': ' + (data.error || t('common.unknownError', '未知错误')), 'error');
            }
        } catch (error) {
            console.error('保存情感映射配置失败:', error);
            showStatus(t('vrmEmotionManager.saveFailed', '保存失败') + ': ' + error.message, 'error');
        }
    }

    // 重置配置
    function resetConfig() {
        applyDefaultConfig();
        showStatus(t('vrmEmotionManager.configReset', '已重置为默认配置'), 'info');
    }

    // 事件监听
    modelSelect.addEventListener('change', async (e) => {
        const selectedOption = e.target.options[e.target.selectedIndex];
        if (selectedOption.value && selectedOption.dataset.info) {
            currentModelInfo = JSON.parse(selectedOption.dataset.info);
            await loadModelExpressions(currentModelInfo.name, currentModelInfo);
            await loadEmotionMapping(currentModelInfo.name);
        } else {
            emotionConfig.style.display = 'none';
            currentModelInfo = null;
        }
    });

    saveBtn.addEventListener('click', saveEmotionMapping);
    resetBtn.addEventListener('click', resetConfig);

    // 初始化
    loadModelList();

    // 暴露到全局（用于调试）
    window.VRMEmotionManager = {
        t,
        showStatus,
        loadModelList,
        loadEmotionMapping,
        saveEmotionMapping,
        resetConfig
    };
})();
