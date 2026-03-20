/**
 * MMD 情感映射管理器 - JavaScript 模块
 * 基于 vrm_emotion_manager.js 适配 MMD MorphTarget 系统
 */

(function() {
    'use strict';

    // DOM 元素
    const modelSelect = document.getElementById('model-select');
    const modelSingleselect = document.getElementById('model-singleselect');
    const modelSingleselectHeader = modelSingleselect.querySelector('.singleselect-header');
    const modelSingleselectText = modelSingleselect.querySelector('.selected-text');
    const modelSingleselectOptions = modelSingleselect.querySelector('.singleselect-options');
    const emotionConfig = document.getElementById('emotion-config');
    const saveBtn = document.getElementById('save-btn');
    const resetBtn = document.getElementById('reset-btn');
    const statusMessage = document.getElementById('status-message');
    const previewButtons = document.getElementById('preview-buttons');

    // 状态变量
    const emotions = ['neutral', 'happy', 'relaxed', 'sad', 'angry', 'surprised', 'fear'];
    let currentModelInfo = null;
    let availableMorphs = [];
    let currentSelectionId = 0;
    let _statusHideTimer = null;

    // 下拉菜单位置计算辅助函数
    function computeDropdownPlacement(header, options, maxHeight = 250) {
        const viewportHeight = window.innerHeight;
        const headerRect = header.getBoundingClientRect();
        const optionsHeight = Math.min(options.scrollHeight, maxHeight);
        const gap = 8;
        const spaceBelow = viewportHeight - headerRect.bottom - gap;
        const spaceAbove = headerRect.top - gap;

        let placement, maxHeightValue;

        if (spaceBelow >= optionsHeight) {
            placement = 'open-down';
            maxHeightValue = maxHeight;
        } else if (spaceAbove >= optionsHeight) {
            placement = 'open-up';
            maxHeightValue = maxHeight;
        } else if (spaceBelow > spaceAbove) {
            placement = 'open-down';
            maxHeightValue = Math.floor(spaceBelow);
        } else {
            placement = 'open-up';
            maxHeightValue = Math.floor(spaceAbove);
        }

        return { placement, maxHeight: maxHeightValue };
    }

    // 应用下拉菜单位置
    function applyDropdownDirection(container, header, options, maxHeight = 250) {
        const { placement, maxHeight: computedMaxHeight } = computeDropdownPlacement(header, options, maxHeight);

        container.classList.toggle('open-up', placement === 'open-up');
        container.classList.toggle('open-down', placement === 'open-down');
        options.style.maxHeight = `${computedMaxHeight}px`;

        requestAnimationFrame(() => {
            if (options.scrollHeight > options.clientHeight) {
                options.classList.add('has-scrollbar');
            } else {
                options.classList.remove('has-scrollbar');
            }
        });
    }

    // i18n 辅助函数
    function t(key, paramsOrFallback, fallback) {
        if (typeof i18next !== 'undefined' && i18next.isInitialized) {
            return i18next.t(key, paramsOrFallback);
        }
        if (typeof paramsOrFallback === 'string') {
            return paramsOrFallback;
        }
        return fallback || key.split('.').pop();
    }

    // 默认 moodMap（与 mmd-expression.js 保持一致）
    const defaultMoodMap = {
        'neutral': ['default', 'ニュートラル'],
        'happy': ['笑い', 'にやり', 'にこり', 'smile', 'happy', 'joy', 'ワ'],
        'sad': ['悲しい', '泣き', 'sad', 'sorrow', 'しょんぼり'],
        'angry': ['怒り', 'angry', 'anger', 'むっ'],
        'surprised': ['驚き', 'びっくり', 'surprised', 'shock', 'おっ'],
        'relaxed': ['穏やか', 'relaxed', 'calm', '微笑み'],
        'fear': ['恐怖', 'fear', 'scared', 'おびえ']
    };

    // 显示状态消息
    function showStatus(message, type = 'info') {
        statusMessage.textContent = message;
        statusMessage.className = `status-message status-${type}`;
        statusMessage.style.display = 'block';

        clearTimeout(_statusHideTimer);
        _statusHideTimer = setTimeout(() => {
            statusMessage.style.display = 'none';
            _statusHideTimer = null;
        }, 3000);
    }

    // 加载模型列表
    async function loadModelList() {
        try {
            const response = await fetch('/api/model/mmd/models');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            const data = await response.json();

            if (data.success && Array.isArray(data.models) && data.models.length > 0) {
                modelSelect.innerHTML = `<option value="">${t('mmdEmotionManager.pleaseSelectModel', '请选择模型')}</option>`;
                modelSingleselectOptions.innerHTML = '';

                data.models.forEach(model => {
                    const option = document.createElement('option');
                    option.value = model.name;
                    option.dataset.info = JSON.stringify(model);
                    option.textContent = model.name;
                    modelSelect.appendChild(option);

                    const item = document.createElement('div');
                    item.className = 'singleselect-item';
                    item.setAttribute('role', 'option');
                    item.setAttribute('tabindex', '0');
                    item.setAttribute('aria-selected', 'false');
                    item.dataset.value = model.name;
                    item.dataset.info = JSON.stringify(model);
                    item.textContent = model.name;
                    item.addEventListener('click', () => selectModelFromDropdown(model.name, model));
                    item.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            selectModelFromDropdown(model.name, model);
                            modelSingleselect.classList.remove('active', 'open-up', 'open-down');
                            modelSingleselectHeader.setAttribute('aria-expanded', 'false');
                        }
                    });
                    modelSingleselectOptions.appendChild(item);
                });

                modelSingleselectText.textContent = t('mmdEmotionManager.pleaseSelectModel', '请选择模型');
            } else {
                modelSelect.innerHTML = `<option value="">${t('mmdEmotionManager.noModelsFound', '没有找到可用的MMD模型')}</option>`;
                modelSingleselectOptions.innerHTML = '';
                modelSingleselectText.textContent = t('mmdEmotionManager.noModelsFound', '没有找到可用的MMD模型');
                showStatus(t('mmdEmotionManager.noModelsFound', '没有找到可用的MMD模型，请先上传模型'), 'warning');
            }
        } catch (error) {
            console.error('加载模型列表失败:', error);
            showStatus(t('mmdEmotionManager.loadModelListFailed', '加载模型列表失败') + ': ' + error.message, 'error');
        }
    }

    // 从下拉框选择模型
    function selectModelFromDropdown(modelName, modelInfo) {
        currentSelectionId++;
        const selectionId = currentSelectionId;

        currentModelInfo = modelInfo;
        modelSelect.value = modelName;
        modelSingleselectText.textContent = modelName;
        modelSingleselect.classList.remove('active', 'open-up', 'open-down');
        modelSingleselectHeader.setAttribute('aria-expanded', 'false');

        modelSingleselectOptions.querySelectorAll('.singleselect-item').forEach(item => {
            const isSelected = item.dataset.value === modelName;
            item.classList.toggle('selected', isSelected);
            item.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        });

        loadModelMorphs(modelName, selectionId).then((success) => {
            if (success && selectionId === currentSelectionId) {
                loadEmotionMapping(modelName, selectionId);
            }
        });
    }

    // 切换模型选择下拉框
    function toggleModelDropdown(event) {
        const wasActive = modelSingleselect.classList.contains('active');

        document.querySelectorAll('.custom-multiselect').forEach(ms => {
            ms.classList.remove('active', 'open-up', 'open-down');
            const h = ms.querySelector('.multiselect-header');
            if (h) h.setAttribute('aria-expanded', 'false');
        });

        if (wasActive) {
            modelSingleselect.classList.remove('active', 'open-up', 'open-down');
            modelSingleselectHeader.setAttribute('aria-expanded', 'false');
        } else {
            modelSingleselect.classList.add('active');
            modelSingleselectHeader.setAttribute('aria-expanded', 'true');
            applyDropdownDirection(modelSingleselect, modelSingleselectHeader, modelSingleselectOptions, 250);
        }

        event.stopPropagation();
    }

    modelSingleselectHeader.addEventListener('click', toggleModelDropdown);
    modelSingleselectHeader.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleModelDropdown(e);
        }
    });

    // 从父窗口获取模型 Morph 列表
    function getMorphsFromParentWindow() {
        return new Promise((resolve) => {
            // 直接从父窗口的 mmdManager 获取
            if (window.opener && !window.opener.closed && window.opener.mmdManager && window.opener.mmdManager.expression) {
                const morphs = window.opener.mmdManager.expression.getMorphNames();
                if (morphs && morphs.length > 0) {
                    console.log('[MMD Emotion] 从父窗口获取到 Morph 列表:', morphs.length, '个');
                    resolve(morphs);
                    return;
                }
            }

            // 尝试通过 postMessage 获取
            if (window.opener && !window.opener.closed) {
                const messageHandler = (event) => {
                    if (event.origin !== window.location.origin) {
                        return;
                    }
                    if (event.data && event.data.type === 'mmd-morphs-response') {
                        window.removeEventListener('message', messageHandler);
                        if (event.data.morphs && event.data.morphs.length > 0) {
                            console.log('[MMD Emotion] 通过 postMessage 获取到 Morph 列表:', event.data.morphs.length, '个');
                            resolve(event.data.morphs);
                        } else {
                            resolve(null);
                        }
                    }
                };
                window.addEventListener('message', messageHandler);
                window.opener.postMessage({ type: 'mmd-get-morphs' }, window.location.origin);

                setTimeout(() => {
                    window.removeEventListener('message', messageHandler);
                    resolve(null);
                }, 3000);
            } else {
                resolve(null);
            }
        });
    }

    // 加载模型 Morph 列表
    async function loadModelMorphs(modelName, selectionId) {
        // 优先从父窗口获取
        let morphsFromParent = null;
        try {
            morphsFromParent = await getMorphsFromParentWindow();
        } catch (e) {
            console.warn('[MMD Emotion] 从父窗口获取 Morph 列表失败:', e);
        }

        if (selectionId !== currentSelectionId) {
            return false;
        }

        if (morphsFromParent && morphsFromParent.length > 0) {
            availableMorphs = morphsFromParent;
            populateSelects();
            populatePreviewButtons();
            emotionConfig.style.display = 'block';
            showStatus(t('mmdEmotionManager.morphsLoadedFromModel', '已从当前模型加载 Morph 列表'), 'success');
            return true;
        }

        // 无法从父窗口获取时，使用默认 Morph 列表（常见 MMD morph 名称）
        availableMorphs = [
            // 表情
            'default', 'ニュートラル',
            '笑い', 'にやり', 'にこり', 'smile', 'happy', 'joy', 'ワ',
            '悲しい', '泣き', 'sad', 'sorrow', 'しょんぼり',
            '怒り', 'angry', 'anger', 'むっ',
            '驚き', 'びっくり', 'surprised', 'shock', 'おっ',
            '穏やか', 'relaxed', 'calm', '微笑み',
            '恐怖', 'fear', 'scared', 'おびえ',
            // 眨眼
            'まばたき', 'blink',
            // 口型
            'あ', 'い', 'う', 'え', 'お',
            'a', 'i', 'u', 'e', 'o'
        ];
        populateSelects();
        populatePreviewButtons();
        emotionConfig.style.display = 'block';
        showStatus(t('mmdEmotionManager.useDefaultMorphs', '使用默认 Morph 列表（请先在主页面加载模型以获取精确列表）'), 'info');
        return true;
    }

    // 填充预览按钮
    function populatePreviewButtons() {
        previewButtons.innerHTML = '';

        // 过滤掉眨眼和口型相关的 morph
        const excludeNames = new Set([
            'まばたき', 'blink', 'まばたき左', 'まばたき右', 'blink_l', 'blink_r',
            'あ', 'い', 'う', 'え', 'お', 'a', 'i', 'u', 'e', 'o'
        ]);
        const filteredMorphs = availableMorphs.filter(name => !excludeNames.has(name));

        if (filteredMorphs.length === 0) {
            previewButtons.innerHTML = `<span style="color: var(--color-text-muted);">${t('mmdEmotionManager.noMorphsFound', '没有可用的 Morph')}</span>`;
            return;
        }

        filteredMorphs.forEach(morphName => {
            const btn = document.createElement('button');
            btn.className = 'preview-btn';
            btn.textContent = morphName;
            btn.dataset.morph = morphName;

            btn.addEventListener('click', () => {
                // 移除其他按钮的 playing 状态
                document.querySelectorAll('.preview-btn').forEach(b => b.classList.remove('playing'));
                btn.classList.add('playing');

                // 发送 morph 预览到父窗口
                if (window.opener && !window.opener.closed && window.opener.mmdManager && window.opener.mmdManager.expression) {
                    // 先清除所有情感 morph
                    window.opener.mmdManager.expression._clearEmotionMorphs();
                    // 设置预览的 morph
                    window.opener.mmdManager.expression.setMorphWeight(morphName, 1.0);
                } else {
                    window.opener?.postMessage({
                        type: 'mmd-preview-morph',
                        morph: morphName
                    }, window.location.origin);
                }

                // 3秒后取消 playing 状态并清除 morph
                setTimeout(() => {
                    btn.classList.remove('playing');
                    if (window.opener && !window.opener.closed && window.opener.mmdManager && window.opener.mmdManager.expression) {
                        window.opener.mmdManager.expression.setMorphWeight(morphName, 0);
                    }
                }, 3000);
            });

            previewButtons.appendChild(btn);
        });
    }

    // 切换多选下拉菜单
    function toggleDropdown(event) {
        const multiselect = event.currentTarget.closest('.custom-multiselect');
        const header = multiselect.querySelector('.multiselect-header');
        const options = multiselect.querySelector('.multiselect-options');
        const wasActive = multiselect.classList.contains('active');

        // 关闭所有其他下拉菜单
        document.querySelectorAll('.custom-multiselect').forEach(ms => {
            ms.classList.remove('active', 'open-up', 'open-down');
            const h = ms.querySelector('.multiselect-header');
            if (h) h.setAttribute('aria-expanded', 'false');
        });
        modelSingleselect.classList.remove('active', 'open-up', 'open-down');
        modelSingleselectHeader.setAttribute('aria-expanded', 'false');

        if (!wasActive) {
            multiselect.classList.add('active');
            if (header) header.setAttribute('aria-expanded', 'true');
            if (options) {
                applyDropdownDirection(multiselect, header, options, 250);
            }
        }

        event.stopPropagation();
    }

    // 点击外部关闭下拉菜单
    window.addEventListener('click', () => {
        document.querySelectorAll('.custom-multiselect').forEach(ms => {
            ms.classList.remove('active', 'open-up', 'open-down');
            const h = ms.querySelector('.multiselect-header');
            if (h) h.setAttribute('aria-expanded', 'false');
        });
        modelSingleselect.classList.remove('active', 'open-up', 'open-down');
        modelSingleselectHeader.setAttribute('aria-expanded', 'false');
    });

    // 更新头部显示
    function updateMultiselectHeader(multiselect) {
        const checkboxes = multiselect.querySelectorAll('input[type="checkbox"]:checked');
        const headerContainer = multiselect.querySelector('.selected-text');

        headerContainer.innerHTML = '';

        if (checkboxes.length === 0) {
            headerContainer.textContent = t('mmdEmotionManager.selectMorph', '选择 Morph');
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
            const morphContainer = document.querySelector(`.emotion-morph-select[data-emotion="${emotion}"] .multiselect-options`);

            if (morphContainer) {
                morphContainer.innerHTML = '';
                morphContainer.onclick = (e) => e.stopPropagation();

                availableMorphs.forEach(morph => {
                    const item = document.createElement('div');
                    item.className = 'multiselect-item';
                    item.setAttribute('role', 'option');

                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.value = morph;
                    checkbox.setAttribute('aria-label', morph);

                    const span = document.createElement('span');
                    span.textContent = morph;

                    item.appendChild(checkbox);
                    item.appendChild(span);

                    item.addEventListener('click', (e) => {
                        if (e.target.tagName !== 'INPUT') {
                            checkbox.checked = !checkbox.checked;
                        }
                        updateMultiselectHeader(morphContainer.closest('.custom-multiselect'));
                        e.stopPropagation();
                    });
                    morphContainer.appendChild(item);
                });

                updateMultiselectHeader(morphContainer.closest('.custom-multiselect'));

                const header = morphContainer.closest('.custom-multiselect').querySelector('.multiselect-header');
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
    async function loadEmotionMapping(modelName, selectionId) {
        try {
            const response = await fetch(`/api/model/mmd/emotion_mapping?model=${encodeURIComponent(modelName)}`);

            if (selectionId != null && selectionId !== currentSelectionId) return;

            if (!response.ok) {
                console.error(`加载情感映射配置失败: HTTP ${response.status}`, await response.text().catch(() => ''));
                applyDefaultConfig();
                showStatus(t('mmdEmotionManager.configLoadFailed', '配置加载失败'), 'error');
                return;
            }

            const data = await response.json();

            if (selectionId != null && selectionId !== currentSelectionId) return;

            if (data.success && data.mapping && Object.keys(data.mapping).length > 0) {
                const config = data.mapping;

                emotions.forEach(emotion => {
                    const morphMS = document.querySelector(`.emotion-morph-select[data-emotion="${emotion}"]`);

                    if (morphMS) {
                        morphMS.querySelectorAll('input').forEach(cb => { cb.checked = false; });
                        updateMultiselectHeader(morphMS);
                    }

                    if (config[emotion]) {
                        const morphNames = Array.isArray(config[emotion]) ? config[emotion] : [config[emotion]];
                        if (morphMS) {
                            morphNames.forEach(name => {
                                const cb = morphMS.querySelector(`input[value="${CSS.escape(name)}"]`);
                                if (cb) cb.checked = true;
                            });
                            updateMultiselectHeader(morphMS);
                        }
                    }
                });

                showStatus(t('mmdEmotionManager.configLoadSuccess', '配置加载成功'), 'success');
            } else {
                applyDefaultConfig();
                showStatus(t('mmdEmotionManager.configUseDefault', '使用默认配置'), 'info');
            }
        } catch (error) {
            console.error('加载情感映射配置失败:', error);
            if (selectionId == null || selectionId === currentSelectionId) {
                applyDefaultConfig();
            }
        }
    }

    // 应用默认配置
    function applyDefaultConfig() {
        emotions.forEach(emotion => {
            const morphMS = document.querySelector(`.emotion-morph-select[data-emotion="${emotion}"]`);

            if (morphMS) {
                morphMS.querySelectorAll('input').forEach(cb => { cb.checked = false; });

                const defaults = defaultMoodMap[emotion] || [];
                defaults.forEach(name => {
                    const cb = morphMS.querySelector(`input[value="${CSS.escape(name)}"]`);
                    if (cb) cb.checked = true;
                });

                updateMultiselectHeader(morphMS);
            }
        });
    }

    // 保存情感映射配置
    async function saveEmotionMapping() {
        if (!currentModelInfo) {
            showStatus(t('mmdEmotionManager.pleaseSelectModelFirst', '请先选择模型'), 'error');
            return;
        }

        const mapping = {};

        emotions.forEach(emotion => {
            const morphMS = document.querySelector(`.emotion-morph-select[data-emotion="${emotion}"]`);

            if (morphMS) {
                const selected = Array.from(morphMS.querySelectorAll('input:checked')).map(cb => cb.value);
                if (selected.length > 0) mapping[emotion] = selected;
            }
        });

        try {
            const response = await fetch('/api/model/mmd/emotion_mapping', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    model: currentModelInfo.name,
                    mapping: mapping
                })
            });

            if (!response.ok) {
                console.error(`保存情感映射配置失败: HTTP ${response.status}`, await response.text().catch(() => ''));
                showStatus(t('mmdEmotionManager.saveFailed', '保存失败') + `: HTTP ${response.status}`, 'error');
                return;
            }

            const data = await response.json();

            if (data.success) {
                showStatus(t('mmdEmotionManager.configSaveSuccess', '配置保存成功！'), 'success');

                // 通知父窗口重新加载 moodMap（仅当父窗口当前模型与编辑的模型一致时）
                if (window.opener && !window.opener.closed && window.opener.mmdManager && window.opener.mmdManager.expression) {
                    const parentModel = window.opener.mmdManager.currentModel;
                    if (parentModel && parentModel.name === currentModelInfo.name) {
                        window.opener.mmdManager.expression.loadMoodMap(currentModelInfo.name);
                    }
                }
            } else {
                showStatus(t('mmdEmotionManager.saveFailed', '保存失败') + ': ' + (data.error || t('common.unknownError', '未知错误')), 'error');
            }
        } catch (error) {
            console.error('保存情感映射配置失败:', error);
            showStatus(t('mmdEmotionManager.saveFailed', '保存失败') + ': ' + error.message, 'error');
        }
    }

    // 重置配置
    function resetConfig() {
        applyDefaultConfig();
        showStatus(t('mmdEmotionManager.configReset', '已重置为默认配置'), 'info');
    }

    // 事件监听
    saveBtn.addEventListener('click', saveEmotionMapping);
    resetBtn.addEventListener('click', resetConfig);

    // 监听父窗口的 postMessage（用于获取 Morph 列表）
    window.addEventListener('message', (event) => {
        if (event.origin !== window.location.origin) return;

        if (event.data && event.data.type === 'mmd-morphs-response') {
            // 已在 getMorphsFromParentWindow 中处理
        }
    });

    // 初始化
    loadModelList();

    // 暴露到全局（用于调试）
    window.MMDEmotionManager = {
        t,
        showStatus,
        loadModelList,
        loadEmotionMapping,
        saveEmotionMapping,
        resetConfig
    };
})();
