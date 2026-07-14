// Part responsibility: master-profile rendering, persistence, hidden-character controls, and autosave helpers.

async function loadMasterProfile() {
    try {
        const resp = await fetch('/api/characters', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        const master = data?.['主人'] || {};
        renderMasterForm(master);
    } catch (e) {
        console.error('加载我的档案失败:', e);
    }
}

function renderMasterForm(master) {
    const form = document.getElementById('master-form');
    if (!form) return;
    form.innerHTML = '';
    const masterProfileName = normalizeCharacterFieldName(master['档案名']);
    const hasMasterProfileName = !!masterProfileName;

    // 档案名
    const baseWrapper = document.createElement('div');
    baseWrapper.className = 'field-row-wrapper';
    const baseLabel = document.createElement('label');
    const profileNameText = window.t ? window.t('character.profileName') : '档案名';
    const requiredText = window.t ? window.t('character.required') : '*';
    baseLabel.innerHTML = '<span data-i18n="character.profileName">' + profileNameText + '</span><span style="color:red" data-i18n="character.required">' + requiredText + '</span>';
    baseWrapper.appendChild(baseLabel);

    const fieldRow = document.createElement('div');
    fieldRow.className = 'field-row';
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.name = '档案名';
    nameInput.required = true;
    nameInput.value = masterProfileName;
    nameInput.autocomplete = 'off';
    nameInput.readOnly = hasMasterProfileName;
    nameInput.setAttribute('aria-readonly', hasMasterProfileName ? 'true' : 'false');
    if (hasMasterProfileName) {
        nameInput.title = window.t
            ? window.t('character.profileNameRenameOnlyHint')
            : '请通过“修改名称”按钮修改档案名';
    }
    fieldRow.appendChild(nameInput);
    baseWrapper.appendChild(fieldRow);

    // 重命名按钮
    const renameBtn = document.createElement('button');
    renameBtn.type = 'button';
    renameBtn.className = 'btn sm';
    renameBtn.style.minWidth = '70px';
    const renameText = window.t ? window.t('character.rename') : '修改名称';
    const renameTitle = window.t ? window.t('character.renameMasterTitle') : '重命名我的档案';
    renameBtn.textContent = renameText;
    renameBtn.title = renameTitle;
    renameBtn.setAttribute('aria-label', renameTitle);
    renameBtn.disabled = !hasMasterProfileName;
    renameBtn.onclick = renameMaster;
    baseWrapper.appendChild(renameBtn);

    form.appendChild(baseWrapper);

    // 自定义字段
    const renderedCustomFields = new Set();
    Object.keys(master).forEach(k => {
        const normalizedKey = normalizeCharacterFieldName(k);
        if (
            !normalizedKey
            || normalizedKey === '档案名'
            || isCharacterReservedFieldName(normalizedKey)
            || renderedCustomFields.has(normalizedKey)
        ) return;
        renderedCustomFields.add(normalizedKey);
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row';

        const label = document.createElement('label');
        _panelSetFieldLabel(label, normalizedKey);
        wrapper.appendChild(label);

        const row = document.createElement('div');
        row.className = 'field-row';
        const textarea = document.createElement('textarea');
        textarea.name = normalizedKey;
        textarea.rows = 1;
        textarea.value = master[k];
        row.appendChild(textarea);
        wrapper.appendChild(row);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn sm delete';
        const deleteText = window.t ? window.t('character.deleteField') : '删除设定';
        delBtn.textContent = deleteText;
        delBtn.onclick = function () { deleteMasterField(this); };
        wrapper.appendChild(delBtn);

        form.appendChild(wrapper);

        // textarea自动调整
        _panelAttachTextareaAutoResize(textarea);
        // 自动保存和变化监听
        if (hasMasterProfileName) {
            attachAutoSaveListener(textarea, 'master');
        }
        textarea.addEventListener('input', showMasterActionButtons);
        textarea.addEventListener('change', showMasterActionButtons);
    });

    // 按钮区
    const btnArea = document.createElement('div');
    btnArea.className = 'btn-area';
    btnArea.style.display = 'flex';
    btnArea.style.justifyContent = 'flex-end';
    btnArea.style.gap = '6px';
    btnArea.style.marginTop = '8px';

    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'btn sm add';
    const addText = window.t ? window.t('character.addMasterField') : '新增设定';
    addBtn.textContent = addText;
    addBtn.onclick = addMasterField;
    btnArea.appendChild(addBtn);

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.id = 'save-master-btn';
    saveBtn.className = 'btn sm';
    saveBtn.style.display = hasMasterProfileName ? 'none' : '';
    const saveText = window.t ? window.t('character.saveMaster') : '保存我的档案';
    saveBtn.textContent = saveText;
    saveBtn.onclick = saveMasterForm;
    btnArea.appendChild(saveBtn);

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.id = 'cancel-master-btn';
    cancelBtn.className = 'btn sm';
    cancelBtn.style.display = 'none';
    const cancelText = window.t ? window.t('character.cancel') : '取消';
    cancelBtn.textContent = cancelText;
    cancelBtn.onclick = function () {
        loadMasterProfile();
    };
    btnArea.appendChild(cancelBtn);

    form.appendChild(btnArea);

    // 档案名只允许通过重命名接口修改，避免绕过改名事件记录。
    if (!hasMasterProfileName) {
        nameInput.addEventListener('input', showMasterActionButtons);
        nameInput.addEventListener('change', showMasterActionButtons);
    }
}

function showMasterActionButtons() {
    const form = document.getElementById('master-form');
    if (!form) return;
    const saveBtn = form.querySelector('#save-master-btn');
    const cancelBtn = form.querySelector('#cancel-master-btn');
    if (saveBtn) saveBtn.style.display = '';
    if (cancelBtn) cancelBtn.style.display = '';
}

function hasMasterFormProfileName(form) {
    const nameInput = form?.querySelector('input[name="档案名"]');
    return !!normalizeCharacterFieldName(nameInput?.value || '');
}

async function saveMasterForm() {
    const form = document.getElementById('master-form');
    if (!form) return;
    const nameInput = form.querySelector('input[name="档案名"]');
    if (!nameInput || !nameInput.value.trim()) {
        showMessage(window.t ? window.t('character.profileNameRequired') : '档案名为必填项', 'error');
        return;
    }
    if (!nameInput.readOnly && !(await ensureValidCharacterProfileName(nameInput.value, nameInput))) {
        return;
    }
    const baseData = nameInput.readOnly
        ? {}
        : { '档案名': normalizeCharacterFieldName(nameInput.value) };
    const { data, duplicateKey } = collectCharacterFields(form, {
        baseData,
        excludeFieldNames: ['档案名'],
    });
    if (duplicateKey) {
        showMessage(window.t ? window.t('character.fieldExists') : '该设定已存在', 'error');
        return;
    }
    try {
        const resp = await fetch('/api/characters/master', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (resp.ok) {
            showMessage(window.t ? window.t('character.saveMasterSuccess') : '我的档案保存成功', 'success');
            await loadMasterProfile();
        } else {
            const err = await resp.text();
            showMessage((window.t ? window.t('character.saveMasterError') : '保存失败') + ': ' + err, 'error');
        }
    } catch (e) {
        showMessage(window.t ? window.t('character.saveMasterError') : '保存我的档案失败', 'error');
    }
}

// 自动保存相关
const _inputOriginalValues = new WeakMap();
function storeOriginalValue(input) {
    _inputOriginalValues.set(input, input.value);
}
function hasInputChanged(input) {
    return _inputOriginalValues.get(input) !== input.value;
}

function attachAutoSaveListener(input, type, catgirlName) {
    if (input.dataset.autoSaveAttached === 'true') return;
    input.dataset.autoSaveAttached = 'true';
    storeOriginalValue(input);
    input.addEventListener('blur', function (e) {
        if (!hasInputChanged(input)) return;
        const relatedTarget = e.relatedTarget;
        if (relatedTarget && (relatedTarget.closest('.btn.delete') || relatedTarget.closest('#cancel-button'))) return;
        setTimeout(() => {
            const activeEl = document.activeElement;
            if (activeEl && (activeEl.closest('.btn.delete') || activeEl.closest('#cancel-button'))) return;
            if (hasInputChanged(input)) {
                if (type === 'master') {
                    autoSaveMasterField(input);
                } else if (type === 'catgirl' && catgirlName) {
                    panelAutoSaveCatgirlField(input, catgirlName);
                }
            }
        }, 0);
    });
}

async function autoSaveMasterField(input) {
    const form = input.closest('form');
    if (!form || form.id !== 'master-form') return;
    if (!hasMasterFormProfileName(form)) return;
    const fieldName = normalizeCharacterFieldName(input.name);
    if (!fieldName) return;
    if (fieldName === '档案名') return;
    const { data: allData, duplicateKey } = collectCharacterFields(form, {
        excludeFieldNames: ['档案名'],
    });
    if (duplicateKey) {
        showMessage(window.t ? window.t('character.fieldExists') : '该设定已存在', 'error');
        return;
    }
    // 空对象用于持久化“清空最后一个自定义字段”的自动保存。
    try {
        const resp = await fetch('/api/characters/master', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(allData)
        });
        if (resp.ok) {
            storeOriginalValue(input);
            const allInputs = form.querySelectorAll('input, textarea');
            allInputs.forEach(inp => storeOriginalValue(inp));
            const stillDirty = Array.from(allInputs).some(inp => hasInputChanged(inp));
            if (!stillDirty) {
                const saveBtn = form.querySelector('#save-master-btn');
                const cancelBtn = form.querySelector('#cancel-master-btn');
                if (saveBtn) saveBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'none';
            }
            showAutoSaveToast(window.t ? window.t('character.autoSaved') : '已自动保存设定');
        }
    } catch (e) {
        console.error('自动保存主人字段失败:', e);
    }
}

async function panelAutoSaveCatgirlField(input, catgirlName) {
    if (!catgirlName) return;
    const form = input.closest('form');
    if (!form) return;
    const fieldName = normalizeCharacterFieldName(input.name);
    if (!fieldName || fieldName === '档案名' || fieldName === 'voice_id') return;
    const { data, duplicateKey, fieldOrder } = collectCharacterFields(form, {
        baseData: { '档案名': catgirlName },
        excludeFieldNames: ['档案名', 'voice_id'],
    });
    if (duplicateKey) {
        showMessage(window.t ? window.t('character.fieldExists') : '该设定已存在', 'error');
        return;
    }
    attachCharacterFieldOrderPayload(data, fieldOrder);
    try {
        const resp = await fetch('/api/characters/catgirl/' + encodeURIComponent(catgirlName), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (resp.ok) {
            syncCharacterCardCache(catgirlName, buildLocalCatgirlRawData(catgirlName, data, fieldOrder));
            storeOriginalValue(input);
            const allInputs = form.querySelectorAll('input, textarea');
            const sentFields = new Set(Object.keys(data));
            allInputs.forEach(inp => {
                if (inp.name && sentFields.has(inp.name)) {
                    storeOriginalValue(inp);
                }
            });
            const stillDirty = Array.from(allInputs).some(inp => hasInputChanged(inp));
            if (!stillDirty) {
                const saveBtn = form.querySelector('#save-button');
                const cancelBtn = form.querySelector('#cancel-button');
                if (saveBtn) saveBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'none';
            }
            showAutoSaveToast(window.t ? window.t('character.autoSaved') : '已自动保存设定');
        }
    } catch (e) {
        console.error('自动保存猫娘字段失败:', e);
    }
}

let _autoSaveToastTimer = null;
let _autoSaveToastEl = null;
function showAutoSaveToast(message) {
    if (!_autoSaveToastEl) {
        _autoSaveToastEl = document.createElement('div');
        _autoSaveToastEl.className = 'auto-save-toast';
        document.body.appendChild(_autoSaveToastEl);
    }
    _autoSaveToastEl.textContent = message;
    _autoSaveToastEl.classList.add('visible');
    if (_autoSaveToastTimer) clearTimeout(_autoSaveToastTimer);
    _autoSaveToastTimer = setTimeout(() => {
        if (_autoSaveToastEl) _autoSaveToastEl.classList.remove('visible');
    }, 2000);
}

async function addMasterField() {
    const form = document.getElementById('master-form');
    if (!form) return;
    let key = '';
    if (typeof showPrompt === 'function') {
        key = await showPrompt(
            window.t ? window.t('character.addMasterFieldPrompt') : '请输入新设定的名称（键名）',
            '',
            window.t ? window.t('character.addMasterFieldTitle') : '新增我的档案字段'
        );
    } else {
        key = prompt(window.t ? window.t('character.addMasterFieldPrompt') : '请输入新设定的名称（键名）');
    }
    key = normalizeCharacterFieldName(key);
    if (!key || key === '档案名' || isCharacterReservedFieldName(key)) return;
    const exists = Array.from(form.querySelectorAll('textarea, input')).some(
        el => normalizeCharacterFieldName(el.name) === key
    );
    if (exists) {
        showMessage(window.t ? window.t('character.fieldExists') : '该设定已存在', 'error');
        return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'field-row-wrapper custom-row';
    const label = document.createElement('label');
    _panelSetFieldLabel(label, key);
    wrapper.appendChild(label);

    const row = document.createElement('div');
    row.className = 'field-row';
    const textarea = document.createElement('textarea');
    textarea.name = key;
    textarea.rows = 1;
    row.appendChild(textarea);
    wrapper.appendChild(row);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'btn sm delete';
    delBtn.textContent = window.t ? window.t('character.deleteField') : '删除设定';
    delBtn.onclick = function () { deleteMasterField(this); };
    wrapper.appendChild(delBtn);

    form.insertBefore(wrapper, form.querySelector('.btn-area'));
    _panelAttachTextareaAutoResize(textarea);
    if (hasMasterFormProfileName(form)) {
        attachAutoSaveListener(textarea, 'master');
    }
    textarea.addEventListener('input', showMasterActionButtons);
    textarea.addEventListener('change', showMasterActionButtons);
    textarea.focus();
    showMasterActionButtons();
}

function deleteMasterField(btn) {
    const wrapper = btn.parentNode;
    const label = wrapper.querySelector('label');
    if (label && label.textContent === (window.t ? window.t('character.profileName') : '档案名')) return;
    wrapper.remove();
    showMasterActionButtons();
}

async function renameMaster() {
    const form = document.getElementById('master-form');
    if (!form) return;
    const nameInput = form.querySelector('input[name="档案名"]');
    const oldName = normalizeCharacterFieldName(nameInput?.value || '');
    if (!oldName) {
        showMessage(window.t ? window.t('character.profileNameRequired') : '档案名为必填项', 'error');
        return;
    }
    const promptText = window.t ? window.t('character.renameMasterPrompt') : '请输入新的档案名';
    const titleText = window.t ? window.t('character.renameMasterTitle') : '重命名我的档案';
    let newName;
    if (typeof showPrompt === 'function') {
        newName = await showPrompt(
            promptText,
            oldName,
            titleText
        );
    } else {
        newName = prompt(promptText, oldName);
    }
    const normalizedNewName = normalizeCharacterFieldName(newName);
    if (!normalizedNewName || normalizedNewName === oldName) return;
    if (!(await ensureValidCharacterProfileName(normalizedNewName, nameInput))) {
        return;
    }
    try {
        const useBodyFallback = /[\\/]/.test(oldName);
        let resp;
        if (useBodyFallback) {
            // 旧配置可能含路径分隔符，无法可靠放进 path 参数，改用普通保存接口修复档案名。
            const { data, duplicateKey } = collectCharacterFields(form, {
                baseData: { '档案名': normalizedNewName },
                excludeFieldNames: ['档案名'],
            });
            if (duplicateKey) {
                showMessage(window.t ? window.t('character.fieldExists') : '该设定已存在', 'error');
                return;
            }
            resp = await fetch('/api/characters/master', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
        } else {
            resp = await fetch('/api/characters/master/' + encodeURIComponent(oldName) + '/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_name: normalizedNewName })
            });
        }
        const result = await resp.json();
        if (result.success) {
            showMessage(window.t ? window.t('character.renameSuccess') : '重命名成功', 'success');
            await loadMasterProfile();
        } else {
            showMessage(result.error || (window.t ? window.t('character.renameFailed') : '重命名失败'), 'error');
        }
    } catch (e) {
        const errorMessage = e.message || String(e);
        showMessage(window.t ? window.t('character.renameError', { error: errorMessage }) : '重命名失败: ' + errorMessage, 'error');
    }
}

function toggleMasterSection() {
    const content = document.getElementById('master-profile-content');
    const header = document.getElementById('master-profile-header');
    if (!content || !header) return;
    const isOpening = !content.classList.contains('open');

    if (content._masterProfileHideTimer) {
        clearTimeout(content._masterProfileHideTimer);
        content._masterProfileHideTimer = null;
    }
    if (content._masterProfileHideContent) {
        content.removeEventListener('transitionend', content._masterProfileHideContent);
        content._masterProfileHideContent = null;
    }

    if (isOpening) {
        content.style.display = 'block';
        content.style.setProperty('--master-profile-viewport-left', `${content.getBoundingClientRect().left}px`);
        content.getBoundingClientRect();
        content.classList.add('open');
        header.classList.add('open');
        header.setAttribute('aria-expanded', 'true');
        return;
    }

    content.classList.remove('open');
    header.classList.remove('open');
    header.setAttribute('aria-expanded', 'false');

    const hideContent = function (event) {
        if (event && event.target !== content) return;
        if (event && event.propertyName !== 'clip-path') return;
        if (!content.classList.contains('open')) {
            content.style.display = 'none';
        }
        content.removeEventListener('transitionend', hideContent);
        if (content._masterProfileHideTimer) {
            clearTimeout(content._masterProfileHideTimer);
            content._masterProfileHideTimer = null;
        }
        if (content._masterProfileHideContent === hideContent) {
            content._masterProfileHideContent = null;
        }
    };

    content.addEventListener('transitionend', hideContent);
    content._masterProfileHideContent = hideContent;
    content._masterProfileHideTimer = setTimeout(hideContent, 280);
}

// ===================== 隐藏猫娘 =====================

function getHiddenCatgirlKeys() {
    try {
        const stored = localStorage.getItem('hidden_catgirls');
        if (!stored) return [];
        const parsed = JSON.parse(stored);
        if (!Array.isArray(parsed)) return [];
        return parsed.filter(x => typeof x === 'string');
    } catch (e) {
        return [];
    }
}

async function workshopHideCatgirl(name) {
    if (name === window._workshopCurrentCatgirl) {
        showMessage(window.t ? window.t('character.cannotHideCurrentNeko') : '不能隐藏当前正在使用的猫娘', 'error');
        return;
    }
    const hiddenKeys = getHiddenCatgirlKeys();
    if (!hiddenKeys.includes(name)) {
        hiddenKeys.push(name);
        localStorage.setItem('hidden_catgirls', JSON.stringify(hiddenKeys));
    }
    renderCharaCardsView();
    renderHiddenCatgirls();
}

function workshopUnhideCatgirl(name) {
    const hiddenKeys = getHiddenCatgirlKeys();
    const newKeys = hiddenKeys.filter(k => k !== name);
    localStorage.setItem('hidden_catgirls', JSON.stringify(newKeys));
    renderCharaCardsView();
    renderHiddenCatgirls();
}

function renderHiddenCatgirls() {
    const area = document.getElementById('hidden-catgirl-area');
    const list = document.getElementById('hidden-catgirl-list');
    const countSpan = document.getElementById('hidden-catgirl-count');
    const toggleBtn = document.getElementById('toggle-hidden-btn');
    if (!area || !list) return;

    const hiddenKeys = getHiddenCatgirlKeys();

    // 更新 toolbar 按钮显示状态
    if (toggleBtn) {
        toggleBtn.style.display = hiddenKeys.length > 0 ? 'inline-flex' : 'none';
        const btnText = toggleBtn.querySelector('span');
        if (btnText) {
            btnText.textContent = window._showHiddenCatgirls
                ? (window.t ? window.t('character.hideHidden') : '隐藏已隐藏')
                : (window.t ? window.t('character.showHidden') : '显示已隐藏');
        }
        toggleBtn.classList.toggle('active', !!window._showHiddenCatgirls);
    }

    if (hiddenKeys.length === 0) {
        area.style.display = 'none';
        return;
    }

    area.style.display = 'block';
    const hiddenText = window.t ? window.t('character.hiddenCatgirls') : '已隐藏猫娘';
    if (countSpan) countSpan.textContent = hiddenText + ' (' + hiddenKeys.length + ')';

    list.innerHTML = '';
    hiddenKeys.forEach(key => {
        const item = document.createElement('div');
        item.className = 'hidden-catgirl-item';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'catgirl-name';
        nameSpan.textContent = key;
        item.appendChild(nameSpan);

        const unhideBtn = document.createElement('button');
        unhideBtn.className = 'btn sm';
        unhideBtn.style.background = '#40C5F1';
        unhideBtn.style.minWidth = '60px';
        unhideBtn.textContent = window.t ? window.t('character.show') : '显示';
        unhideBtn.onclick = function () {
            workshopUnhideCatgirl(key);
        };
        item.appendChild(unhideBtn);

        list.appendChild(item);
    });
}

function toggleHiddenCatgirlsHeader() {
    const list = document.getElementById('hidden-catgirl-list');
    const arrow = document.getElementById('hidden-catgirl-arrow');
    const btn = document.querySelector('.hidden-catgirl-header-btn');
    if (!list) return;
    const isHidden = list.style.display === 'none';
    list.style.display = isHidden ? 'block' : 'none';
    if (arrow) arrow.classList.toggle('expanded', isHidden);
    if (btn) btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
}

function toggleShowHiddenCatgirls() {
    window._showHiddenCatgirls = !window._showHiddenCatgirls;
    renderCharaCardsView();
    renderHiddenCatgirls();
}

// ===================== 面板自动保存（供 buildCatgirlDetailForm 调用） =====================

function panelAttachAutoSaveListener(input, catgirlName) {
    if (input.dataset.autoSaveAttached === 'true') return;
    input.dataset.autoSaveAttached = 'true';
    storeOriginalValue(input);
    input.addEventListener('blur', function (e) {
        if (!hasInputChanged(input)) return;
        const relatedTarget = e.relatedTarget;
        if (relatedTarget && (relatedTarget.closest('.btn.delete') || relatedTarget.closest('#cancel-button') || relatedTarget.closest('#rename-catgirl-btn'))) return;
        setTimeout(() => {
            const activeEl = document.activeElement;
            if (activeEl && (activeEl.closest('.btn.delete') || activeEl.closest('#cancel-button') || activeEl.closest('#rename-catgirl-btn'))) return;
            if (hasInputChanged(input)) {
                panelAutoSaveCatgirlField(input, catgirlName);
            }
        }, 0);
    });
}

// ===================== 猫猫辅助生成猫娘设定（陪伴式聊天面板） =====================
// 设计：点击「✨ 猫猫辅助生成」会在屏幕右侧拉出一个驻留的聊天面板，扮演一只
// 「设定捏人助手猫娘」（暂用 YUI 的卡面顶替，未来会换成开发猫角色）。面板里：
//   - 先一句话描述 → AI 抛 2-4 道带 chip 的澄清问题 → AI 一次性生成全部字段
//     并自动应用到表单 → 进入自由聊天模式
//   - 聊天模式下用户可以随时让助手再调字段（"让她更外向"、"招牌台词换一句"），
//     LLM 在 /api/card-assist/chat 返回结构化 actions，前端自动 patch 表单
//   - 同时监视表单：用户在面板外手改字段时，把这条改动以「你刚改了 X」的
//     system 气泡告诉助手 + 用户，保持双方对当前状态的共识
// 助手不主动调 LLM 评论（成本考虑）；用户随时可以用 quick chip 让她审一审。

// 判断某字段名是否是「系统/工坊保留字段」——AI 不该把它当普通设定去写。
// ⚠ 之前这里维护一份写死的部分列表，漏了 live3d_sub_type / vrm_animation / lighting /
// live2d_item_id 等：那些 key 会被渲染成普通 AI 字段、autosave 报成功，但后端保存时被
// collectCharacterFields / _filter_mutable_catgirl_fields 丢掉，刷新后字段消失、改动静默
// 丢失（Codex #3331668038）。改成复用角色编辑器同一套 isCharacterReservedFieldName（走后端
// 实时配置 + ReservedFieldsUtils 兜底，与后端 CHARACTER_RESERVED_FIELDS 同源），再叠加
// card-assist 特有的 '档案名'（表单元数据 input 的固定 name，不在保留字段配置里）。
