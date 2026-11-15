/**
 * VRM 情感映射管理器 - JavaScript 模块
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
    const loadStatePanel = document.getElementById('emotion-load-state');
    const loadMessage = document.getElementById('emotion-load-message');
    const retryBtn = document.getElementById('retry-load-btn');
    const unavailableBindings = document.getElementById('unavailable-bindings');

    // 状态变量
    const emotions = ['neutral', 'happy', 'relaxed', 'sad', 'angry', 'surprised'];
    let currentModelInfo = null;
    let availableExpressions = [];
    let currentSelectionId = 0;
    let phase = 'idle';
    let capability = null;
    let operation = null;
    let saveController = null;
    let saveDeadline = null;
    let modelListController = null;
    let statusTimer = null;
    let previewTimer = null;
    let disposed = false;
    let retainedBindings = {};
    const LOAD_TIMEOUT_MS = 120000;

    function setState(state, message = '') {
        phase = state;
        const ready = state === 'ready';
        loadStatePanel.dataset.state = state;
        loadStatePanel.hidden = ready || state === 'idle';
        loadStatePanel.className = `status-message status-${state === 'failed' ? 'error' : 'info'}`;
        loadMessage.textContent = message;
        retryBtn.hidden = state !== 'failed';
        retryBtn.disabled = state === 'loading';
        emotionConfig.style.display = ready ? 'block' : 'none';
        emotionConfig.toggleAttribute('inert', !ready);
        emotionConfig.setAttribute('aria-disabled', String(!ready));
        emotionConfig.querySelectorAll('input, button').forEach(el => { el.disabled = !ready; });
        emotionConfig.querySelectorAll('.multiselect-header').forEach(el => {
            el.setAttribute('aria-disabled', String(!ready));
            el.tabIndex = ready ? 0 : -1;
        });
        saveBtn.disabled = !ready || !!saveController;
        resetBtn.disabled = !ready;
    }

    function getSource() {
        try {
            return window.opener && !window.opener.closed ? window.opener.VRMEmotionSource : null;
        } catch (_) {
            return null;
        }
    }

    function canOperate() {
        if (disposed || phase !== 'ready' || !capability) return false;
        const source = getSource();
        if (!source || !source.isCurrent(capability)) {
            setState('failed', t('vrmEmotionManager.sourceChanged', '预览模型已改变或不可用，请重试加载所选模型'));
            return false;
        }
        return true;
    }

    function waitForNextCheck(signal) {
        return new Promise((resolve, reject) => {
            if (signal.aborted) { reject(signal.reason); return; }
            const cancel = () => { clearTimeout(timer); signal.removeEventListener('abort', cancel); reject(signal.reason); };
            const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, 250);
            signal.addEventListener('abort', cancel, { once: true });
        });
    }

    function cancelOperation(cancelSave = true) {
        if (operation) {
            clearTimeout(operation.deadline);
            operation.controller.abort();
            operation = null;
        }
        if (cancelSave) {
            saveController?.abort();
            saveController = null;
            clearTimeout(saveDeadline);
            saveDeadline = null;
        }
        clearTimeout(previewTimer);
        previewTimer = null;
    }

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
        clearTimeout(statusTimer);
        statusMessage.textContent = message;
        statusMessage.className = `status-message status-${type}`;
        statusMessage.style.display = 'block';

        statusTimer = setTimeout(() => {
            statusTimer = null;
            statusMessage.style.display = 'none';
        }, 3000);
    }

    // 加载模型列表
    async function loadModelList() {
        modelListController?.abort();
        const controller = new AbortController();
        modelListController = controller;
        try {
            const response = await fetch('/api/model/vrm/models', { signal: controller.signal });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            const data = await response.json();
            if (disposed || controller.signal.aborted) return;

            if (data.success && Array.isArray(data.models) && data.models.length > 0) {
                modelSelect.innerHTML = `<option value="">${t('vrmEmotionManager.pleaseSelectModel', '请选择模型')}</option>`;
                modelSingleselectOptions.innerHTML = '';

                // Mapping APIs share one identity per exact stem, including built-in/user copies.
                const mappingNames = new Set();
                data.models.forEach(model => {
                    if (mappingNames.has(model.name)) return;
                    mappingNames.add(model.name);
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

                modelSingleselectText.textContent = t('vrmEmotionManager.pleaseSelectModel', '请选择模型');
            } else {
                modelSelect.innerHTML = `<option value="">${t('vrmEmotionManager.noModelsFound', '没有找到可用的VRM模型')}</option>`;
                modelSingleselectOptions.innerHTML = '';
                modelSingleselectText.textContent = t('vrmEmotionManager.noModelsFound', '没有找到可用的VRM模型');
                showStatus(t('vrmEmotionManager.noModelsFound', '没有找到可用的VRM模型，请先上传模型'), 'warning');
            }
        } catch (error) {
            if (disposed || controller.signal.aborted) return;
            console.error('加载模型列表失败:', error);
            showStatus(t('vrmEmotionManager.loadModelListFailed', '加载模型列表失败') + ': ' + error.message, 'error');
        } finally {
            if (modelListController === controller) modelListController = null;
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

        void loadModelExpressions(modelName, modelInfo, selectionId);
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
            
            // 检测下拉菜单是否超出视口，选择展开方向
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

    // Only the selected model's completed load can grant editing permission.
    async function loadModelExpressions(modelName, modelInfo, selectionId, force = false) {
        // A submitted save may already have committed. Keep its single bounded
        // request alive; its captured selection fences UI on late completion.
        cancelOperation(false);
        capability = null;
        availableExpressions = [];
        retainedBindings = {};
        unavailableBindings.hidden = true;
        previewButtons.replaceChildren();
        clearTimeout(statusTimer);
        statusTimer = null;
        statusMessage.style.display = 'none';
        setState('loading', t('vrmEmotionManager.waitingForModel', '正在加载所选模型，请稍候…'));
        const controller = new AbortController();
        const signal = controller.signal;
        const job = { controller, deadline: setTimeout(() => controller.abort(new Error(
            t('vrmEmotionManager.loadTimeout', '加载超时，请重试；原配置未修改'))), LOAD_TIMEOUT_MS) };
        operation = job;
        try {
            let result;
            while (!signal.aborted) {
                const source = getSource();
                if (!source) throw new Error(t('vrmEmotionManager.sourceUnavailable', '请从模型管理窗口打开此页面，并保持该窗口开启'));
                result = source.request(modelInfo, force);
                force = false;
                if (result.state !== 'loading') break;
                await waitForNextCheck(signal);
            }
            if (signal.aborted) throw signal.reason;
            if (selectionId !== currentSelectionId || disposed) return false;
            if (result.state === 'failed') throw new Error(result.error || t(
                `vrmEmotionManager.${result.errorCode || 'modelLoadFailed'}`, result.errorCode === 'recoveryBlocked'
                    ? '两次模型加载仍未结束，请等待完成或重新打开模型管理窗口后重试' : '所选模型未成功加载，请重试'));
            if (result.state !== 'ready' || !Array.isArray(result.expressions)
                || new URL(result.modelUrl, window.location.href).href !== new URL(modelInfo.url, window.location.href).href) {
                throw new Error(t('vrmEmotionManager.sourceChanged', '预览模型已改变或不可用，请重试加载所选模型'));
            }
            const realExpressions = [...new Set(result.expressions.filter(name => typeof name === 'string' && name))];
            if (!realExpressions.length) {
                setState('empty', t('vrmEmotionManager.noConfigurableExpressions', '模型已加载，但没有可配置的表情'));
                return false;
            }
            capability = result;
            // neutral is a reset operation, added only after genuine configurable expressions exist.
            availableExpressions = [...new Set(['neutral', ...realExpressions])];
            populateSelects();
            populatePreviewButtons();
            await loadEmotionMapping(modelName, selectionId, signal);
            if (signal.aborted || selectionId !== currentSelectionId || disposed) return false;
            if (!getSource()?.isCurrent(capability)) throw new Error(t('vrmEmotionManager.sourceChanged', '预览模型已改变或不可用，请重试加载所选模型'));
            setState('ready');
            showStatus(t('vrmEmotionManager.expressionsLoadedFromModel', '已从当前模型加载表情列表'), 'success');
            return true;
        } catch (error) {
            if (selectionId !== currentSelectionId || disposed || operation !== job) return false;
            capability = null;
            setState('failed', error?.message || t('vrmEmotionManager.modelLoadFailed', '所选模型未成功加载，请重试'));
            return false;
        } finally {
            clearTimeout(job.deadline);
            if (operation === job) operation = null;
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
                if (!canOperate() || !getSource().preview(capability, exprName)) return;
                // 移除其他按钮的playing状态
                document.querySelectorAll('.preview-btn').forEach(b => b.classList.remove('playing'));
                btn.classList.add('playing');

                // 3秒后取消playing状态
                clearTimeout(previewTimer);
                previewTimer = setTimeout(() => {
                    previewTimer = null;
                    btn.classList.remove('playing');
                }, 3000);
            });

            previewButtons.appendChild(btn);
        });
    }

    // 切换下拉菜单
    function toggleDropdown(event) {
        if (!canOperate()) return;
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
            
            // 检测下拉菜单是否超出视口，选择展开方向
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
                    item.setAttribute('role', 'option');

                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.value = expression;
                    checkbox.setAttribute('aria-label', expression);

                    const span = document.createElement('span');
                    span.textContent = expression;

                    item.appendChild(checkbox);
                    item.appendChild(span);

                    item.addEventListener('click', (e) => {
                        if (!canOperate()) return;
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
    async function loadEmotionMapping(modelName, selectionId = currentSelectionId, signal = operation?.controller.signal) {
        const response = await fetch(`/api/model/vrm/emotion_mapping/${encodeURIComponent(modelName)}`, { signal });
        if (!response.ok) throw new Error(t('vrmEmotionManager.configLoadFailed', '配置加载失败') + `: HTTP ${response.status}`);
        const data = await response.json();
        if (disposed || signal?.aborted || selectionId !== currentSelectionId) return;
        if (!data.success) throw new Error(t('vrmEmotionManager.configLoadFailed', '配置加载失败'));
        retainedBindings = {};
        if (!data.config) {
            applyDefaultConfig();
            return;
        }
        emotions.forEach(emotion => {
            const expressionMS = document.querySelector(`.emotion-expression-select[data-emotion="${emotion}"]`);
            expressionMS.querySelectorAll('input').forEach(cb => { cb.checked = false; });
            const value = data.config[emotion];
            const names = (Array.isArray(value) ? value : value ? [value] : []).filter(name => typeof name === 'string');
            names.forEach(name => {
                const cb = expressionMS.querySelector(`input[value="${CSS.escape(name)}"]`);
                if (cb) cb.checked = true;
            });
            const missing = [...new Set(names.filter(name => !availableExpressions.includes(name)))];
            if (missing.length) retainedBindings[emotion] = missing;
            updateMultiselectHeader(expressionMS);
        });
        const retained = Object.entries(retainedBindings).map(([emotion, names]) => `${emotion}: ${names.join(', ')}`);
        unavailableBindings.hidden = !retained.length;
        unavailableBindings.textContent = t('vrmEmotionManager.retainedBindings', '以下已有绑定在当前模型中不可配置，保留但不参与预览：') + ' ' + retained.join('; ');
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

        if (!canOperate() || saveController) return;
        const selectionId = currentSelectionId;
        const modelName = currentModelInfo.name;
        const controller = new AbortController();
        saveController = controller;
        saveBtn.disabled = true;
        const deadline = setTimeout(() => {
            controller.abort();
            if (!disposed && selectionId === currentSelectionId && saveController === controller) {
                showStatus(t('vrmEmotionManager.saveTimeout', '保存超时，结果未确认；请检查配置后再重试'), 'error');
            }
        }, LOAD_TIMEOUT_MS);
        saveDeadline = deadline;

        const config = {};

        emotions.forEach(emotion => {
            const expressionMS = document.querySelector(`.emotion-expression-select[data-emotion="${emotion}"]`);

            if (expressionMS) {
                const selected = [...new Set([
                    ...Array.from(expressionMS.querySelectorAll('input:checked')).map(cb => cb.value),
                    ...(retainedBindings[emotion] || [])
                ])];
                if (selected.length > 0) config[emotion] = selected;
            }
        });

        try {
            const response = await fetch(`/api/model/vrm/emotion_mapping/${encodeURIComponent(modelName)}`, {
                method: 'POST',
                signal: controller.signal,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(config)
            });

            if (disposed || controller.signal.aborted || selectionId !== currentSelectionId) return;

            if (!response.ok) {
                console.error(`保存情感映射配置失败: HTTP ${response.status}`, await response.text().catch(() => ''));
                if (disposed || controller.signal.aborted || selectionId !== currentSelectionId) return;
                showStatus(t('vrmEmotionManager.saveFailed', '保存失败') + `: HTTP ${response.status}`, 'error');
                return;
            }

            const data = await response.json();
            if (disposed || controller.signal.aborted || selectionId !== currentSelectionId) return;

            if (data.success) {
                showStatus(t('vrmEmotionManager.configSaveSuccess', '配置保存成功！'), 'success');
            } else {
                showStatus(t('vrmEmotionManager.saveFailed', '保存失败') + ': ' + (data.error || t('common.unknownError', '未知错误')), 'error');
            }
        } catch (error) {
            if (disposed || controller.signal.aborted || selectionId !== currentSelectionId) return;
            console.error('保存情感映射配置失败:', error);
            showStatus(t('vrmEmotionManager.saveFailed', '保存失败') + ': ' + error.message, 'error');
        } finally {
            clearTimeout(deadline);
            if (saveController === controller) {
                saveController = null;
                saveDeadline = null;
                saveBtn.disabled = !canOperate();
            }
        }
    }

    // 重置配置
    function resetConfig() {
        if (!canOperate()) return;
        retainedBindings = {};
        unavailableBindings.hidden = true;
        unavailableBindings.textContent = '';
        applyDefaultConfig();
        showStatus(t('vrmEmotionManager.configReset', '已重置为默认配置'), 'info');
    }

    // 事件监听
    saveBtn.addEventListener('click', saveEmotionMapping);
    resetBtn.addEventListener('click', resetConfig);
    retryBtn.addEventListener('click', () => {
        if (disposed || !currentModelInfo || phase === 'loading') return;
        currentSelectionId += 1;
        void loadModelExpressions(currentModelInfo.name, currentModelInfo, currentSelectionId, true);
    });

    function dispose(event) {
        if (event?.persisted) return;
        disposed = true;
        currentSelectionId += 1;
        cancelOperation();
        modelListController?.abort();
        modelListController = null;
        clearTimeout(statusTimer);
        statusTimer = null;
        capability = null;
        retainedBindings = {};
        availableExpressions = [];
        window.removeEventListener('pagehide', dispose);
        window.removeEventListener('focus', recheckCapability);
    }
    function recheckCapability() { if (phase === 'ready') canOperate(); }
    window.addEventListener('pagehide', dispose);
    window.addEventListener('focus', recheckCapability);

    // 初始化
    loadModelList();

    // 暴露到全局（用于调试）
    window.VRMEmotionManager = {
        t,
        showStatus,
        loadModelList,
        saveEmotionMapping,
        resetConfig,
        dispose
    };
})();
