// 允许的来源列表
const ALLOWED_ORIGINS = [window.location.origin];

// 自动调整textarea高度
function autoResizeTextarea(textarea) {
    // 重置高度为auto以计算正确的高度
    textarea.style.height = 'auto';
    const style = getComputedStyle(textarea);
    const minHeight = parseInt(style.minHeight) || 34;

    // 计算内容高度，考虑padding
    const paddingTop = parseInt(style.paddingTop) || 0;
    const paddingBottom = parseInt(style.paddingBottom) || 0;

    // 设置高度为scrollHeight，但限制最大高度为三行
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
    if (contentHeight > maxContentHeight) {
        textarea.style.overflowY = 'auto';
    } else {
        textarea.style.overflowY = 'hidden';
    }
}

// 辅助函数：为textarea附加自动调整高度的功能
function attachTextareaAutoResize(textarea) {
    if (!textarea) return;

    // 初始化高度
    autoResizeTextarea(textarea);

    // 检查是否已经附加过事件监听器，防止重复绑定
    if (textarea.dataset.autoResizeAttached === 'true') {
        return;
    }

    // 添加输入和焦点事件监听器
    textarea.addEventListener('input', function () {
        autoResizeTextarea(this);
    });
    textarea.addEventListener('focus', function () {
        autoResizeTextarea(this);
    });

    // 标记已附加
    textarea.dataset.autoResizeAttached = 'true';
}

// 初始化所有textarea的自动调整功能
function initAutoResizeTextareas() {
    const textareas = document.querySelectorAll('textarea');
    textareas.forEach(textarea => {
        // 初始调整
        autoResizeTextarea(textarea);

        // 检查是否已经附加过事件监听器，防止重复绑定
        if (textarea.dataset.autoResizeAttached === 'true') {
            return;
        }

        // 监听输入事件
        textarea.addEventListener('input', function () {
            autoResizeTextarea(this);
        });

        // 监听focus事件，确保获得焦点时也调整高度
        textarea.addEventListener('focus', function () {
            autoResizeTextarea(this);
        });

        // 标记已附加
        textarea.dataset.autoResizeAttached = 'true';
    });
}

// 折叠面板切换
function toggleFold(fold) {
    fold.classList.toggle('open');
}

// 档案名长度限制：最多 20 个计数单位（纯中文不超过 10 个字）
// 计数规则：ASCII(<=0x7F) 计 1，其它字符计 2
const PROFILE_NAME_MAX_UNITS = 20;

const PROFILE_NAME_MAX_HINT_KEY = 'character.profileNameMaxHint';
const PROFILE_NAME_TOO_LONG_KEY = 'character.profileNameTooLong';
const NEW_PROFILE_NAME_REQUIRED_KEY = 'character.newProfileNameRequired';
const NEW_PROFILE_NAME_TOO_LONG_KEY = 'character.newProfileNameTooLong';

/**
 * 获取字段的本地化显示标签
 * 用于将中文键名（如"性别"）翻译为当前语言的显示文本（如"Gender"）
 * @param {string} fieldName - 字段的原始键名
 * @returns {string} 翻译后的标签文本
 */
function getFieldLabel(fieldName) {
    // 尝试从 i18n 获取翻译
    if (window.t) {
        const translated = window.t(`characterProfile.labels.${fieldName}`);
        // 如果翻译结果不等于 key 本身，说明找到了翻译
        if (translated && translated !== `characterProfile.labels.${fieldName}`) {
            return translated;
        }
    }
    // 没有翻译则返回原始键名
    return fieldName;
}

function tOrFallback(key, fallback, params) {
    if (window.t && typeof window.t === 'function') {
        try {
            return window.t(key, params || {});
        } catch (e) {
            // ignore
        }
    }
    return fallback;
}

function profileNameCountUnits(str) {
    if (!str) return 0;
    let units = 0;
    for (const ch of String(str)) {
        units += (ch.charCodeAt(0) <= 0x7F) ? 1 : 2;
    }
    return units;
}

function profileNameTrimToMaxUnits(str, maxUnits) {
    if (!str) return '';
    let units = 0;
    let out = '';
    for (const ch of String(str)) {
        const inc = (ch.charCodeAt(0) <= 0x7F) ? 1 : 2;
        if (units + inc > maxUnits) break;
        out += ch;
        units += inc;
    }
    return out;
}

function flashProfileNameTooLong(inputEl) {
    if (!inputEl) return;

    const msg = tOrFallback(PROFILE_NAME_TOO_LONG_KEY, '档案名过长');

    // 红框：优先给胶囊容器加 class（chara_manager 页面），同时也给 input 自己加 class（兼容弹窗）
    const fieldRow = inputEl.closest ? inputEl.closest('.field-row') : null;
    if (fieldRow) fieldRow.classList.add('profile-name-too-long');
    inputEl.classList.add('profile-name-too-long');
    inputEl.setAttribute('aria-invalid', 'true');

    // 临时提示：放在 field-row 下方
    let tip = null;
    if (fieldRow) {
        tip = fieldRow.querySelector(':scope > .profile-name-too-long-tip');
        if (!tip) {
            tip = document.createElement('div');
            tip.className = 'profile-name-too-long-tip';
            fieldRow.appendChild(tip);
        }
        tip.textContent = msg;
    }

    // 用浏览器原生校验气泡提示一次，但不阻塞后续提交（立即清理 validity）
    try {
        inputEl.setCustomValidity(msg);
        if (typeof inputEl.reportValidity === 'function') inputEl.reportValidity();
    } catch (e) {
        // ignore
    } finally {
        setTimeout(() => {
            try { inputEl.setCustomValidity(''); } catch (e) { /* ignore */ }
        }, 0);
    }

    // 1s 后自动恢复
    const token = String(Date.now());
    inputEl.dataset.profileNameTooLongToken = token;
    setTimeout(() => {
        if (inputEl.dataset.profileNameTooLongToken !== token) return;
        if (fieldRow) fieldRow.classList.remove('profile-name-too-long');
        inputEl.classList.remove('profile-name-too-long');
        inputEl.removeAttribute('aria-invalid');
        if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
    }, 1000);
}

function attachProfileNameLimiter(inputEl) {
    if (!inputEl || inputEl.dataset.profileNameLimiterAttached === 'true') return;
    inputEl.dataset.profileNameLimiterAttached = 'true';

    // IME 组合输入期间不要修改 value/selection，否则可能打断中文输入
    let composing = false;

    // 仅作为辅助上限；真正限制由计数单位逻辑实现
    try {
        inputEl.maxLength = PROFILE_NAME_MAX_UNITS;
    } catch (e) {
        // ignore
    }

    // 删除输入框提示（placeholder/title 由需求移除），这里只做长度限制与超限反馈

    const enforce = () => {
        if (composing) return;
        if (inputEl.readOnly || inputEl.disabled) return;
        const before = inputEl.value;
        const beforeUnits = profileNameCountUnits(before);
        const after = profileNameTrimToMaxUnits(before, PROFILE_NAME_MAX_UNITS);
        if (before !== after) {
            const caret = (typeof inputEl.selectionStart === 'number') ? inputEl.selectionStart : null;
            inputEl.value = after;
            if (caret !== null) {
                const newPos = Math.min(caret, after.length);
                try { inputEl.setSelectionRange(newPos, newPos); } catch (e) { /* ignore */ }
            }

            // 用户尝试输入超限：红框标记 + 提示
            if (beforeUnits > PROFILE_NAME_MAX_UNITS) {
                flashProfileNameTooLong(inputEl);
            }
        }

        // 理论上不会超过（已截断），这里保持表单可提交
        try { inputEl.setCustomValidity(''); } catch (e) { /* ignore */ }
    };

    inputEl.addEventListener('input', enforce);
    inputEl.addEventListener('compositionstart', () => {
        composing = true;
    });
    // 中文输入法：composition 期间不要强制截断，结束时再强制一次
    inputEl.addEventListener('compositionend', () => {
        composing = false;
        enforce();
    });
    enforce();
}

// 事件委托：覆盖动态创建的猫娘表单
if (!window._profileNameLimiterDelegated) {
    document.body.addEventListener('focusin', function (e) {
        const target = e.target;
        if (target && target.matches && target.matches('input[name="档案名"]')) {
            attachProfileNameLimiter(target);
        }
    });
    document.body.addEventListener('input', function (e) {
        const target = e.target;
        if (target && target.matches && target.matches('input[name="档案名"]')) {
            attachProfileNameLimiter(target);
        }
    });
    window._profileNameLimiterDelegated = true;
}

// 事件委托，支持所有动态表单的折叠按钮和箭头符号
if (!window._charaManagerFoldHandler) {
    document.body.addEventListener('click', function (e) {
        if (e.target.classList.contains('fold-toggle') || (e.target.classList.contains('arrow') && e.target.parentNode.classList.contains('fold-toggle'))) {
            let toggle = e.target.classList.contains('fold-toggle') ? e.target : e.target.parentNode;
            let fold = toggle.closest('.fold');
            if (fold) {
                fold.classList.toggle('open');
                // 动态切换箭头（图片旋转）
                let arrow = toggle.querySelector('.arrow');
                if (arrow && arrow.tagName === 'IMG') {
                    arrow.style.transform = fold.classList.contains('open') ? 'rotate(0deg)' : 'rotate(-90deg)';
                }

                // 立即保存高级设置下拉栏状态
                // 检查是否是最外层的fold（进阶设定）
                const advancedSettingsText = window.t ? window.t('character.advancedSettings') : '进阶设定';
                if (!fold.parentElement.closest('.fold') && toggle.textContent.includes(advancedSettingsText)) {
                    // 获取当前表单
                    const form = fold.closest('form');
                    if (form) {
                        // 尝试从表单的_catgirlName属性或档案名字段获取猫娘名称
                        let catgirlName = form._catgirlName;
                        if (!catgirlName) {
                            const nameField = form.querySelector('[name="档案名"]');
                            if (nameField) {
                                catgirlName = nameField.value.trim();
                            }
                        }

                        // 如果有猫娘名称，保存展开状态
                        if (catgirlName) {
                            const isOpen = fold.classList.contains('open');
                            localStorage.setItem(`catgirl_advanced_${catgirlName}`, isOpen.toString());
                        }
                    }
                }
            }
        }
    });
    window._charaManagerFoldHandler = true;
}

// 角色数据缓存
let characterData = null;
let characterReservedFieldsConfig = {
    system_reserved_fields: [],
    workshop_reserved_fields: [],
    all_reserved_fields: []
};

function getAllReservedFields() {
    if (characterReservedFieldsConfig) {
        const allFields = Array.isArray(characterReservedFieldsConfig.all_reserved_fields)
            ? characterReservedFieldsConfig.all_reserved_fields
            : [];
        const systemFields = Array.isArray(characterReservedFieldsConfig.system_reserved_fields)
            ? characterReservedFieldsConfig.system_reserved_fields
            : [];
        const workshopFields = Array.isArray(characterReservedFieldsConfig.workshop_reserved_fields)
            ? characterReservedFieldsConfig.workshop_reserved_fields
            : [];
        const merged = [...new Set([...allFields, ...systemFields, ...workshopFields])];
        if (merged.length > 0) {
            return merged;
        }
    }
    // 后端不可用时的兜底，避免前端行为回退到“无保留字段过滤”
    return [
        'live2d', 'voice_id', 'system_prompt', 'model_type', 'vrm', 'vrm_animation', 'lighting', 'vrm_rotation', 'live2d_item_id',
        '原始数据', '文件路径', '创意工坊物品ID', 'description', 'tags', 'name', '描述', '标签', '关键词'
    ];
}

async function loadCharacterReservedFieldsConfig() {
    const safeDefaults = {
        system_reserved_fields: [],
        workshop_reserved_fields: [],
        all_reserved_fields: []
    };
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);
    try {
        const resp = await fetch('/api/config/character_reserved_fields', { signal: controller.signal });
        if (!resp.ok) {
            characterReservedFieldsConfig = safeDefaults;
            console.error(`加载角色保留字段配置失败: HTTP ${resp.status}`);
            return;
        }
        const data = await resp.json();
        if (data && data.success) {
            characterReservedFieldsConfig = {
                system_reserved_fields: Array.isArray(data.system_reserved_fields) ? data.system_reserved_fields : [],
                workshop_reserved_fields: Array.isArray(data.workshop_reserved_fields) ? data.workshop_reserved_fields : [],
                all_reserved_fields: Array.isArray(data.all_reserved_fields) ? data.all_reserved_fields : []
            };
        } else {
            characterReservedFieldsConfig = safeDefaults;
            console.error(
                '加载角色保留字段配置失败: success 标志无效',
                (data && (data.error || data.message || data.status)) || data
            );
        }
    } catch (e) {
        characterReservedFieldsConfig = safeDefaults;
        console.error('加载角色保留字段配置失败，使用安全默认值:', e);
    } finally {
        clearTimeout(timeoutId);
    }
}

// 通过服务端API同步工坊角色卡（服务端统一扫描，无需前端逐个fetch）
async function autoScanWorkshopCharacterCards() {
    try {
        const response = await fetch('/api/steam/workshop/sync-characters', { method: 'POST' });
        if (!response.ok) {
            const errText = await response.text().catch(() => '');
            console.error(`[工坊扫描] 服务端返回错误: HTTP ${response.status} ${response.statusText}`, errText);
            return false;
        }
        const result = await response.json();
        if (result.success === false) {
            console.error(`[工坊扫描] 服务端同步失败: ${result.error || result.message || '未知错误'}`, result);
            return false;
        }
        if (result.added > 0) {
            console.log(`[工坊扫描] 服务端同步完成：新增 ${result.added} 个角色卡，跳过 ${result.skipped} 个已存在`);
            return true;
        }
        console.log('[工坊扫描] 服务端同步完成：无新增角色卡');
        return false;
    } catch (error) {
        console.error('[工坊扫描] 服务端角色卡同步请求异常:', error);
        return false;
    }
}

// 导入单个工坊角色卡文件，返回是否成功添加
async function importWorkshopCharaFile(filePath, itemId) {
    void itemId;
    try {
        const readResponse = await fetch(`/api/steam/workshop/read-file?path=${encodeURIComponent(filePath)}`);
        const readResult = await readResponse.json();

        if (readResult.success) {
            const charaData = JSON.parse(readResult.content);

            // 档案名是必需字段，用作 characters.json 中的 key
            if (!charaData['档案名']) {
                console.log(`[工坊扫描] 角色卡 ${filePath} 缺少"档案名"字段，跳过`);
                return false;
            }

            const RESERVED_FIELDS = getAllReservedFields();

            // 转换为符合catgirl API格式的数据（不包含保留字段）
            const catgirlFormat = {
                '档案名': charaData['档案名']
            };

            // 跳过的字段：档案名（已处理）、保留字段
            const skipKeys = ['档案名', ...RESERVED_FIELDS];
            const dangerousKeys = ['__proto__', 'constructor', 'prototype'];

            // 添加所有非保留字段，并防止原型污染
            for (const [key, value] of Object.entries(charaData)) {
                if (Object.prototype.hasOwnProperty.call(charaData, key) &&
                    !skipKeys.includes(key) &&
                    !dangerousKeys.includes(key) &&
                    value !== undefined &&
                    value !== null &&
                    value !== '') {
                    catgirlFormat[key] = value;
                }
            }

            // 重要：如果角色卡有 live2d 字段，需要同时保存 live2d_item_id
            // 这样首页加载时才能正确构建工坊模型的路径
            // live2d_item_id 由后端指定功能管理，这里不再由通用导入流程直写

            // 静默添加到系统
            const addResponse = await fetch('/api/characters/catgirl', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(catgirlFormat)
            });
            const addResult = await addResponse.json();
            return addResult.success === true;
        }
    } catch (e) {
        // 静默处理
    }
    return false;
}

// 加载角色数据
// 跟踪当前展开的猫娘名称
let expandedCatgirlName = null;

async function loadCharacterData() {
    try {
        const resp = await fetch('/api/characters');
        if (!resp.ok) {
            throw new Error(`HTTP error! status: ${resp.status}`);
        }
        characterData = await resp.json();
        renderMaster();
        renderCatgirls();
        updateSwitchButtons();

        // 如果有之前展开的猫娘，自动展开它
        if (expandedCatgirlName) {
            const catgirls = characterData['猫娘'] || {};
            if (catgirls[expandedCatgirlName]) {
                // 使用 setTimeout 确保 DOM 已经渲染完成
                setTimeout(() => {
                    // 遍历所有猫娘块，找到匹配的
                    const blocks = document.querySelectorAll('.catgirl-block');
                    blocks.forEach(block => {
                        const titleSpan = block.querySelector('.catgirl-title');
                        if (titleSpan && titleSpan.textContent === expandedCatgirlName) {
                            const btn = block.querySelector('.catgirl-expand');
                            const details = block.querySelector('.catgirl-details');
                            if (btn && details && details.style.display === 'none') {
                                // 展开这个猫娘
                                details.style.display = 'block';
                                btn.style.transform = 'rotate(180deg)';
                                showCatgirlForm(expandedCatgirlName, details);
                            }
                        }
                    });
                }, 0);
            } else {
                // 如果猫娘不存在了，清除记录
                expandedCatgirlName = null;
            }
        }
    } catch (error) {
        console.error('加载角色数据失败:', error);
        if (window.showAlert) {
            window.showAlert(window.t ? window.t('character.loadFailed') : '加载角色数据失败');
        }
    }
}

// 初始化textarea自动调整高度功能
setTimeout(() => {
    initAutoResizeTextareas();
}, 100);

// 渲染主人表单
function renderMaster() {
    const master = characterData['主人'] || {};
    const form = document.getElementById('master-form');
    // 清空原有自定义项
    Array.from(form.querySelectorAll('.custom-row')).forEach(e => e.remove());

    // 只有档案名是硬编码的必填字段（HTML模板中已定义）
    let profileInput = form.querySelector('[name="档案名"]');
    if (!profileInput) {
        // 如果档案名元素不存在（不应该发生），动态创建
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper';

        const label = document.createElement('label');
        const labelTextSpan = document.createElement('span');
        labelTextSpan.setAttribute('data-i18n', 'character.profileName');
        labelTextSpan.textContent = window.t ? window.t('character.profileName') : '档案名';
        label.appendChild(labelTextSpan);
        const requiredStar = document.createElement('span');
        requiredStar.style.color = 'red';
        requiredStar.setAttribute('data-i18n', 'character.required');
        requiredStar.textContent = (window.t && typeof window.t === 'function') ? window.t('character.required') : '*';
        label.appendChild(requiredStar);
        wrapper.appendChild(label);

        const row = document.createElement('div');
        row.className = 'field-row';
        profileInput = document.createElement('input');
        profileInput.type = 'text';
        profileInput.name = '档案名';
        profileInput.required = true;
        profileInput.maxLength = 20;
        profileInput.autocomplete = 'off';
        row.appendChild(profileInput);
        wrapper.appendChild(row);

        // 修改名称按钮
        const renameBtn = document.createElement('button');
        renameBtn.type = 'button';
        renameBtn.className = 'btn sm';
        renameBtn.id = 'rename-master-btn';
        const renameText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/edit.png" alt="" class="edit-icon"> <span data-i18n="character.rename">${window.t('character.rename')}</span>` : '<img src="/static/icons/edit.png" alt="" class="edit-icon"> 修改名称';
        renameBtn.innerHTML = renameText;
        wrapper.appendChild(renameBtn);

        const buttonArea = form.querySelector('div[style]');
        if (buttonArea) {
            form.insertBefore(wrapper, buttonArea);
        } else {
            form.appendChild(wrapper);
        }
    }

    // 设置档案名的值
    profileInput.value = master['档案名'] || '';

    // 确保档案名的修改按钮存在
    const profileWrapper = profileInput.closest('.field-row-wrapper');
    if (profileWrapper && !profileWrapper.querySelector('#rename-master-btn')) {
        const renameBtn = document.createElement('button');
        renameBtn.type = 'button';
        renameBtn.className = 'btn sm';
        renameBtn.id = 'rename-master-btn';
        const renameText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/edit.png" alt="" class="edit-icon"> <span data-i18n="character.rename">${window.t('character.rename')}</span>` : '<img src="/static/icons/edit.png" alt="" class="edit-icon"> 修改名称';
        renameBtn.innerHTML = renameText;
        profileWrapper.appendChild(renameBtn);
    }

    // 所有其他字段（性别、昵称等）完全由数据驱动
    Object.keys(master).forEach(k => {
        if (k === '档案名') return; // 档案名已在上方处理
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row';

        // 创建label元素（在wrapper中）
        const label = document.createElement('label');
        label.textContent = getFieldLabel(k);
        wrapper.appendChild(label);

        // 创建field-row（胶囊框）
        const row = document.createElement('div');
        row.className = 'field-row';

        // 创建textarea元素
        const textarea = document.createElement('textarea');
        textarea.name = k;
        textarea.value = master[k];
        textarea.rows = 1;
        textarea.placeholder = (window.t && typeof window.t === 'function') ? window.t('character.detailDescriptionPlaceholder') : '可输入详细描述';
        row.appendChild(textarea);

        // 为textarea添加自动调整高度功能
        attachTextareaAutoResize(textarea);

        // 将field-row添加到wrapper
        wrapper.appendChild(row);

        // 创建删除按钮 - 在胶囊框外面
        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'btn sm delete';
        const deleteFieldText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteField">${window.t('character.deleteField')}</span>` : '<img src="/static/icons/delete.png" alt="" class="delete-icon"> 删除设定';
        deleteBtn.innerHTML = deleteFieldText;
        deleteBtn.addEventListener('click', function () {
            deleteMasterField(this);
        });
        wrapper.appendChild(deleteBtn);
        form.insertBefore(wrapper, form.querySelector('div[style]'));
    });

    // 为新渲染的元素重新设置事件监听
    setupMasterFormListeners();

    // 初始化主人表单中的textarea自动调整高度功能
    setTimeout(() => {
        initAutoResizeTextareas();
    }, 100);
}

// 新增主人自定义设定
// 主人表单按钮显示/隐藏逻辑
function setupMasterFormListeners() {
    const masterFormEl = document.getElementById('master-form');
    if (!masterFormEl) return;

    // 获取保存和取消按钮
    const saveBtn = masterFormEl.querySelector('#save-master-btn');
    const cancelBtn = masterFormEl.querySelector('#cancel-master-btn');

    // 显示操作按钮的函数
    function showMasterActionButtons() {
        if (saveBtn) saveBtn.style.display = '';
        if (cancelBtn) cancelBtn.style.display = '';
    }

    // 为所有输入元素添加事件监听（跳过已绑定的持久元素，防止重复调用堆积）
    const inputs = masterFormEl.querySelectorAll('input, textarea');
    inputs.forEach(input => {
        if (input.dataset.masterListenersBound) return;
        input.dataset.masterListenersBound = '1';
        input.addEventListener('change', showMasterActionButtons);
        input.addEventListener('input', showMasterActionButtons);
    });

    // 为删除按钮添加点击事件监听
    const deleteButtons = masterFormEl.querySelectorAll('.btn.delete');
    deleteButtons.forEach(btn => {
        btn.addEventListener('click', showMasterActionButtons);
        btn.addEventListener('click', function () {
            deleteMasterField(this);
        });
    });

    // 为主人档案重命名按钮添加点击事件监听
    const renameBtn = masterFormEl.querySelector('#rename-master-btn');
    if (renameBtn) {
        renameBtn.onclick = async function () {
            const profileName = masterFormEl.querySelector('input[name="档案名"]').value;
            await window.renameMaster(profileName);
        };
    }

    // 为新增设定按钮添加点击事件监听
    const addBtn = masterFormEl.querySelector('#add-master-field-btn');
    if (addBtn) {
        addBtn.onclick = async function () {
            const key = await showPrompt(
                window.t ? window.t('character.addMasterFieldPrompt') : '请输入新设定的名称（键名）',
                '',
                window.t ? window.t('character.addMasterFieldTitle') : '新增主人设定'
            );
            if (!key || ["档案名"].includes(key)) return;
            if (masterFormEl.querySelector(`[name='${CSS.escape(key)}']`)) {
                await showAlert(window.t ? window.t('character.fieldExists') : '该设定已存在');
                return;
            }
            const wrapper = document.createElement('div');
            wrapper.className = 'field-row-wrapper custom-row';

            const labelEl = document.createElement('label');
            labelEl.textContent = key;
            wrapper.appendChild(labelEl);

            const row = document.createElement('div');
            row.className = 'field-row';

            const textareaEl = document.createElement('textarea');
            textareaEl.name = key;
            textareaEl.rows = 1;
            textareaEl.placeholder = '可输入详细描述';
            row.appendChild(textareaEl);

            // 将field-row添加到wrapper
            wrapper.appendChild(row);

            // 创建删除按钮 - 在胶囊框外面
            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'btn sm delete';
            // 确保使用 innerHTML 以支持图标
            const deleteFieldText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteField">${window.t('character.deleteField')}</span>` : '<img src="/static/icons/delete.png" alt="" class="delete-icon"> 删除设定';
            delBtn.innerHTML = deleteFieldText;
            delBtn.addEventListener('click', function () { deleteMasterField(this); });
            wrapper.appendChild(delBtn);

            masterFormEl.insertBefore(wrapper, masterFormEl.querySelector('div[style]'));

            // 显示操作按钮
            showMasterActionButtons();

            // 为新添加的元素添加事件监听
            const newTextarea = row.querySelector('textarea');
            newTextarea.addEventListener('change', showMasterActionButtons);
            newTextarea.addEventListener('input', showMasterActionButtons);

            // 为新增的textarea添加自动调整高度功能
            attachTextareaAutoResize(newTextarea);

            // 为删除按钮添加事件监听和点击处理
            delBtn.addEventListener('click', showMasterActionButtons);
        };
    }

    // 删除主人字段函数
    window.deleteMasterField = function (btn) {
        const wrapper = btn.parentNode; // field-row-wrapper（按钮现在直接在wrapper中）
        // 档案名不能删
        const profileNameText = window.t ? window.t('character.profileName') : '档案名';
        const label = wrapper.querySelector('label');
        if (label && label.textContent.includes(profileNameText)) return;
        wrapper.remove();
        showMasterActionButtons(); // 删除字段后显示操作按钮
    };

    // 取消按钮功能
    if (cancelBtn) {
        cancelBtn.onclick = function () {
            // 重新加载主人数据以取消修改
            renderMaster();
            // 隐藏操作按钮
            if (saveBtn) saveBtn.style.display = 'none';
            if (cancelBtn) cancelBtn.style.display = 'none';
        };
    }
}

if (!window._addMasterFieldHandler) {
    var masterFormEl = document.getElementById('master-form');
    if (masterFormEl) {
        // 添加按钮区域，初始隐藏保存和取消按钮
        // 确保使用 data-i18n 属性，以便在语言切换时自动更新
        const addFieldText = `<img src="/static/icons/add.png" alt="" class="add-icon"> <span data-i18n="character.addMasterField">${window.t ? window.t('character.addMasterField') : '新增设定'}</span>`;
        const saveMasterText = `<span data-i18n="character.saveMaster">${window.t ? window.t('character.saveMaster') : '保存主人设定'}</span>`;
        const cancelText = `<span data-i18n="character.cancel">${window.t ? window.t('character.cancel') : '取消'}</span>`;

        masterFormEl.insertAdjacentHTML('beforeend', `
            <div style="display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:8px">
                <button type="button" class="btn sm add" id="add-master-field-btn" style="min-width:120px">${addFieldText}</button>
                <button type="submit" class="btn sm" id="save-master-btn" style="display:none;min-width:120px">${saveMasterText}</button>
                <button type="button" class="btn sm" id="cancel-master-btn" style="display:none;min-width:120px">${cancelText}</button>
            </div>
        `);
    }

    // 设置事件监听
    setupMasterFormListeners();

    window._addMasterFieldHandler = true;
}

// 保存主人
const masterForm = document.getElementById('master-form');
masterForm.onsubmit = async function (e) {
    e.preventDefault();
    const data = {};
    for (const [k, v] of new FormData(masterForm).entries()) {
        if (k && v) data[k] = v;
    }
    if (!data['档案名']) {
        await showAlert(window.t ? window.t('character.profileNameRequired') : '档案名为必填项');
        return;
    }

    try {
        const response = await fetch('/api/characters/master', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            let errorMsg = '保存失败';
            try {
                const errorData = await response.json();
                errorMsg = errorData.detail || errorData.message || errorMsg;
            } catch (e) {
                // 如果不是 JSON 响应
            }
            throw new Error(errorMsg);
        }

        await loadCharacterData();

        // 只有在成功保存后才隐藏保存和取消按钮
        const saveBtn = masterForm.querySelector('#save-master-btn');
        const cancelBtn = masterForm.querySelector('#cancel-master-btn');
        if (saveBtn) saveBtn.style.display = 'none';
        if (cancelBtn) cancelBtn.style.display = 'none';
    } catch (error) {
        console.error('保存主人设定时出错:', error);
        const localizedError = window.t ? window.t('character.saveMasterError') : '保存主人设定失败';
        await showAlert(`${localizedError}: ${error.message}`);
    }
};

// 渲染猫娘列表
function renderCatgirls() {
    const list = document.getElementById('catgirl-list');
    list.innerHTML = '';
    const catgirls = characterData['猫娘'] || {};
    Object.keys(catgirls).forEach(key => {
        const cat = catgirls[key];
        const block = document.createElement('div');
        block.className = 'catgirl-block';

        // header
        const header = document.createElement('div');
        header.className = 'catgirl-header';

        const expandBtn = document.createElement('img');
        expandBtn.className = 'catgirl-expand';
        expandBtn.src = '/static/icons/dropdown_arrow.png';
        expandBtn.alt = '';
        expandBtn.style.cursor = 'pointer';
        expandBtn.style.width = '32px';
        expandBtn.style.height = '32px';
        expandBtn.style.marginRight = '6px';
        expandBtn.style.userSelect = 'none';
        expandBtn.style.transition = 'transform 0.2s';
        expandBtn.style.transform = 'rotate(-90deg)';
        header.appendChild(expandBtn);

        const titleSpan = document.createElement('span');
        titleSpan.className = 'catgirl-title';
        titleSpan.style.color = '#40C5F1';
        titleSpan.style.fontWeight = '600';
        titleSpan.style.fontSize = '1.4rem';
        titleSpan.textContent = key;
        header.appendChild(titleSpan);

        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'catgirl-actions';

        const switchBtn = document.createElement('button');
        switchBtn.className = 'btn sm';
        switchBtn.id = 'switch-btn-' + key;
        switchBtn.style.background = '#40C5F1';
        switchBtn.style.minWidth = '120px';
        // 确保使用 innerHTML 以支持图标
        const switchText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/star.png" alt="" class="star-icon"> <span data-i18n="character.switchCatgirl">${window.t('character.switchCatgirl')}</span>` : '<img src="/static/icons/star.png" alt="" class="star-icon"> 切换猫娘';
        switchBtn.innerHTML = switchText;
        switchBtn.addEventListener('click', function () { switchCatgirl(key); });
        actionsDiv.appendChild(switchBtn);

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'btn sm delete';
        deleteBtn.style.minWidth = '120px';
        // 确保使用 innerHTML 以支持图标
        const deleteText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteCatgirl">${window.t('character.deleteCatgirl')}</span>` : '<img src="/static/icons/delete.png" alt="" class="delete-icon"> 删除猫娘';
        deleteBtn.innerHTML = deleteText;
        deleteBtn.addEventListener('click', function () { deleteCatgirl(key); });
        actionsDiv.appendChild(deleteBtn);

        header.appendChild(actionsDiv);

        const detailsDiv = document.createElement('div');
        detailsDiv.className = 'catgirl-details';
        detailsDiv.style.display = 'none';

        block.appendChild(header);
        block.appendChild(detailsDiv);

        // 从 localStorage 读取展开状态
        const storageKey = `catgirl_expand_${key}`;
        const savedState = localStorage.getItem(storageKey);
        const shouldBeOpen = savedState === 'true';

        if (shouldBeOpen) {
            detailsDiv.style.display = 'block';
            expandBtn.style.transform = 'rotate(0deg)';
            expandedCatgirlName = key;
            // 延迟加载表单，确保 DOM 已渲染
            setTimeout(() => {
                showCatgirlForm(key, detailsDiv);
            }, 0);
        }

        expandBtn.onclick = function () {
            const isOpen = detailsDiv.style.display === '' || detailsDiv.style.display === 'block';
            if (isOpen) {
                detailsDiv.style.display = 'none';
                expandBtn.style.transform = 'rotate(-90deg)';
                detailsDiv.innerHTML = '';
                // 保存状态到 localStorage
                localStorage.setItem(storageKey, 'false');
                // 清除展开记录
                if (expandedCatgirlName === key) {
                    expandedCatgirlName = null;
                }
            } else {
                detailsDiv.style.display = 'block';
                expandBtn.style.transform = 'rotate(0deg)';
                // 保存状态到 localStorage
                localStorage.setItem(storageKey, 'true');
                // 记录当前展开的猫娘
                expandedCatgirlName = key;
                showCatgirlForm(key, detailsDiv);
            }
        };
        list.appendChild(block);
    });
}

// 随机颜色函数
function randomColor() {
    // 生成明亮、柔和的随机色
    const h = Math.floor(Math.random() * 360);
    const s = 60 + Math.floor(Math.random() * 25); // 60-85%
    const l = 45 + Math.floor(Math.random() * 30); // 45-75%
    return `hsl(${h},${s}%,${l}%)`;
}

// 新增猫娘
const addBtn = document.getElementById('add-catgirl-btn');
addBtn.onclick = function () {
    showCatgirlForm(null);
};

// 编辑猫娘
window.editCatgirl = function (key) {
    showCatgirlForm(key);
};

// 删除猫娘
window.deleteCatgirl = async function (key) {
    const catgirls = characterData['猫娘'] || {};
    if (Object.keys(catgirls).length <= 1) {
        await showAlert(window.t ? window.t('character.onlyOneCatgirlLeft') : '只剩一只猫娘，无法删除！');
        return;
    }
    // 检查是否是当前猫娘
    try {
        const currentResponse = await fetch('/api/characters/current_catgirl');
        const currentData = await currentResponse.json();
        const currentCatgirl = currentData.current_catgirl || '';

        if (key === currentCatgirl) {
            await showAlert(window.t ? window.t('character.cannotDeleteCurrentCatgirl') : '不能删除当前正在使用的猫娘！\n\n请先切换到其他猫娘后再删除。');
            return;
        }
    } catch (error) {
        console.error('获取当前猫娘失败:', error);
        await showAlert(window.t ? window.t('character.cannotConfirmCatgirlStatus') : '无法确认当前猫娘状态，删除操作已取消');
        return;
    }

    // 确保角色名称正确显示
    // 确保角色名称正确显示，如果翻译函数返回包含 {name} 占位符，则使用默认消息
    let confirmMsg;
    if (window.t) {
        const translated = window.t('character.confirmDeleteCatgirl', { name: key });
        // 如果翻译结果包含未替换的占位符，使用默认消息
        if (translated && translated.includes('{name}')) {
            confirmMsg = `确定要删除猫娘"${key}"？`;
        } else {
            confirmMsg = translated || `确定要删除猫娘"${key}"？`;
        }
    } else {
        confirmMsg = `确定要删除猫娘"${key}"？`;
    }
    const confirmTitle = window.t ? window.t('character.deleteCatgirlTitle') : '删除猫娘';
    if (!await showConfirm(confirmMsg, confirmTitle, { danger: true })) return;
    await fetch('/api/characters/catgirl/' + encodeURIComponent(key), { method: 'DELETE' });
    // 清除 localStorage 中的展开状态记录
    localStorage.removeItem(`catgirl_expand_${key}`);
    // 清除进阶设定折叠状态记录
    localStorage.removeItem(`catgirl_advanced_${key}`);
    await loadCharacterData();
};

// 显示保存和取消按钮的全局函数
function showActionButtons(form) {
    if (!form) return;
    // 使用表单内的查询选择器找到对应的按钮
    const saveBtn = form.querySelector('#save-button');
    const cancelBtn = form.querySelector('#cancel-button');
    if (saveBtn) saveBtn.style.display = '';
    if (cancelBtn) cancelBtn.style.display = '';
}

// 显示猫娘编辑/新增表单
function showCatgirlForm(key, container) {
    let cat = key ? characterData['猫娘'][key] : {};
    let isNew = !key;
    let form = document.createElement('form');
    form.id = key ? 'catgirl-form-' + key : 'catgirl-form-new';

    // 新增猫娘时，为表单添加内边距，使其与已建立猫娘的样式一致
    if (isNew && !container) {
        form.style.padding = '16px 20px';
    }

    // 保存猫娘名称，用于后续恢复进阶设定的展开状态
    form._catgirlName = key;
    // 先渲染基础项（使用 DOM API，避免插入未转义内容）
    const baseWrapper = document.createElement('div');
    baseWrapper.className = 'field-row-wrapper';

    const baseLabel = document.createElement('label');
    const profileNameText = (window.t && typeof window.t === 'function') ? window.t('character.profileName') : '档案名';
    const requiredText = (window.t && typeof window.t === 'function') ? window.t('character.required') : '*';
    baseLabel.innerHTML = `<span data-i18n="character.profileName">${profileNameText}</span><span style="color:red" data-i18n="character.required">${requiredText}</span>`;
    baseWrapper.appendChild(baseLabel);

    const fieldRow = document.createElement('div');
    fieldRow.className = 'field-row';

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.name = '档案名';
    nameInput.required = true;
    nameInput.value = key || '';
    if (!isNew) nameInput.readOnly = true;
    attachProfileNameLimiter(nameInput);
    fieldRow.appendChild(nameInput);

    baseWrapper.appendChild(fieldRow);

    if (!isNew) {
        const renameBtn = document.createElement('button');
        renameBtn.type = 'button';
        renameBtn.className = 'btn sm';
        renameBtn.id = 'rename-catgirl-btn';
        renameBtn.style.marginLeft = '8px';
        renameBtn.style.minWidth = '120px';
        // 确保使用 innerHTML 以支持图标
        const renameText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/edit.png" alt="" class="edit-icon"> <span data-i18n="character.rename">${window.t('character.rename')}</span>` : '<img src="/static/icons/edit.png" alt="" class="edit-icon"> 修改名称';
        renameBtn.innerHTML = renameText;
        baseWrapper.appendChild(renameBtn);
    }
    form.appendChild(baseWrapper);
    // 渲染自定义项：保留字段统一由后端下发并在前端隐藏
    const ALL_RESERVED_FIELDS = ['档案名', ...getAllReservedFields()];

    Object.keys(cat).forEach(k => {
        if (!ALL_RESERVED_FIELDS.includes(k)) {
            const wrapper = document.createElement('div');
            wrapper.className = 'field-row-wrapper custom-row';
            // 确保使用 innerHTML 以支持图标
            const deleteFieldText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteField">${window.t('character.deleteField')}</span>` : '<img src="/static/icons/delete.png" alt="" class="delete-icon"> 删除设定';

            const labelEl = document.createElement('label');
            labelEl.textContent = getFieldLabel(k);
            wrapper.appendChild(labelEl);

            const fieldRow = document.createElement('div');
            fieldRow.className = 'field-row';
            const textareaEl = document.createElement('textarea');
            textareaEl.name = k;
            textareaEl.rows = 1;
            textareaEl.placeholder = '可输入详细描述';
            textareaEl.value = cat[k];
            fieldRow.appendChild(textareaEl);

            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'btn sm delete';
            delBtn.innerHTML = deleteFieldText;
            delBtn.addEventListener('click', function () { deleteCatgirlField(this); });
            fieldRow.appendChild(delBtn);

            wrapper.appendChild(fieldRow);
            form.appendChild(wrapper);

            // 为渲染的textarea添加自动调整高度功能
            attachTextareaAutoResize(textareaEl);
        }
    });
    // 渲染进阶设定
    // 进阶设定（使用 DOM API）
    const fold = document.createElement('div');
    fold.className = 'fold';

    const foldToggle = document.createElement('div');
    foldToggle.className = 'fold-toggle';
    const arrowSpan = document.createElement('img');
    arrowSpan.className = 'arrow';
    arrowSpan.src = '/static/icons/dropdown_arrow.png';
    arrowSpan.alt = '';
    arrowSpan.style.width = '32px';
    arrowSpan.style.height = '32px';
    arrowSpan.style.verticalAlign = 'middle';
    arrowSpan.style.transition = 'transform 0.2s';
    arrowSpan.style.transform = 'rotate(-90deg)';
    foldToggle.appendChild(arrowSpan);
    foldToggle.appendChild(document.createTextNode(' '));
    const toggleText = document.createTextNode(window.t ? window.t('character.advancedSettings') : '进阶设定');
    foldToggle.appendChild(toggleText);
    fold.appendChild(foldToggle);

    const foldContent = document.createElement('div');
    foldContent.className = 'fold-content';

    // 模型设定 row（支持Live2D和VRM）- 不需要胶囊框
    const modelWrapper = document.createElement('div');
    modelWrapper.className = 'field-row-wrapper';
    const modelLabel = document.createElement('label');
    modelLabel.textContent = window.t ? window.t('character.modelSettings') : '模型设定';
    modelLabel.style.fontSize = '1rem';
    modelWrapper.appendChild(modelLabel);

    const modelLink = document.createElement('span');
    modelLink.className = 'live2d-link'; // 保持class名以兼容现有样式
    modelLink.title = window.t ? window.t('character.manageModel') : '点击管理模型';
    modelLink.style.color = '#40C5F1';
    modelLink.style.cursor = 'pointer';
    modelLink.style.textDecoration = 'underline';
    modelLink.style.display = 'flex';
    modelLink.style.alignItems = 'center';

    // 显示当前模型（优先显示VRM，如果没有则显示Live2D）
    const modelType = cat['model_type'] || 'live2d';
    let modelDisplayText = '';
    if (modelType === 'vrm' && cat['vrm']) {
        const vrmPath = cat['vrm'];
        const vrmName = vrmPath ? (vrmPath.split(/[\\/]/).pop() || vrmPath) : '';
        modelDisplayText = `VRM: ${vrmName}`;
    } else if (cat['live2d']) {
        modelDisplayText = cat['live2d'];
    } else {
        modelDisplayText = window.t ? window.t('character.modelNotSet') : '未设置';
    }

    modelLink.textContent = modelDisplayText;
    modelWrapper.appendChild(modelLink);
    foldContent.appendChild(modelWrapper);
    // voice_id row
    const voiceWrapper = document.createElement('div');
    voiceWrapper.className = 'field-row-wrapper';
    const voiceLabel = document.createElement('label');
    voiceLabel.textContent = window.t ? window.t('character.voiceSetting') : '音色设定';
    voiceLabel.style.fontSize = '1rem';
    voiceWrapper.appendChild(voiceLabel);

    const voiceRow = document.createElement('div');
    voiceRow.className = 'field-row';
    voiceRow.style.overflow = 'visible';
    voiceRow.style.position = 'relative';
    voiceRow.style.alignItems = 'center';
    voiceRow.style.flex = '0 0 auto';
    voiceRow.style.width = 'auto';
    voiceRow.style.minWidth = '200px';
    voiceRow.style.maxWidth = '300px';
    const voiceSelect = document.createElement('select');
    voiceSelect.name = 'voice_id';
    voiceSelect.className = 'form-control';
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
    voiceWrapper.appendChild(voiceRow);

    const registerVoiceBtn = document.createElement('button');
    registerVoiceBtn.type = 'button';
    registerVoiceBtn.className = 'btn sm';
    registerVoiceBtn.style.marginLeft = '8px';
    registerVoiceBtn.style.minWidth = '120px';
    // 确保使用 innerHTML 以支持图标
    const registerVoiceText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/sound.png" alt="" class="sound-icon"> <span data-i18n="character.registerNewVoice">${window.t('character.registerNewVoice')}</span>` : '<img src="/static/icons/sound.png" alt="" class="sound-icon"> 注册新声音';
    registerVoiceBtn.innerHTML = registerVoiceText;
    registerVoiceBtn.addEventListener('click', async function () {
        const catgirlName = form.querySelector('[name="档案名"]').value;
        if (!catgirlName) {
            await showAlert(window.t ? window.t('character.fillProfileNameFirstForVoice') : '请先填写猫娘档案名，然后再注册音色');
            return;
        }

        // 如果是新建角色，检查角色是否已经创建
        if (isNew) {
            if (!characterData['猫娘'] || !characterData['猫娘'][catgirlName]) {
                await showAlert(window.t ? window.t('character.createCharacterFirstForVoice') : '请先点击"确认新猫娘"按钮创建角色，然后再注册音色');
                return;
            }
        }

        openVoiceClone(catgirlName);
    });
    voiceWrapper.appendChild(registerVoiceBtn);
    foldContent.appendChild(voiceWrapper);

    fold.appendChild(foldContent);

    // Add Field 按钮区 - 放在 Advanced Settings 之前
    const addFieldArea = document.createElement('div');
    addFieldArea.className = 'btn-area add-field-area';
    addFieldArea.style.display = 'flex';
    addFieldArea.style.alignItems = 'center';
    addFieldArea.style.marginTop = '10px';
    addFieldArea.style.marginBottom = '10px';
    addFieldArea.style.gap = '12px';

    // 添加一个占位符，宽度和 label 一致 (80px)
    const addFieldLabelPlaceholder = document.createElement('div');
    addFieldLabelPlaceholder.style.minWidth = '80px';
    addFieldLabelPlaceholder.style.flexShrink = '0';
    addFieldArea.appendChild(addFieldLabelPlaceholder);

    // 添加一个 flex 容器来占满剩余空间，让按钮靠右
    const addFieldSpacer = document.createElement('div');
    addFieldSpacer.style.flex = '1';
    addFieldArea.appendChild(addFieldSpacer);

    const addFieldBtn = document.createElement('button');
    addFieldBtn.type = 'button';
    addFieldBtn.className = 'btn sm add';
    addFieldBtn.id = 'add-catgirl-field-btn';
    addFieldBtn.style.minWidth = '120px';
    // 确保使用 innerHTML 以支持图标
    const addFieldText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/add.png" alt="" class="add-icon"> <span data-i18n="character.addField">${window.t('character.addField')}</span>` : '<img src="/static/icons/add.png" alt="" class="add-icon"> 新增设定';
    addFieldBtn.innerHTML = addFieldText;
    addFieldArea.appendChild(addFieldBtn);

    form.appendChild(addFieldArea);
    form.appendChild(fold);

    // 操作按钮区（保存和取消）- 放在 Advanced Settings 之后
    const btnArea = document.createElement('div');
    btnArea.className = 'btn-area';
    btnArea.style.display = 'flex';
    btnArea.style.alignItems = 'center';
    btnArea.style.marginTop = '10px';
    btnArea.style.gap = '12px';

    // 添加一个占位符，宽度和 label 一致 (80px)
    const labelPlaceholder = document.createElement('div');
    labelPlaceholder.style.minWidth = '80px';
    labelPlaceholder.style.flexShrink = '0';
    btnArea.appendChild(labelPlaceholder);

    // 添加一个 flex 容器来占满剩余空间，让按钮靠右
    const spacer = document.createElement('div');
    spacer.style.flex = '1';
    btnArea.appendChild(spacer);

    const saveButton = document.createElement('button');
    saveButton.type = 'submit';
    saveButton.className = 'btn sm';
    saveButton.id = 'save-button';
    saveButton.style.display = 'none';
    saveButton.style.minWidth = '120px';
    saveButton.textContent = isNew ? (window.t ? window.t('character.confirmNewCatgirl') : '确认新猫娘') : (window.t ? window.t('character.saveChanges') : '保存修改');
    btnArea.appendChild(saveButton);

    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.className = 'btn sm';
    cancelButton.id = 'cancel-button';
    cancelButton.style.display = 'none';
    cancelButton.style.minWidth = '120px';
    cancelButton.textContent = window.t ? window.t('character.cancel') : '取消';
    cancelButton.addEventListener('click', function () {
        if (form.querySelector('#save-button')) form.querySelector('#save-button').style.display = 'none';
        if (form.querySelector('#cancel-button')) form.querySelector('#cancel-button').style.display = 'none';
        loadCharacterData();
    });
    btnArea.appendChild(cancelButton);

    form.appendChild(btnArea);
    // 模型设定弹窗逻辑
    const modelLinkEl = form.querySelector('.live2d-link');
    if (modelLinkEl) {
        modelLinkEl.onclick = async function () {
            const catgirlName = form.querySelector('[name="档案名"]').value;
            if (!catgirlName) {
                await showAlert(window.t ? window.t('character.fillProfileNameFirst') : '请先填写猫娘档案名，然后再设置模型');
                return;
            }

            // 如果是新建角色，检查角色是否已经创建
            if (isNew) {
                if (!characterData['猫娘'] || !characterData['猫娘'][catgirlName]) {
                    await showAlert(window.t ? window.t('character.createCharacterFirstForModel') : '请先点击"确认新猫娘"按钮保存角色，然后再设置模型');
                    return;
                }
            }

            const url = `/model_manager?lanlan_name=${encodeURIComponent(catgirlName)}`;

            // 检查是否已有该URL的窗口打开
            if (!window._openSettingsWindows) {
                window._openSettingsWindows = {};
            }

            if (window._openSettingsWindows[url]) {
                const existingWindow = window._openSettingsWindows[url];
                // 检查窗口是否仍然打开
                if (existingWindow && !existingWindow.closed) {
                    // 聚焦到已存在的窗口
                    existingWindow.focus();
                    return;
                } else {
                    // 窗口已关闭，清除引用
                    delete window._openSettingsWindows[url];
                }
            }

            const popup = window.open(
                url,
                '_blank',
                'toolbar=no,location=no,status=no,menubar=no,scrollbars=yes,resizable=yes,width=' + screen.availWidth + ',height=' + screen.availHeight + ',top=0,left=0'
            );
            if (!popup) {
                await showAlert(window.t ? window.t('character.allowPopups') : '请允许弹窗！');
                return;
            }

            // 保存窗口引用
            window._openSettingsWindows[url] = popup;

            popup.moveTo(0, 0);
            popup.resizeTo(screen.availWidth, screen.availHeight);
            const timer = setInterval(() => {
                if (popup.closed) {
                    clearInterval(timer);
                    // 清除窗口引用
                    if (window._openSettingsWindows[url] === popup) {
                        delete window._openSettingsWindows[url];
                    }
                    loadCharacterData();
                }
            }, 500);
        };
    }
    // 表单特定的showActionButtons封装
    const formShowActionButtons = function () {
        showActionButtons(form);
    };

    // 新猫娘始终显示确认按钮
    if (isNew) {
        setTimeout(() => {
            formShowActionButtons();
        }, 0);
    }

    // 监听表单元素变化
    function setupChangeListeners() {
        // 为所有输入元素添加change事件监听
        const inputs = form.querySelectorAll('input, select, textarea');
        inputs.forEach(input => {
            input.addEventListener('change', formShowActionButtons);
            // 对于textarea和文本输入，也监听input事件以获得实时响应
            if (input.type === 'text' || input.tagName === 'TEXTAREA') {
                input.addEventListener('input', formShowActionButtons);
            }
        });

        // 为删除按钮添加点击事件监听
        const deleteButtons = form.querySelectorAll('.btn.delete');
        deleteButtons.forEach(btn => {
            btn.addEventListener('click', formShowActionButtons);
        });
    }

    // 调用函数绑定事件监听器
    setupChangeListeners();

    // 新增自定义项按钮
    form.querySelector('#add-catgirl-field-btn').onclick = async function () {
        const key = await showPrompt(
            window.t ? window.t('character.addCatgirlFieldPrompt') : '请输入新设定的名称（键名）',
            '',
            window.t ? window.t('character.addCatgirlFieldTitle') : '新增猫娘设定'
        );
        // 保留字段（由后端统一管理）不允许用户手动添加
        const FORBIDDEN_FIELD_NAMES = ["档案名", ...getAllReservedFields()];
        if (!key || FORBIDDEN_FIELD_NAMES.includes(key)) return;
        if (form.querySelector(`[name='${CSS.escape(key)}']`)) {
            await showAlert(window.t ? window.t('character.fieldExists') : '该设定已存在');
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row';
        const deleteFieldText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteField">${window.t('character.deleteField')}</span>` : '<img src="/static/icons/delete.png" alt="" class="delete-icon"> 删除设定';

        const labelEl = document.createElement('label');
        labelEl.textContent = key;
        wrapper.appendChild(labelEl);

        const fieldRow = document.createElement('div');
        fieldRow.className = 'field-row';
        const textareaEl = document.createElement('textarea');
        textareaEl.name = key;
        textareaEl.rows = 1;
        textareaEl.placeholder = '可输入详细描述';
        fieldRow.appendChild(textareaEl);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn sm delete';
        delBtn.innerHTML = deleteFieldText;
        delBtn.addEventListener('click', function () { deleteCatgirlField(this); });
        fieldRow.appendChild(delBtn);

        wrapper.appendChild(fieldRow);
        form.insertBefore(wrapper, form.querySelector('.add-field-area'));

        // 新增字段后显示操作按钮
        formShowActionButtons();

        // 为新添加的输入元素添加事件监听
        const newTextarea = fieldRow.querySelector('textarea');
        newTextarea.addEventListener('change', formShowActionButtons);
        newTextarea.addEventListener('input', formShowActionButtons);

        // 为新增的textarea添加自动调整高度功能
        attachTextareaAutoResize(newTextarea);

        const newDeleteBtn = fieldRow.querySelector('button');
        newDeleteBtn.addEventListener('click', formShowActionButtons);
    };

    // 设置删除字段的全局函数
    window.deleteCatgirlField = function (btn) {
        const wrapper = btn.closest('.field-row-wrapper');
        if (wrapper) {
            const form = wrapper.closest('form');
            wrapper.remove();
            if (form) showActionButtons(form); // 删除字段后显示操作按钮
        }
    };

    // 在 form.onsubmit 之前添加
    async function loadVoices() {
        try {
            const response = await fetch('/api/characters/voices');
            const data = await response.json();
            const select = form.querySelector('select[name="voice_id"]');
            if (select && data && data.voices) {
                // 清空现有选项并使用 DOM API 创建
                while (select.firstChild) select.removeChild(select.firstChild);
                const voiceNotSetText = window.t ? window.t('character.voiceNotSet') : '未指定音色';
                const defaultOption = document.createElement('option');
                defaultOption.value = '';
                defaultOption.textContent = voiceNotSetText;
                select.appendChild(defaultOption);
                // 添加音色选项
                const voiceOwners = data.voice_owners || {};
                Object.entries(data.voices).forEach(([voiceId, voiceData]) => {
                    const option = document.createElement('option');
                    option.value = voiceId;
                    // 显示优先级：使用该音色的角色名 > prefix > 截断的 voice_id
                    const owners = voiceOwners[voiceId];
                    let displayName = '';
                    if (owners && owners.length > 0) {
                        displayName = owners.join(', ');
                    } else if (voiceData.prefix) {
                        displayName = voiceData.prefix;
                    }
                    const shortId = voiceId.length > 20 ? voiceId.substring(0, 18) + '…' : voiceId;
                    option.textContent = displayName ? displayName + ' (' + shortId + ')' : voiceId;
                    option.title = voiceId;
                    if (voiceId === String(cat['voice_id'] || '').trim()) option.selected = true;
                    select.appendChild(option);
                });
                // 添加免费预设音色（不可移除，放在最后）
                if (data.free_voices && Object.keys(data.free_voices).length > 0) {
                    const freeGroup = document.createElement('optgroup');
                    const freeLabel = window.t ? window.t('character.freePresetVoices') : '免费预设音色';
                    freeGroup.label = '── ' + freeLabel + ' ──';
                    Object.entries(data.free_voices).forEach(([displayName, voiceId]) => {
                        const option = document.createElement('option');
                        option.value = voiceId;
                        option.textContent = displayName;
                        if (voiceId === String(cat['voice_id'] || '').trim()) option.selected = true;
                        freeGroup.appendChild(option);
                    });
                    select.appendChild(freeGroup);
                }
            }
            // 加载 GPT-SoVITS 声音列表（等待完成以避免表单提交时丢失 gsv: 音色）
            await loadGsvVoices(select, String(cat['voice_id'] || '').trim());
        } catch (error) {
            console.error('加载音色列表失败:', error);
        }
    }

    // 加载 GPT-SoVITS 声音列表并追加到 select
    const GSV_PREFIX = 'gsv:';
    async function loadGsvVoices(select, currentVoiceId) {
        if (!select) return;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);

        const ensureGsvFallback = () => {
            if (!currentVoiceId || !currentVoiceId.startsWith(GSV_PREFIX)) return;
            if (select.querySelector('option[value="' + CSS.escape(currentVoiceId) + '"]')) {
                select.value = currentVoiceId;
                return;
            }

            let gsvGroup = select.querySelector('optgroup[data-gsv-group="true"]');
            if (!gsvGroup) {
                gsvGroup = document.createElement('optgroup');
                const gsvLabel = window.t ? window.t('character.gptsovitsVoices') : 'GPT-SoVITS 声音';
                gsvGroup.label = '── ' + gsvLabel + ' ──';
                gsvGroup.dataset.gsvGroup = 'true';
                select.appendChild(gsvGroup);
            }

            const fallbackOpt = document.createElement('option');
            fallbackOpt.value = currentVoiceId;
            fallbackOpt.textContent = currentVoiceId.substring(GSV_PREFIX.length) + ' (?)';
            gsvGroup.appendChild(fallbackOpt);
            select.value = currentVoiceId;
        };

        try {
            const resp = await fetch('/api/characters/custom_tts_voices', { signal: controller.signal });
            clearTimeout(timeoutId);
            const result = await resp.json();
            if (result.success && Array.isArray(result.voices) && result.voices.length > 0) {
                const gsvGroup = document.createElement('optgroup');
                const gsvLabel = window.t ? window.t('character.gptsovitsVoices') : 'GPT-SoVITS 声音';
                gsvGroup.label = '── ' + gsvLabel + ' ──';
                gsvGroup.dataset.gsvGroup = 'true';
                result.voices.forEach(v => {
                    const option = document.createElement('option');
                    option.value = v.voice_id;
                    option.textContent = v.name + (v.version ? ' (' + v.version + ')' : '');
                    if (v.description) option.title = v.description;
                    if (v.voice_id === currentVoiceId) option.selected = true;
                    gsvGroup.appendChild(option);
                });
                select.appendChild(gsvGroup);
                // 如果当前 voice_id 是 gsv: 前缀但不在已有选项中，手动添加
                if (currentVoiceId && currentVoiceId.startsWith(GSV_PREFIX) && !select.querySelector('option[value="' + CSS.escape(currentVoiceId) + '"]')) {
                    const fallbackOpt = document.createElement('option');
                    fallbackOpt.value = currentVoiceId;
                    fallbackOpt.textContent = currentVoiceId.substring(GSV_PREFIX.length) + ' (?)';
                    gsvGroup.appendChild(fallbackOpt);
                }
                // 确保 select.value 与 currentVoiceId 一致（可靠地取消默认选项）
                if (currentVoiceId && currentVoiceId.startsWith(GSV_PREFIX)) {
                    select.value = currentVoiceId;
                }
            }
            ensureGsvFallback();
        } catch (e) {
            clearTimeout(timeoutId);
            // GPT-SoVITS 不可用时静默忽略
            console.debug('GPT-SoVITS voices not available:', e.message);
            ensureGsvFallback();
        }
    }

    // 立即调用加载音色，并在提交前等待初始化完成
    const voicesLoadPromise = loadVoices();





    form.onsubmit = async function (e) {
        e.preventDefault();
        // 防止重复提交
        if (form.dataset.submitting === 'true') {
            console.log('表单正在提交中，忽略重复提交');
            return;
        }
        form.dataset.submitting = 'true';

        try {
            await voicesLoadPromise;
            const fd = new FormData(form);
            const data = {};
            const selectedVoiceId = (form.querySelector('select[name="voice_id"]')?.value ?? '').trim();
            const previousVoiceId = String(cat['voice_id'] || '').trim();
            for (const [k, v] of fd.entries()) {
                // 保留字段统一由专用接口维护，通用角色保存接口不再透传
                if (k === 'voice_id') {
                    continue;
                }
                const normalizedValue = typeof v === 'string' ? v.trim() : v;
                if (k && normalizedValue) {
                    data[k] = normalizedValue;
                }
            }
            if (!data['档案名']) {
                await showAlert(window.t ? window.t('character.profileNameRequired') : '档案名为必填项');
                return;
            }

            // 验证Live2D模型文件存在性
            if (data['live2d'] && data['live2d'].trim() !== '') {
                try {
                    const response = await fetch(`/api/characters/catgirl/l2d/${encodeURIComponent(data['档案名'])}`);
                    const modelInfo = await response.json();

                    if (!modelInfo.valid) {
                        const confirmFallback = await showConfirm(
                            window.t ? window.t('character.live2dModelError', { name: data['档案名'], path: data['live2d'] }) : `猫娘"${data['档案名']}"的Live2D模型文件不存在或已损坏。\n\n当前模型路径: ${data['live2d']}\n\n是否回退到默认模型(mao_pro)？\n\n点击"确定"使用默认模型，点击"取消"保持当前设置。`,
                            window.t ? window.t('character.live2dModelErrorTitle') : 'Live2D模型异常'
                        );

                        if (confirmFallback) {
                            // 回退到默认模型
                            const fallbackResponse = await fetch(`/api/characters/catgirl/l2d/${encodeURIComponent(data['档案名'])}`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ fallback_to_default: true })
                            });
                            const fallbackResult = await fallbackResponse.json();

                            if (fallbackResult.success) {
                                await showAlert(window.t ? window.t('character.fallbackToDefaultModel', { path: fallbackResult.model_path }) : `已回退到默认模型: ${fallbackResult.model_path}`);
                                // 更新表单中的live2d字段
                                data['live2d'] = fallbackResult.model_path;
                            } else {
                                await showAlert(window.t ? window.t('character.fallbackFailed') : '回退到默认模型失败，请检查模型设置');
                            }
                        } else {
                            // 用户选择保持当前设置，继续保存
                            console.warn(`用户选择保持无效的Live2D模型设置: ${data['live2d']}`);
                        }
                    }
                } catch (error) {
                    console.error('验证Live2D模型失败:', error);
                    const continueSave = await showConfirm(
                        window.t ? window.t('character.cannotVerifyLive2d') : `无法验证Live2D模型文件存在性。\n\n是否继续保存？\n\n点击"确定"继续保存，点击"取消"中止保存。`,
                        window.t ? window.t('character.verificationFailed') : '验证失败'
                    );
                    if (!continueSave) {
                        return;
                    }
                }
            }

            console.log('提交数据:', data);
            const response = await fetch('/api/characters/catgirl' + (isNew ? '' : '/' + encodeURIComponent(key)), {
                method: isNew ? 'POST' : 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                const errorText = await response.text();
                console.error('API请求失败:', response.status, errorText);

                // 尝试解析JSON错误消息
                let errorMessage = errorText;
                try {
                    const errorJson = JSON.parse(errorText);
                    if (errorJson.error) {
                        errorMessage = errorJson.error;
                    }
                } catch (e) {
                    // 如果不是JSON格式，保持原错误文本
                }

                await showAlert(window.t ? window.t('character.saveFailedWithError', { error: errorMessage }) : '保存失败: ' + errorMessage);
                return;
            }

            const result = await response.json();
            console.log('保存结果:', result);

            if (result.success === false) {
                await showAlert(result.error || (window.t ? window.t('character.saveFailed') : '保存失败'));
                return;
            }

            // voice_id 通过专用接口更新，避免走通用角色编辑接口
            if (selectedVoiceId !== previousVoiceId) {
                if (selectedVoiceId) {
                    try {
                        const voiceResp = await fetch(`/api/characters/catgirl/voice_id/${encodeURIComponent(data['档案名'])}`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ voice_id: selectedVoiceId })
                        });
                        const voiceResult = await voiceResp.json().catch(() => ({}));
                        if (!voiceResp.ok || voiceResult.success === false) {
                            const detail = (voiceResult && voiceResult.error) || `${voiceResp.status} ${voiceResp.statusText}`;
                            await showAlert(
                                window.t
                                    ? window.t('character.partialSaveVoiceFailed', { error: detail })
                                    : `角色已保存，但音色更新失败: ${detail}`
                            );
                        }
                    } catch (voiceErr) {
                        await showAlert(
                            window.t
                                ? window.t('character.partialSaveVoiceFailed', { error: voiceErr.message || String(voiceErr) })
                                : `角色已保存，但音色更新失败: ${voiceErr.message || String(voiceErr)}`
                        );
                    }
                } else if (previousVoiceId) {
                    try {
                        const clearResp = await fetch(`/api/characters/catgirl/${encodeURIComponent(data['档案名'])}/unregister_voice`, {
                            method: 'POST'
                        });
                        const clearResult = await clearResp.json().catch(() => ({}));
                        if (!clearResp.ok || clearResult.success === false) {
                            const detail = (clearResult && clearResult.error) || `${clearResp.status} ${clearResp.statusText}`;
                            await showAlert(
                                window.t
                                    ? window.t('character.partialSaveVoiceFailed', { error: detail })
                                    : `角色已保存，但音色更新失败: ${detail}`
                            );
                        }
                    } catch (clearErr) {
                        await showAlert(
                            window.t
                                ? window.t('character.partialSaveVoiceFailed', { error: clearErr.message || String(clearErr) })
                                : `角色已保存，但音色更新失败: ${clearErr.message || String(clearErr)}`
                        );
                    }
                }
            }

            // 保存当前展开的猫娘名称，以便重新加载后自动展开
            let formCatgirlName = data['档案名'];
            if (formCatgirlName) {
                // 验证并清理猫娘名称，避免特殊字符
                formCatgirlName = formCatgirlName.trim();
                if (formCatgirlName) {
                    expandedCatgirlName = formCatgirlName;
                }
            }

            // 在重新加载数据前，隐藏当前表单的按钮
            form.querySelector('#save-button').style.display = 'none';
            form.querySelector('#cancel-button').style.display = 'none';

            await loadCharacterData();
        } catch (error) {
            console.error('保存出错:', error);
            await showAlert(window.t ? window.t('character.saveError', { error: error.message }) : '保存时发生错误: ' + error.message);
        } finally {
            form.dataset.submitting = 'false';
        }
    };
    // 绑定"修改名称"按钮事件
    if (!isNew) {
        const renameBtn = form.querySelector('#rename-catgirl-btn');
        if (renameBtn) {
            renameBtn.addEventListener('click', async function () {
                await window.renameCatgirl(key);
            });
        }
    }
    // 渲染到指定容器
    if (container) {
        container.innerHTML = '';
        container.appendChild(form);

        // 如果是编辑现有猫娘，尝试恢复进阶设定的展开状态
        if (key) {
            setTimeout(() => {
                const savedState = localStorage.getItem(`catgirl_advanced_${key}`);
                if (savedState === 'true') {
                    const advancedSettingsFold = form.querySelector('.fold');
                    const toggle = advancedSettingsFold.querySelector('.fold-toggle');
                    if (advancedSettingsFold && toggle) {
                        advancedSettingsFold.classList.add('open');
                        const arrow = toggle.querySelector('.arrow');
                        if (arrow) arrow.style.transform = 'rotate(0deg)';
                    }
                }
            }, 0);
        }
    } else {
        // 兼容原有逻辑
        const list = document.getElementById('catgirl-list');
        // 先移除所有表单
        Array.from(list.querySelectorAll('form')).forEach(f => f.style.display = 'none');
        // 找到或新建表单
        if (key) {
            let oldForm = document.getElementById('catgirl-form-' + key);
            if (oldForm) oldForm.style.display = '';
        } else {
            // 检查是否已经存在"新增猫娘"的表单，如果存在，先移除旧的容器，防止出现空白条
            const existingNewForm = document.getElementById('catgirl-form-new');
            if (existingNewForm) {
                const existingBlock = existingNewForm.closest('.catgirl-block');
                if (existingBlock) {
                    existingBlock.remove();
                }
            }
            // 新增猫娘时，创建一个新的表单块
            const block = document.createElement('div');
            block.className = 'catgirl-block';
            block.appendChild(form);
            list.prepend(block);
        }
    }

    // 初始化猫娘表单中的textarea自动调整高度功能
    setTimeout(() => {
        initAutoResizeTextareas();
    }, 100);
}
// deleteCatgirlField函数已在showCatgirlForm内部定义为全局函数

// 主人档案名重命名
window.renameMaster = async function (oldName) {
    let _renameMasterDidOverLimit = false;
    const newName = await showPrompt(
        window.t ? window.t('character.enterNewProfileName') : '请输入新的主人档案名',
        oldName,
        window.t ? window.t('character.renameMasterTitle') : '重命名主人',
        {
            inputAttributes: {
                maxlength: PROFILE_NAME_MAX_UNITS,
                autocomplete: 'off'
            },
            normalize: (v) => {
                const trimmed = String(v ?? '').trim();
                _renameMasterDidOverLimit = profileNameCountUnits(trimmed) > PROFILE_NAME_MAX_UNITS;
                return profileNameTrimToMaxUnits(trimmed, PROFILE_NAME_MAX_UNITS);
            },
            validator: (v) => {
                const trimmed = String(v ?? '').trim();
                if (!trimmed) return tOrFallback(NEW_PROFILE_NAME_REQUIRED_KEY, '新档案名不能为空');
                if (profileNameCountUnits(trimmed) > PROFILE_NAME_MAX_UNITS) {
                    return tOrFallback(PROFILE_NAME_TOO_LONG_KEY, '档案名过长');
                }
                return '';
            },
            onInput: (inputEl) => {
                if (_renameMasterDidOverLimit) {
                    _renameMasterDidOverLimit = false;
                    flashProfileNameTooLong(inputEl);
                }
            }
        }
    );
    if (!newName || newName === oldName) return;
    if (characterData['主人'][newName]) {
        await showAlert(window.t ? window.t('character.profileNameExists') : '该档案名已存在');
        return;
    }
    // 调用API重命名
    const res = await fetch('/api/characters/master/' + encodeURIComponent(oldName) + '/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName })
    });
    const result = await res.json();
    if (result.success) {
        await loadCharacterData();
        await showAlert(window.t ? window.t('character.renameSuccess') : '重命名成功');
    } else {
        await showAlert(window.t ? window.t('character.renameError', { error: result.message }) : '重命名失败: ' + (result.message || '未知错误'));
    }
}

// 猫娘档案名重命名
window.renameCatgirl = async function (oldName) {
    // 先检查是否是当前角色且在语音模式下
    try {
        const statusRes = await fetch('/api/characters/catgirl/' + encodeURIComponent(oldName) + '/voice_mode_status');
        const statusData = await statusRes.json();

        if (statusData.is_current && statusData.is_voice_mode) {
            await showAlert(window.t ? window.t('character.cannotRenameInVoiceMode') : '语音状态下无法修改角色名称，请先停止语音对话后再修改');
            return;
        }
    } catch (error) {
        console.warn('检查语音模式状态失败:', error);
        // 如果检查失败，继续执行，让后端来处理
    }

    let _renameCatgirlDidOverLimit = false;
    const newName = await showPrompt(
        window.t ? window.t('character.enterNewProfileName') : '请输入新的猫娘档案名',
        oldName,
        window.t ? window.t('character.renameCatgirlTitle') : '重命名猫娘',
        {
            inputAttributes: {
                maxlength: PROFILE_NAME_MAX_UNITS,
                autocomplete: 'off'
            },
            normalize: (v) => {
                const trimmed = String(v ?? '').trim();
                _renameCatgirlDidOverLimit = profileNameCountUnits(trimmed) > PROFILE_NAME_MAX_UNITS;
                return profileNameTrimToMaxUnits(trimmed, PROFILE_NAME_MAX_UNITS);
            },
            validator: (v) => {
                const trimmed = String(v ?? '').trim();
                if (!trimmed) return tOrFallback(NEW_PROFILE_NAME_REQUIRED_KEY, '新档案名不能为空');
                if (profileNameCountUnits(trimmed) > PROFILE_NAME_MAX_UNITS) {
                    return tOrFallback(PROFILE_NAME_TOO_LONG_KEY, '档案名过长');
                }
                return '';
            },
            onInput: (inputEl) => {
                if (_renameCatgirlDidOverLimit) {
                    _renameCatgirlDidOverLimit = false;
                    flashProfileNameTooLong(inputEl);
                }
            }
        }
    );
    if (!newName || newName === oldName) return;
    if (characterData['猫娘'][newName]) {
        await showAlert(window.t ? window.t('character.profileNameExists') : '该档案名已存在');
        return;
    }
    // 调用API重命名
    const res = await fetch('/api/characters/catgirl/' + encodeURIComponent(oldName) + '/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName })
    });
    const result = await res.json();
    if (result.success) {
        // 如果重命名的是当前展开的猫娘，更新展开记录
        if (expandedCatgirlName === oldName) {
            expandedCatgirlName = newName;
        }
        // 迁移 localStorage 中的展开状态记录
        const oldStorageKey = `catgirl_expand_${oldName}`;
        const newStorageKey = `catgirl_expand_${newName}`;
        const savedState = localStorage.getItem(oldStorageKey);
        if (savedState !== null) {
            localStorage.setItem(newStorageKey, savedState);
            localStorage.removeItem(oldStorageKey);
        }

        // 更新记忆文件中的角色名称
        try {
            await fetch('/api/memory/update_catgirl_name', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ old_name: oldName, new_name: newName })
            });
        } catch (error) {
            console.error('更新记忆文件中的角色名称失败:', error);
            // 不阻止主流程，继续加载数据
        }

        await loadCharacterData();
    } else {
        await showAlert(result.error || (window.t ? window.t('character.renameFailed') : '重命名失败'));
    }
}

function openApiKeySettings() {
    // 检查是否已有弹窗存在
    const existingModal = document.getElementById('api-key-settings-modal');
    if (existingModal) {
        // 如果已存在，聚焦到该弹窗（通过点击背景区域）
        existingModal.style.display = 'block';
        return;
    }

    // 创建弹窗容器
    let modal = document.createElement('div');
    modal.id = 'api-key-settings-modal';
    modal.style.position = 'fixed';
    modal.style.left = '0';
    modal.style.top = '0';
    modal.style.width = '100vw';
    modal.style.height = '100vh';
    modal.style.background = 'rgba(0,0,0,0.4)';
    modal.style.zIndex = '9999';

    // 监听关闭消息
    const apiKeyMessageHandler = function (e) {
        if (!ALLOWED_ORIGINS.includes(e.origin)) return;
        if (e.data && e.data.type === 'close_api_key_settings') {
            const modalToRemove = document.getElementById('api-key-settings-modal');
            if (modalToRemove) {
                document.body.removeChild(modalToRemove);
            }
            window.removeEventListener('message', apiKeyMessageHandler);
        }
    };

    modal.onclick = function (e) {
        if (e.target === modal) {
            window.removeEventListener('message', apiKeyMessageHandler);
            document.body.removeChild(modal);
        }
    };
    // 创建iframe
    let iframe = document.createElement('iframe');
    iframe.src = '/api_key';
    iframe.style.width = '800px';
    iframe.style.height = '720px';
    iframe.style.border = 'none';
    iframe.style.background = '#fff';
    iframe.style.display = 'block';
    iframe.style.margin = '50px auto';
    iframe.style.borderRadius = '8px';

    window.addEventListener('message', apiKeyMessageHandler);
    modal.appendChild(iframe);
    document.body.appendChild(modal);
}

function openVoiceClone(lanlanName) {
    // 使用 window.openOrFocusWindow 打开独立窗口
    const url = '/voice_clone?lanlan_name=' + encodeURIComponent(lanlanName);
    const lanlanNameForKey = lanlanName || 'default';
    const windowName = 'neko_voice_clone_' + encodeURIComponent(lanlanNameForKey);

    // 计算窗口位置，使其居中显示
    const width = 700;
    const height = 750;
    const left = Math.max(0, Math.floor((screen.width - width) / 2));
    const top = Math.max(0, Math.floor((screen.height - height) / 2));

    const features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;

    if (typeof window.openOrFocusWindow === 'function') {
        window.openOrFocusWindow(url, windowName, features);
    } else {
        // 兼容处理：如果 openOrFocusWindow 不存在，直接使用 window.open
        window.open(url, windowName, features);
    }
}



// 解除声音注册
window.unregisterVoice = async function (catgirlName) {
    const confirmMsg = window.t ? window.t('character.confirmUnregisterVoice', { name: catgirlName }) : `确定要解除猫娘"${catgirlName}"的声音注册吗？`;
    const confirmTitle = window.t ? window.t('character.unregisterVoiceTitle') : '解除声音注册';
    if (!await showConfirm(confirmMsg, confirmTitle, { danger: true })) {
        return;
    }

    try {
        const response = await fetch('/api/characters/catgirl/' + encodeURIComponent(catgirlName) + '/unregister_voice', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (result.success) {
            await showAlert(window.t ? window.t('character.voiceUnregistered') : '声音注册已解除');
            await loadCharacterData(); // 刷新数据
        } else {
            await showAlert(result.error || (window.t ? window.t('character.unregisterFailed') : '解除注册失败'));
        }
    } catch (error) {
        console.error('解除注册出错:', error);
        await showAlert(window.t ? window.t('character.unregisterError') : '解除注册时发生错误');
    }
}

// Beacon功能 - 页面关闭时发送信号给服务器
let beaconSent = false;

function sendBeacon() {
    if (beaconSent) return; // 防止重复发送
    beaconSent = true;

    try {
        // 使用navigator.sendBeacon确保信号不被拦截
        const payload = JSON.stringify({
            timestamp: Date.now(),
            action: 'shutdown'
        });
        const blob = new Blob([payload], { type: 'application/json' });
        const success = navigator.sendBeacon('/api/beacon/shutdown', blob);

        if (success) {
            console.log('Beacon信号已发送');
        } else {
            console.warn('Beacon发送失败，尝试使用fetch');
            // 备用方案：使用fetch
            fetch('/api/beacon/shutdown', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    timestamp: Date.now(),
                    action: 'shutdown'
                }),
                keepalive: true // 确保请求在页面关闭时仍能发送
            }).catch(err => console.log('备用beacon发送失败:', err));
        }
    } catch (e) {
        console.log('Beacon发送异常:', e);
    }
}

// 监听API Key变更事件
window.addEventListener('message', function (event) {
    if (!ALLOWED_ORIGINS.includes(event.origin)) return;
    if (event.data && event.data.type === 'api_key_changed') {
        // API Key已更改，刷新角色数据以显示更新后的Voice ID状态
        console.log('API Key已更改，正在刷新角色数据...');
        loadCharacterData();
    } else if (event.data && event.data.type === 'voice_id_updated') {
        const lanlanName = event.data.lanlan_name;
        const voiceId = event.data.voice_id;
        if (!lanlanName || !voiceId) return;

        try {
            if (characterData && characterData['猫娘'] && characterData['猫娘'][lanlanName]) {
                characterData['猫娘'][lanlanName]['voice_id'] = voiceId;
            }

            const switchBtn = document.getElementById(`switch-btn-${lanlanName}`);
            const block = switchBtn ? switchBtn.closest('.catgirl-block') : null;
            const select = block ? block.querySelector('select[name="voice_id"]') : null;
            if (!select) return;

            fetch('/api/characters/voices').then(r => r.json()).then(data => {
                if (!data || !data.voices) return;
                while (select.firstChild) select.removeChild(select.firstChild);
                const voiceNotSetText = window.t ? window.t('character.voiceNotSet') : '未指定音色';
                const defaultOption = document.createElement('option');
                defaultOption.value = '';
                defaultOption.textContent = voiceNotSetText;
                select.appendChild(defaultOption);

                const voiceOwners2 = data.voice_owners || {};
                Object.entries(data.voices).forEach(([id, voiceData]) => {
                    const option = document.createElement('option');
                    option.value = id;
                    const owners = voiceOwners2[id];
                    let dn = '';
                    if (owners && owners.length > 0) {
                        dn = owners.join(', ');
                    } else if (voiceData.prefix) {
                        dn = voiceData.prefix;
                    }
                    const shortId = id.length > 20 ? id.substring(0, 18) + '…' : id;
                    option.textContent = dn ? dn + ' (' + shortId + ')' : id;
                    option.title = id;
                    select.appendChild(option);
                });

                // 添加免费预设音色
                if (data.free_voices && Object.keys(data.free_voices).length > 0) {
                    const freeGroup = document.createElement('optgroup');
                    const freeLabel = window.t ? window.t('character.freePresetVoices') : '免费预设音色';
                    freeGroup.label = '── ' + freeLabel + ' ──';
                    Object.entries(data.free_voices).forEach(([displayName, id]) => {
                        const option = document.createElement('option');
                        option.value = id;
                        option.textContent = displayName;
                        freeGroup.appendChild(option);
                    });
                    select.appendChild(freeGroup);
                }

                // 处理 GPT-SoVITS voice_id：若当前列表没有该 gsv: 选项，添加兜底项避免 select.value 失效
                const hasVoiceOption = Array.from(select.options).some(opt => opt.value === voiceId);
                if (voiceId.startsWith('gsv:') && !hasVoiceOption) {
                    const gsvGroup = document.createElement('optgroup');
                    const gsvLabel = window.t ? window.t('character.gptsovitsVoices') : 'GPT-SoVITS 声音';
                    gsvGroup.label = '── ' + gsvLabel + ' ──';

                    const gsvOption = document.createElement('option');
                    gsvOption.value = voiceId;
                    gsvOption.textContent = voiceId.substring(4) + ' (?)';
                    gsvGroup.appendChild(gsvOption);
                    select.appendChild(gsvGroup);
                }

                select.value = voiceId;
            }).catch(() => {});
        } catch (e) {}
    }
});

// 监听页面关闭事件
window.addEventListener('beforeunload', sendBeacon);
window.addEventListener('unload', sendBeacon);



// 更新切换按钮状态
function updateSwitchButtons() {
    fetch('/api/characters/current_catgirl')
        .then(response => response.json())
        .then(data => {
            const currentCatgirl = data.current_catgirl || '';
            const catgirls = characterData['猫娘'] || {};

            Object.keys(catgirls).forEach(name => {
                const switchBtn = document.getElementById(`switch-btn-${name}`);
                if (switchBtn) {
                    if (name === currentCatgirl) {
                        const currentText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/star.png" alt="" class="star-icon"> <span data-i18n="character.currentCatgirl">${window.t('character.currentCatgirl')}</span>` : '<img src="/static/icons/star.png" alt="" class="star-icon"> 当前猫娘';
                        switchBtn.innerHTML = currentText;
                        switchBtn.style.background = '#40C5F1';
                        switchBtn.style.color = '#fff';
                        switchBtn.style.minWidth = '120px';
                        switchBtn.disabled = true;
                    } else {
                        const switchText = (window.t && typeof window.t === 'function') ? `<img src="/static/icons/star.png" alt="" class="star-icon"> <span data-i18n="character.switchCatgirl">${window.t('character.switchCatgirl')}</span>` : '<img src="/static/icons/star.png" alt="" class="star-icon"> 切换猫娘';
                        switchBtn.innerHTML = switchText;
                        switchBtn.style.background = '#40C5F1';
                        switchBtn.style.minWidth = '120px';
                        switchBtn.disabled = false;
                    }
                }
            });
        })
        .catch(error => {
            console.error('获取当前猫娘失败:', error);
        });
}

// 切换猫娘
async function switchCatgirl(catgirlName) {
    try {
        const response = await fetch('/api/characters/current_catgirl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ catgirl_name: catgirlName })
        });

        const result = await response.json();
        if (result.success) {
            await loadCharacterData(); // 重新加载数据以更新按钮状态
        } else {
            await showAlert(result.error || (window.t ? window.t('character.switchFailed') : '切换失败'));
        }
    } catch (error) {
        console.error('切换猫娘失败:', error);
        await showAlert(window.t ? window.t('character.switchError') : '切换猫娘时发生错误');
    }
}



// 初始化页面事件监听
function setupPageEventListeners() {
    // 关闭页面按钮
    const closeBtn = document.getElementById('close-chara-manager-btn');
    if (closeBtn) {
        closeBtn.addEventListener('click', closeCharaManagerPage);
    }

    // API Key 设置按钮
    const apiKeyBtn = document.getElementById('api-key-settings-btn');
    if (apiKeyBtn) {
        apiKeyBtn.addEventListener('click', openApiKeySettings);
    }

    // 新增猫娘按钮
    const addCatgirlBtn = document.getElementById('add-catgirl-btn');
    if (addCatgirlBtn) {
        addCatgirlBtn.addEventListener('click', function () {
            showCatgirlForm(null);
        });
    }
}

// 页面加载时拉取数据，并在后台异步扫描工坊角色卡
async function initPage() {
    await loadCharacterReservedFieldsConfig();

    // 1. 先快速加载已有的本地角色数据
    // 我们不等待工坊扫描，先让页面呈现出来
    await loadCharacterData();

    // 2. 初始化页面事件监听
    setupPageEventListeners();

    // 3. 延迟执行工坊角色卡扫描
    // 使用 setTimeout 将其放到任务队列末尾，并等待几秒钟，让浏览器优先处理页面渲染和交互
    setTimeout(() => {
        console.log('[工坊扫描] 开始异步扫描工坊角色卡...');
        autoScanWorkshopCharacterCards().then(async (hasNewCards) => {
            if (hasNewCards) {
                console.log('[工坊扫描] 发现新角色卡，正在更新列表...');
                // 发现新卡时才刷新数据
                await loadCharacterData();
            } else {
                console.log('[工坊扫描] 未发现新角色卡');
            }
        }).catch(err => {
            console.error('[工坊扫描] 扫描过程中发生错误:', err);
        });
    }, 1000); // 延迟1秒开始扫描，确保页面交互流畅
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPage);
} else {
    initPage();
}

// Electron白屏修复
(function () {
    const fixWhiteScreen = () => {
        if (document.body) {
            void document.body.offsetHeight;
            const currentOpacity = document.body.style.opacity || '1';
            document.body.style.opacity = '0.99';
            requestAnimationFrame(() => {
                document.body.style.opacity = currentOpacity;
            });
        }
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fixWhiteScreen);
    } else {
        fixWhiteScreen();
    }
    window.addEventListener('load', fixWhiteScreen);
})();

// 监听语言切换事件，更新动态创建的文本
window.addEventListener('localechange', () => {
    // 重新加载角色数据以更新所有动态文本
    loadCharacterData();
    // 更新标题的 data-text 属性以保持样式
    const titleH2 = document.querySelector('.container-header h2');
    if (titleH2) {
        titleH2.setAttribute('data-text', titleH2.textContent);
    }
});

// 在页面加载后和 i18n 更新后同步 data-text 属性
function updateTitleDataText() {
    const titleH2 = document.querySelector('.container-header h2');
    if (titleH2) {
        titleH2.setAttribute('data-text', titleH2.textContent);
    }
}

// 监听 i18n 更新完成
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        setTimeout(updateTitleDataText, 500);
    });
} else {
    setTimeout(updateTitleDataText, 500);
}

// 使用 MutationObserver 监听标题文本变化
const titleObserver = new MutationObserver(() => {
    updateTitleDataText();
});

const titleH2 = document.querySelector('.container-header h2');
if (titleH2) {
    titleObserver.observe(titleH2, { childList: true, characterData: true, subtree: true });
}

// 页面卸载时断开观察器
window.addEventListener('unload', () => {
    if (titleObserver) titleObserver.disconnect();
});

// 主人保存按钮也用.btn.sm
const masterSaveBtn = document.querySelector('#master-form button[type="submit"]');
if (masterSaveBtn) masterSaveBtn.classList.add('sm');

// 关闭角色管理页面
function closeCharaManagerPage() {
    if (window.opener) {
        // 如果是通过 window.open() 打开的，直接关闭
        window.close();
    } else if (window.parent && window.parent !== window) {
        // 如果在 iframe 中，通知父窗口关闭
        window.parent.postMessage({ type: 'close_chara_manager' }, window.location.origin);
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

// 初始化通用教程管理器（从 HTML 内联脚本移至此处，避免 CSP 限制）
document.addEventListener('DOMContentLoaded', function() {
    if (typeof initUniversalTutorialManager === 'function') {
        initUniversalTutorialManager();
    }
});
