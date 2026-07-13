// Part responsibility: detail-form construction, voice selectors, save, switch, and delete actions.

function buildCatgirlDetailForm(name, rawData, isNew, container) {
    const previousForm = container && typeof container.querySelector === 'function'
        ? container.querySelector('form')
        : null;
    if (previousForm && previousForm._voiceSelectCleanup) {
        previousForm._voiceSelectCleanup();
    }
    if (previousForm && previousForm._characterPersonalityUpdateHandler) {
        window.removeEventListener('neko:character-personality-updated', previousForm._characterPersonalityUpdateHandler);
    }

    let cat = rawData || {};
    let form = document.createElement('form');
    form.id = name ? 'catgirl-form-' + name : 'catgirl-form-new';
    form.style.padding = '0';
    form._catgirlName = name;
    form._isNew = !!isNew;
    form.onsubmit = function (e) { e.preventDefault(); };

    // 档案名
    const baseWrapper = document.createElement('div');
    baseWrapper.className = 'field-row-wrapper profile-row';

    const baseLabel = document.createElement('label');
    const profileNameText = (window.t && typeof window.t === 'function') ? window.t('character.profileName') : '档案名';
    const requiredText = (window.t && typeof window.t === 'function') ? window.t('character.required') : '*';
    baseLabel.innerHTML = '<span data-i18n="character.profileName">' + profileNameText + '</span><span style="color:red" data-i18n="character.required">' + requiredText + '</span>';
    baseWrapper.appendChild(baseLabel);

    const fieldRow = document.createElement('div');
    fieldRow.className = 'field-row';
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.name = '档案名';
    nameInput.required = true;
    nameInput.value = name || '';
    if (!isNew) nameInput.readOnly = true;
    // 新建猫娘时，名称变化后重置自动创建状态
    if (isNew) {
        nameInput.addEventListener('change', function () {
            if (form._autoCreated && form._autoCreatedName !== nameInput.value.trim()) {
                form._autoCreatedDetachedName = form._autoCreatedName;
                form._autoCreated = false;
                form._autoCreatedName = '';
            }
        });
    }
    _panelAttachProfileNameLimiter(nameInput);
    fieldRow.appendChild(nameInput);
    baseWrapper.appendChild(fieldRow);

    // 重命名按钮（非新建时显示）
    if (!isNew) {
        const renameBtn = document.createElement('button');
        renameBtn.type = 'button';
        renameBtn.className = 'btn sm row-action-btn rename-action';
        renameBtn.id = 'rename-catgirl-btn';
        const renameText = (window.t && typeof window.t === 'function')
            ? '<img src="/static/icons/edit.png" alt="" class="edit-icon"> <span data-i18n="character.rename">' + window.t('character.rename') + '</span>'
            : '<img src="/static/icons/edit.png" alt="" class="edit-icon"> 修改名称';
        renameBtn.innerHTML = renameText;
        renameBtn.addEventListener('click', async function () {
            let newName;
            if (typeof showPrompt === 'function') {
                newName = await showPrompt(
                    window.t ? window.t('character.renamePrompt') : '请输入新的档案名',
                    name,
                    window.t ? window.t('character.renameTitle') : '修改名称'
                );
            } else {
                newName = prompt(window.t ? window.t('character.renamePrompt') : '请输入新的档案名', name);
            }
            if (!newName || newName.trim() === '' || newName.trim() === name) return;
            const normalizedNewName = newName.trim();
            if (!(await ensureValidCharacterProfileName(normalizedNewName))) {
                return;
            }
            try {
                const resp = await fetch('/api/characters/catgirl/' + encodeURIComponent(name) + '/rename', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_name: normalizedNewName })
                });
                const result = await resp.json();
                if (result.success) {
                    closeCatgirlPanel();
                    await loadCharacterCards();
                    showMessage(window.t ? window.t('character.renameSuccess') : '重命名成功', 'success');
                } else {
                    const errMsg = result.error || (window.t ? window.t('character.renameFailed') : '重命名失败');
                    if (typeof showAlert === 'function') {
                        await showAlert(errMsg);
                    } else {
                        alert(errMsg);
                    }
                }
            } catch (e) {
                console.error('重命名失败:', e);
                if (typeof showAlert === 'function') {
                    const errorMessage = e.message || String(e);
                    await showAlert(window.t ? window.t('character.renameError', { error: errorMessage }) : '重命名失败: ' + errorMessage);
                }
            }
        });
        baseWrapper.appendChild(renameBtn);
    }
    form.appendChild(baseWrapper);

    // 自定义字段
    const ALL_RESERVED = typeof getWorkshopHiddenFields === 'function' ? ['档案名', ...getWorkshopHiddenFields()] : ['档案名'];
    const renderedCustomFields = new Set();
    getOrderedCharacterFieldKeys(cat, ALL_RESERVED).forEach(k => {
        const normalizedKey = normalizeCharacterFieldName(k);
        if (!normalizedKey || ALL_RESERVED.includes(normalizedKey) || renderedCustomFields.has(normalizedKey)) return;
        const val = cat[k];
        if (val === null || val === undefined) return;
        renderedCustomFields.add(normalizedKey);

        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row setting-field-row';

        const labelEl = document.createElement('label');
        _panelSetFieldLabel(labelEl, normalizedKey);
        wrapper.appendChild(labelEl);

        const fr = document.createElement('div');
        fr.className = 'field-row';
        const textareaEl = document.createElement('textarea');
        textareaEl.name = normalizedKey;
        textareaEl.rows = 1;
        textareaEl.placeholder = (window.t && typeof window.t === 'function')
            ? window.t('character.detailDescriptionPlaceholder')
            : '可输入详细描述';
        textareaEl.value = cat[k];
        fr.appendChild(textareaEl);
        wrapper.appendChild(fr);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn sm delete row-action-btn delete-action setting-field-delete';
        _panelConfigureFieldDeleteButton(delBtn);
        delBtn.addEventListener('click', function () {
            wrapper.remove();
            const sb = form.querySelector('#save-button');
            const cb = form.querySelector('#cancel-button');
            if (sb) sb.style.display = '';
            if (cb) cb.style.display = '';
        });
        wrapper.appendChild(delBtn);

        form.appendChild(wrapper);

        // textarea自动调整高度
        _panelAttachTextareaAutoResize(textareaEl);
    });

    // 新增设定按钮区
    const addFieldArea = document.createElement('div');
    addFieldArea.className = 'btn-area add-field-area settings-toolbar-row';
    addFieldArea.style.display = 'flex';
    addFieldArea.style.alignItems = 'center';
    addFieldArea.style.marginTop = '10px';
    addFieldArea.style.marginBottom = '10px';
    addFieldArea.style.gap = '12px';

    const addFieldLabelPlaceholder = document.createElement('div');
    addFieldLabelPlaceholder.style.minWidth = '80px';
    addFieldLabelPlaceholder.style.flexShrink = '0';
    addFieldArea.appendChild(addFieldLabelPlaceholder);

    const addFieldSpacer = document.createElement('div');
    addFieldSpacer.style.flex = '1';
    addFieldArea.appendChild(addFieldSpacer);

    // 猫猫辅助生成按钮（位于「新增设定」左侧）。
    // `settings-secondary-action` 是 grid placement marker —— 详情面板的 settings
    // toolbar row 用 CSS Grid 把 `.btn.sm` 默认塞到 grid-column: 4；不显式标 col
    // 的话 AI 按钮和 Add 按钮会在同一列里堆成上下两行。靠这个 class 把它推到
    // col 3，跟 `.settings-save-action` / `.settings-cancel-action` 是同一套 pattern。
    const aiAssistBtn = document.createElement('button');
    aiAssistBtn.type = 'button';
    aiAssistBtn.className = 'btn sm ai-assist settings-secondary-action';
    aiAssistBtn.id = 'panel-ai-assist-catgirl-btn';
    aiAssistBtn.style.minWidth = '140px';
    const aiAssistText = (window.t && typeof window.t === 'function')
        ? '<span class="ai-assist-icon" aria-hidden="true">✨</span> <span data-i18n="character.aiAssist">' + window.t('character.aiAssist') + '</span>'
        : '<span class="ai-assist-icon" aria-hidden="true">✨</span> <span data-i18n="character.aiAssist">猫猫辅助生成</span>';
    aiAssistBtn.innerHTML = aiAssistText;
    aiAssistBtn.onclick = function () {
        try {
            openCardAssistCompanion(form, name, isNew);
        } catch (err) {
            console.error('[card-assist] open companion failed:', err);
            if (typeof showAlertDialog === 'function') {
                showAlertDialog(String(err && err.message || err), { type: 'error' });
            }
        }
    };
    addFieldArea.appendChild(aiAssistBtn);

    const addFieldBtn = document.createElement('button');
    addFieldBtn.type = 'button';
    addFieldBtn.className = 'btn sm add settings-primary-action';
    addFieldBtn.id = 'panel-add-catgirl-field-btn';
    const addFieldText = (window.t && typeof window.t === 'function')
        ? '<img src="/static/icons/add.png" alt="" class="add-icon"> <span data-i18n="character.addField">' + window.t('character.addField') + '</span>'
        : '<img src="/static/icons/add.png" alt="" class="add-icon"> 新增设定';
    addFieldBtn.innerHTML = addFieldText;
    addFieldBtn.onclick = async function () {
        let key;
        if (typeof showPrompt === 'function') {
            key = await showPrompt(
                window.t ? window.t('character.addCatgirlFieldPrompt') : '请输入新设定的名称（键名）',
                '',
                window.t ? window.t('character.addCatgirlFieldTitle') : '新增猫娘设定'
            );
        } else {
            key = prompt(window.t ? window.t('character.addCatgirlFieldPrompt') : '请输入新设定的名称（键名）');
        }
        key = normalizeCharacterFieldName(key);
        const FORBIDDEN = ALL_RESERVED;
        if (!key || FORBIDDEN.includes(key)) return;
        if (Array.from(form.querySelectorAll('input, textarea, select')).some(el => normalizeCharacterFieldName(el.name) === key)) {
            if (typeof showAlert === 'function') {
                await showAlert(window.t ? window.t('character.fieldExists') : '该设定已存在');
            } else {
                alert(window.t ? window.t('character.fieldExists') : '该设定已存在');
            }
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row setting-field-row';

        const labelEl = document.createElement('label');
        _panelSetFieldLabel(labelEl, key);
        wrapper.appendChild(labelEl);

        const fr = document.createElement('div');
        fr.className = 'field-row';
        const textareaEl = document.createElement('textarea');
        textareaEl.name = key;
        textareaEl.rows = 1;
        textareaEl.placeholder = (window.t && typeof window.t === 'function')
            ? window.t('character.detailDescriptionPlaceholder')
            : '可输入详细描述';
        fr.appendChild(textareaEl);
        wrapper.appendChild(fr);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn sm delete row-action-btn delete-action setting-field-delete';
        _panelConfigureFieldDeleteButton(delBtn);
        delBtn.addEventListener('click', function () {
            wrapper.remove();
            if (saveButton) saveButton.style.display = '';
            if (cancelButton) cancelButton.style.display = '';
        });
        wrapper.appendChild(delBtn);

        form.insertBefore(wrapper, addFieldArea);
        _panelAttachTextareaAutoResize(textareaEl);
        if (!isNew && name) {
            panelAttachAutoSaveListener(textareaEl, name);
        }
        if (saveButton) saveButton.style.display = '';
        if (cancelButton) cancelButton.style.display = '';
    };
    addFieldArea.appendChild(addFieldBtn);
    form.appendChild(addFieldArea);

    function readCharacterPersonalitySelection(characterData) {
        const reserved = characterData && typeof characterData === 'object' ? characterData['_reserved'] : null;
        const override = reserved && typeof reserved === 'object' ? reserved['persona_override'] : null;
        const profile = override && typeof override.profile === 'object' ? override.profile : {};
        const presetId = override && typeof override === 'object' ? String(override.preset_id || '').trim() : '';
        const hasOverride = !!(override && presetId);
        // 通过 i18n 键获取本地化显示名，回退到 profile 原始值
        const fallbackName = String(profile['性格原型'] || presetId).trim();
        const i18nKey = presetId ? 'memory.characterSelection.' + presetId + '.name' : '';
        var displayName = '';
        if (hasOverride) {
            if (typeof window.t === 'function' && i18nKey) {
                var translated = window.t(i18nKey, fallbackName);
                displayName = (typeof translated === 'string' && translated && translated !== i18nKey)
                    ? translated
                    : fallbackName;
            } else {
                displayName = fallbackName;
            }
        }
        return {
            hasOverride,
            presetId,
            profile,
            displayName: displayName,
        };
    }

    function applyCharacterPersonalitySelection(selection) {
        const reserved = cat['_reserved'] && typeof cat['_reserved'] === 'object'
            ? cat['_reserved']
            : (cat['_reserved'] = {});
        if (!selection || selection.mode !== 'override') {
            delete reserved['persona_override'];
            if (!Object.keys(reserved).length) {
                delete cat['_reserved'];
            }
            return;
        }

        reserved['persona_override'] = {
            preset_id: String(selection.preset_id || '').trim(),
            source: String(selection.source || '').trim(),
            selected_at: String(selection.selected_at || '').trim(),
            profile: selection.profile && typeof selection.profile === 'object'
                ? { ...selection.profile }
                : {},
        };
    }

    function isPersonalityPanelAlive() {
        if (!container || !container.isConnected) {
            return false;
        }
        const overlay = typeof container.closest === 'function'
            ? container.closest('.catgirl-panel-overlay')
            : null;
        return !!(overlay && overlay.isConnected && overlay.dataset.closing !== 'true');
    }

    const personalityWrapper = document.createElement('div');
    personalityWrapper.className = 'field-row-wrapper personality-row';
    const personalityLabel = document.createElement('label');
    personalityLabel.textContent = window.t ? window.t('character.personalitySetting') : '人格设定';
    personalityLabel.style.fontSize = '1rem';
    personalityWrapper.appendChild(personalityLabel);

    const personalityRow = document.createElement('div');
    personalityRow.className = 'field-row';
    const personalitySummary = document.createElement('div');
    personalitySummary.style.flex = '1';
    personalitySummary.style.padding = '0 12px';
    personalitySummary.style.color = '#40C5F1';
    personalitySummary.style.fontSize = '0.95rem';
    personalitySummary.style.whiteSpace = 'nowrap';
    personalitySummary.style.overflow = 'hidden';
    personalitySummary.style.textOverflow = 'ellipsis';
    const personalitySelection = readCharacterPersonalitySelection(cat);
    personalitySummary.textContent = personalitySelection.hasOverride
        ? personalitySelection.displayName
        : (window.t ? window.t('character.personalityUseDefault') : '跟随角色卡默认设定');
    personalityRow.appendChild(personalitySummary);
    personalityWrapper.appendChild(personalityRow);

    const personalitySelectBtn = document.createElement('button');
    personalitySelectBtn.type = 'button';
    personalitySelectBtn.className = 'btn sm row-action-btn personality-select-action';
    personalitySelectBtn.dataset.testid = 'character-personality-select';
    personalitySelectBtn.innerHTML = '<img src="/static/icons/character_icon.png" alt="" class="personality-icon"> <span>'
        + (window.t ? window.t('character.personalitySelect') : '选择人格') + '</span>';
    personalitySelectBtn.disabled = !!isNew;
    personalitySelectBtn.addEventListener('click', async function () {
        if (isNew) {
            return;
        }
        if (!window.CharacterPersonalityOnboarding || typeof window.CharacterPersonalityOnboarding.openFromSettings !== 'function') {
            if (typeof showAlert === 'function') {
                await showAlert(window.t ? window.t('character.personalityModuleUnavailable') : '人格选择模块尚未加载');
            }
            return;
        }
        await window.CharacterPersonalityOnboarding.openFromSettings(name);
    });
    personalityWrapper.appendChild(personalitySelectBtn);

    const personalityClearBtn = document.createElement('button');
    personalityClearBtn.type = 'button';
    personalityClearBtn.className = 'btn sm delete row-action-btn personality-clear-action';
    personalityClearBtn.dataset.testid = 'character-personality-clear';
    personalityClearBtn.innerHTML = '<img src="/static/icons/roload_icon.png" alt="" class="restore-icon"> <span>'
        + (window.t ? window.t('character.personalityClear') : '恢复默认') + '</span>';
    personalityClearBtn.disabled = !personalitySelection.hasOverride;
    personalityClearBtn.addEventListener('click', async function () {
        if (!name || personalityClearBtn.disabled) {
            return;
        }
        try {
            const response = await fetch(`/api/characters/character/${encodeURIComponent(name)}/persona-selection`, {
                method: 'DELETE',
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                throw new Error(result && result.error ? result.error : `Request failed: ${response.status}`);
            }
            applyCharacterPersonalitySelection(result.selection);
            if (isPersonalityPanelAlive()) {
                buildCatgirlDetailForm(name, cat, false, container);
            }
            if (typeof loadCharacterCards === 'function') {
                loadCharacterCards().catch(e => console.warn('刷新角色列表失败:', e));
            }
            showMessage(window.t ? window.t('character.personalityCleared') : '已恢复角色卡默认人格', 'success');
        } catch (e) {
            console.error('清除人格设定失败:', e);
            if (typeof showAlert === 'function') {
                await showAlert(window.t ? window.t('character.personalityClearFailed') : '清除人格设定失败');
            }
        }
    });
    personalityWrapper.appendChild(personalityClearBtn);
    form.appendChild(personalityWrapper);

    // 模型信息仅用于保存时保留 Live2D 待机动作，模型管理入口已移到卡面按钮。
    function validateModelPath(path) {
        if (path === undefined || path === null) return '';
        if (typeof path !== 'string') path = String(path);
        const strValue = path.trim();
        if (strValue === '' || strValue === 'undefined' || strValue === 'null') return '';
        if (strValue.toLowerCase().includes('undefined') || strValue.toLowerCase().includes('null')) return '';
        return strValue;
    }

    const modelType = cat['model_type'] || 'live2d';
    const normalizedModelType = modelType === 'vrm' ? 'live3d' : modelType;
    const live2dPath = validateModelPath(cat['live2d']);

    // 音色设定
    const voiceWrapper = document.createElement('div');
    voiceWrapper.className = 'field-row-wrapper voice-row';
    const voiceLabel = document.createElement('label');
    voiceLabel.textContent = window.t ? window.t('character.voiceSetting') : '音色设定';
    voiceLabel.style.fontSize = '1rem';
    voiceWrapper.appendChild(voiceLabel);

    const voiceRow = document.createElement('div');
    voiceRow.className = 'field-row';
    voiceRow.style.overflow = 'visible';
    voiceRow.style.position = 'relative';
    voiceRow.style.alignItems = 'center';
    const voiceSelect = document.createElement('select');
    voiceSelect.name = 'voice_id';
    voiceSelect.className = 'form-control voice-native-select';
    voiceSelect.tabIndex = -1;
    voiceSelect.setAttribute('aria-hidden', 'true');
    voiceSelect.style.flex = '0 0 auto';
    voiceSelect.style.width = '100%';
    voiceSelect.style.position = 'relative';
    voiceSelect.style.zIndex = '1000';
    voiceSelect.style.border = 'none';
    voiceSelect.style.background = 'transparent';
    voiceSelect.style.appearance = 'auto';
    voiceSelect.style.alignSelf = 'stretch';
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = window.t ? window.t('character.voiceNotSet') : '未指定音色';
    voiceSelect.appendChild(defaultOption);
    voiceRow.appendChild(voiceSelect);
    const voiceSelectUi = _panelCreateVoiceSelectUi(voiceSelect);
    voiceRow.appendChild(voiceSelectUi.container);
    form._voiceSelectCleanup = voiceSelectUi.destroy;
    voiceWrapper.appendChild(voiceRow);

    // 注册新声音按钮
    const registerVoiceBtn = document.createElement('button');
    registerVoiceBtn.type = 'button';
    registerVoiceBtn.className = 'btn sm row-action-btn voice-register-action';
    const registerVoiceText = (window.t && typeof window.t === 'function')
        ? '<img src="/static/icons/sound.png" alt="" class="sound-icon"> <span data-i18n="character.registerNewVoice">' + window.t('character.registerNewVoice') + '</span>'
        : '<img src="/static/icons/sound.png" alt="" class="sound-icon"> 注册新声音';
    registerVoiceBtn.innerHTML = registerVoiceText;
    registerVoiceBtn.addEventListener('click', async function () {
        const catgirlName = form.querySelector('[name="档案名"]').value;
        if (!catgirlName) {
            if (typeof showAlert === 'function') {
                await showAlert(window.t ? window.t('character.fillProfileNameFirstForVoice') : '请先填写猫娘档案名，然后再注册音色');
            }
            return;
        }
        if (typeof openVoiceClone === 'function') {
            openVoiceClone(catgirlName);
        } else {
            const url = '/voice_clone?lanlan_name=' + encodeURIComponent(catgirlName);
            const windowName = 'neko_voice_clone_' + encodeURIComponent(catgirlName || 'default');
            const width = 700;
            const height = 900;
            const left = Math.max(0, Math.floor((screen.width - width) / 2));
            const top = Math.max(0, Math.floor((screen.height - height) / 2));
            const features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;
            if (typeof window.openOrFocusWindow === 'function') {
                window.openOrFocusWindow(url, windowName, features);
            } else {
                window.open(url, windowName, features);
            }
        }
    });
    voiceWrapper.appendChild(registerVoiceBtn);
    form.appendChild(voiceWrapper);

    // 操作按钮区
    const btnArea = document.createElement('div');
    btnArea.className = 'btn-area settings-action-row';
    btnArea.style.display = 'flex';
    btnArea.style.alignItems = 'center';
    btnArea.style.marginTop = '10px';
    btnArea.style.gap = '12px';

    const labelPlaceholder = document.createElement('div');
    labelPlaceholder.style.minWidth = '80px';
    labelPlaceholder.style.flexShrink = '0';
    btnArea.appendChild(labelPlaceholder);

    const spacer = document.createElement('div');
    spacer.style.flex = '1';
    btnArea.appendChild(spacer);

    const saveButton = document.createElement('button');
    saveButton.type = 'button';
    saveButton.id = 'save-button';
    saveButton.className = 'btn sm settings-save-action';
    if (!isNew) saveButton.style.display = 'none';
    saveButton.innerHTML = '<img src="/static/icons/set_on.png" alt="" class="save-icon"> <span>'
        + (isNew
            ? (window.t ? window.t('character.confirmNewCatgirl') : '确认新猫娘')
            : (window.t ? window.t('character.saveChanges') : '保存修改'))
        + '</span>';
    saveButton.onclick = function () { saveCatgirlFromPanel(form, name, isNew); };
    btnArea.appendChild(saveButton);

    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.id = 'cancel-button';
    cancelButton.className = 'btn sm settings-cancel-action';
    if (!isNew) cancelButton.style.display = 'none';
    cancelButton.innerHTML = '<img src="/static/icons/close_button.png" alt="" class="cancel-icon"> <span>'
        + (window.t ? window.t('character.cancel') : '取消') + '</span>';
    cancelButton.onclick = function () {
        if (saveButton) saveButton.style.display = 'none';
        if (cancelButton) cancelButton.style.display = 'none';
        if (isNew) {
            closeCatgirlPanel();
        } else {
            const container = form.parentNode;
            try {
                buildCatgirlDetailForm(name, cat, false, container);
            } catch (e) {
                console.error('恢复猫娘数据失败:', e);
                closeCatgirlPanel();
            }
        }
    };
    btnArea.appendChild(cancelButton);

    form.appendChild(btnArea);
    container.innerHTML = '';
    container.appendChild(form);

    if (!isNew && name) {
        const handleCharacterPersonalityUpdated = async function (event) {
            const detail = event && event.detail ? event.detail : {};
            if (String(detail.characterName || '').trim() !== name) {
                return;
            }
            try {
                const response = await fetch(`/api/characters/character/${encodeURIComponent(name)}/persona-selection`, {
                    cache: 'no-store',
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result && result.error ? result.error : `Request failed: ${response.status}`);
                }
                applyCharacterPersonalitySelection(result.selection);
                if (isPersonalityPanelAlive()) {
                    buildCatgirlDetailForm(name, cat, false, container);
                }
                if (typeof loadCharacterCards === 'function') {
                    loadCharacterCards().catch(e => console.warn('刷新角色列表失败:', e));
                }
            } catch (e) {
                console.warn('刷新人格设定展示失败:', e);
            }
        };
        form._characterPersonalityUpdateHandler = handleCharacterPersonalityUpdated;
        window.addEventListener('neko:character-personality-updated', handleCharacterPersonalityUpdated);
    }

    // 绑定变化监听以显隐保存/取消按钮（新建猫娘始终显示）
    if (!isNew) {
        function showCatgirlActionButtons() {
            if (saveButton) saveButton.style.display = '';
            if (cancelButton) cancelButton.style.display = '';
        }
        form.querySelectorAll('input, textarea, select').forEach(input => {
            input.addEventListener('change', showCatgirlActionButtons);
            if (input.type === 'text' || input.tagName === 'TEXTAREA') {
                input.addEventListener('input', showCatgirlActionButtons);
            }
        });
        form.querySelectorAll('.btn.delete').forEach(btn => {
            btn.addEventListener('click', showCatgirlActionButtons);
        });
    }

    // 加载音色列表
    const voicesLoadPromise = _loadPanelVoices(voiceSelect, String(cat['voice_id'] || '').trim()).then(() => {
        voiceSelectUi.refresh();
    }, () => {
        voiceSelectUi.refresh();
    });
    form._voicesLoadPromise = voicesLoadPromise;
    form._previousVoiceId = String(cat['voice_id'] || '').trim();
    form._live2dModel = live2dPath;
    form._modelType = normalizedModelType;

    // 初始化textarea自动调整
    setTimeout(() => {
        form.querySelectorAll('textarea').forEach(ta => _panelAttachTextareaAutoResize(ta));
    }, 100);

    // 为已存在猫娘的表单添加自动保存监听器（新建猫娘不启用，因为尚未创建记录）
    if (!isNew && name) {
        setTimeout(() => {
            form.querySelectorAll('input, textarea').forEach(inp => {
                if (inp.name && inp.name !== 'voice_id') {
                    panelAttachAutoSaveListener(inp, name);
                }
            });
        }, 150);
    }
}

// 档案名输入限制器
function _panelAttachProfileNameLimiter(input) {
    if (!input) return;
    const MAX_LEN = 50;
    let composing = false;
    input.addEventListener('compositionstart', () => { composing = true; });
    input.addEventListener('compositionend', () => {
        composing = false;
        checkLen();
    });
    function checkLen() {
        if (composing) return;
        const fieldRow = input.closest('.field-row');
        if (!fieldRow) return;
        if (input.value.length > MAX_LEN) {
            fieldRow.classList.add('profile-name-too-long');
            let tip = fieldRow.querySelector('.profile-name-too-long-tip');
            if (!tip) {
                tip = document.createElement('span');
                tip.className = 'profile-name-too-long-tip';
                fieldRow.appendChild(tip);
            }
            tip.textContent = (window.t ? window.t('character.profileNameTooLong') : '档案名过长') + ' (' + input.value.length + '/' + MAX_LEN + ')';
        } else {
            fieldRow.classList.remove('profile-name-too-long');
            const tip = fieldRow.querySelector('.profile-name-too-long-tip');
            if (tip) tip.remove();
        }
    }
    input.addEventListener('input', checkLen);
}

// label 设置（支持i18n + 超长title提示）
function _panelSetFieldLabel(labelEl, key) {
    const MAX_LABEL_LEN = 8;
    let displayText = key;
    if (window.t && typeof window.t === 'function') {
        const profileLabelKey = 'characterProfile.labels.' + key;
        const translatedProfileLabel = window.t(profileLabelKey);
        if (translatedProfileLabel && translatedProfileLabel !== profileLabelKey) {
            displayText = translatedProfileLabel;
        } else {
            const fieldKey = 'character.field.' + key;
            const translatedFieldLabel = window.t(fieldKey);
            if (translatedFieldLabel && translatedFieldLabel !== fieldKey) {
                displayText = translatedFieldLabel;
            }
        }
    }
    labelEl.textContent = displayText;
    if (displayText.length > MAX_LABEL_LEN) {
        labelEl.title = displayText;
    }
}

function _panelConfigureFieldDeleteButton(button) {
    const deleteText = (window.t && typeof window.t === 'function')
        ? window.t('character.deleteField')
        : '删除设定';
    button.removeAttribute('title');
    button.setAttribute('aria-label', deleteText);
    button.innerHTML = '<img src="/static/icons/delete.png" alt="" class="delete-icon" aria-hidden="true">';
}

function _panelResizeTextarea(textarea) {
    if (!textarea) return;
    textarea.style.height = 'auto';
    const style = getComputedStyle(textarea);
    const minHeight = parseInt(style.minHeight) || 30;

    // 计算内容高度，考虑padding
    const paddingTop = parseInt(style.paddingTop) || 0;
    const paddingBottom = parseInt(style.paddingBottom) || 0;

    const scrollHeight = textarea.scrollHeight;
    const contentHeight = scrollHeight - paddingTop - paddingBottom;

    // 三行高度的估算：line-height*3
    const computedLineHeight = parseFloat(style.lineHeight);
    const fontSize = parseFloat(style.fontSize) || 14;
    const lineHeight = isNaN(computedLineHeight) ? fontSize * 1.2 : computedLineHeight;
    const threeLinesHeight = lineHeight * 3;
    const maxContentHeight = threeLinesHeight;
    const newContentHeight = Math.min(maxContentHeight, contentHeight);
    const newHeight = Math.max(minHeight, newContentHeight + paddingTop + paddingBottom);

    textarea.style.height = newHeight + 'px';

    // 根据内容是否超过三行来决定是否显示滚动条
    const fieldRow = textarea.closest('.field-row');
    if (fieldRow) {
        if (contentHeight > maxContentHeight) {
            textarea.style.overflowY = 'auto';
            fieldRow.classList.add('has-scrollbar');
        } else {
            textarea.style.overflowY = 'hidden';
            fieldRow.classList.remove('has-scrollbar');
        }
    }
}

function _panelRequestTextareaAutoResize(textarea) {
    if (!textarea) return;
    _panelResizeTextarea(textarea);
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => _panelResizeTextarea(textarea));
    } else {
        setTimeout(() => _panelResizeTextarea(textarea), 0);
    }
}

// textarea自动调整高度（匹配原版逻辑：三行最大高度 + scrollbar类切换）
function _panelAttachTextareaAutoResize(textarea) {
    if (!textarea) return;
    if (textarea.dataset.autoResizeAttached) {
        _panelRequestTextareaAutoResize(textarea);
        return;
    }
    textarea.dataset.autoResizeAttached = 'true';

    function resize() {
        _panelRequestTextareaAutoResize(textarea);
    }

    textarea.addEventListener('input', resize);
    textarea.addEventListener('focus', resize);
    resize();
}

function _panelGetNativeVoiceProviderLabel(nativeEntries) {
    if (!Array.isArray(nativeEntries)) return '';
    for (const [, voiceData] of nativeEntries) {
        const provider = voiceData && String(voiceData.provider || '').trim();
        if (provider === 'free') {
            return _panelVoiceI18n('voice.providerFreeApi', 'Free API');
        }
        if (VoiceDisplayUtils.isKnownProvider(provider, { includeFree: false })) {
            return _panelVoiceProviderShortName(provider);
        }
        const label = voiceData && (voiceData.provider_label || provider);
        if (label) return String(label);
    }
    return '';
}

function _panelFormatNativeVoiceGroupLabel(nativeEntries) {
    const providerLabel = _panelGetNativeVoiceProviderLabel(nativeEntries);
    if (providerLabel) {
        return window.t
            ? window.t('character.nativePresetVoices', { provider: providerLabel })
            : providerLabel + ' 原生音色';
    }
    return window.t ? window.t('character.nativePresetVoicesGeneric') : '原生预设音色';
}

function _panelNormalizeVoiceGroupLabel(label) {
    return String(label || '').replace(/^[\s\-—–─]+|[\s\-—–─]+$/g, '').trim();
}

function _panelGetRegisteredVoiceDisplayName(voiceId, voiceData) {
    if (voiceData && typeof voiceData === 'object') {
        const prefix = String(voiceData.prefix || '').trim();
        if (prefix) return prefix;

        const name = String(voiceData.name || '').trim();
        if (name) return name;
    }
    return String(voiceId || '').trim();
}

// ── source-first 选声：把音色按「provider · 来源」分组（声音来源统一架构 §5）──
// 品牌名跨语言通用，用 JS 常量；只有 local（本地 CosyVoice）/ free（免费）与「· 来源」
// 后缀需本地化（voice.provider.* / voice.source.*）。
function _panelVoiceI18n(key, fallback) {
    return VoiceDisplayUtils.t(key, fallback);
}

function _panelVoiceProviderShortName(provider) {
    return VoiceDisplayUtils.providerShortName(provider, {
        freeKey: 'voice.providerFree',
        freeFallback: 'Free',
    });
}

function _panelVoiceSourceLabel(source) {
    const s = String(source || '').trim();
    const map = {
        preset: ['voice.sourcePreset', 'Preset'],
        clone: ['voice.sourceClone', 'Clone'],
        design: ['voice.sourceDesign', 'Voice Design'],
    };
    const entry = map[s];
    return entry ? _panelVoiceI18n(entry[0], entry[1]) : s;
}

function _panelNativeVoiceDisplayName(voiceId, voiceData) {
    return VoiceDisplayUtils.nativeVoiceDisplayName(voiceId, voiceData);
}

// 「<Provider> · <来源>」组标签，如 "ElevenLabs · 克隆" / "Gemini · 预制"
function _panelVoiceSourceGroupLabel(provider, source) {
    return _panelVoiceProviderShortName(provider) + ' · ' + _panelVoiceSourceLabel(source);
}

// 创建音色自定义单选下拉，原生 select 只负责表单值。
function _panelCreateVoiceSelectUi(selectEl) {
    const container = document.createElement('div');
    container.className = 'voice-custom-select';

    const header = document.createElement('button');
    header.type = 'button';
    header.className = 'voice-select-header';
    header.setAttribute('aria-haspopup', 'listbox');
    header.setAttribute('aria-expanded', 'false');

    const selectedText = document.createElement('span');
    selectedText.className = 'voice-select-selected';
    selectedText.textContent = selectEl.options[selectEl.selectedIndex]?.textContent || '';
    header.appendChild(selectedText);

    const options = document.createElement('div');
    options.className = 'voice-select-options';
    options.id = 'voice-select-options-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
    options.setAttribute('role', 'listbox');
    header.setAttribute('aria-controls', options.id);

    container.appendChild(header);
    container.appendChild(options);

    function getItems() {
        return Array.from(options.querySelectorAll('.voice-select-option:not(.disabled)'));
    }

    function updateScrollbarState() {
        requestAnimationFrame(() => {
            options.classList.toggle('has-scrollbar', options.scrollHeight > options.clientHeight);
        });
    }

    function setOptionTabbability(isTabbable) {
        options.querySelectorAll('.voice-select-option').forEach(item => {
            if (item.classList.contains('disabled')) {
                item.setAttribute('tabindex', '-1');
                return;
            }
            item.setAttribute('tabindex', isTabbable ? '0' : '-1');
        });
    }

    function applyDropdownDirection() {
        const maxHeight = 250;
        const gap = 8;
        const headerRect = header.getBoundingClientRect();
        const optionHeight = Math.min(options.scrollHeight || maxHeight, maxHeight);
        const spaceBelow = window.innerHeight - headerRect.bottom - gap;
        const spaceAbove = headerRect.top - gap;
        let placement = 'open-down';
        let computedMaxHeight = maxHeight;

        if (spaceBelow >= optionHeight) {
            placement = 'open-down';
        } else if (spaceAbove >= optionHeight) {
            placement = 'open-up';
        } else if (spaceAbove > spaceBelow) {
            placement = 'open-up';
            computedMaxHeight = Math.max(80, Math.floor(spaceAbove));
        } else {
            computedMaxHeight = Math.max(80, Math.floor(spaceBelow));
        }

        container.classList.toggle('open-up', placement === 'open-up');
        container.classList.toggle('open-down', placement === 'open-down');
        options.style.maxHeight = computedMaxHeight + 'px';
        updateScrollbarState();
    }

    function closeDropdown(restoreFocus = false) {
        const wasActive = container.classList.contains('active');
        container.classList.remove('active', 'open-up', 'open-down');
        header.setAttribute('aria-expanded', 'false');
        setOptionTabbability(false);
        if (restoreFocus && wasActive && header.isConnected) {
            header.focus();
        }
    }

    function openDropdown() {
        document.querySelectorAll('.voice-custom-select.active').forEach(activeSelect => {
            if (activeSelect === container) return;
            activeSelect.classList.remove('active', 'open-up', 'open-down');
            const activeHeader = activeSelect.querySelector('.voice-select-header');
            if (activeHeader) activeHeader.setAttribute('aria-expanded', 'false');
            activeSelect.querySelectorAll('.voice-select-option:not(.disabled)').forEach(item => {
                item.setAttribute('tabindex', '-1');
            });
        });

        container.classList.add('active');
        header.setAttribute('aria-expanded', 'true');
        setOptionTabbability(true);
        applyDropdownDirection();

        const selectedItem = options.querySelector('.voice-select-option.selected:not(.disabled)');
        if (selectedItem) selectedItem.scrollIntoView({ block: 'nearest' });
    }

    function toggleDropdown() {
        if (container.classList.contains('active')) {
            closeDropdown();
        } else {
            openDropdown();
        }
    }

    function syncSelectionState() {
        const selectedOption = selectEl.options[selectEl.selectedIndex] || selectEl.querySelector('option');
        const displayText = selectedOption ? selectedOption.textContent : '';
        selectedText.textContent = displayText;
        header.title = selectedOption ? (selectedOption.title || displayText) : '';

        // 只高亮第一个值匹配项：海外免费列表里 default(pin) 与 Leda(原生) voice_id
        // 同为 "Leda"（刻意不去重），若按 value 全量比较会多项同时选中。原生
        // <select> 在重复 value 下 selectedIndex 也只落第一个，这里与之对齐。
        let matched = false;
        options.querySelectorAll('.voice-select-option').forEach(item => {
            const isSelected = !matched && item.dataset.value === selectEl.value;
            if (isSelected) matched = true;
            item.classList.toggle('selected', isSelected);
            item.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        });
    }

    function selectOptionValue(value) {
        if (selectEl.value === value) {
            closeDropdown(true);
            return;
        }
        selectEl.value = value;
        selectEl.dispatchEvent(new Event('change', { bubbles: true }));
        closeDropdown(true);
    }

    function focusItemByOffset(currentItem, offset) {
        const items = getItems();
        if (items.length === 0) return;
        const currentIndex = items.indexOf(currentItem);
        const nextIndex = currentIndex >= 0
            ? (currentIndex + offset + items.length) % items.length
            : 0;
        items[nextIndex].focus();
    }

    function appendOptionItem(option) {
        const item = document.createElement('div');
        item.className = 'voice-select-option';
        item.setAttribute('role', 'option');
        item.setAttribute('tabindex', '-1');
        item.dataset.value = option.value;
        item.textContent = option.textContent || option.value;
        item.title = option.title || item.textContent;

        if (option.disabled) {
            item.classList.add('disabled');
            item.setAttribute('aria-disabled', 'true');
        } else {
            item.addEventListener('click', () => selectOptionValue(option.value));
            item.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectOptionValue(option.value);
                } else if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    focusItemByOffset(item, 1);
                } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    focusItemByOffset(item, -1);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    closeDropdown(true);
                }
            });
        }

        options.appendChild(item);
    }

    function refresh() {
        options.innerHTML = '';
        Array.from(selectEl.children).forEach(child => {
            if (child.tagName === 'OPTGROUP') {
                const groupOptions = Array.from(child.children).filter(option => option.tagName === 'OPTION');
                if (groupOptions.length > 0) {
                    const groupLabel = document.createElement('div');
                    groupLabel.className = 'voice-select-group-label';
                    const groupLabelText = document.createElement('span');
                    groupLabelText.className = 'voice-select-group-text';
                    groupLabelText.textContent = _panelNormalizeVoiceGroupLabel(child.label);
                    groupLabel.appendChild(groupLabelText);
                    options.appendChild(groupLabel);
                    groupOptions.forEach(appendOptionItem);
                }
            } else if (child.tagName === 'OPTION') {
                appendOptionItem(child);
            }
        });
        syncSelectionState();
        setOptionTabbability(container.classList.contains('active'));
        updateScrollbarState();
    }

    function handleDocumentClick(event) {
        if (!container.contains(event.target)) {
            closeDropdown();
        }
    }

    function handleDocumentKeydown(event) {
        if (event.key === 'Escape' && container.classList.contains('active')) {
            closeDropdown(true);
        }
    }

    header.addEventListener('click', toggleDropdown);
    header.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            toggleDropdown();
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            if (!container.classList.contains('active')) openDropdown();
            const selectedItem = options.querySelector('.voice-select-option.selected:not(.disabled)');
            (selectedItem || getItems()[0])?.focus();
        }
    });
    selectEl.addEventListener('change', syncSelectionState);
    document.addEventListener('click', handleDocumentClick);
    document.addEventListener('keydown', handleDocumentKeydown);

    refresh();

    return {
        container,
        refresh,
        destroy() {
            closeDropdown();
            selectEl.removeEventListener('change', syncSelectionState);
            document.removeEventListener('click', handleDocumentClick);
            document.removeEventListener('keydown', handleDocumentKeydown);
            container.remove();
        }
    };
}

// 加载音色列表（完整复制原版逻辑）
async function _loadPanelVoices(selectEl, currentVoiceId) {
    const GSV_PREFIX = 'gsv:';

    try {
        const response = await fetch('/api/characters/voices');
        if (!response.ok) return;
        const data = await response.json();

        if (data && data.voices) {
            // 清空现有选项
            while (selectEl.firstChild) selectEl.removeChild(selectEl.firstChild);
            const defaultOption = document.createElement('option');
            defaultOption.value = '';
            defaultOption.textContent = window.t ? window.t('character.voiceNotSet') : '未指定音色';
            selectEl.appendChild(defaultOption);

            // 置顶音色（海外免费 free_intl：yui + default），紧跟在"未指定音色"之后，
            // 排在列表最上面。展示名按 i18n_key 本地化；Leda 不去重，仍出现在 Gemini
            // 长列表里（default pin 的目标）。
            if (Array.isArray(data.pinned_voices) && data.pinned_voices.length > 0) {
                data.pinned_voices.forEach(function (pin) {
                    if (!pin || !pin.voice_id) return;
                    const option = document.createElement('option');
                    option.value = pin.voice_id;
                    option.textContent = (window.t && pin.i18n_key)
                        ? window.t(pin.i18n_key)
                        : (pin.prefix || pin.voice_id);
                    option.title = pin.voice_id;
                    if (pin.voice_id === currentVoiceId) option.selected = true;
                    selectEl.appendChild(option);
                });
            }

            // 注册的音色：按「provider · 来源」分组成 optgroup（source-first，§5）。来源取
            // voiceData.source（design=描述生成 / clone=克隆），缺省按 clone（存量克隆音色没有
            // source 字段）。同 (provider, 来源) 复用同一组；provider 缺失归到「其他 · …」。
            const _cloneGroups = {};
            Object.entries(data.voices).forEach(function ([voiceId, voiceData]) {
                const provider = (voiceData && voiceData.provider) || '';
                const source = (voiceData && voiceData.source === 'design') ? 'design' : 'clone';
                const groupKey = provider + '|' + source;
                if (!_cloneGroups[groupKey]) {
                    const grp = document.createElement('optgroup');
                    grp.label = _panelNormalizeVoiceGroupLabel(_panelVoiceSourceGroupLabel(provider, source));
                    grp.dataset.voiceSourceGroup = source;
                    _cloneGroups[groupKey] = grp;
                    selectEl.appendChild(grp);
                }
                const option = document.createElement('option');
                option.value = voiceId;
                // 克隆音色的可读名称存在 prefix 中，不能被角色占用信息或 voice_id 覆盖。
                option.textContent = _panelGetRegisteredVoiceDisplayName(voiceId, voiceData);
                option.title = voiceId;
                if (voiceId === currentVoiceId) option.selected = true;
                _cloneGroups[groupKey].appendChild(option);
            });

            // 免费预设音色
            if (data.free_voices && Object.keys(data.free_voices).length > 0) {
                const freeGroup = document.createElement('optgroup');
                freeGroup.label = _panelNormalizeVoiceGroupLabel(_panelVoiceSourceGroupLabel('free', 'preset'));
                freeGroup.dataset.voiceSourceGroup = 'preset';
                Object.entries(data.free_voices).forEach(function ([voiceKey, voiceId]) {
                    const option = document.createElement('option');
                    option.value = voiceId;
                    option.textContent = window.t ? window.t('voice.freeVoice.' + voiceKey) : voiceKey;
                    if (voiceId === currentVoiceId) option.selected = true;
                    freeGroup.appendChild(option);
                });
                selectEl.appendChild(freeGroup);
            }

            // 当前 Realtime Provider 的原生音色（由后端按 core_api_type 注入）
            // 去重范围：已注册自定义音色 + 已渲染的免费预设音色 ID，
            // 避免任一冲突时下拉里重复条目和多重 selected 视觉态。
            // 自定义/免费音色优先保留，与 _has_custom_tts 的路由优先级一致。
            if (data.native_voices && Object.keys(data.native_voices).length > 0) {
                const renderedVoiceIds = new Set();
                Object.keys(data.voices || {}).forEach(function (id) {
                    renderedVoiceIds.add(String(id).toLowerCase());
                });
                if (data.free_voices) {
                    Object.values(data.free_voices).forEach(function (id) {
                        if (id) renderedVoiceIds.add(String(id).toLowerCase());
                    });
                }
                const nativeEntries = Object.entries(data.native_voices)
                    .filter(function ([voiceId]) { return !renderedVoiceIds.has(String(voiceId).toLowerCase()); });
                if (nativeEntries.length > 0) {
                    const nativeGroup = document.createElement('optgroup');
                    // native 预制：「<Provider> · 预制」（provider 取自 voiceData.provider_label/provider）
                    const _nativeProviderLabel = _panelGetNativeVoiceProviderLabel(nativeEntries)
                        || _panelVoiceI18n('voice.providerUnknown', 'Other');
                    nativeGroup.label = _panelNormalizeVoiceGroupLabel(
                        _nativeProviderLabel + ' · ' + _panelVoiceSourceLabel('preset')
                    );
                    nativeGroup.dataset.voiceSourceGroup = 'preset';
                    nativeEntries.forEach(function ([voiceId, voiceData]) {
                        const option = document.createElement('option');
                        option.value = voiceId;
                        option.textContent = _panelNativeVoiceDisplayName(voiceId, voiceData);
                        option.title = voiceId;
                        if (voiceId === currentVoiceId) option.selected = true;
                        nativeGroup.appendChild(option);
                    });
                    selectEl.appendChild(nativeGroup);
                }
            }
        }

        // 加载 GPT-SoVITS 声音列表
        await _loadPanelGsvVoices(selectEl, currentVoiceId);

        // 保底：currentVoiceId 在任何分支都没渲染时（Gemini 别名、免费版被过滤掉的
        // CosyVoice 云端 voice_id、catalog 没暴露的 ID 等），下拉里没匹配项 select
        // 会回到首项；下次保存表单会被误判为"已清空"走 unregister_voice 分支，把
        // 用户保存的音色丢掉。给未知值补一条 "(?)" 占位条，保留原值供后端 normalize。
        // 必须放在所有 loader（含 _loadPanelGsvVoices）之后才能正确判断是否已渲染；
        // gsv: 前缀 ID 由 _loadPanelGsvVoices.ensureGsvFallback 自行兜底，跳过避免双插。
        if (currentVoiceId
            && !currentVoiceId.startsWith(GSV_PREFIX)
            && !selectEl.querySelector('option[value="' + CSS.escape(currentVoiceId) + '"]')) {
            const fallbackGroup = document.createElement('optgroup');
            const fallbackLabel = window.t ? window.t('character.savedVoiceFallback') : '当前已保存音色';
            fallbackGroup.label = _panelNormalizeVoiceGroupLabel(fallbackLabel);
            fallbackGroup.dataset.savedVoiceFallbackGroup = 'true';
            const fallbackOption = document.createElement('option');
            fallbackOption.value = currentVoiceId;
            fallbackOption.textContent = currentVoiceId + ' (?)';
            fallbackOption.title = currentVoiceId;
            fallbackOption.selected = true;
            fallbackGroup.appendChild(fallbackOption);
            selectEl.appendChild(fallbackGroup);
            selectEl.value = currentVoiceId;
        }
    } catch (e) {
        console.warn('加载音色列表失败:', e);
    }
}

// GPT-SoVITS 声音列表
async function _loadPanelGsvVoices(selectEl, currentVoiceId) {
    const GSV_PREFIX = 'gsv:';

    function ensureGsvFallback() {
        if (!currentVoiceId || !currentVoiceId.startsWith(GSV_PREFIX)) return;
        if (selectEl.querySelector('option[value="' + CSS.escape(currentVoiceId) + '"]')) {
            selectEl.value = currentVoiceId;
            return;
        }
        let gsvGroup = selectEl.querySelector('optgroup[data-gsv-group="true"]');
        if (!gsvGroup) {
            gsvGroup = document.createElement('optgroup');
            gsvGroup.label = _panelNormalizeVoiceGroupLabel(_panelVoiceSourceGroupLabel('gptsovits', 'clone'));
            gsvGroup.dataset.gsvGroup = 'true';
            gsvGroup.dataset.voiceSourceGroup = 'clone';
            selectEl.appendChild(gsvGroup);
        }
        const fallbackOpt = document.createElement('option');
        fallbackOpt.value = currentVoiceId;
        fallbackOpt.textContent = currentVoiceId.substring(GSV_PREFIX.length) + ' (?)';
        gsvGroup.appendChild(fallbackOpt);
        selectEl.value = currentVoiceId;
    }

    // GSV 不可用时把后端给的 code 翻成一行人话塞到下拉里——以前是静默丢，
    // 用户连"为啥没出现"都看不到，只能猜是 server 没起还是开关没勾。
    const _gsvT = (key, fallback) => (window.t && typeof window.t === 'function' && window.t(key)) || fallback;

    function _appendGsvDiagnosticOption(message) {
        const diagGroup = document.createElement('optgroup');
        diagGroup.label = '── GPT-SoVITS ──';
        diagGroup.dataset.gsvDiagGroup = 'true';
        const diagOpt = document.createElement('option');
        diagOpt.value = '';
        diagOpt.disabled = true;
        diagOpt.textContent = message;
        diagGroup.appendChild(diagOpt);
        selectEl.appendChild(diagGroup);
    }

    function _diagnoseFailure(result, status) {
        const code = result && result.code;
        if (code === 'GPTSOVITS_NOT_ENABLED') {
            return _gsvT('character.gsvDiagNotEnabled', 'GPT-SoVITS 未启用 (请在 API 设置勾选)');
        }
        if (code === 'CUSTOM_API_NOT_ENABLED') {
            return _gsvT('character.gsvDiagUrlMissing', 'GPT-SoVITS URL 未配置 (请在 API 设置填写)');
        }
        if (code === 'TTS_CUSTOM_URL_NOT_CONFIGURED') {
            return _gsvT('character.gsvDiagUrlInvalid', 'GPT-SoVITS URL 未配置或不是 http(s)');
        }
        if (code === 'TTS_CUSTOM_URL_LOCALHOST_ONLY') {
            return _gsvT('character.gsvDiagUrlLocalhostOnly', 'GPT-SoVITS URL 必须是 localhost');
        }
        if (status === 502 || (result && /连接 GPT-SoVITS API 失败/.test(result.error || ''))) {
            return _gsvT('character.gsvDiagUnreachable', 'GPT-SoVITS server 未运行或不可达');
        }
        const base = _gsvT('character.gsvDiagLoadFailed', 'GPT-SoVITS 加载失败');
        return base + (result && result.error ? ': ' + result.error : '');
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    try {
        const resp = await fetch('/api/characters/custom_tts_voices?provider=gptsovits', { signal: controller.signal });
        clearTimeout(timeoutId);
        // 网关/反代可能返回 HTML 或空体，resp.json() 抛错会把 "Unexpected token <"
        // 这种技术细节经 catch 暴露给用户，这里兜底成空对象走正常诊断分支。
        const result = await resp.json().catch(() => ({}));
        if (result.success && Array.isArray(result.voices) && result.voices.length > 0) {
            const gsvGroup = document.createElement('optgroup');
            gsvGroup.label = _panelNormalizeVoiceGroupLabel(_panelVoiceSourceGroupLabel('gptsovits', 'clone'));
            gsvGroup.dataset.gsvGroup = 'true';
            gsvGroup.dataset.voiceSourceGroup = 'clone';
            result.voices.forEach(function (v) {
                const option = document.createElement('option');
                option.value = v.voice_id;
                option.textContent = v.name + (v.version ? ' (' + v.version + ')' : '');
                if (v.description) option.title = v.description;
                if (v.voice_id === currentVoiceId) option.selected = true;
                gsvGroup.appendChild(option);
            });
            selectEl.appendChild(gsvGroup);
            if (currentVoiceId && currentVoiceId.startsWith(GSV_PREFIX) && !selectEl.querySelector('option[value="' + CSS.escape(currentVoiceId) + '"]')) {
                const fallbackOpt = document.createElement('option');
                fallbackOpt.value = currentVoiceId;
                fallbackOpt.textContent = currentVoiceId.substring(GSV_PREFIX.length) + ' (?)';
                gsvGroup.appendChild(fallbackOpt);
            }
            if (currentVoiceId && currentVoiceId.startsWith(GSV_PREFIX)) {
                selectEl.value = currentVoiceId;
            }
        } else if (result && result.success && Array.isArray(result.voices) && result.voices.length === 0) {
            _appendGsvDiagnosticOption(_gsvT('character.gsvDiagEmpty', 'GPT-SoVITS server 没有任何声音 (空列表)'));
        } else {
            _appendGsvDiagnosticOption(_diagnoseFailure(result, resp.status));
        }
        ensureGsvFallback();
    } catch (e) {
        clearTimeout(timeoutId);
        console.debug('GPT-SoVITS voices not available:', e.message);
        if (e.name === 'AbortError') {
            _appendGsvDiagnosticOption(_gsvT('character.gsvDiagTimeout', 'GPT-SoVITS server 响应超时 (>3s)'));
        } else {
            const base = _gsvT('character.gsvDiagLoadFailed', 'GPT-SoVITS 加载失败');
            _appendGsvDiagnosticOption(base + (e && e.message ? ': ' + e.message : ''));
        }
        ensureGsvFallback();
    }
}

async function rebuildSavedCatgirlPanel(form, catgirlName) {
    const container = form?.parentNode;
    if (!container || !catgirlName) return;
    try {
        const freshData = await loadCharacterData();
        const rawData = freshData?.['猫娘']?.[catgirlName] || {};
        const wrapper = container.closest('.catgirl-panel-wrapper');
        // 新建→已创建 原地切换：跟 openCatgirlPanel 那条路径对偶，给 wrapper 也补上
        // dataset.catgirlName，否则 _refreshOpenCatgirlPanelActions 找不到面板对应的角色名、
        // 切角色后这个 panel 的按钮态不会被刷新。catgirlName 在函数顶部已 guard 过。
        if (wrapper) {
            wrapper.dataset.catgirlName = catgirlName;
        }
        const leftSection = wrapper?.querySelector('.catgirl-panel-left');
        const metaBlock = leftSection?.querySelector('#card-meta-block');
        if (metaBlock && typeof renderCardMetaBlock === 'function') {
            renderCardMetaBlock(metaBlock, catgirlName, false, rawData);
        }
        if (leftSection) {
            leftSection.querySelector('.card-panel-actions')?.remove();
            leftSection.appendChild(buildCreatedCatgirlPanelActions(catgirlName));
        }
        buildCatgirlDetailForm(catgirlName, rawData, false, container);
    } catch (e) {
        console.warn('[角色面板] 切换到已创建角色状态失败:', e);
    }
}

async function saveCatgirlFromPanel(form, originalName, isNew) {
    // 返回 true 表示真正落库成功；false 表示任一失败/早退分支（重复提交、
    // 校验失败、HTTP 错、success:false）。调用方（如 card-assist 的"应用并保存"）
    // 依赖这个返回值决定是否关闭面板/弹成功提示，否则会出现保存失败但 UI 报成功的假象。
    if (form.dataset.submitting === 'true') {
        console.log('表单正在提交中，忽略重复提交');
        return false;
    }
    form.dataset.submitting = 'true';

    try {
        // 等待音色加载完成
        if (form._voicesLoadPromise) {
            await form._voicesLoadPromise;
        }

        // 收集表单数据
        const nameInput = form.querySelector('input[name="档案名"]');
        if (!nameInput || !nameInput.value.trim()) {
            await showAlertDialog(window.t ? window.t('character.profileNameRequired') : '请输入档案名', { type: 'warning' });
            return false;
        }
        const shouldUseStrictProfileNameRule = isNew || !nameInput.readOnly;
        if (shouldUseStrictProfileNameRule && !(await ensureValidCharacterProfileName(nameInput.value, nameInput))) {
            return false;
        }
        if (!shouldUseStrictProfileNameRule && !(await ensureSafeExistingCharacterPathName(nameInput.value, nameInput))) {
            return false;
        }

        const selectedVoiceId = (form.querySelector('select[name="voice_id"]')?.value ?? '').trim();
        const previousVoiceId = form._previousVoiceId || '';
        const { data, duplicateKey, fieldOrder } = collectCharacterFields(form, {
            baseData: { '档案名': nameInput.value.trim() },
            excludeFieldNames: ['档案名', 'voice_id'],
        });
        if (duplicateKey) {
            showMessage(window.t ? window.t('character.fieldExists') : '该设定已存在', 'error');
            return;
        }
        attachCharacterFieldOrderPayload(data, fieldOrder);

        // 如果新建猫娘已被临时保存（自动创建），则改用 PUT 更新
        const shouldSelectAfterSave = !!isNew;
        const effectiveIsNew = isNew && !form._autoCreated;
        const url = '/api/characters/catgirl' + (effectiveIsNew ? '' : '/' + encodeURIComponent(effectiveIsNew ? '' : (form._autoCreatedName || originalName)));
        const response = await fetch(url, {
            method: effectiveIsNew ? 'POST' : 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            const errorText = await response.text();
            let errorMessage = errorText;
            try {
                const errorJson = JSON.parse(errorText);
                if (errorJson.error) errorMessage = errorJson.error;
            } catch (e) { /* keep original */ }
            showMessage(window.t ? window.t('character.saveFailedWithError', { error: errorMessage }) : '保存失败: ' + errorMessage, 'error');
            return false;
        }

        const result = await response.json();
        if (result.success === false) {
            showMessage(result.error || (window.t ? window.t('character.saveFailed') : '保存失败'), 'error');
            return false;
        }
        const savedCatgirlName = String(result.character_name || data['档案名'] || '').trim();
        if (savedCatgirlName && savedCatgirlName !== data['档案名']) {
            data['档案名'] = savedCatgirlName;
            if (nameInput) nameInput.value = savedCatgirlName;
        }
        const localRawData = buildLocalCatgirlRawData(savedCatgirlName, data, fieldOrder);
        let savedVoiceIdForLocalRawData = null;
        let savedRawDataForCache = localRawData;
        syncCharacterCardCache(savedCatgirlName, localRawData, { render: !shouldSelectAfterSave });
        if (form._autoCreatedDetachedName) {
            await rollbackAutoCreatedCatgirl(form, form._autoCreatedDetachedName);
            form._autoCreated = false;
            form._autoCreatedName = '';
        } else if (form._autoCreated) {
            form._autoCreated = false;
            form._autoCreatedName = '';
        }

        // voice_id 通过专用接口更新
        if (selectedVoiceId !== previousVoiceId) {
            if (selectedVoiceId) {
                const voiceSwitchOpId = createVoiceConfigSwitchOpId(savedCatgirlName);
                notifyVoiceConfigSwitching(savedCatgirlName, true, voiceSwitchOpId);
                try {
                    const voiceResp = await fetch('/api/characters/catgirl/voice_id/' + encodeURIComponent(savedCatgirlName), {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ voice_id: selectedVoiceId })
                    });
                    const voiceResult = await voiceResp.json().catch(() => ({}));
                    // 留 console 痕迹：toast 一闪而过看不清，这里把 PUT 的完整 status/payload
                    // 持久打到 console，遇到 "保存后再打开 voice 又没了" 这类问题能直接定位
                    // 是 PUT 被拒、还是后续 cleanup_invalid_voice_ids 把它清掉了。
                    console.log(
                        '[character voice PUT]',
                        'name=', savedCatgirlName,
                        'voice_id=', selectedVoiceId,
                        'status=', voiceResp.status,
                        'response=', voiceResult,
                    );
                    if (!voiceResp.ok || voiceResult.success === false) {
                        const detail = (voiceResult && voiceResult.error) || (voiceResp.status + ' ' + voiceResp.statusText);
                        // available_voices 直接打出来，方便看到 backend 当前认到的合法音色
                        if (voiceResult && Array.isArray(voiceResult.available_voices)) {
                            console.warn('[character voice PUT] backend 当前合法音色:', voiceResult.available_voices);
                        }
                        showMessage(
                            window.t ? window.t('character.partialSaveVoiceFailed', { error: detail }) : '角色已保存，但音色更新失败: ' + detail,
                            'error'
                        );
                    } else {
                        savedVoiceIdForLocalRawData = selectedVoiceId;
                        applyLocalVoiceIdToRawData(localRawData, savedVoiceIdForLocalRawData);
                    }
                } catch (voiceErr) {
                    showMessage(
                        window.t ? window.t('character.partialSaveVoiceFailed', { error: voiceErr.message || String(voiceErr) }) : '角色已保存，但音色更新失败: ' + (voiceErr.message || String(voiceErr)),
                        'error'
                    );
                } finally {
                    notifyVoiceConfigSwitching(savedCatgirlName, false, voiceSwitchOpId);
                }
            } else if (previousVoiceId) {
                const voiceSwitchOpId = createVoiceConfigSwitchOpId(savedCatgirlName);
                notifyVoiceConfigSwitching(savedCatgirlName, true, voiceSwitchOpId);
                try {
                    const clearResp = await fetch('/api/characters/catgirl/' + encodeURIComponent(savedCatgirlName) + '/unregister_voice', {
                        method: 'POST'
                    });
                    const clearResult = await clearResp.json().catch(() => ({}));
                    if (!clearResp.ok || clearResult.success === false) {
                        const detail = (clearResult && clearResult.error) || (clearResp.status + ' ' + clearResp.statusText);
                        showMessage(
                            window.t ? window.t('character.partialSaveVoiceFailed', { error: detail }) : '角色已保存，但音色更新失败: ' + detail,
                            'error'
                        );
                    } else {
                        savedVoiceIdForLocalRawData = '';
                        applyLocalVoiceIdToRawData(localRawData, savedVoiceIdForLocalRawData);
                    }
                } catch (clearErr) {
                    showMessage(
                        window.t ? window.t('character.partialSaveVoiceFailed', { error: clearErr.message || String(clearErr) }) : '角色已保存，但音色更新失败: ' + (clearErr.message || String(clearErr)),
                        'error'
                    );
                } finally {
                    notifyVoiceConfigSwitching(savedCatgirlName, false, voiceSwitchOpId);
                }
            }
        }

        // 保存 Live2D 待机动作（如果当前是 Live2D 模型且动作选择器有值）
        if (!isNew && form._modelType === 'live2d' && form._live2dModel) {
            const motionSelect = document.getElementById('preview-motion-select');
            const idleAnimation = motionSelect ? (motionSelect.value || '') : '';
            try {
                const l2dResp = await fetch('/api/characters/catgirl/l2d/' + encodeURIComponent(savedCatgirlName), {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        model_type: 'live2d',
                        live2d: form._live2dModel,
                        live2d_idle_animation: idleAnimation
                    })
                });
                const l2dResult = await l2dResp.json().catch(() => ({}));
                if (!l2dResp.ok || l2dResult.success === false) {
                    console.warn('[saveCatgirlFromPanel] 保存待机动作失败:', l2dResult.error || l2dResp.statusText);
                }
            } catch (l2dErr) {
                console.warn('[saveCatgirlFromPanel] 保存待机动作请求失败:', l2dErr);
            }
        }

        let selectedAfterSave = !shouldSelectAfterSave;
        if (shouldSelectAfterSave) {
            try {
                selectedAfterSave = await applyCurrentCatgirlSelection(savedCatgirlName, { showError: false });
                if (!selectedAfterSave) {
                    showMessage(
                        window.t ? window.t('character.switchFailed') : '切换失败',
                        'warning'
                    );
                }
            } catch (switchError) {
                console.warn('[角色面板] 新建角色已保存，但自动切换当前角色失败:', switchError);
                showMessage(
                    window.t ? window.t('character.switchError') : '切换猫娘时发生错误',
                    'warning'
                );
            }
        }

        if (!shouldSelectAfterSave || selectedAfterSave) {
            showMessage(isNew
                ? (window.t ? window.t('character.newCatgirlSuccess') : '新猫娘创建成功')
                : (window.t ? window.t('character.saveSuccess') : '保存成功'), 'success');
        }
        if (isNew) {
            const catgirlName = savedCatgirlName;
            const hasCardFace = window._cardFaceNames && window._cardFaceNames.has(catgirlName);
            if (!hasCardFace) {
                const makerParams = new URLSearchParams({
                    name: catgirlName,
                    mode: 'maker',
                    fallback_default_on_close: '1'
                });
                const makerUrl = `/card_maker?${makerParams.toString()}`;
                const makerWindow = openManagedPopup(
                    makerUrl,
                    CHARACTER_MANAGER_CARD_MAKER_WINDOW_NAME,
                    'width=1200,height=800'
                );
                if (!makerWindow) {
                    await showAlertDialog(window.t ? window.t('character.cardMakerPopupBlocked') : '卡面制作页面未能自动打开，请允许浏览器弹窗后重试，或点击卡面区域手动打开。', { type: 'warning' });
                    await rebuildSavedCatgirlPanel(form, catgirlName);
                } else {
                    closeCatgirlPanel();
                }
            } else {
                closeCatgirlPanel();
            }
        } else {
            const container = form.parentNode;
            const saveBtn = form.querySelector('#save-button');
            const cancelBtn = form.querySelector('#cancel-button');
            if (saveBtn) saveBtn.style.display = 'none';
            if (cancelBtn) cancelBtn.style.display = 'none';
            try {
                const freshData = await loadCharacterData();
                const freshRawData = freshData && freshData['猫娘'] && freshData['猫娘'][savedCatgirlName]
                    ? freshData['猫娘'][savedCatgirlName]
                    : {};
                savedRawDataForCache = mergeFreshCatgirlRawDataWithLocal(freshRawData, localRawData);
                if (savedVoiceIdForLocalRawData !== null) {
                    applyLocalVoiceIdToRawData(savedRawDataForCache, savedVoiceIdForLocalRawData);
                }
                setLocalRawDataFieldOrder(savedRawDataForCache, fieldOrder);
                syncCharacterCardCache(savedCatgirlName, savedRawDataForCache);
                buildCatgirlDetailForm(savedCatgirlName, savedRawDataForCache, false, container);
            } catch (e) {
                console.error('重新加载猫娘数据失败:', e);
                if (savedVoiceIdForLocalRawData !== null) {
                    applyLocalVoiceIdToRawData(localRawData, savedVoiceIdForLocalRawData);
                }
                buildCatgirlDetailForm(savedCatgirlName, localRawData, false, container);
            }
        }
        await loadCharacterCards();
        setLocalRawDataFieldOrder(savedRawDataForCache, fieldOrder);
        syncCharacterCardCache(savedCatgirlName, savedRawDataForCache);
        return true;
    } catch (error) {
        console.error('保存猫娘失败:', error);
        const errorMessage = error.message || String(error);
        showMessage(window.t ? window.t('character.saveError', { error: errorMessage }) : '保存时发生错误: ' + errorMessage, 'error');
        return false;
    } finally {
        form.dataset.submitting = 'false';
    }
}

async function ensureCanModifyCardsOutsideVoiceMode() {
    // 检查语音状态 - 先获取权威当前角色，再检查语音模式
    // cache: 'no-store' 防止浏览器/WebView 复用旧响应导致语音保护 fail-open
    try {
        const currentResp = await fetch('/api/characters/current_catgirl', { cache: 'no-store' });
        if (!currentResp.ok) {
            throw new Error(`current_catgirl request failed: ${currentResp.status}`);
        }
        const currentData = await currentResp.json();
        const currentCatgirl = currentData.current_catgirl || '';

        if (currentCatgirl) {
            if (isUnsafeCharacterPathSegment(currentCatgirl)) {
                console.warn('[CharacterCardManager] 当前角色名不能安全放进 URL path，跳过语音状态检查以允许救援切换:', currentCatgirl);
                return { ok: true, currentCatgirl, skippedVoiceCheckForInvalidName: true };
            }
            const voiceResp = await fetch(
                `/api/characters/catgirl/${encodeURIComponent(currentCatgirl)}/voice_mode_status`,
                { cache: 'no-store' }
            );
            if (!voiceResp.ok) {
                throw new Error(`voice_mode_status request failed: ${voiceResp.status}`);
            }
            const voiceData = await voiceResp.json();
            if (voiceData && voiceData.invalid_name) {
                console.warn('[CharacterCardManager] 当前角色名已被后端标记为非法，跳过语音状态检查以允许救援切换:', currentCatgirl);
                return { ok: true, currentCatgirl, skippedVoiceCheckForInvalidName: true };
            }
            if (voiceData.is_voice_mode) {
                const msg = window.t ? window.t('character.cannotModifyInVoiceMode') : '语音状态下无法切换或删除角色卡，请先关闭语音控制';
                showMessage(msg, 'error', 6000);
                await showAlertDialog(msg, { type: 'error' });
                return { ok: false };
            }
        }
        return { ok: true, currentCatgirl };
    } catch (error) {
        console.error('检查语音模式状态失败:', error);
        const msg = window.t ? window.t('character.voiceModeCheckFailed') : '检查语音模式状态失败，请稍后重试';
        showMessage(msg, 'error', 6000);
        await showAlertDialog(msg, { type: 'error' });
        return { ok: false };
    }
}

function isUnsafeCharacterPathSegment(name) {
    const value = String(name || '').trim();
    return !value
        || value === '.'
        || value === '..'
        || value.endsWith('.')
        || value.includes('..')
        || value.includes('/')
        || value.includes('\\')
        || /[\u0000-\u001F\u007F]/.test(value);
}

// 跨窗口通知主窗口（index.html / chat.html）热切换角色
// 后端的 WebSocket 通知只会送到已有活跃 session 的连接；用户从角色管理页直接切角色时，
// 主窗口未必握着 session（比如还没点过开始），WebSocket 路径会沉默。BroadcastChannel
// 兜底覆盖这一情况，且对端 handleCatgirlSwitch 自带 isSwitchingCatgirl/同名跳过的去重。
let _nekoPageChannelForCharaSwitch = null;
function _broadcastCatgirlSwitched(newCatgirl, oldCatgirl) {
    if (!newCatgirl || newCatgirl === oldCatgirl) return;
    if (typeof BroadcastChannel === 'undefined') return;
    try {
        if (!_nekoPageChannelForCharaSwitch) {
            _nekoPageChannelForCharaSwitch = new BroadcastChannel('neko_page_channel');
        }
        _nekoPageChannelForCharaSwitch.postMessage({
            action: 'catgirl_switched',
            new_catgirl: newCatgirl,
            old_catgirl: oldCatgirl,
            timestamp: Date.now()
        });
    } catch (e) {
        console.warn('[CharaCardManager] catgirl_switched 广播失败:', e);
    }
}

async function applyCurrentCatgirlSelection(name, options = {}) {
    const targetName = String(name || '').trim();
    if (!targetName) return false;

    let oldCatgirl = String(options.oldCatgirl || window._workshopCurrentCatgirl || '').trim();
    if (!oldCatgirl) {
        try {
            const currentResp = await fetch('/api/characters/current_catgirl', { cache: 'no-store' });
            if (currentResp.ok) {
                const currentData = await currentResp.json();
                oldCatgirl = String(currentData.current_catgirl || '').trim();
            }
        } catch (e) {
            console.warn('[CharaCardManager] 读取当前角色失败，继续尝试切换:', e);
        }
    }

    if (oldCatgirl === targetName) {
        window._workshopCurrentCatgirl = targetName;
        return true;
    }

    const response = await fetch('/api/characters/current_catgirl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ catgirl_name: targetName })
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.success) {
        if (options.showError !== false) {
            showMessage(result.error || (window.t ? window.t('character.switchFailed') : '切换失败'), 'error');
        }
        return false;
    }

    window._workshopCurrentCatgirl = targetName;
    renderCharaCardsView();
    _refreshOpenCatgirlPanelActions();

    if (typeof window.handleCatgirlSwitch === 'function') {
        const currentName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
        if (currentName !== targetName) {
            await Promise.resolve(window.handleCatgirlSwitch(targetName, oldCatgirl)).catch(e => {
                console.warn('[CharaCardManager] 同页角色切换失败:', e);
            });
        }
    }
    _broadcastCatgirlSwitched(targetName, oldCatgirl);
    return true;
}

// 角色卡详情面板（modal）目前只读取 _workshopCurrentCatgirl 的初始值来决定按钮态，
// 切角色后必须主动同步开着的面板，否则用户在小窗里点完按钮会觉得"毫无反应"。
//
// 单一数据源：依赖 wrapper.dataset.catgirlName 判定面板对应角色。任何创建/重建面板的
// 路径都必须设这个 dataset（目前是 openCatgirlPanel 和 rebuildSavedCatgirlPanel）；
// 不从表单 [name="档案名"] 兜底读，避免拿到用户编辑中的脏值。
function _refreshOpenCatgirlPanelActions() {
    const wrapper = document.getElementById('catgirl-panel-wrapper');
    if (!wrapper) return;
    const panelName = wrapper.dataset.catgirlName || '';
    if (!panelName) return;
    const isCurrent = (window._workshopCurrentCatgirl || '') === panelName;
    const switchBtn = wrapper.querySelector('.card-panel-actions .switch-btn');
    if (switchBtn) {
        switchBtn.disabled = isCurrent;
    }
    const deleteBtn = wrapper.querySelector('.card-panel-actions .delete-btn');
    if (deleteBtn) {
        deleteBtn.classList.toggle('disabled', isCurrent);
        deleteBtn.title = isCurrent
            ? (window.t ? window.t('character.cannotDeleteCurrentCard') : '当前正在使用的角色卡无法删除，请先切换到其他角色卡')
            : (window.t ? window.t('character.deleteCard') : '删除角色卡');
    }
}

// 切换猫娘
async function workshopSwitchCatgirl(name) {
    const guard = await ensureCanModifyCardsOutsideVoiceMode();
    if (!guard.ok) {
        return;
    }

    const oldCatgirl = guard.currentCatgirl || window._workshopCurrentCatgirl || '';

    try {
        const switched = await applyCurrentCatgirlSelection(name, { oldCatgirl });
        if (switched) {
            showMessage(window.t ? window.t('character.switchSuccess') : '切换成功', 'success');
        }
    } catch (error) {
        console.error('切换猫娘失败:', error);
        showMessage(window.t ? window.t('character.switchError') : '切换猫娘时发生错误', 'error');
    }
}

// 删除猫娘
// 返回值约定：成功删除返回 true；任何早退/失败/用户取消都返回 false——给调用方据此决定是否关面板
async function workshopDeleteCatgirl(name, options = {}) {
    const shouldReload = options && options.skipReload ? false : true;
    // 先做语音态预检并拿到权威当前角色名，避免别窗口切换后本地缓存失效
    const guard = await ensureCanModifyCardsOutsideVoiceMode();
    if (!guard.ok) {
        return false;
    }

    // 用权威值校验“是否当前角色”——本地 window._workshopCurrentCatgirl 在跨窗口切换后可能过期
    const authoritativeCurrent = guard.currentCatgirl || window._workshopCurrentCatgirl;
    if (name === authoritativeCurrent) {
        const msg = window.t ? window.t('character.cannotDeleteCurrentCard') : '不能删除当前正在使用的角色卡';
        showMessage(msg, 'error', 6000);
        await showAlertDialog(msg, { type: 'error' });
        return false;
    }

    // 检查是否只剩一只猫娘
    try {
        const resp = await fetch('/api/characters', { cache: 'no-store' });
        if (resp.ok) {
            const allData = await resp.json();
            const catgirls = allData?.['猫娘'] || {};
            if (Object.keys(catgirls).length <= 1) {
                showMessage(window.t ? window.t('character.onlyOneCatgirlLeft') : '只剩一只猫娘，无法删除！', 'error');
                return false;
            }
        }
    } catch (e) {
        // 如果检查失败，继续让用户尝试（后端也有保护）
    }

    // 确认删除
    let confirmMsg;
    if (window.t) {
        const translated = window.t('character.confirmDeleteCard', { name: name });
        confirmMsg = (translated && translated.includes('{name}'))
            ? `确定要删除猫娘"${name}"？`
            : (translated || `确定要删除猫娘"${name}"？`);
    } else {
        confirmMsg = `确定要删除猫娘"${name}"？`;
    }

    // 统一使用与「导出角色卡」同款风格的 Confirm 弹窗
    const confirmTitle = window.t ? window.t('character.deleteCardTitle') : '删除角色卡';
    const okText = window.t ? window.t('common.delete') : '删除';
    const cancelText = window.t ? window.t('common.cancel') : '取消';
    const confirmed = await showConfirmDialog(confirmMsg, {
        title: confirmTitle,
        okText,
        cancelText,
        danger: true,
    });
    if (!confirmed) return false;

    try {
        const useBodyDelete = isUnsafeCharacterPathSegment(name);
        const resp = await fetch(
            useBodyDelete ? '/api/characters/catgirl/delete' : '/api/characters/catgirl/' + encodeURIComponent(name),
            useBodyDelete
                ? {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                }
                : { method: 'DELETE' }
        );
        if (!resp.ok) {
            let serverMsg = '';
            try {
                const data = await resp.json();
                serverMsg = data?.error || data?.message || '';
            } catch (_) { /* 响应不是 JSON 就退回到默认文案 */ }
            const msg = serverMsg || (window.t ? window.t('character.deleteError') : '删除猫娘时发生错误');
            showMessage(msg, 'error', 6000);
            await showAlertDialog(msg, { type: 'error' });
            return false;
        }
        if (shouldReload) {
            // 重新加载角色卡列表
            await loadCharacterCards();
        }
        return true;
    } catch (error) {
        console.error('删除猫娘失败:', error);
        showMessage(window.t ? window.t('character.deleteError') : '删除猫娘时发生错误', 'error');
        return false;
    }
}

// ====== 占位符环形3D文字 ======
