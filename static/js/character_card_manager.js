// 角色保留字段配置（优先从后端集中配置加载；失败时使用前端兜底）
// 共用工具由 reserved_fields_utils.js 提供（ReservedFieldsUtils）
let characterReservedFieldsConfig = ReservedFieldsUtils.emptyConfig();
let _reservedFieldsReady = null;

const SYSTEM_RESERVED_FIELDS_FALLBACK = ReservedFieldsUtils.SYSTEM_RESERVED_FIELDS_FALLBACK;
const WORKSHOP_RESERVED_FIELDS_FALLBACK = ReservedFieldsUtils.WORKSHOP_RESERVED_FIELDS_FALLBACK;

function _safeArray(value) {
    return ReservedFieldsUtils._safeArray(value);
}

function _uniqueFields(fields) {
    return [...new Set(fields)];
}

function _getReservedConfigOrFallback() {
    const systemReserved = _safeArray(characterReservedFieldsConfig.system_reserved_fields);
    const workshopReserved = _safeArray(characterReservedFieldsConfig.workshop_reserved_fields);
    const allReserved = _safeArray(characterReservedFieldsConfig.all_reserved_fields);
    if (systemReserved.length || workshopReserved.length || allReserved.length) {
        return {
            system_reserved_fields: systemReserved,
            workshop_reserved_fields: workshopReserved,
            all_reserved_fields: allReserved.length > 0 ? allReserved : _uniqueFields([...systemReserved, ...workshopReserved])
        };
    }
    return {
        system_reserved_fields: SYSTEM_RESERVED_FIELDS_FALLBACK,
        workshop_reserved_fields: WORKSHOP_RESERVED_FIELDS_FALLBACK,
        all_reserved_fields: _uniqueFields([...SYSTEM_RESERVED_FIELDS_FALLBACK, ...WORKSHOP_RESERVED_FIELDS_FALLBACK])
    };
}

function getWorkshopReservedFields() {
    const cfg = _getReservedConfigOrFallback();
    const extraSystemFields = ['live2d_item_id', '_reserved', 'item_id', 'idleAnimation', 'idleAnimations', 'mmd_idle_animation', 'mmd_idle_animations']
        .filter(f => cfg.all_reserved_fields.includes(f));
    return _uniqueFields([...cfg.workshop_reserved_fields, ...extraSystemFields]);
}

function getWorkshopHiddenFields() {
    const cfg = _getReservedConfigOrFallback();
    // 完全遵照角色管理：隐藏所有 system + workshop 保留字段
    return _uniqueFields([...cfg.all_reserved_fields]);
}

function loadCharacterReservedFieldsConfig() {
    _reservedFieldsReady = ReservedFieldsUtils.load().then(cfg => {
        characterReservedFieldsConfig = cfg;
    });
    return _reservedFieldsReady;
}

function ensureReservedFieldsLoaded() {
    return _reservedFieldsReady || Promise.resolve();
}

// 顶部 tab 按钮初始化（旧版自定义 tooltip 因为文本与按钮文字重复且定位有误已移除）
document.addEventListener('DOMContentLoaded', function () {
    void loadCharacterReservedFieldsConfig();

    // 云存档管理按钮
    const openCloudsaveManagerBtn = document.getElementById('open-cloudsave-manager-btn');
    if (openCloudsaveManagerBtn) {
        openCloudsaveManagerBtn.addEventListener('click', openCloudsaveManager);
    }
});

// 构建云存档管理页 URL（带当前 UI 语言；角色名由云存档页内自行选择）
function buildCloudsaveManagerUrl() {
    const query = new URLSearchParams();
    const currentUiLanguage = getCurrentUiLanguage();
    if (currentUiLanguage) query.set('ui_lang', currentUiLanguage);
    // 若页面上下文已有当前选中角色，也带上以便云存档页直接定位
    if (typeof window._currentCatgirl === 'string' && window._currentCatgirl.trim()) {
        query.set('lanlan_name', window._currentCatgirl.trim());
    }
    const qs = query.toString();
    return qs ? '/cloudsave_manager?' + qs : '/cloudsave_manager';
}

// 打开云存档管理窗口（与 chara_manager.js 中的实现保持行为一致）
function openCloudsaveManager() {
    const url = buildCloudsaveManagerUrl();
    const windowName = 'neko_cloudsave_manager';
    const width = 1180;
    const height = 860;
    const left = Math.max(0, Math.floor((screen.width - width) / 2));
    const top = Math.max(0, Math.floor((screen.height - height) / 2));
    const features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;

    const existingWindow = window._openedWindows && window._openedWindows[windowName];
    if (existingWindow && !existingWindow.closed) {
        try {
            const targetUrl = new URL(url, window.location.origin).toString();
            if (existingWindow.location.href !== targetUrl) {
                existingWindow.location.href = targetUrl;
            }
            existingWindow.focus();
            return;
        } catch (error) {
            console.warn('更新云存档管理窗口地址失败:', error);
        }
    }

    const openedWindow = typeof window.openOrFocusWindow === 'function'
        ? window.openOrFocusWindow(url, windowName, features)
        : window.open(url, windowName, features);

    if (openedWindow && !openedWindow.closed) {
        if (!window._openedWindows || typeof window._openedWindows !== 'object') {
            window._openedWindows = {};
        }
        window._openedWindows[windowName] = openedWindow;
    }

    if (!openedWindow) {
        window.location.href = url;
    }
}
window.openCloudsaveManager = openCloudsaveManager;

// 响应式标签页处理
function updateTabsLayout() {
    const tabs = document.getElementById('workshop-tabs');
    const containerWidth = tabs.parentElement.clientWidth;

    // 定义切换阈值
    const thresholdWidth = 400;

    if (containerWidth < thresholdWidth) {
        tabs.classList.remove('normal');
        tabs.classList.add('compact');
    } else {
        tabs.classList.remove('compact');
        tabs.classList.add('normal');
    }
}

// 初始化时调用一次
window.addEventListener('DOMContentLoaded', updateTabsLayout);
// 监听窗口大小变化
window.addEventListener('resize', updateTabsLayout);

// 点击模态框外部关闭
function closeModalOnOutsideClick(event) {
    const modal = document.getElementById('itemDetailsModal');
    if (event.target === modal) {
        closeModal();
    }
}

// 检查当前模型是否为默认模型（mao_pro）
function isDefaultModel() {
    // 使用保存的角色卡模型名称
    const currentModel = window.currentCharacterCardModel || '';
    return currentModel === 'mao_pro';
}

// 更新上传按钮状态（不再依赖model-select元素）
function updateModelDisplayAndUploadState() {
    const isDefault = isDefaultModel();

    // 更新上传按钮状态
    const uploadButtons = [
        document.querySelector('button[onclick="handleUploadToWorkshop()"]'),
        document.querySelector('#uploadToWorkshopModal .btn-primary[onclick="uploadItem()"]')
    ];

    uploadButtons.forEach(btn => {
        if (btn) {
            if (isDefault) {
                btn.disabled = true;
                btn.style.opacity = '0.5';
                btn.style.cursor = 'not-allowed';
                btn.title = window.t ? window.t('steam.defaultModelCannotUpload') : '默认模型无法上传到创意工坊';
            } else {
                btn.disabled = false;
                btn.style.opacity = '';
                btn.style.cursor = '';
                btn.title = '';
            }
        }
    });
}

// 上传区域切换功能 - 改为显示modal
function toggleUploadSection() {

    // 检查是否为默认模型
    if (isDefaultModel()) {
        showMessage(window.t ? window.t('steam.defaultModelCannotUpload') : '默认模型无法上传到创意工坊', 'error');
        return;
    }

    const uploadModal = document.getElementById('uploadToWorkshopModal');
    if (uploadModal) {
        const isHidden = uploadModal.style.display === 'none' || uploadModal.style.display === '';
        if (isHidden) {
            // 显示modal
            uploadModal.style.display = 'flex';
            // 更新翻译
            if (window.updatePageTexts) {
                window.updatePageTexts();
            }
        } else {
            // 隐藏modal时调用closeUploadModal以处理临时文件
            closeUploadModal();
        }
    } else {
    }
}

// 关闭上传modal

// 重复上传提示modal相关函数
function openDuplicateUploadModal(message) {
    const modal = document.getElementById('duplicateUploadModal');
    const messageElement = document.getElementById('duplicate-upload-message');
    if (modal && messageElement) {
        messageElement.textContent = message || (window.t ? window.t('steam.characterCardAlreadyUploadedMessage') : '该角色卡已经上传到创意工坊');
        modal.style.display = 'flex';
    }
}

function closeDuplicateUploadModal() {
    const modal = document.getElementById('duplicateUploadModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

function closeDuplicateUploadModalOnOutsideClick(event) {
    const modal = document.getElementById('duplicateUploadModal');
    if (event.target === modal) {
        closeDuplicateUploadModal();
    }
}

// 取消上传确认modal相关函数
function openCancelUploadModal() {
    const modal = document.getElementById('cancelUploadModal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

function closeCancelUploadModal() {
    const modal = document.getElementById('cancelUploadModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

function closeCancelUploadModalOnOutsideClick(event) {
    const modal = document.getElementById('cancelUploadModal');
    if (event.target === modal) {
        closeCancelUploadModal();
    }
}

function confirmCancelUpload() {
    // 用户确认，删除临时文件
    if (currentUploadTempFolder) {
        cleanupTempFolder(currentUploadTempFolder, true);
    }
    // 清除临时目录路径和上传状态
    currentUploadTempFolder = null;
    isUploadCompleted = false;
    // 关闭取消上传modal
    closeCancelUploadModal();
    // 关闭上传modal
    const uploadModal = document.getElementById('uploadToWorkshopModal');
    if (uploadModal) {
        uploadModal.style.display = 'none';
    }
    // 刷新页面
    window.location.reload();
}

function closeUploadModal() {
    // 检查是否有临时文件且未上传
    if (currentUploadTempFolder && !isUploadCompleted) {
        // 显示取消上传确认modal
        openCancelUploadModal();
    } else {
        // 没有临时文件或已上传，直接关闭
        const uploadModal = document.getElementById('uploadToWorkshopModal');
        if (uploadModal) {
            uploadModal.style.display = 'none';
        }
        // 重置状态
        currentUploadTempFolder = null;
        isUploadCompleted = false;
        // 刷新页面
        window.location.reload();
    }
}

// 点击modal外部关闭
function closeUploadModalOnOutsideClick(event) {
    const modal = document.getElementById('uploadToWorkshopModal');
    if (event.target === modal) {
        closeUploadModal();
    }
}

// 标签页切换功能
// 从localStorage加载同步数据并填充到创意工坊上传表单
function applyWorkshopSyncData() {
    try {
        // 从localStorage获取同步数据
        const workshopSyncDataStr = localStorage.getItem('workshopSyncData');
        if (workshopSyncDataStr) {
            const workshopSyncData = JSON.parse(workshopSyncDataStr);

            // 1. 填充标签
            const tagsContainer = document.getElementById('tags-container');
            if (tagsContainer) {
                // 清空现有标签
                tagsContainer.innerHTML = '';

                // 添加从角色卡同步的标签
                if (workshopSyncData.tags && Array.isArray(workshopSyncData.tags)) {
                    workshopSyncData.tags.forEach(tag => {
                        addTag(tag);
                    });
                }
            }

            // 2. 填充描述（现在是 div 元素）
            const itemDescription = document.getElementById('item-description');
            if (itemDescription) {
                itemDescription.textContent = workshopSyncData.description || '';
            } else {
                console.error('未找到创意工坊描述元素');
            }
        } else {
        }
    } catch (error) {
        console.error('应用同步数据时出错:', error);
    }
}

// 视图切换防抖锁，防止动画期间重复点击
let _viewSwitching = false;

function switchTab(tabId, event) {
    if (_viewSwitching) return;

    const selectedTab = document.getElementById(tabId);
    if (!selectedTab) return;

    // 已经是激活状态，直接同步按钮高亮即可
    const tabButtons = document.querySelectorAll('.tab');
    if (selectedTab.classList.contains('active') && !selectedTab.classList.contains('tab-leaving')) {
        tabButtons.forEach(btn => {
            const onclick = btn.getAttribute('onclick') || '';
            btn.classList.toggle('active', onclick.includes(tabId));
        });
        return;
    }

    _viewSwitching = true;

    // 同步按钮 active 状态（点击事件 / 编程调用都覆盖）
    tabButtons.forEach(btn => {
        const onclick = btn.getAttribute('onclick') || '';
        btn.classList.toggle('active', onclick.includes(tabId));
    });
    if (event && event.currentTarget && event.currentTarget.classList) {
        event.currentTarget.classList.add('active');
    }
    const sidebarButtons = document.querySelectorAll('.sidebar-tab-button');
    sidebarButtons.forEach(btn => {
        const onclick = btn.getAttribute('onclick') || '';
        btn.classList.toggle('active', onclick.includes(tabId));
    });

    // 找到当前激活视图（要离场的）
    const tabContents = document.querySelectorAll('.tab-content');
    let leavingTab = null;
    tabContents.forEach(content => {
        if (content !== selectedTab && content.classList.contains('active')) {
            leavingTab = content;
        }
        // 清理可能残留的内联 display（早期版本）
        if (content !== selectedTab && content !== leavingTab) {
            content.style.display = '';
            content.classList.remove('active', 'tab-leaving', 'tab-entering');
        }
    });

    const finalize = () => {
        _viewSwitching = false;
    };

    if (leavingTab && leavingTab !== selectedTab) {
        // 旧视图执行 leaving 动画，新视图同步入场（重叠以遮住底层蓝色背景）
        leavingTab.classList.remove('active');
        leavingTab.classList.add('tab-leaving');

        selectedTab.classList.add('active', 'tab-entering');
        if (window.updatePageTexts) window.updatePageTexts();

        // 旧视图保持原状作为底层；新视图自上而下"拉下帘幕"完全覆盖（500ms 与 CSS @keyframes viewCurtainReveal 时长一致）
        setTimeout(() => {
            leavingTab.classList.remove('tab-leaving');
            leavingTab.style.display = '';
        }, 520);
        // 新视图入场结束
        setTimeout(() => {
            selectedTab.classList.remove('tab-entering');
            finalize();
        }, 520);
    } else {
        // 没有离场视图（首次或同 tab）：直接显示
        selectedTab.classList.add('active');
        if (window.updatePageTexts) window.updatePageTexts();
        finalize();
    }

    // 上传 modal 初始隐藏
    const uploadModal = document.getElementById('uploadToWorkshopModal');
    if (uploadModal) {
        uploadModal.style.display = 'none';
    }

    // 切换到角色卡：自动扫描模型并恢复选中
    if (tabId === 'character-cards-content') {
        scanModels();
        const characterCardSelect = document.getElementById('character-card-select');
        const selectedId = characterCardSelect ? characterCardSelect.value : null;
        if (selectedId && window.characterCards) {
            const selectedCard = window.characterCards.find(c => String(c.id) === selectedId);
            if (selectedCard) {
                expandCharacterCardSection(selectedCard);
            }
        }
    }

// 订阅内容：检查 Steam 状态
    if (tabId === 'subscriptions-content') {
        checkSteamStatus();
    }
}

// 提示：由于浏览器安全限制，浏览按钮仅提供路径输入提示

// 选择文件夹并填充到指定输入框
async function selectFolderForInput(inputId) {
    try {
        // 检查浏览器是否支持 File System Access API
        if (!('showDirectoryPicker' in window)) {
            showMessage(window.t ? window.t('steam.folderPickerNotSupported') : '当前浏览器不支持目录选择，请手动输入路径', 'warning');
            // 移除 readonly 属性让用户可以手动输入
            document.getElementById(inputId).removeAttribute('readonly');
            return;
        }

        const dirHandle = await window.showDirectoryPicker({
            mode: 'read'
        });

        // 获取选中目录的路径（通过目录名称）
        // 注意：File System Access API 不直接提供完整路径，只提供目录名称
        // 我们需要通知用户已选择的目录名
        const folderName = dirHandle.name;

        // 由于浏览器安全限制，无法获取完整路径
        // 提示用户输入完整路径
        showMessage(window.t ? window.t('steam.folderSelectedPartial', { name: folderName }) :
            `已选择目录: "${folderName}"。由于浏览器安全限制，请手动输入完整路径`, 'warning');

        // 移除 readonly 让用户可以输入完整路径
        document.getElementById(inputId).removeAttribute('readonly');
        document.getElementById(inputId).focus();

    } catch (error) {
        if (error.name === 'AbortError') {
            // 用户取消了选择
            showMessage(window.t ? window.t('steam.folderSelectionCancelled') : '已取消目录选择', 'info');
        } else {
            console.error('选择目录失败:', error);
            showMessage(window.t ? window.t('steam.folderSelectionError') : '选择目录失败', 'error');
        }
    }
}


// 检查文件是否存在
async function doesFileExist(filePath) {
    try {
        const response = await fetch(`/api/file-exists?path=${encodeURIComponent(filePath)}`);
        const result = await response.json();
        return result.exists;
    } catch (error) {
        // 如果API不可用，返回false
        return false;
    }
}

// 查找预览图片
async function findPreviewImage(folderPath) {
    try {
        // 尝试查找常见的预览图片文件
        const commonImageNames = ['preview.jpg', 'preview.png', 'thumbnail.jpg', 'thumbnail.png', 'icon.jpg', 'icon.png', 'header.jpg', 'header.png'];

        for (const imageName of commonImageNames) {
            const imagePath = `${folderPath}/${imageName}`;
            if (await doesFileExist(imagePath)) {
                return imagePath;
            }
        }

        // 如果找不到常见预览图，尝试使用API获取文件夹中的第一个图片文件
        const response = await fetch(`/api/find-first-image?folder=${encodeURIComponent(folderPath)}`);
        const result = await response.json();

        if (result.success && result.imagePath) {
            return result.imagePath;
        }
    } catch (error) {
        console.error('查找预览图片失败:', error);
    }

    return null;
}

// 添加完整版本的formatDate函数（包含日期和时间）
function formatDate(timestamp) {
    if (!timestamp) return '未知';

    const date = new Date(timestamp);
    // 使用toLocaleString同时显示日期和时间
    return date.toLocaleString();
}

// 文件路径选择辅助功能
function validatePathInput(elementId) {
    const element = document.getElementById(elementId);
    element.addEventListener('blur', function () {
        const path = this.value.trim();
        if (path && path.includes('\\\\')) {
            // 将双反斜杠替换为单反斜杠，Windows路径格式
            this.value = path.replace(/\\\\/g, '\\');
        }
    });
}

// 为路径输入框添加验证
validatePathInput('content-folder');
validatePathInput('preview-image');

// 标签管理功能
const tagInput = document.getElementById('item-tags');
const tagsContainer = document.getElementById('tags-container');

// 监听输入事件，当输入空格时添加标签
if (tagInput) {
    tagInput.addEventListener('input', (e) => {
        if (e.target.value.endsWith(' ') && e.target.value.trim() !== '') {
            e.preventDefault();
            addTag(e.target.value.trim());
            e.target.value = '';
        }
    });

    // 兼容回车键添加标签
    tagInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && e.target.value.trim() !== '') {
            e.preventDefault();
            addTag(e.target.value.trim());
            e.target.value = '';
        }
    });
}

// 角色卡标签输入框事件监听
const characterCardTagInput = document.getElementById('character-card-tag-input');
if (characterCardTagInput) {
    characterCardTagInput.addEventListener('input', (e) => {
        if (e.target.value.endsWith(' ') && e.target.value.trim() !== '') {
            e.preventDefault();
            addTag(e.target.value.trim(), 'character-card');
            e.target.value = '';
        }
    });

    characterCardTagInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && e.target.value.trim() !== '') {
            e.preventDefault();
            addTag(e.target.value.trim(), 'character-card');
            e.target.value = '';
        }
    });
}

function updateCharacterCardTagScrollControls() {
    const wrapper = document.getElementById('character-card-tags-wrapper');
    const leftButton = document.getElementById('character-card-tags-scroll-left');
    const rightButton = document.getElementById('character-card-tags-scroll-right');
    if (!wrapper || !leftButton || !rightButton) return;

    const hasOverflow = (wrapper.scrollWidth - wrapper.clientWidth) > 2;
    const atStart = wrapper.scrollLeft <= 2;
    const atEnd = (wrapper.scrollLeft + wrapper.clientWidth) >= (wrapper.scrollWidth - 2);

    leftButton.classList.toggle('is-hidden', !hasOverflow);
    rightButton.classList.toggle('is-hidden', !hasOverflow);
    leftButton.disabled = !hasOverflow || atStart;
    rightButton.disabled = !hasOverflow || atEnd;
}

function scrollCharacterCardTags(direction) {
    const wrapper = document.getElementById('character-card-tags-wrapper');
    if (!wrapper) return;

    const scrollAmount = Math.max(wrapper.clientWidth * 0.75, 120);
    wrapper.scrollBy({
        left: direction * scrollAmount,
        behavior: 'smooth'
    });

    window.setTimeout(updateCharacterCardTagScrollControls, 220);
}

function addTag(tagText, type = '', locked = false) {
    // 根据type参数获取对应的标签容器元素
    const containerId = type ? `${type}-tags-container` : 'tags-container';
    const tagsContainer = document.getElementById(containerId);
    if (!tagsContainer) {
        console.error(`Tags container ${containerId} not found`);
        return;
    }

    // 检查标签字数限制
    if (tagText.length > 30) {
        showMessage(window.t ? window.t('steam.tagTooLong') : '标签长度不能超过30个字符', 'error');
        return;
    }

    // 检查标签数量限制（locked标签不受限制）
    const existingTags = Array.from(tagsContainer.querySelectorAll('.tag'));
    if (!locked && existingTags.length >= 4) {
        showMessage(window.t ? window.t('steam.tagLimitReached') : '最多只能添加4个标签', 'error');
        return;
    }

    // 检查是否已存在相同标签
    const existingTagTexts = existingTags.map(tag =>
        tag.textContent.replace('×', '').replace('🔒', '').trim()
    );

    if (existingTagTexts.includes(tagText)) {
        // 如果标签已存在，直接返回（不显示错误消息，因为可能是自动添加的）
        if (locked) return;
        showMessage(window.t ? window.t('steam.tagExists') : '该标签已存在', 'error');
        return;
    }

    const tagElement = document.createElement('div');
    tagElement.className = 'tag' + (locked ? ' tag-locked' : '');

    // 根据locked和type决定是否显示删除按钮
    if (locked) {
        // 锁定的标签不能删除，显示锁定图标
        const lockedTitle = window.t ? window.t('steam.customTemplateTagLocked') : '此标签为自动添加，无法移除';
        tagElement.innerHTML = `${tagText}<span class="tag-locked-icon" title="${lockedTitle}">🔒</span>`;
        tagElement.setAttribute('data-locked', 'true');
    } else if (type === 'character-card') {
        tagElement.innerHTML = `${tagText}<span class="tag-remove" onclick="removeTag(this, 'character-card')">×</span>`;
    } else {
        tagElement.innerHTML = `${tagText}<span class="tag-remove" onclick="removeTag(this)">×</span>`;
    }

    // 锁定的标签插入到最前面
    if (locked && tagsContainer.firstChild) {
        tagsContainer.insertBefore(tagElement, tagsContainer.firstChild);
    } else {
        tagsContainer.appendChild(tagElement);
    }

    if (type === 'character-card') {
        updateCharacterCardTagScrollControls();
    }
}

function removeTag(tagElement, type = '') {
    if (tagElement && tagElement.parentElement) {
        tagElement.parentElement.remove();
    } else {
        console.error('Invalid tag element');
    }

    if (type === 'character-card') {
        updateCharacterCardTagScrollControls();
    }
}

// 消息显示功能 - 增强版
// 自定义确认模态框
function showConfirmModal(message, confirmCallback, cancelCallback = null) {
    // 创建确认模态框容器
    const modalOverlay = document.createElement('div');
    modalOverlay.className = 'confirm-modal-overlay';

    const modalContainer = document.createElement('div');
    modalContainer.className = 'confirm-modal-container';

    const modalContent = document.createElement('div');
    modalContent.className = 'confirm-modal-content';

    const modalMessage = document.createElement('div');
    modalMessage.className = 'confirm-modal-message';
    modalMessage.innerHTML = `<i class="fa fa-question-circle" style="margin-right: 8px;"></i>${escapeHtml(message)}`;

    const modalActions = document.createElement('div');
    modalActions.className = 'confirm-modal-actions';

    // 取消按钮
    const cancelButton = document.createElement('button');
    cancelButton.className = 'btn btn-secondary';
    cancelButton.textContent = window.t ? window.t('common.cancel') : '取消';
    cancelButton.onclick = () => {
        modalOverlay.remove();
        if (cancelCallback) cancelCallback();
    };

    // 确认按钮
    const confirmButton = document.createElement('button');
    confirmButton.className = 'btn btn-danger';
    confirmButton.textContent = window.t ? window.t('common.confirm') : '确认';
    confirmButton.onclick = () => {
        modalOverlay.remove();
        if (confirmCallback) confirmCallback();
    };

    // 组装模态框
    modalActions.appendChild(cancelButton);
    modalActions.appendChild(confirmButton);
    modalContent.appendChild(modalMessage);
    modalContent.appendChild(modalActions);
    modalContainer.appendChild(modalContent);
    modalOverlay.appendChild(modalContainer);

    // 添加到页面
    document.body.appendChild(modalOverlay);

    // 添加CSS样式
    if (!document.getElementById('confirm-modal-styles')) {
        const style = document.createElement('style');
        style.id = 'confirm-modal-styles';
        style.textContent = `
            .confirm-modal-overlay {
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background-color: rgba(0, 0, 0, 0.5);
                display: flex;
                justify-content: center;
                align-items: center;
                z-index: 9999;
                animation: fadeIn 0.3s ease;
            }

            .confirm-modal-container {
                display: flex;
                justify-content: center;
                align-items: center;
                width: 100%;
                height: 100%;
            }

            .confirm-modal-content {
                background-color: white;
                border-radius: 8px;
                padding: 24px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
                min-width: 400px;
                max-width: 90%;
                animation: slideUp 0.3s ease;
                color: #333;
            }
            
            .confirm-modal-content.dark-theme {
                background-color: white;
                color: #333;
            }

            .confirm-modal-message {
                font-size: 16px;
                margin-bottom: 20px;
                line-height: 1.5;
                color: inherit;
            }

            .confirm-modal-actions {
                display: flex;
                justify-content: flex-end;
                gap: 10px;
            }

            @keyframes fadeIn {
                from { opacity: 0; }
                to { opacity: 1; }
            }

            @keyframes slideUp {
                from { transform: translateY(20px); opacity: 0; }
                to { transform: translateY(0); opacity: 1; }
            }
        `;
        document.head.appendChild(style);
    }
}

function showMessage(message, type = 'info', duration = 3000) {
    // 统一为「导出角色卡」同款风格的居中顶部浮层卡片（非模态），
    // 保证桌面端网页也能稳定显示。调用签名保持与旧版兼容。
    function createMessageArea() {
        const container = document.createElement('div');
        container.id = 'message-area';
        container.className = 'message-area';
        document.body.appendChild(container);
        return container;
    }

    const messageArea = document.getElementById('message-area') || createMessageArea();

    // 布局：居中、顶部向下滑入，堆叠显示
    messageArea.style.position = 'fixed';
    messageArea.style.top = '24px';
    messageArea.style.left = '50%';
    messageArea.style.transform = 'translateX(-50%)';
    messageArea.style.right = '';
    messageArea.style.maxWidth = '90vw';
    messageArea.style.width = 'auto';
    messageArea.style.zIndex = '2147483647';
    messageArea.style.display = 'flex';
    messageArea.style.flexDirection = 'column';
    messageArea.style.alignItems = 'center';
    messageArea.style.pointerEvents = 'none';

    const typeConfig = {
        error:   { icon: 'fa-exclamation-circle', accent: '#ff5a5a', grad: 'linear-gradient(135deg,#ff7a7a,#ff5a5a)' },
        warning: { icon: 'fa-exclamation-triangle', accent: '#f0ad4e', grad: 'linear-gradient(135deg,#f6c266,#f0ad4e)' },
        success: { icon: 'fa-check-circle', accent: '#58c38a', grad: 'linear-gradient(135deg,#6ec5a8,#58c38a)' },
        info:    { icon: 'fa-info-circle', accent: '#40C5F1', grad: 'linear-gradient(135deg,#40C5F1,#5dd4f7)' },
    };
    const cfg = typeConfig[type] || typeConfig.info;

    const card = document.createElement('div');
    card.className = 'ccm-toast-card ccm-toast-' + type;
    card.style.cssText = [
        'background:#fff',
        'border-radius:14px',
        'padding:12px 18px',
        'min-width:260px',
        'max-width:min(560px, 90vw)',
        'box-shadow:0 14px 40px rgba(0,0,0,0.18)',
        'display:flex',
        'align-items:flex-start',
        'gap:10px',
        'margin-bottom:10px',
        'font-family:inherit',
        'color:#333',
        'font-size:13.5px',
        'line-height:1.5',
        'pointer-events:auto',
        'border-left:4px solid ' + cfg.accent,
        'opacity:0',
        'transform:translateY(-8px)',
        'transition:opacity 0.22s ease, transform 0.22s ease',
    ].join(';');

    const iconEl = document.createElement('i');
    iconEl.className = 'fa ' + cfg.icon;
    iconEl.style.cssText = 'color:' + cfg.accent + ';font-size:18px;margin-top:2px;flex-shrink:0';
    card.appendChild(iconEl);

    const body = document.createElement('div');
    body.style.cssText = 'flex:1;min-width:0;word-break:break-word;white-space:pre-wrap';
    body.textContent = (typeof message === 'string') ? message : String(message);
    card.appendChild(body);

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.innerHTML = '<i class="fa fa-times"></i>';
    closeBtn.style.cssText = 'background:transparent;border:none;color:#888;cursor:pointer;font-size:14px;padding:2px 4px;border-radius:4px;flex-shrink:0';
    closeBtn.onmouseenter = () => { closeBtn.style.background = 'rgba(0,0,0,0.06)'; closeBtn.style.color = '#333'; };
    closeBtn.onmouseleave = () => { closeBtn.style.background = 'transparent'; closeBtn.style.color = '#888'; };
    const dismiss = () => {
        card.style.opacity = '0';
        card.style.transform = 'translateY(-8px)';
        setTimeout(() => { if (card.parentNode) card.parentNode.removeChild(card); }, 220);
    };
    closeBtn.onclick = dismiss;
    card.appendChild(closeBtn);

    messageArea.appendChild(card);
    requestAnimationFrame(() => {
        card.style.opacity = '1';
        card.style.transform = 'translateY(0)';
    });

    if (duration > 0) {
        setTimeout(dismiss, duration);
    }

    return card;
}

// HTML转义函数
function escapeHtml(text) {
    if (typeof text !== 'string') {
        return String(text);
    }
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}


// 共享的提示框功能
function showToast(message, duration = 3000) {
    let container = document.getElementById('message-area');
    if (!container) {
        container = document.createElement('div');
        container.id = 'message-area';
        container.className = 'message-area';
        document.body.appendChild(container);
    }

    // 若容器由模板/其他逻辑预先创建，首个 toast 沿用旧 zIndex 会被新模态遮挡；
    // 无条件刷新定位 / 层级，确保每次都落在最顶层。
    container.style.position = 'fixed';
    container.style.top = '20px';
    container.style.right = '20px';
    container.style.maxWidth = '400px';
    container.style.zIndex = '2147483647';
    container.style.display = 'flex';
    container.style.flexDirection = 'column';
    container.style.alignItems = 'flex-end';
    container.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';

    const messageElement = document.createElement('div');
    // 使用 textContent 避免 HTML 注入风险 (resolved duplicate innerHTML comment review safely)
    messageElement.textContent = message;
    messageElement.style.cssText = `
        padding: 15px 20px;
        margin-bottom: 10px;
        background: #e8f5e9;
        color: #2e7d32;
        border-radius: 6px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        font-weight: bold;
        opacity: 0;
        transform: translateY(-10px);
        transition: opacity 0.3s ease, transform 0.3s ease;
    `;

    container.appendChild(messageElement);

    setTimeout(() => {
        messageElement.style.opacity = '1';
        messageElement.style.transform = 'translateY(0)';
    }, 10);

    setTimeout(() => {
        messageElement.style.opacity = '0';
        messageElement.style.transform = 'translateY(-10px)';
        setTimeout(() => {
            messageElement.remove();
        }, 300);
    }, duration);
}

// 加载状态管理器
function LoadingManager() {
    const loadingCount = { value: 0 };

    return {
        show: function (message = window.t ? window.t('common.loading') : '加载中...') {
            loadingCount.value++;
            if (loadingCount.value === 1) {
                const loadingOverlay = document.createElement('div');
                loadingOverlay.id = 'loading-overlay';
                loadingOverlay.style.cssText = `
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background: rgba(255, 255, 255, 0.8);
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    z-index: 9999;
                    backdrop-filter: blur(2px);
                `;

                const loadingSpinner = document.createElement('div');
                loadingSpinner.style.cssText = `
                    border: 4px solid #f3f3f3;
                    border-top: 4px solid #3498db;
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    animation: spin 1s linear infinite;
                    margin-bottom: 15px;
                `;

                const loadingText = document.createElement('div');
                loadingText.textContent = message;
                loadingText.style.fontSize = '16px';
                loadingText.style.color = '#333';

                // 添加CSS动画
                let style = document.getElementById('loading-overlay-style');
                if (!style) {
                    style = document.createElement('style');
                    style.id = 'loading-overlay-style';
                    style.textContent = `
                        @keyframes spin {
                            0% { transform: rotate(0deg); }
                            100% { transform: rotate(360deg); }
                        }
                    `;
                    document.head.appendChild(style);
                }

                loadingOverlay.appendChild(loadingSpinner);
                loadingOverlay.appendChild(loadingText);
                document.body.appendChild(loadingOverlay);
            }
        },

        hide: function () {
            loadingCount.value--;
            if (loadingCount.value <= 0) {
                loadingCount.value = 0;
                const overlay = document.getElementById('loading-overlay');
                if (overlay) {
                    overlay.remove();
                }
            }
        }
    };
}

// 创建全局加载管理器实例
const loading = new LoadingManager();

// 表单验证函数
function validateForm() {
    let isValid = true;
    const errorMessages = [];

    // 验证标题（现在是 div 元素，使用 textContent）
    const title = document.getElementById('item-title').textContent.trim();
    if (!title) {
        errorMessages.push(window.t ? window.t('steam.titleRequired') : '请输入标题');
        document.getElementById('item-title').classList.add('error');
        isValid = false;
    } else {
        document.getElementById('item-title').classList.remove('error');
    }

    // 验证内容文件夹
    const contentFolder = document.getElementById('content-folder').value.trim();
    if (!contentFolder) {
        errorMessages.push(window.t ? window.t('steam.contentFolderRequired') : '请指定内容文件夹');
        document.getElementById('content-folder').classList.add('error');
        isValid = false;
    } else {
        // 简单的路径格式验证
        if (/^[a-zA-Z]:\\/.test(contentFolder) || /^\//.test(contentFolder) || /^\.\.?[\\\/]/.test(contentFolder)) {
            document.getElementById('content-folder').classList.remove('error');
        } else {
            errorMessages.push(window.t ? window.t('steam.invalidFolderFormat') : '内容文件夹路径格式不正确');
            document.getElementById('content-folder').classList.add('error');
            isValid = false;
        }
    }

    // 验证预览图片
    const previewImage = document.getElementById('preview-image').value.trim();
    if (!previewImage) {
        errorMessages.push(window.t ? window.t('steam.previewImageRequired') : '请上传预览图片');
        document.getElementById('preview-image').classList.add('error');
        isValid = false;
    } else {
        // 验证图片格式
        const imageExtRegex = /\.(jpg|jpeg|png)$/i;
        if (!imageExtRegex.test(previewImage)) {
            errorMessages.push(window.t ? window.t('steam.previewImageFormat') : '预览图片格式必须为PNG、JPG或JPEG');
            document.getElementById('preview-image').classList.add('error');
            isValid = false;
        } else {
            document.getElementById('preview-image').classList.remove('error');
        }
    }

    // 显示验证错误消息
    if (errorMessages.length > 0) {
        showMessage(errorMessages.join('\n'), 'error', 5000);
    }

    return isValid;
}

// 禁用/启用按钮函数
function setButtonState(buttonElement, isDisabled) {
    if (buttonElement) {
        buttonElement.disabled = isDisabled;
        if (isDisabled) {
            buttonElement.classList.add('button-disabled');
        } else {
            buttonElement.classList.remove('button-disabled');
        }
    }
}

function sanitizeWorkshopVoicePrefix(value, fallback = 'voice') {
    const normalized = String(value || '').replace(/[^a-zA-Z0-9]/g, '').slice(0, 10);
    if (normalized) return normalized;
    const fallbackNormalized = String(fallback || '').replace(/[^a-zA-Z0-9]/g, '').slice(0, 10);
    return fallbackNormalized || 'voice';
}

function normalizeWorkshopTempPath(path) {
    return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '');
}

function getSelectedReferenceAudioFile() {
    const fileInput = document.getElementById('voice-reference-file');
    return fileInput && fileInput.files && fileInput.files.length ? fileInput.files[0] : null;
}

function updateReferenceAudioDisplay() {
    const fileNameDisplay = document.getElementById('voice-reference-file-name');
    const selectedFile = getSelectedReferenceAudioFile();
    if (!fileNameDisplay) return;
    fileNameDisplay.textContent = selectedFile
        ? selectedFile.name
        : (window.t ? window.t('steam.voiceReferenceNoFileSelected') : '未选择文件');
}

function clearReferenceAudioSelection() {
    const fileInput = document.getElementById('voice-reference-file');
    if (fileInput) {
        fileInput.value = '';
    }
    updateReferenceAudioDisplay();
}

function selectReferenceAudio() {
    const fileInput = document.getElementById('voice-reference-file');
    if (!fileInput) return;

    fileInput.onchange = function (e) {
        const selectedFile = e.target.files && e.target.files[0];
        if (!selectedFile) {
            updateReferenceAudioDisplay();
            return;
        }

        const validExtension = /\.(mp3|wav)$/i.test(selectedFile.name);
        if (!validExtension) {
            showMessage('参考语音只支持 mp3 或 wav 格式', 'error');
            clearReferenceAudioSelection();
            return;
        }

        const maxSize = 20 * 1024 * 1024;
        if (selectedFile.size > maxSize) {
            showMessage('参考语音大小不能超过 20MB', 'error');
            clearReferenceAudioSelection();
            return;
        }

        const itemTitle = document.getElementById('item-title')?.textContent.trim() || 'voice';
        const prefixInput = document.getElementById('voice-reference-prefix');
        const displayNameInput = document.getElementById('voice-reference-display-name');
        if (prefixInput && !prefixInput.value.trim()) {
            prefixInput.value = sanitizeWorkshopVoicePrefix(itemTitle, 'voice');
        }
        if (displayNameInput && !displayNameInput.value.trim()) {
            displayNameInput.value = itemTitle;
        }
        updateReferenceAudioDisplay();
    };

    fileInput.click();
}

function resetWorkshopVoiceReferenceFields(defaultTitle = '') {
    const displayNameInput = document.getElementById('voice-reference-display-name');
    const prefixInput = document.getElementById('voice-reference-prefix');
    const languageSelect = document.getElementById('voice-reference-language');
    const providerSelect = document.getElementById('voice-reference-provider-hint');

    clearReferenceAudioSelection();
    if (displayNameInput) displayNameInput.value = defaultTitle || '';
    if (prefixInput) prefixInput.value = sanitizeWorkshopVoicePrefix(defaultTitle, 'voice');
    if (languageSelect) languageSelect.value = 'ch';
    if (providerSelect) providerSelect.value = 'cosyvoice';
}

async function uploadWorkshopReferenceAudio(contentFolder, defaultTitle) {
    const selectedFile = getSelectedReferenceAudioFile();
    if (!selectedFile) return null;

    const prefixInput = document.getElementById('voice-reference-prefix');
    const displayNameInput = document.getElementById('voice-reference-display-name');
    const languageSelect = document.getElementById('voice-reference-language');
    const providerSelect = document.getElementById('voice-reference-provider-hint');

    const prefix = sanitizeWorkshopVoicePrefix(prefixInput?.value, defaultTitle || 'voice');
    if (prefixInput) {
        prefixInput.value = prefix;
    }

    const formData = new FormData();
    formData.append('file', selectedFile, selectedFile.name);
    formData.append('content_folder', contentFolder);
    formData.append('prefix', prefix);
    formData.append('display_name', displayNameInput?.value.trim() || defaultTitle || prefix);
    formData.append('ref_language', languageSelect?.value || 'ch');
    formData.append('provider_hint', providerSelect?.value || 'cosyvoice');

    showMessage('正在写入参考语音...', 'info');
    const response = await fetch('/api/steam/workshop/upload-reference-audio', {
        method: 'POST',
        body: formData
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
        throw new Error(data.error || '参考语音上传失败');
    }
    return data;
}

async function removeWorkshopReferenceAudio(contentFolder) {
    const response = await fetch('/api/steam/workshop/remove-reference-audio', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ content_folder: contentFolder })
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
        throw new Error(data.error || '参考语音清理失败');
    }
    return data;
}

// 上传物品功能
function uploadItem() {
    // 检查是否为默认模型
    if (isDefaultModel()) {
        showMessage(window.t ? window.t('steam.defaultModelCannotUpload') : '默认模型无法上传到创意工坊', 'error');
        return;
    }
    // 获取路径
    let contentFolder = document.getElementById('content-folder').value.trim();
    let previewImage = document.getElementById('preview-image').value.trim();

    if (!contentFolder) {
        showMessage(window.t ? window.t('steam.enterContentFolderPath') : '请输入内容文件夹路径', 'error');
        document.getElementById('content-folder').focus();
        return;
    }

    // 增强的路径规范化处理
    contentFolder = contentFolder.replace(/\\/g, '/');
    if (previewImage) {
        previewImage = previewImage.replace(/\\/g, '/');
    }

    // 显示路径验证通知
    showMessage(window.t ? window.t('steam.validatingFolderPath', { path: contentFolder }) : `正在验证文件夹路径: ${contentFolder}`, 'info');

    // 如果没有预览图片，仍然允许继续上传，后端会尝试自动查找或使用默认机制
    if (!previewImage) {
        showMessage(window.t ? window.t('steam.previewImageNotProvided') : '未提供预览图片，系统将尝试自动生成', 'warning');
    }

    // 验证表单
    if (!validateForm()) {
        return;
    }

    // 收集表单数据（title 和 description 现在是 div 元素，使用 textContent）
    const title = document.getElementById('item-title')?.textContent.trim() || '';
    const description = document.getElementById('item-description')?.textContent.trim() || '';
    // 内容文件夹和预览图片路径已经在上面定义过了，不再重复定义
    const visibilitySelect = document.getElementById('visibility');
    const allowComments = document.getElementById('allow-comments')?.checked || false;

    // 收集标签（包括锁定的标签）
    let tags = [];
    const tagElements = document.querySelectorAll('#tags-container .tag');
    if (tagElements && tagElements.length > 0) {
        tags = Array.from(tagElements)
            .filter(tag => tag && tag.textContent)
            .map(tag => tag.textContent.replace('×', '').replace('🔒', '').trim())
            .filter(tag => tag); // 过滤空标签
    }

    // 转换可见性选项为数值
    let visibility = 0; // 默认公开
    if (visibilitySelect) {
        const value = visibilitySelect.value;
        if (value === 'friends') {
            visibility = 1;
        } else if (value === 'private') {
            visibility = 2;
        }
    }

    // 获取角色卡名称（用于更新 .workshop_meta.json）
    const characterCardName = document.getElementById('character-card-name')?.value.trim() || '';

    // 准备上传数据
    const uploadData = {
        title: title,
        description: description,
        content_folder: contentFolder,
        preview_image: previewImage,
        visibility: visibility,
        tags: tags,
        allow_comments: allowComments,
        character_card_name: characterCardName  // 传递角色卡名称，用于更新 .workshop_meta.json
    };

    // 获取上传按钮并禁用
    const uploadButton = document.querySelector('#uploadToWorkshopModal button.btn-primary');
    let originalText = '';
    if (uploadButton) {
        originalText = uploadButton.textContent || '';
        uploadButton.textContent = window.t ? window.t('common.loading') : 'Uploading...';
        setButtonState(uploadButton, true);
    }

    // 显示上传中消息
    showMessage(window.t ? window.t('steam.preparingUpload') : '正在准备上传...', 'success', 0); // 0表示不自动关闭

    const selectedReferenceAudio = getSelectedReferenceAudioFile();
    const isManagedWorkshopTempFolder =
        normalizeWorkshopTempPath(contentFolder) &&
        normalizeWorkshopTempPath(currentUploadTempFolder) &&
        normalizeWorkshopTempPath(contentFolder) === normalizeWorkshopTempPath(currentUploadTempFolder);

    let voiceReferenceSyncPromise = Promise.resolve(null);
    if (isManagedWorkshopTempFolder) {
        voiceReferenceSyncPromise = selectedReferenceAudio
            ? uploadWorkshopReferenceAudio(contentFolder, title || characterCardName || 'voice')
            : removeWorkshopReferenceAudio(contentFolder);
    } else if (selectedReferenceAudio) {
        showMessage('参考语音当前仅支持角色卡打包后的工坊临时目录上传，已跳过该样本。', 'warning', 6000);
    }

    // 发送API请求
    voiceReferenceSyncPromise
        .then(() => fetch('/api/steam/workshop/publish', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(uploadData)
        }))
        .then(async response => {
            const data = await response.json().catch(() => null);
            if (!response.ok) {
                throw new Error(data?.message || data?.error || `HTTP错误，状态码: ${response.status}`);
            }
            return data;
        })
        .then(data => {
            // 恢复按钮状态
            if (uploadButton) {
                uploadButton.textContent = originalText;
                setButtonState(uploadButton, false);
            }

            // 清除所有现有消息
            const messageArea = document.getElementById('message-area');
            if (messageArea) {
                messageArea.innerHTML = '';
            }

            if (data.success) {
                // 标记上传已完成
                isUploadCompleted = true;

                showMessage(window.t ? window.t('steam.uploadSuccess') : '上传成功！', 'success', 5000);

                // 显示物品ID
                if (data.published_file_id) {
                    showMessage(window.t ? window.t('steam.itemIdDisplay', { itemId: data.published_file_id }) : `物品ID: ${data.published_file_id}`, 'success', 5000);

                    // 上传成功后，自动删除临时目录
                    if (currentUploadTempFolder) {
                        cleanupTempFolder(currentUploadTempFolder, true);
                    }

                    // 使用Steam overlay打开物品页面
                    try {
                        const published_id = data.published_file_id;
                        const overlayUrl = `steam://url/CommunityFilePage/${published_id}`;
                        const webUrl = `https://steamcommunity.com/sharedfiles/filedetails/?id=${published_id}`;

                        // 检查是否支持Steam overlay
                        if (window.steam && typeof window.steam.ActivateGameOverlayToWebPage === 'function') {
                            window.steam.ActivateGameOverlayToWebPage(overlayUrl);
                        } else {
                            // Electron / 嵌入浏览器环境下直接打开 steam:// 可能导致窗口异常，回退到网页链接
                            window.open(webUrl, '_blank', 'noopener');
                        }
                    } catch (e) {
                        console.error('无法打开Steam overlay:', e);
                    }

                    // 延迟关闭modal并跳转到角色卡页面
                    setTimeout(() => {
                        // 关闭上传modal
                        const uploadModal = document.getElementById('uploadToWorkshopModal');
                        if (uploadModal) {
                            uploadModal.style.display = 'none';
                        }
                        // 重置状态
                        currentUploadTempFolder = null;
                        isUploadCompleted = false;
                        // 跳转到角色卡页面
                        switchTab('character-cards-content');
                    }, 2000); // 2秒后关闭并跳转
                }

                // 如果需要接受协议
                if (data.needs_to_accept_agreement) {
                    showMessage(window.t ? window.t('steam.workshopAgreementRequired') : '请先同意Steam Workshop使用协议', 'warning', 8000);
                }

                // 清空表单（title 和 description 现在是 div 元素，使用 textContent）
                const formElements = [
                    { id: 'item-title', property: 'textContent', value: '' },
                    { id: 'item-description', property: 'textContent', value: '' },
                    { id: 'content-folder', property: 'value', value: '' },
                    { id: 'preview-image', property: 'value', value: '' },
                    { id: 'voice-reference-display-name', property: 'value', value: '' },
                    { id: 'voice-reference-prefix', property: 'value', value: '' },
                    { id: 'voice-reference-language', property: 'value', value: 'ch' },
                    { id: 'voice-reference-provider-hint', property: 'value', value: 'cosyvoice' },
                    { id: 'visibility', property: 'value', value: 'public' },
                    { id: 'allow-comments', property: 'checked', value: true }
                ];

                formElements.forEach(element => {
                    const el = document.getElementById(element.id);
                    if (el) {
                        el[element.property] = element.value;
                    }
                });
                clearReferenceAudioSelection();

                // 清空标签
                const tagsContainer = document.getElementById('tags-container');
                if (tagsContainer) {
                    tagsContainer.innerHTML = '';
                }

                // 添加默认标签
                    addTag(window.t ? window.t('steam.defaultTagMod') : '模组');

                // 显示成功提示和操作选项
                setTimeout(() => {
                    const messageArea = document.getElementById('message-area');
                    const actionMessage = document.createElement('div');
                    actionMessage.className = 'success-message';
                    actionMessage.innerHTML = `
                    <span>${window.t ? window.t('steam.operationComplete') : 'Operation complete, you can:'}</span>
                    <button class="button button-sm" onclick="closeUploadModal()">${window.t ? window.t('steam.hideUploadSection') : 'Hide Upload Section'}</button>
                    <span class="message-close" onclick="this.parentElement.remove()">×</span>
                `;
                    messageArea.appendChild(actionMessage);
                }, 1000);
            } else {
                // 上传失败，重置上传完成标志
                isUploadCompleted = false;
                showMessage(window.t ? window.t('steam.uploadError', { error: data.error || (window.t ? window.t('common.unknownError') : '未知错误') }) : `上传失败: ${data.error || '未知错误'}`, 'error', 8000);
                if (data.message) {
                    showMessage(window.t ? window.t('steam.uploadWarning', { message: data.message }) : `警告: ${data.message}`, 'warning', 8000);
                }

                // 提供重试建议
                setTimeout(() => {
                    const retryButton = document.createElement('button');
                    retryButton.className = 'button button-sm';
                    retryButton.textContent = window.t ? window.t('steam.retryUpload') : '重试上传';
                    retryButton.onclick = uploadItem;

                    const messageArea = document.getElementById('message-area');
                    const retryMessage = document.createElement('div');
                    retryMessage.className = 'error-message';
                    retryMessage.innerHTML = `<span>${window.t ? window.t('steam.retryPrompt') : 'Would you like to retry the upload?'}</span>
                    <button class="button button-sm" onclick="uploadItem()">${window.t ? window.t('steam.retryUpload') : 'Retry Upload'}</button>
                    <span class="message-close" onclick="this.parentElement.remove()">×</span>`;
                    messageArea.appendChild(retryMessage);
                }, 2000);
            }
        })
        .catch(error => {
            console.error('上传失败:', error);

            // 上传失败，重置上传完成标志
            isUploadCompleted = false;

            // 恢复按钮状态
            if (uploadButton) {
                uploadButton.textContent = originalText;
                setButtonState(uploadButton, false);
            }

            // 清除所有现有消息
            const messageArea = document.getElementById('message-area');
            if (messageArea) {
                messageArea.innerHTML = '';
            }

            let errorMessage = window.t ? window.t('steam.uploadGeneralError') : '上传失败';

            // 根据错误类型提供更具体的提示
            if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
                errorMessage = window.t ? window.t('steam.uploadNetworkError') : '网络错误，请检查您的连接';
                showMessage(window.t ? window.t('steam.uploadErrorFormat', { message: errorMessage }) : errorMessage, 'error', 8000);
                showMessage(window.t ? window.t('steam.checkNetworkConnection') : '请检查您的网络连接', 'warning', 8000);
            } else if (error.message.includes('HTTP错误')) {
                errorMessage = window.t ? window.t('steam.uploadHttpError', { error: error.message }) : `HTTP错误: ${error.message}`;
                showMessage(window.t ? window.t('steam.uploadErrorFormat', { message: errorMessage }) : errorMessage, 'error', 8000);
                showMessage(window.t ? window.t('steam.serverProblem', { message: window.t ? window.t('common.tryAgainLater') : '请稍后重试' }) : '服务器问题，请稍后重试', 'warning', 8000);
            } else {
                showMessage(window.t ? window.t('steam.uploadErrorFormat', { message: window.t ? window.t('steam.uploadErrorWithMessage', { error: error.message }) : `错误: ${error.message}` }) : `错误: ${error.message}`, 'error', 8000);
            }
        });
}

// 分页相关变量
let allSubscriptions = []; // 存储所有订阅物品
let currentPage = 1;
let itemsPerPage = 10;
let totalPages = 1;
let currentSortField = 'timeAdded'; // 默认按添加时间排序
let currentSortOrder = 'desc'; // 默认降序

function getWorkshopManagerLanlanName() {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('lanlan_name') || '';
}

function openWorkshopVoiceClone(itemId) {
    const params = new URLSearchParams({
        workshop_item_id: String(itemId),
        source: 'workshop'
    });
    const lanlanName = getWorkshopManagerLanlanName();
    if (lanlanName) {
        params.set('lanlan_name', lanlanName);
    }

    const url = `/voice_clone?${params.toString()}`;
    const popup = window.open(url, `workshopVoiceClone_${itemId}`, 'width=920,height=860,scrollbars=yes,resizable=yes');
    if (!popup) {
        window.location.href = url;
    }
}

// escapeHtml 已在上方定义（DOM-based，非 string 走 String(text) 转换）

// 安全获取作者显示名（始终返回字符串，兼容 item 为 null/undefined）
function safeAuthorName(item) {
    const raw = item?.authorName || (item?.steamIDOwner != null ? String(item.steamIDOwner) : '');
    return String(raw) || (window.t ? window.t('steam.unknownAuthor') : '未知作者');
}

// 加载订阅物品
function loadSubscriptions() {
    const subscriptionsList = document.getElementById('subscriptions-list');
    subscriptionsList.innerHTML = `<div class="empty-state"><p>${window.t ? window.t('steam.loadingSubscriptions') : '正在加载您的订阅物品...'}</p></div>`;

    // 调用后端API获取订阅物品列表
    fetch('/api/steam/workshop/subscribed-items')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (!data.success) {
                subscriptionsList.innerHTML = `<div class="empty-state"><p>${window.t ? window.t('steam.fetchFailed') : 'Failed to fetch subscribed items'}: ${data.error || (window.t ? window.t('common.unknownError') : 'Unknown error')}</p></div>`;
                // 如果有消息提示，显示给用户
                if (data.message) {
                    showMessage(data.message, 'error');
                }
                updatePagination(); // 更新分页状态
                return;
            }

            // 保存所有订阅物品到全局变量
            allSubscriptions = data.items || [];

            // 【成就】有订阅物品时解锁创意工坊成就
            if (allSubscriptions.length > 0) {
                if (window.parent && window.parent.unlockAchievement) {
                    window.parent.unlockAchievement('ACH_WORKSHOP_USE').catch(err => {
                        console.error('解锁创意工坊成就失败:', err);
                    });
                } else if (window.opener && window.opener.unlockAchievement) {
                    window.opener.unlockAchievement('ACH_WORKSHOP_USE').catch(err => {
                        console.error('解锁创意工坊成就失败:', err);
                    });
                } else if (window.unlockAchievement) {
                    window.unlockAchievement('ACH_WORKSHOP_USE').catch(err => {
                        console.error('解锁创意工坊成就失败:', err);
                    });
                }
            }

            // 应用排序（从下拉框获取排序方式）
            const sortSelect = document.getElementById('sort-subscription');
            if (sortSelect) {
                const [field, order] = sortSelect.value.split('_');
                sortSubscriptions(field, order);
            } else {
                // 默认按日期降序排序
                sortSubscriptions('date', 'desc');
            }

            // 计算总页数
            totalPages = Math.ceil(allSubscriptions.length / itemsPerPage);
            if (totalPages < 1) totalPages = 1;
            if (currentPage > totalPages) currentPage = totalPages;

            // 显示当前页的数据
            renderSubscriptionsPage();

            // 更新分页UI
            updatePagination();
        })
        .catch(error => {
            console.error('获取订阅物品失败:', error);
            subscriptionsList.innerHTML = `<div class="empty-state"><p>${window.t ? window.t('steam.fetchFailed') : '获取订阅物品失败'}: ${error.message}</p></div>`;
            showMessage(window.t ? window.t('steam.cannotConnectToServer') : '无法连接到服务器，请稍后重试', 'error');
        });
}

// 渲染当前页的订阅物品
function renderSubscriptionsPage() {
    const subscriptionsList = document.getElementById('subscriptions-list');

    if (allSubscriptions.length === 0) {
        subscriptionsList.innerHTML = `<div class="empty-state"><p>${window.t ? window.t('steam.noSubscriptions') : 'You haven\'t subscribed to any workshop items yet'}</p></div>`;
        return;
    }

    // 计算当前页的数据范围
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const currentItems = allSubscriptions.slice(startIndex, endIndex);

    // 生成卡片HTML
    subscriptionsList.innerHTML = currentItems.map(item => {
        // 格式化物品数据为前端所需格式
        // 确保publishedFileId转换为字符串，避免类型错误
        const formattedItem = {
            id: String(item.publishedFileId),
            rawName: item.title || `${window.t ? window.t('steam.unknownItem') : '未知物品'}_${String(item.publishedFileId)}`,
            name: escapeHtml(item.title || `${window.t ? window.t('steam.unknownItem') : '未知物品'}_${String(item.publishedFileId)}`),
            author: escapeHtml(safeAuthorName(item)),
            rawAuthor: safeAuthorName(item),
            subscribedDate: item.timeAdded ? new Date(item.timeAdded * 1000).toLocaleDateString() : (window.t ? window.t('steam.unknownDate') : '未知日期'),
            lastUpdated: item.timeUpdated ? new Date(item.timeUpdated * 1000).toLocaleDateString() : (window.t ? window.t('steam.unknownDate') : '未知日期'),
            size: formatFileSize(item.fileSizeOnDisk || item.fileSize || 0),
            previewUrl: encodeURI(item.previewUrl || item.previewImageUrl || '../static/icons/Steam_icon_logo.png'),
            state: item.state || {},
            // 添加安装路径信息
            installedFolder: item.installedFolder || '',
            description: escapeHtml(item.description || (window.t ? window.t('steam.noDescription') : '暂无描述')),
            timeAdded: item.timeAdded || 0,
            fileSize: item.fileSizeOnDisk || item.fileSize || 0,
            voiceReferenceAvailable: !!item.voiceReferenceAvailable,
            voiceReferenceDisplayName: escapeHtml(item.voiceReference?.displayName || ''),
        };

        // 确定状态类和文本
        let statusClass = 'status-subscribed';
        let statusText = window.t ? window.t('steam.status.subscribed') : '已订阅';

        if (formattedItem.state.downloading) {
            statusClass = 'status-downloading';
            statusText = window.t ? window.t('steam.status.downloading') : '下载中';
        } else if (formattedItem.state.needsUpdate) {
            statusClass = 'status-needs-update';
            statusText = window.t ? window.t('steam.status.needsUpdate') : '需要更新';
        } else if (formattedItem.state.installed) {
            statusClass = 'status-installed';
            statusText = window.t ? window.t('steam.status.installed') : '已安装';
        }

        return `
            <div class="workshop-card">
                <div class="card-header">
                    <img src="${formattedItem.previewUrl}" alt="${formattedItem.name}" class="card-image" onerror="this.src='../static/icons/Steam_icon_logo.png'">
                    <div class="status-badge ${statusClass}">
                        <svg class="badge-bg" viewBox="-5 -5 115 115">
                            <path d="M6.104,38.038 C1.841,45.421 1.841,54.579 6.104,61.962 L18.785,83.923 C23.048,91.306 30.979,95.885 39.505,95.885 L64.865,95.885 C73.391,95.885 81.322,91.306 85.585,83.923 L98.266,61.962 C102.529,54.579 102.529,45.421 98.266,38.038 L85.585,16.077 C81.322,8.694 73.391,4.115 64.865,4.115 L39.505,4.115 C30.979,4.115 23.048,8.694 18.785,16.077 Z"
                                  fill="#21b8ff"
                                  stroke="#dcf4ff"
                                  stroke-width="8" />
                        </svg>
                        <div class="badge-text">${statusText}</div>
                    </div>
                </div>
                <div class="card-content">
                    <h3 class="card-title">${formattedItem.name}<img src="/static/icons/paw_ui.png" class="card-title-paw" alt=""></h3>
                    <div class="author-info">
                        <div class="author-avatar">${escapeHtml(String(formattedItem.rawAuthor).substring(0, 2).toUpperCase())}</div>
                        <span>${window.t ? window.t('steam.author') : '作者:'} ${formattedItem.author}</span>
                    </div>
                    <div class="card-info-grid">
                        <div class="card-info-item"><span class="info-label">${window.t ? window.t('steam.subscribed_date') : '订阅日期:'}</span> <span class="info-value">${formattedItem.subscribedDate}</span></div>
                        <div class="card-info-item"><span class="info-label">${window.t ? window.t('steam.last_updated') : '上次更新:'}</span> <span class="info-value">${formattedItem.lastUpdated}</span></div>
                        <div class="card-info-item"><span class="info-label">${window.t ? window.t('steam.size') : '大小:'}</span> <span class="info-value">${formattedItem.size}</span></div>
                    </div>
                    ${formattedItem.state && formattedItem.state.downloading && item.downloadProgress ?
                `<div class="download-progress">
                            <div class="progress-bar">
                                <div class="progress-fill" style="width: ${item.downloadProgress.percentage}%">
                                    ${item.downloadProgress.percentage.toFixed(1)}%
                                </div>
                            </div>
                        </div>` : ''
            }
                    <div class="card-actions">
                        ${formattedItem.voiceReferenceAvailable ? `
                        <button class="button button-primary" onclick="openWorkshopVoiceClone('${formattedItem.id}')" title="${formattedItem.voiceReferenceDisplayName || ''}" style="margin-bottom: 8px;">
                            ${window.t ? window.t('steam.openVoiceClone') : '在语音克隆页打开'}
                        </button>` : ''}
                        <button class="button button-danger" data-item-id="${formattedItem.id}" data-item-name="${formattedItem.name}" onclick="unsubscribeItem(this.dataset.itemId, this.dataset.itemName)">${window.t ? window.t('steam.unsubscribe') : '取消订阅'}</button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

// 更新分页控件
function updatePagination() {
    const pagination = document.querySelector('.pagination');
    if (!pagination) return;

    const prevBtn = pagination.querySelector('.pagination-btn-wrapper:first-child button');
    const nextBtn = pagination.querySelector('.pagination-btn-wrapper:last-child button');
    const pageInfo = pagination.querySelector('span');

    // 更新页码信息
    if (pageInfo) {
        const options = { currentPage: currentPage, totalPages: totalPages };
        pageInfo.setAttribute('data-i18n-options', JSON.stringify(options));
        pageInfo.textContent = window.t ? window.t('steam.pagination', options) : `${currentPage} / ${totalPages}`;
    }

    // 更新上一页按钮状态
    if (prevBtn) {
        prevBtn.disabled = currentPage <= 1;
    }

    // 更新下一页按钮状态
    if (nextBtn) {
        nextBtn.disabled = currentPage >= totalPages;
    }
}

// 前往上一页
function goToPrevPage() {
    if (currentPage > 1) {
        currentPage--;
        renderSubscriptionsPage();
        updatePagination();
    }
}

// 前往下一页
function goToNextPage() {
    if (currentPage < totalPages) {
        currentPage++;
        renderSubscriptionsPage();
        updatePagination();
    }
}

// 排序订阅物品
function sortSubscriptions(field, order) {
    if (allSubscriptions.length <= 1) return;

    allSubscriptions.sort((a, b) => {
        let aValue, bValue;

        // 根据不同字段获取对应的值
        switch (field) {
            case 'name':
                aValue = (a.title || String(a.publishedFileId || '')).toLowerCase();
                bValue = (b.title || String(b.publishedFileId || '')).toLowerCase();
                break;
            case 'date':
                aValue = a.timeAdded || 0;
                bValue = b.timeAdded || 0;
                break;
            case 'size':
                aValue = a.fileSizeOnDisk || a.fileSize || 0;
                bValue = b.fileSizeOnDisk || b.fileSize || 0;
                break;
            case 'update':
                aValue = a.timeUpdated || 0;
                bValue = b.timeUpdated || 0;
                break;
            default:
                // 默认按名称排序
                aValue = (a.title || String(a.publishedFileId || '')).toLowerCase();
                bValue = (b.title || String(b.publishedFileId || '')).toLowerCase();
        }

        // 处理空值
        if (aValue === undefined || aValue === null) aValue = '';
        if (bValue === undefined || bValue === null) bValue = '';

        // 字符串比较
        if (typeof aValue === 'string') {
            return order === 'asc' ?
                aValue.localeCompare(bValue) :
                bValue.localeCompare(aValue);
        }
        // 数字比较
        return order === 'asc' ?
            (aValue - bValue) :
            (bValue - aValue);
    });
}

// 应用排序
function applySort(sortValue) {
    // 解析排序值
    const [field, order] = sortValue.split('_');

    // 重置到第一页
    currentPage = 1;

    // 应用排序
    sortSubscriptions(field, order);

    // 重新渲染页面
    renderSubscriptionsPage();

    // 更新分页
    updatePagination();
}

// 过滤订阅物品
function filterSubscriptions(searchTerm) {
    // 简单实现过滤功能
    searchTerm = searchTerm.toLowerCase().trim();

    // 保存原始数据
    if (window.originalSubscriptions === undefined) {
        window.originalSubscriptions = [...allSubscriptions];
    }

    // 如果搜索词为空，恢复原始数据
    if (!searchTerm) {
        if (window.originalSubscriptions) {
            allSubscriptions = [...window.originalSubscriptions];
        }
        // 重新应用当前排序
        const sortSelect = document.getElementById('sort-subscription');
        if (sortSelect) {
            applySort(sortSelect.value);
        }
        return;
    }

    // 过滤物品
    let itemsToFilter = window.originalSubscriptions || [...allSubscriptions];
    const filteredItems = itemsToFilter.filter(item => {
        const title = (item.title || '').toLowerCase();
        return title.includes(searchTerm);
    });

    allSubscriptions = filteredItems;

    // 重新计算分页
    totalPages = Math.ceil(allSubscriptions.length / itemsPerPage);
    if (totalPages < 1) totalPages = 1;
    if (currentPage > totalPages) currentPage = totalPages;

    // 渲染过滤后的结果
    renderSubscriptionsPage();
    updatePagination();
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0 || bytes === undefined) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 获取状态文本
function getStatusText(state) {
    if (state.downloading) {
        return window.t ? window.t('steam.status.downloading') : '下载中';
    } else if (state.needsUpdate) {
        return window.t ? window.t('steam.status.needsUpdate') : '需要更新';
    } else if (state.installed) {
        return window.t ? window.t('steam.status.installed') : '已安装';
    } else if (state.subscribed) {
        return window.t ? window.t('steam.status.subscribed') : '已订阅';
    } else {
        return window.t ? window.t('steam.status.unknown') : '未知';
    }
}

// 打开模态框
function openModal() {
    const modal = document.getElementById('itemDetailsModal');
    modal.style.display = 'flex';
    // 阻止页面滚动
    document.documentElement.style.overflowY = 'hidden';
}

// 关闭模态框
function closeModal() {
    const modal = document.getElementById('itemDetailsModal');
    modal.style.display = 'none';
    // 恢复页面滚动
    document.documentElement.style.overflowY = '';
}

// 点击模态框外部关闭
function closeModalOnOutsideClick(event) {
    const modal = document.getElementById('itemDetailsModal');
    if (event.target === modal) {
        closeModal();
    }
}


// 查看物品详情
function viewItemDetails(itemId) {
    // 显示加载消息
    showMessage(window.t ? window.t('steam.loadingItemDetailsById', { id: itemId }) : `正在加载物品ID: ${itemId} 的详细信息...`, 'success');

    // 调用后端API获取物品详情
    fetch(`/api/steam/workshop/item/${itemId}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (!data.success) {
                showMessage(window.t ? window.t('steam.getItemDetailsFailedWithError', { error: data.error || (window.t ? window.t('common.unknownError') : '未知错误') }) : `获取物品详情失败: ${data.error || '未知错误'}`, 'error');
                return;
            }

            const item = data.item;
            const formattedItem = {
                id: item.publishedFileId.toString(),
                name: item.title,
                author: escapeHtml(safeAuthorName(item)),
                rawAuthor: safeAuthorName(item),
                subscribedDate: new Date(item.timeAdded * 1000).toLocaleDateString(),
                lastUpdated: new Date(item.timeUpdated * 1000).toLocaleDateString(),
                size: formatFileSize(item.fileSize),
                previewUrl: item.previewUrl || item.previewImageUrl || '../static/icons/Steam_icon_logo.png',
                description: escapeHtml(item.description || (window.t ? window.t('steam.noDescription') : '暂无描述')),
                downloadCount: 'N/A',
                rating: 'N/A',
                tags: [window.t ? window.t('steam.defaultTagMod') : '模组'], // 默认标签，实际应用中应该从API获取
                state: item.state || {} // 添加state属性，确保后续代码可以正常访问
            };

            // 确定状态类和文本
            let statusClass = 'status-subscribed';
            let statusText = getStatusText(formattedItem.state || {});

            if (formattedItem.state && formattedItem.state.downloading) {
                statusClass = 'status-downloading';
            } else if (formattedItem.state && formattedItem.state.needsUpdate) {
                statusClass = 'status-needs-update';
            } else if (formattedItem.state && formattedItem.state.installed) {
                statusClass = 'status-installed';
            }

            // 获取作者头像（使用首字母作为占位符）
            const authorInitial = escapeHtml(String(formattedItem.rawAuthor).substring(0, 2).toUpperCase());

            // 更新模态框内容
            document.getElementById('modalTitle').textContent = formattedItem.name;

            const detailContent = document.getElementById('itemDetailContent');
            detailContent.innerHTML = `
            <img src="${formattedItem.previewUrl}" alt="${formattedItem.name}" class="item-preview-large" onerror="this.src='../static/icons/Steam_icon_logo.png'">

            <div class="item-info-grid">
                <p class="item-info-item">
                    <span class="item-info-label">${window.t ? window.t('steam.author') : '作者:'}</span>
                    <div class="author-info">
                        <div class="author-avatar">${authorInitial}</div>
                        <span>${formattedItem.author}</span>
                    </div>
                </p>
                <p class="item-info-item"><span class="item-info-label">${window.t ? window.t('steam.subscribed_date') : '订阅日期:'}</span> ${formattedItem.subscribedDate}</p>
                <p class="item-info-item"><span class="item-info-label">${window.t ? window.t('steam.last_updated') : '上次更新:'}</span> ${formattedItem.lastUpdated}</p>
                <p class="item-info-item"><span class="item-info-label">${window.t ? window.t('steam.size') : '大小:'}</span> ${formattedItem.size}</p>
                <p class="item-info-item">
                    <span class="item-info-label">${window.t ? window.t('steam.status_label') : '状态:'}</span>
                    <span class="status-badge ${statusClass}">${statusText}</span>
                </p>
                <p class="item-info-item"><span class="item-info-label">${window.t ? window.t('steam.download_count') : '下载次数:'}</span> ${formattedItem.downloadCount}</p>
                ${formattedItem.state && formattedItem.state.downloading && item.downloadProgress ?
                    `<p class="item-info-item" style="grid-column: span 2;">
                        <div class="download-progress">
                            <div class="progress-bar">
                                <div class="progress-fill" style="width: ${item.downloadProgress.percentage}%">
                                    ${item.downloadProgress.percentage.toFixed(1)}%
                                </div>
                            </div>
                        </div>
                    </p>` : ''
                }
            </div>

            <div>
                <h4>${window.t ? window.t('steam.tags') : '标签'}</h4>
                <div class="tags-container">
                    ${formattedItem.tags.map(tag => `
                        <div class="tag">${tag}</div>
                    `).join('')}
                </div>
            </div>

            <div>
                <h4>${window.t ? window.t('steam.description') : '描述'}</h4>
                <p class="item-description">${formattedItem.description}</p>
            </div>
        `;

            // 打开模态框
            openModal();
        })
        .catch(error => {
            console.error('获取物品详情失败:', error);
            showMessage(window.t ? window.t('steam.cannotLoadItemDetails') : '无法加载物品详情', 'error');
        });
}

// 取消订阅功能
function unsubscribeItem(itemId, itemName) {
    if (!confirm(window.t ? window.t('steam.unsubscribeConfirm', { name: itemName }) : `确定要取消订阅 "${itemName}" 吗？`)) {
        return;
    }

    // 查找当前卡片并添加移除动画效果（用于回滚）
    let pendingCard = null;
    const cards = document.querySelectorAll('.workshop-card');
    for (let card of cards) {
        const cardTitleEl = card.querySelector('.card-title');
        if (cardTitleEl && cardTitleEl.textContent === itemName) {
            pendingCard = card;
            card.style.opacity = '0.6';
            card.style.transform = 'scale(0.95)';
            break;
        }
    }

    const restoreCard = () => {
        if (pendingCard) {
            pendingCard.style.opacity = '';
            pendingCard.style.transform = '';
        }
    };

    // 调用后端API执行取消订阅操作
    showMessage(window.t ? window.t('steam.cancellingSubscription', { name: itemName }) : `Cancelling subscription to "${itemName}"...`, 'success');

    fetch('/api/steam/workshop/unsubscribe', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ item_id: itemId })
    })
        .then(async response => {
            // 统一解析响应体，即使非 2xx 也尝试读取 JSON，以便展示后端的 error/message
            let data = null;
            try {
                data = await response.json();
            } catch (_) {
                data = null;
            }
            return { response, data };
        })
        .then(({ response, data }) => {
            // 诊断日志：只记录状态/计数，避免把 cleanup_summary 里的本地路径 /
            // 角色名直接落到浏览器或 Electron 日志里，泄露用户信息。
            const summaryForLog = data && data.cleanup_summary ? data.cleanup_summary : {};
            console.info('[unsubscribe response]', {
                status: response.status,
                ok: response.ok,
                success: !!(data && data.success),
                code: data && data.code,
                status_text: data && data.status,
                has_cleanup_summary: !!(data && data.cleanup_summary),
                cleaned_count: Array.isArray(summaryForLog.cleaned_characters) ? summaryForLog.cleaned_characters.length : 0,
                removed_memory_count: Array.isArray(summaryForLog.removed_memory_paths) ? summaryForLog.removed_memory_paths.length : 0,
                error_count: Array.isArray(summaryForLog.errors) ? summaryForLog.errors.length : 0,
            });
            if (!response.ok) {
                // 后端前置校验失败：按 code 映射到本地化 key，避免把后端
                // 硬编码的中文 error 文案直接甩给英文/繁中用户。
                const code = data && data.code;
                if (code === 'CURRENT_CATGIRL_IN_USE') {
                    const characterName = data.character_name || itemName;
                    const blockedMsg = (window.t ? window.t('steam.unsubscribeCurrentCatgirlBlocked', { name: characterName }) : '') || data.error || `不能取消订阅当前正在使用的猫娘「${characterName}」，请先切换到其他角色后再取消订阅。`;
                    // 优先使用 toast；同时用 alert 兜底，确保在 toast 被其它高层 overlay
                    // 遮挡时用户仍能看到阻断原因（这是阻断性 action，用户必须知情）
                    showMessage(blockedMsg, 'warning', 6000);
                    try { window.alert(blockedMsg); } catch (_) { /* 忽略 alert 被禁用 */ }
                    restoreCard();
                    return;
                }
                if (code === 'LOCAL_CONFIG_CLEANUP_FAILED') {
                    const msg = (window.t ? window.t('steam.unsubscribeLocalConfigCleanupFailed') : '') || data.error || '本地角色配置清理失败，已取消本次 Steam 退订请求，请修复后重试。';
                    showMessage(msg, 'error', 8000);
                    try { window.alert(msg); } catch (_) { /* ignore */ }
                    restoreCard();
                    return;
                }
                if (code === 'STEAM_UNSUBSCRIBE_FAILED') {
                    const detail = (data && data.error) || `HTTP ${response.status}`;
                    const msg = (window.t ? window.t('steam.unsubscribeSteamRequestFailed', { error: detail }) : '') || `Steam 退订请求发送失败: ${detail}`;
                    showMessage(msg, 'error', 8000);
                    restoreCard();
                    return;
                }
                const errorMsg = (data && (data.error || data.message)) || `HTTP ${response.status}`;
                showMessage(`${window.t ? window.t('steam.unsubscribeFailed') : '取消订阅失败'}: ${errorMsg}`, 'error');
                restoreCard();
                return;
            }

            if (data && data.success) {
                // 显示异步操作状态
                let statusMessage = window.t ? window.t('steam.unsubscribeAccepted', { name: itemName }) : `已接受取消订阅: ${itemName}`;
                if (data.status === 'accepted') {
                    statusMessage = window.t ? window.t('steam.unsubscribeProcessing', { name: itemName }) : `正在处理取消订阅: ${itemName}`;
                }
                showMessage(statusMessage, 'success');

                // 同步清理汇总：让用户直接看到"角色卡和记忆删了多少"（诊断价值）
                const summary = data.cleanup_summary || {};
                const cleanedChars = Array.isArray(summary.cleaned_characters) ? summary.cleaned_characters : [];
                const removedPaths = Array.isArray(summary.removed_memory_paths) ? summary.removed_memory_paths : [];
                const errors = Array.isArray(summary.errors) ? summary.errors : [];

                if (cleanedChars.length > 0 || removedPaths.length > 0) {
                    const charactersStr = cleanedChars.join('、') || '-';
                    const detailMsg = (window.t ? window.t('steam.unsubscribeCleanupDetail', {
                        characterCount: cleanedChars.length,
                        characters: charactersStr,
                        memoryPathCount: removedPaths.length,
                    }) : '') || `已清理角色卡: ${cleanedChars.length} 个（${charactersStr}）；已删除记忆路径: ${removedPaths.length} 条`;
                    showMessage(detailMsg, 'success', 6000);
                    // 只记录计数，避免 removed_memory_paths 里的本地路径被日志收集
                    console.info('[unsubscribe cleanup summary]', {
                        cleaned_count: cleanedChars.length,
                        removed_memory_count: removedPaths.length,
                        error_count: errors.length,
                    });
                } else if ((summary.candidate_characters || []).length === 0) {
                    // 后端没在 characters.json 中找到关联角色（反向索引空 + 磁盘扫描空）
                    console.warn('[unsubscribe] 未找到与该物品关联的角色，仅删除订阅文件夹');
                    const noAssocMsg = (window.t && window.t('steam.unsubscribeNoAssociation')) || '未找到与此订阅关联的角色，仅删除了订阅文件夹；若有残留记忆请手动处理';
                    showMessage(noAssocMsg, 'warning', 6000);
                }
                if (errors.length > 0) {
                    // 只记录数量和 stage，避免 error.error 里的路径 / 角色名泄露
                    console.warn('[unsubscribe cleanup errors]', {
                        count: errors.length,
                        stages: errors.map((e) => e && e.stage).filter(Boolean),
                    });
                    const firstErr = errors[0] || {};
                    const errMsg = (window.t ? window.t('steam.unsubscribeCleanupErrors', {
                        count: errors.length,
                        stage: firstErr.stage || '',
                        error: firstErr.error || '',
                    }) : '') || `清理过程出现 ${errors.length} 个错误，首个: ${firstErr.stage || ''} -> ${firstErr.error || ''}`;
                    showMessage(errMsg, 'warning', 8000);
                    try { window.alert(errMsg); } catch (_) { /* ignore */ }
                }

                // 乐观更新：立即在本地列表里剔除该条目，UI 无需等 Steam 回调即可看到
                // "已消失"的视觉反馈。即便 Steam 端还没完成剔除（后端 /subscribed-items
                // 仍可能短暂返回它），下一次 loadSubscriptions 会用后端数据覆盖。
                try {
                    if (Array.isArray(allSubscriptions)) {
                        const before = allSubscriptions.length;
                        allSubscriptions = allSubscriptions.filter(
                            (item) => String(item && item.publishedFileId) !== String(itemId)
                        );
                        if (allSubscriptions.length !== before) {
                            totalPages = Math.max(1, Math.ceil(allSubscriptions.length / itemsPerPage));
                            if (currentPage > totalPages) currentPage = totalPages;
                            renderSubscriptionsPage();
                            updatePagination();
                        }
                    }
                } catch (optErr) {
                    console.warn('[unsubscribe] 乐观更新失败，将依赖下一次 loadSubscriptions:', optErr);
                }

                // accepted 表示 Steam/后端取消订阅还在异步收敛；立即 loadSubscriptions
                // 会把刚刚乐观剔除的卡片重新拉回来。延迟一次，等 Steam 端完成剔除后再刷。
                // 其它状态（同步完成）直接刷新即可。
                if (data.status === 'accepted') {
                    setTimeout(loadSubscriptions, 1500);
                } else {
                    loadSubscriptions();
                }
            } else {
                const errorMsg = (data && (data.error || data.message)) || (window.t ? window.t('common.unknownError') : '未知错误');
                showMessage(`${window.t ? window.t('steam.unsubscribeFailed') : '取消订阅失败'}: ${errorMsg}`, 'error');
                restoreCard();
            }
        })
        .catch(error => {
            console.error('取消订阅失败:', error);
            showMessage(window.t ? window.t('steam.unsubscribeError') : '取消订阅失败', 'error');
            restoreCard();
        });
}

// 全局变量：存储所有可用模型信息
let availableModels = [];
// VRM/MMD 模型列表
let availableVrmModels = [];
let availableMmdModels = [];

// 自动扫描创意工坊角色卡并添加到系统（仅同步角色卡，不再自动注册参考语音）
async function autoScanAndAddWorkshopCharacterCards() {
    try {
        try {
            const syncResponse = await fetch('/api/steam/workshop/sync-characters', { method: 'POST' });
            if (!syncResponse.ok) {
                console.error(`[工坊同步] 服务端返回错误: HTTP ${syncResponse.status} ${syncResponse.statusText}`);
            } else {
                const syncResult = await syncResponse.json();
                if (syncResult.success) {
                    if (syncResult.added > 0) {
                        console.log(`[工坊同步] 服务端同步完成：新增 ${syncResult.added} 个角色卡，跳过 ${syncResult.skipped} 个已存在`);
                        // 刷新角色卡列表
                        loadCharacterCards();
                    } else {
                        console.log('[工坊同步] 服务端同步完成：无新增角色卡');
                    }
                } else {
                    console.error(`[工坊同步] 服务端同步失败: ${syncResult.error || '未知错误'}`, syncResult);
                }
            }
        } catch (syncError) {
            console.error('[工坊同步] 服务端角色卡同步请求失败:', syncError);
        }
    } catch (error) {
        console.error('自动扫描和添加角色卡失败:', error);
    }
}

// 扫描单个角色卡文件
async function scanCharaFile(filePath, itemId, itemTitle) {
    try {
        await ensureReservedFieldsLoaded();
        // 使用新的read-file API读取文件内容
        const readResponse = await fetch(`/api/steam/workshop/read-file?path=${encodeURIComponent(filePath)}`);
        const readResult = await readResponse.json();

        if (readResult.success) {
            // 解析文件内容
            const charaData = JSON.parse(readResult.content);

            // 档案名是必需字段，用作 characters.json 中的 key
            if (!charaData['档案名']) {
                return;
            }

            const charaName = charaData['档案名'];

            // 工坊保留字段 - 这些字段不应该从外部角色卡数据中读取
            // description/tags 及其中文版本是工坊上传时自动生成的，不属于角色卡原始数据
            // live2d_item_id 是系统自动管理的，不应该从外部数据读取
            const RESERVED_FIELDS = getWorkshopReservedFields();

            // 转换为符合catgirl API格式的数据（不包含保留字段）
            const catgirlFormat = {
                '档案名': charaName
            };

            // 跳过的字段：档案名（已处理）、保留字段
            const skipKeys = ['档案名', ...RESERVED_FIELDS];

            // 添加所有非保留字段
            for (const [key, value] of Object.entries(charaData)) {
                if (!skipKeys.includes(key) && value !== undefined && value !== null && value !== '') {
                    catgirlFormat[key] = value;
                }
            }

            // 重要：如果角色卡有 live2d 字段，需要同时保存 live2d_item_id
            // 这样首页加载时才能正确构建工坊模型的路径
            if (catgirlFormat['live2d'] && itemId) {
                catgirlFormat['live2d_item_id'] = String(itemId);
            }

            // 调用catgirl API添加到系统
            const addResponse = await fetch('/api/characters/catgirl', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(catgirlFormat)
            });

            const addResult = await addResponse.json();

            if (addResult.success) {
                // 延迟刷新角色卡列表，确保数据已保存
                setTimeout(() => {
                    loadCharacterCards();
                }, 500);
            } else {
                const errorMsg = `角色卡 ${charaName} 已存在或添加失败: ${addResult.error}`;
                console.log(errorMsg);
                showMessage(errorMsg, 'warning');
            }
        } else if (readResult.error !== '文件不存在') {
            console.error(`读取角色卡文件 ${filePath} 失败:`, readResult.error);
        }
    } catch (error) {
        if (error.message !== 'Failed to fetch') {
            console.error(`处理角色卡文件 ${filePath} 时出错:`, error);
        }
    }
}

// 检查Steam状态，未运行时弹窗提醒
async function checkSteamStatus() {
    try {
        const response = await fetch('/api/steam/workshop/status');
        if (!response.ok) return;
        const data = await response.json();
        if (data.success && !data.steamworks_initialized) {
            const title = window.t ? window.t('steam.steamNotRunningTitle') : 'Steam 未运行';
            const message = window.t ? window.t('steam.steamNotRunningMessage') : '检测到Steam客户端未运行或未登录。\n\n创意工坊功能需要Steam客户端支持，请：\n1. 下载并安装Steam客户端\n2. 启动Steam并登录您的账号\n3. 重新打开此页面';
            showAlert(message, title);
        }
    } catch (e) {
        console.error('Steam status check failed:', e);
    }
}

// 初始化页面
window.addEventListener('load', function () {
    // 检查是否需要切换到特定标签页
    const lastActiveTab = localStorage.getItem('lastActiveTab');
    if (lastActiveTab) {
        switchTab(lastActiveTab);
        // 清除存储的标签页信息
        localStorage.removeItem('lastActiveTab');
    }

    // 标签仅从后端读取，不提供手动添加功能
    // addCharacterCardTag('character-card', window.t ? window.t('steam.defaultTagCharacter') : 'Character');

    // 初始化i18n文本
    if (document.getElementById('loading-text')) {
        document.getElementById('loading-text').textContent = window.t ? window.t('steam.loadingSubscriptions') : '正在加载您的订阅物品...';
    }
    if (document.getElementById('reload-button')) {
        document.getElementById('reload-button').textContent = window.t ? window.t('steam.reload') : '重新加载';
    }
    if (document.getElementById('search-subscription')) {
        document.getElementById('search-subscription').placeholder = window.t ? window.t('steam.searchPlaceholder') : '搜索订阅内容...';
    }
    updateReferenceAudioDisplay();

    // 页面加载时自动加载订阅内容
    loadSubscriptions();

    // 页面加载时自动加载角色卡
    loadCharacterCards();

    // 页面加载时自动扫描创意工坊角色卡并添加到系统
    autoScanAndAddWorkshopCharacterCards();

    // 监听语言变化事件，刷新当前页面显示
    // 仅使用 localechange，因为 i18next languageChanged 已会触发 localechange
    function updateLocaleDependent() {
        loadSubscriptions();
        syncTitleDataText();
    }
    updateLocaleDependent();
    window.addEventListener('localechange', updateLocaleDependent);

});

// 角色卡相关函数

// 同步标题 data-text 属性（i18n 更新后伪元素需要同步）
function syncTitleDataText() {
    const titleH2 = document.querySelector('.page-title-bar h2');
    if (titleH2) {
        titleH2.setAttribute('data-text', titleH2.textContent);
    }
}

// 加载角色卡列表
// 加载角色卡数据
async function loadCharacterData() {
    try {
        const resp = await fetch('/api/characters/');
        return await resp.json();
    } catch (error) {
        console.error('加载角色数据失败:', error);
        showMessage(window.t ? window.t('steam.loadCharacterDataFailed', { error: error.message || String(error) }) : '加载角色数据失败', 'error');
        return null;
    }
}

// 全局变量：角色卡列表
let globalCharacterCards = [];

// 全局变量：当前打开的角色卡ID（用于模态框操作）
let currentCharacterCardId = null;

// 加载角色卡列表
async function loadCharacterCards() {
    // 显示加载状态
    const characterCardsList = document.getElementById('character-cards-list');
    if (characterCardsList) {
        characterCardsList.innerHTML = `
            <div class="loading-state">
                <p data-i18n="steam.loadingCharacterCards">正在加载角色卡...</p>
            </div>
        `;
    }

    // 获取角色数据
    const characterData = await loadCharacterData();
    if (!characterData) return;

    // 调用scanModels()获取可用模型列表
    await scanModels();

    // 转换角色数据为角色卡格式（定义为全局变量，供其他函数使用）
    window.characterCards = [];
    let idCounter = 1;

    // 只处理猫娘数据，忽略其他角色类型（包括主人）
    const catgirls = characterData['猫娘'] || {};
    for (const [name, data] of Object.entries(catgirls)) {
        // 兼容实际的数据结构 - 使用可用字段创建角色卡
        // 只从description或角色卡描述字段获取描述信息
        let description = window.t ? window.t('steam.noDescription') : '暂无描述';
        if (data['description']) {
            description = data['description'];
        } else if (data['描述']) {
            description = data['描述'];
        } else if (data['角色卡描述']) {
            description = data['角色卡描述'];
        }

        // 只从关键词字段获取标签信息，不自动生成标签
        let tags = [];
        if (data['关键词'] && Array.isArray(data['关键词']) && data['关键词'].length > 0) {
            tags = data['关键词'];
        }

        window.characterCards.push({
            id: idCounter++,
            name: name,
            description: description,
            tags: tags,
            rawData: data,  // 保存原始数据，方便详情页使用
            originalName: name  // 保存原始键名
        });
    }

    // 从character_cards文件夹加载角色卡
    try {
        const response = await fetch('/api/characters/character-card/list');
        if (response.ok) {
            const data = await response.json();
            if (data.success) {
                for (const card of data.character_cards) {
                    window.characterCards.push({
                        id: idCounter++,
                        name: card.name,
                        description: card.description,
                        tags: card.tags,
                        rawData: card.rawData
                    });
                }
            }
        }
    } catch (error) {
        console.error('从character_cards文件夹加载角色卡失败:', error);
    }

    // 扫描模型文件夹中的character_settings JSON文件（兼容旧格式）
    for (const model of availableModels) {
        try {
            // 调用API获取模型文件列表
            const response = await fetch(`/api/live2d/model_files/${model.name}`);
            if (response.ok) {
                const data = await response.json();
                if (data.success) {
                    // 检查是否有*.chara.json格式的角色卡文件
                    const jsonFiles = data.json_files || [];
                    const characterSettingsFiles = jsonFiles.filter(file =>
                        file.endsWith('.chara.json')
                    );

                    // 如果找到character_settings文件，解析并添加到角色卡列表
                    for (const file of characterSettingsFiles) {
                        try {
                            // 获取完整的文件内容
                            // 构建正确的文件URL - 从模型配置文件路径推断
                            const modelJsonUrl = model.path;
                            const modelRootUrl = modelJsonUrl.substring(0, modelJsonUrl.lastIndexOf('/') + 1);
                            const fileUrl = modelRootUrl + file;

                            const fileResponse = await fetch(fileUrl);
                            if (fileResponse.ok) {
                                const jsonData = await fileResponse.json();
                                // 检查是否包含"type": "character_settings"
                                if (jsonData && jsonData.type === 'character_settings') {
                                    window.characterCards.push({
                                        id: idCounter++,
                                        name: jsonData.name || `${model.name}_settings`,
                                            description: jsonData.description || (window.t ? window.t('steam.characterSettingsFile') : '角色设置文件'),
                                        tags: jsonData.tags || [],
                                        rawData: jsonData  // 保存原始数据，方便详情页使用
                                    });
                                }
                            }
                        } catch (fileError) {
                            console.error(`解析文件${file}失败:`, fileError);
                        }
                    }
                }
            }
        } catch (error) {
            console.error(`获取模型${model.name}文件列表失败:`, error);
        }
    }

    // 渲染角色卡列表（改为下拉选单）
    const characterCardSelect = document.getElementById('character-card-select');

    if (characterCardSelect) {
        // 清空现有选项（保留第一个默认选项）
        while (characterCardSelect.options.length > 1) {
            characterCardSelect.remove(1);
        }

        if (window.characterCards && window.characterCards.length > 0) {
            // 填充下拉选项
            window.characterCards.forEach(card => {
                const option = document.createElement('option');
                option.value = card.id;
                option.text = card.name;
                characterCardSelect.add(option);
            });

            // 添加change事件监听器
            characterCardSelect.onchange = function () {
                const selectedId = this.value;
                if (selectedId) {
                    // 注意：select.value 返回字符串，card.id 可能是数字或字符串，使用 == 进行宽松比较
                    const selectedCard = window.characterCards.find(c => String(c.id) === selectedId);
                    if (selectedCard) {
                        expandCharacterCardSection(selectedCard);
                    }
                }
            };

        } else {
            // 没有角色卡时，也可以保留默认选项或者显示无
        }
    }

    // 将角色卡列表保存到全局变量（已使用window.characterCards，这里保持兼容）
    globalCharacterCards = window.characterCards || [];

    // 获取当前猫娘
    try {
        const currentResp = await fetch('/api/characters/current_catgirl');
        const currentData = await currentResp.json();
        window._workshopCurrentCatgirl = currentData.current_catgirl || '';
    } catch (e) {
        window._workshopCurrentCatgirl = '';
    }

    // 预取已设置卡面的猫娘名单（避免逐个发起 404 请求）
    await loadCardFaceNames();
    // 预取卡面元数据（作者/创建时间/来源）
    await loadCardMetas();

    // 渲染卡片/列表视图
    renderCharaCardsView();

    // 显示刷新成功消息
    if (window.characterCards && window.characterCards.length > 0) {
        showMessage(window.t ? window.t('steam.characterCardsRefreshed', { count: window.characterCards.length }) : `已刷新角色卡列表，共 ${window.characterCards.length} 个角色卡`, 'success');
    } else {
        showMessage(window.t ? window.t('steam.characterCardsRefreshedEmpty') : '已刷新角色卡列表，暂无角色卡', 'info');
    }

    // 同步加载主人档案和已隐藏猫娘列表
    loadMasterProfile();
    renderHiddenCatgirls();
}

// ===== 角色卡 卡片/列表 视图 =====

// 已设置卡面的猫娘名集合（避免无卡面的 404 控制台噪声）
window._cardFaceNames = window._cardFaceNames || new Set();
async function loadCardFaceNames() {
    try {
        const resp = await fetch('/api/characters/card-faces');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data && data.success && Array.isArray(data.names)) {
            window._cardFaceNames = new Set(data.names);
        }
    } catch (e) {
        // 忽略，退化为不加载头像
    }
}

// 卡面元数据缓存 { name: { author, origin, created_at, updated_at } }
window._cardMetas = window._cardMetas || {};
async function loadCardMetas() {
    try {
        const resp = await fetch('/api/characters/card-metas');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data && data.success && data.metas && typeof data.metas === 'object') {
            window._cardMetas = data.metas;
        }
    } catch (e) {
        // 忽略，退化为面板内单独请求
    }
}

// 格式化 ISO 时间为本地化短字符串
function _formatCardMetaTime(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        return `${y}-${m}-${day} ${hh}:${mm}`;
    } catch (e) { return iso; }
}

// 渲染卡面信息块（作者、创建时间、来源）
function renderCardMetaBlock(container, name, isNew, rawData) {
    container.innerHTML = '';
    if (isNew || !name) {
        const placeholder = document.createElement('div');
        placeholder.className = 'card-meta-placeholder';
        placeholder.textContent = window.t ? window.t('character.cardNotCreated') : '尚未创建角色卡';
        container.appendChild(placeholder);
        return;
    }

    // 优先用缓存，否则惰性请求
    let meta = window._cardMetas && window._cardMetas[name];
    const draw = (m) => {
        container.innerHTML = '';
        const origin = (m && m.origin) || 'self';
        const author = (m && m.author) || '';
        const createdAt = (m && m.created_at) || '';

        const title = document.createElement('div');
        title.className = 'card-meta-title';
        title.textContent = window.t ? window.t('character.cardMeta') : '卡面信息';
        container.appendChild(title);

        // 来源徽章
        const originRow = document.createElement('div');
        originRow.className = 'card-meta-row card-meta-origin';
        const originLabel = document.createElement('span');
        originLabel.className = 'card-meta-label';
        originLabel.textContent = window.t ? window.t('character.cardOriginLabel') : '来源';
        const originValue = document.createElement('span');
        originValue.className = 'card-meta-origin-badge origin-' + origin;
        const originKey = origin === 'imported' ? 'character.cardOriginImported'
            : origin === 'steam' ? 'character.cardOriginSteam'
                : 'character.cardOriginSelf';
        const originText = window.t ? window.t(originKey) : (origin === 'imported' ? '导入' : origin === 'steam' ? '创意工坊' : '本地');
        originValue.textContent = originText;
        originRow.appendChild(originLabel);
        originRow.appendChild(originValue);
        container.appendChild(originRow);

        // 作者（可编辑：仅 origin=self）
        const authorRow = document.createElement('div');
        authorRow.className = 'card-meta-row card-meta-author';
        const authorLabel = document.createElement('span');
        authorLabel.className = 'card-meta-label';
        authorLabel.textContent = window.t ? window.t('character.cardAuthor') : '作者';
        authorRow.appendChild(authorLabel);

        if (origin === 'self') {
            const authorInput = document.createElement('input');
            authorInput.type = 'text';
            authorInput.className = 'card-meta-author-input';
            authorInput.value = author;
            authorInput.maxLength = 64;
            authorInput.placeholder = window.t ? window.t('character.cardAuthorPlaceholder') : '请输入作者';
            let saving = false;
            const saveAuthor = async () => {
                if (saving) return;
                const newVal = (authorInput.value || '').trim();
                if (newVal === (author || '').trim()) return;
                saving = true;
                try {
                    const resp = await fetch('/api/characters/catgirl/' + encodeURIComponent(name) + '/card-meta', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ author: newVal })
                    });
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    const data = await resp.json();
                    if (window._cardMetas) window._cardMetas[name] = data.meta || { ...m, author: newVal };
                    showMessage(window.t ? window.t('character.cardAuthorUpdated') : '作者已更新', 'success');
                } catch (e) {
                    showMessage(window.t ? window.t('character.cardAuthorUpdateFailed') : '作者更新失败', 'error');
                    authorInput.value = author;
                } finally { saving = false; }
            };
            authorInput.addEventListener('blur', saveAuthor);
            authorInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); authorInput.blur(); }
            });
            authorRow.appendChild(authorInput);
        } else {
            const authorValue = document.createElement('span');
            authorValue.className = 'card-meta-value card-meta-readonly';
            authorValue.textContent = author || '-';
            authorValue.title = window.t ? window.t('character.cardAuthorReadonly') : '导入/工坊角色卡的作者不可修改';
            authorRow.appendChild(authorValue);
        }
        container.appendChild(authorRow);

        // 创建时间
        if (createdAt) {
            const timeRow = document.createElement('div');
            timeRow.className = 'card-meta-row card-meta-time';
            const timeLabel = document.createElement('span');
            timeLabel.className = 'card-meta-label';
            timeLabel.textContent = window.t ? window.t('character.cardCreatedAt') : '创建时间';
            const timeValue = document.createElement('span');
            timeValue.className = 'card-meta-value';
            timeValue.textContent = _formatCardMetaTime(createdAt);
            timeRow.appendChild(timeLabel);
            timeRow.appendChild(timeValue);
            container.appendChild(timeRow);
        }
    };

    if (meta) {
        draw(meta);
    } else {
        // 占位
        const loading = document.createElement('div');
        loading.className = 'card-meta-placeholder';
        loading.textContent = '...';
        container.appendChild(loading);
        // 异步拉取
        fetch('/api/characters/catgirl/' + encodeURIComponent(name) + '/card-meta')
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.meta) {
                    if (window._cardMetas) window._cardMetas[name] = data.meta;
                    draw(data.meta);
                } else {
                    draw(null);
                }
            })
            .catch(() => draw(null));
    }
}

// 从 PNG neKo 辅助块中提取 ZIP 数据
function _extractNekoChunk(uint8Array) {
    if (uint8Array.length < 8) return null;
    if (uint8Array[0] !== 0x89 || uint8Array[1] !== 0x50 || uint8Array[2] !== 0x4E ||
        uint8Array[3] !== 0x47 || uint8Array[4] !== 0x0D || uint8Array[5] !== 0x0A ||
        uint8Array[6] !== 0x1A || uint8Array[7] !== 0x0A) {
        return null;
    }
    const view = new DataView(uint8Array.buffer, uint8Array.byteOffset, uint8Array.byteLength);
    let offset = 8;
    while (offset + 12 <= uint8Array.length) {
        const chunkLen = view.getUint32(offset, false);
        if (chunkLen > 0x7FFFFFFF) return null;
        const chunkEnd = offset + 12 + chunkLen;
        if (chunkEnd > uint8Array.length) return null;
        const t0 = uint8Array[offset + 4];
        const t1 = uint8Array[offset + 5];
        const t2 = uint8Array[offset + 6];
        const t3 = uint8Array[offset + 7];
        if (t0 === 0x6E && t1 === 0x65 && t2 === 0x4B && t3 === 0x6F) {
            const dataStart = offset + 8;
            return uint8Array.slice(dataStart, dataStart + chunkLen);
        }
        if (t0 === 0x49 && t1 === 0x45 && t2 === 0x4E && t3 === 0x44) break;
        offset = chunkEnd;
    }
    return null;
}

async function handleImportCharacterCard(event) {
    const file = event.target.files[0];
    if (!file) return;
    event.target.value = '';

    const isNekoFile = file.name.endsWith('.nekocfg');
    const isPngFile = file.type.startsWith('image/') || file.name.endsWith('.png');
    if (!isNekoFile && !isPngFile) {
        showMessage(window.t ? window.t('character.importInvalidFile') : '请选择有效的PNG图片文件或.nekocfg设定文件', 'warning');
        return;
    }

    const loadingText = window.t ? window.t('character.importingCard') : '正在导入角色卡...';
    showMessage(loadingText, 'info');

    try {
        const arrayBuffer = await file.arrayBuffer();
        let fileData;
        if (isNekoFile) {
            fileData = new Uint8Array(arrayBuffer);
        } else {
            const uint8Array = new Uint8Array(arrayBuffer);
            fileData = _extractNekoChunk(uint8Array);
            if (!fileData) {
                // 回退：查找旧版 NEKOCHARA 标记
                const marker = new TextEncoder().encode('NEKOCHARA\x00');
                let markerIndex = -1;
                for (let i = uint8Array.length - marker.length; i >= 0; i--) {
                    let found = true;
                    for (let j = 0; j < marker.length; j++) {
                        if (uint8Array[i + j] !== marker[j]) { found = false; break; }
                    }
                    if (found) { markerIndex = i; break; }
                }
                if (markerIndex === -1 || markerIndex < 8) {
                    throw new Error(window.t ? window.t('character.importNoMarker') : '该图片不是有效的角色卡文件');
                }
                const zipSizeBytes = uint8Array.slice(markerIndex - 8, markerIndex);
                const zipSize = new DataView(zipSizeBytes.buffer).getUint32(0, true);
                if (zipSize <= 0 || zipSize > uint8Array.length) {
                    throw new Error(window.t ? window.t('character.importNoMarker') : '该图片不是有效的角色卡文件');
                }
                const zipStart = markerIndex - 8 - zipSize;
                if (zipStart < 0 || zipStart + zipSize > markerIndex - 8) {
                    throw new Error(window.t ? window.t('character.importNoMarker') : '该图片不是有效的角色卡文件');
                }
                fileData = uint8Array.slice(zipStart, markerIndex - 8);
            }
        }

        const formData = new FormData();
        const blob = new Blob([fileData], { type: isNekoFile ? 'application/octet-stream' : 'application/zip' });
        formData.append('zip_file', blob, isNekoFile ? file.name : 'character_data.zip');
        // 对于 PNG 载体，额外上传原始图片作为卡面回退（老角色卡兼容）
        if (isPngFile) {
            const pngBlob = new Blob([new Uint8Array(arrayBuffer)], { type: 'image/png' });
            formData.append('card_image', pngBlob, file.name || 'card.png');
        }

        const response = await fetch('/api/characters/import-card', { method: 'POST', body: formData });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: '导入失败' }));
            throw new Error(errorData.error || `HTTP ${response.status}`);
        }
        const result = await response.json();

        const successText = window.t ? window.t('character.importCardSuccess', { name: result.character_name }) : `角色卡 "${result.character_name}" 导入成功`;
        showMessage(successText, 'success');

        // 刷新角色卡列表（含 sidecar / 卡面 / 视图重新渲染）
        if (typeof loadCharacterCards === 'function') {
            await loadCharacterCards();
        } else if (typeof loadCharacterData === 'function') {
            await loadCharacterData();
        }
    } catch (error) {
        console.error('导入角色卡失败:', error);
        const errorText = window.t ? window.t('character.importCardFailed', { error: error.message }) : `导入角色卡失败: ${error.message}`;
        showMessage(errorText, 'error');
    }
}

// 绑定导入按钮（页面加载后）
function _setupImportCardButton() {
    const btn = document.getElementById('chara-import-btn');
    const input = document.getElementById('chara-import-input');
    if (btn && input && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => input.click());
        input.addEventListener('change', handleImportCharacterCard);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setupImportCardButton);
} else {
    _setupImportCardButton();
}

// ===== API 设置弹窗 =====
const _API_KEY_ALLOWED_ORIGINS = [window.location.origin];
function openApiKeySettings() {
    const existingModal = document.getElementById('api-key-settings-modal');
    if (existingModal) {
        existingModal.style.display = 'block';
        return;
    }
    const modal = document.createElement('div');
    modal.id = 'api-key-settings-modal';
    modal.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:100vh;background:rgba(0,0,0,0.4);z-index:9999';

    const apiKeyMessageHandler = function (e) {
        if (!_API_KEY_ALLOWED_ORIGINS.includes(e.origin)) return;
        if (e.data && e.data.type === 'close_api_key_settings') {
            const m = document.getElementById('api-key-settings-modal');
            if (m && m.parentNode) m.parentNode.removeChild(m);
            window.removeEventListener('message', apiKeyMessageHandler);
        }
    };

    modal.onclick = function (e) {
        if (e.target === modal) {
            window.removeEventListener('message', apiKeyMessageHandler);
            if (modal.parentNode) modal.parentNode.removeChild(modal);
        }
    };

    const iframe = document.createElement('iframe');
    iframe.src = '/api_key';
    iframe.style.cssText = 'width:800px;height:720px;border:none;background:#fff;display:block;margin:50px auto;border-radius:8px';

    window.addEventListener('message', apiKeyMessageHandler);
    modal.appendChild(iframe);
    document.body.appendChild(modal);
}

function _setupApiKeySettingsButton() {
    const btn = document.getElementById('api-key-settings-btn');
    if (btn && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', openApiKeySettings);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setupApiKeySettingsButton);
} else {
    _setupApiKeySettingsButton();
}

// ===== 统一弹窗样式 =====
// 与导出角色卡弹窗风格一致的通用 Confirm / Alert / Toast
// 目的：在桌面端网页中也能稳定显示（替换老的 top-corner showMessage / 原生 confirm）。

function _createManagerModal({ title, message, variant = 'info', buttons = [], dismissOnOverlay = true, icon = null }) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'ccm-modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:10002;display:flex;align-items:center;justify-content:center;animation:ccmFadeIn 0.18s ease';

        const dialog = document.createElement('div');
        dialog.style.cssText = 'background:#fff;border-radius:14px;padding:22px 26px 18px;min-width:340px;max-width:90vw;box-shadow:0 14px 40px rgba(0,0,0,0.25);font-family:inherit;animation:ccmSlideUp 0.22s ease';

        const accentColor = {
            info: '#40C5F1',
            success: '#58c38a',
            warning: '#f0ad4e',
            error: '#ff5a5a',
            danger: '#ff5a5a',
        }[variant] || '#40C5F1';

        if (title) {
            const t = document.createElement('div');
            t.style.cssText = 'font-size:16px;font-weight:700;color:#222;margin-bottom:8px;display:flex;align-items:center;gap:8px';
            if (icon) {
                const i = document.createElement('i');
                i.className = 'fa ' + icon;
                i.style.cssText = 'color:' + accentColor + ';font-size:16px';
                t.appendChild(i);
            }
            const ts = document.createElement('span');
            ts.textContent = title;
            t.appendChild(ts);
            dialog.appendChild(t);
        }

        if (message) {
            const d = document.createElement('div');
            d.style.cssText = 'font-size:13px;color:#555;margin-bottom:18px;line-height:1.5;white-space:pre-wrap;word-break:break-word';
            d.textContent = message;
            dialog.appendChild(d);
        }

        const footer = document.createElement('div');
        footer.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap';

        const mkBtn = (label, btnVariant) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.textContent = label;
            const base = 'padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:filter 0.15s,transform 0.1s';
            if (btnVariant === 'primary') {
                b.style.cssText = base + ';background:linear-gradient(135deg,#40C5F1,#5dd4f7);color:#fff;box-shadow:0 2px 6px rgba(64,197,241,0.3)';
            } else if (btnVariant === 'danger') {
                b.style.cssText = base + ';background:linear-gradient(135deg,#ff7a7a,#ff5a5a);color:#fff;box-shadow:0 2px 6px rgba(255,90,90,0.3)';
            } else {
                b.style.cssText = base + ';background:#f3f5f7;color:#333';
            }
            b.onmouseenter = () => { b.style.filter = 'brightness(1.06)'; b.style.transform = 'translateY(-1px)'; };
            b.onmouseleave = () => { b.style.filter = ''; b.style.transform = ''; };
            return b;
        };

        const close = (value) => {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            resolve(value);
        };

        (buttons || []).forEach(bt => {
            const btn = mkBtn(bt.label, bt.variant || 'secondary');
            btn.onclick = () => close(bt.value);
            footer.appendChild(btn);
        });

        dialog.appendChild(footer);
        overlay.appendChild(dialog);
        if (dismissOnOverlay) {
            overlay.onclick = (e) => { if (e.target === overlay) close(null); };
        }
        // ESC 关闭
        const escHandler = (e) => {
            if (e.key === 'Escape') { document.removeEventListener('keydown', escHandler); close(null); }
        };
        document.addEventListener('keydown', escHandler);

        // 注入一次性动画 keyframes
        if (!document.getElementById('ccm-modal-keyframes')) {
            const st = document.createElement('style');
            st.id = 'ccm-modal-keyframes';
            st.textContent = '@keyframes ccmFadeIn{from{opacity:0}to{opacity:1}}@keyframes ccmSlideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}@keyframes ccmSlideOut{from{opacity:1;transform:translateY(0)}to{opacity:0;transform:translateY(-8px)}}';
            document.head.appendChild(st);
        }

        document.body.appendChild(overlay);
    });
}

// 确认对话框（Promise<boolean>）
function showConfirmDialog(message, options = {}) {
    const title = options.title || (window.t ? window.t('common.confirm') : '确认');
    const okText = options.okText || (window.t ? window.t('common.confirm') : '确认');
    const cancelText = options.cancelText || (window.t ? window.t('common.cancel') : '取消');
    const variant = options.danger ? 'danger' : 'info';
    const icon = options.danger ? 'fa-exclamation-triangle' : 'fa-question-circle';
    return _createManagerModal({
        title,
        message,
        variant,
        icon,
        buttons: [
            { label: cancelText, variant: 'secondary', value: false },
            { label: okText, variant: options.danger ? 'danger' : 'primary', value: true },
        ],
    }).then(v => v === true);
}

// 提示对话框（Promise<void>，仅 OK 按钮）
function showAlertDialog(message, options = {}) {
    const typeMap = {
        error:   { titleKey: 'common.error',   fallback: '错误', icon: 'fa-exclamation-circle', variant: 'error' },
        warning: { titleKey: 'common.warning', fallback: '警告', icon: 'fa-exclamation-triangle', variant: 'warning' },
        success: { titleKey: 'common.success', fallback: '成功', icon: 'fa-check-circle', variant: 'success' },
        info:    { titleKey: 'common.alert',   fallback: '提示', icon: 'fa-info-circle', variant: 'info' },
    };
    const t = typeMap[options.type || 'info'];
    const title = options.title || (window.t ? window.t(t.titleKey) : t.fallback);
    const okText = options.okText || (window.t ? window.t('common.ok') : '确定');
    return _createManagerModal({
        title,
        message,
        variant: t.variant,
        icon: t.icon,
        buttons: [{ label: okText, variant: 'primary', value: true }],
    });
}

// ===== 导出角色卡（弹窗：取消 / 仅导出设定 / 导出角色卡） =====
function showExportOptionsModal(catgirlName) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'export-options-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:10001;display:flex;align-items:center;justify-content:center';

        const dialog = document.createElement('div');
        dialog.style.cssText = 'background:#fff;border-radius:14px;padding:22px 26px 18px;min-width:360px;max-width:90vw;box-shadow:0 14px 40px rgba(0,0,0,0.25);font-family:inherit';

        const title = document.createElement('div');
        title.style.cssText = 'font-size:16px;font-weight:700;color:#222;margin-bottom:8px';
        title.textContent = (window.t ? window.t('character.exportOptions') : '导出角色卡');
        dialog.appendChild(title);

        const desc = document.createElement('div');
        desc.style.cssText = 'font-size:13px;color:#555;margin-bottom:18px;line-height:1.5';
        const descTpl = window.t ? window.t('character.exportOptionsDesc') : '请选择要导出的内容：';
        desc.textContent = descTpl + ' 「' + catgirlName + '」';
        dialog.appendChild(desc);

        const footer = document.createElement('div');
        footer.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap';

        const mkBtn = (label, variant) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.textContent = label;
            const base = 'padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:filter 0.15s,transform 0.1s';
            if (variant === 'primary') {
                b.style.cssText = base + ';background:linear-gradient(135deg,#40C5F1,#5dd4f7);color:#fff;box-shadow:0 2px 6px rgba(64,197,241,0.3)';
            } else {
                b.style.cssText = base + ';background:#f3f5f7;color:#333';
            }
            b.onmouseenter = () => { b.style.filter = 'brightness(1.06)'; b.style.transform = 'translateY(-1px)'; };
            b.onmouseleave = () => { b.style.filter = ''; b.style.transform = ''; };
            return b;
        };

        const cancelBtn = mkBtn(window.t ? window.t('common.cancel') : '取消', 'secondary');
        cancelBtn.onclick = () => { close(); resolve(null); };
        footer.appendChild(cancelBtn);

        const settingsBtn = mkBtn(window.t ? window.t('character.exportSettingsOnly') : '仅导出设定', 'secondary');
        settingsBtn.onclick = () => { close(); resolve('settings-only'); };
        footer.appendChild(settingsBtn);

        const fullBtn = mkBtn(window.t ? window.t('character.exportFull') : '导出角色卡', 'primary');
        fullBtn.onclick = () => { close(); resolve('full'); };
        footer.appendChild(fullBtn);

        dialog.appendChild(footer);
        overlay.appendChild(dialog);
        overlay.onclick = (e) => { if (e.target === overlay) { close(); resolve(null); } };
        document.body.appendChild(overlay);

        function close() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }
    });
}

async function _downloadBlobAs(blob, filename, pickerType) {
    // pickerType: { description, accept }，限制保存对话框文件类型
    try {
        if ('showSaveFilePicker' in window && pickerType) {
            const fh = await window.showSaveFilePicker({ suggestedName: filename, types: [pickerType] });
            const w = await fh.createWritable();
            await w.write(blob);
            await w.close();
            return true;
        }
    } catch (err) {
        if (err && err.name === 'AbortError') return false;
        // 其它错误回退到 <a> 下载
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { if (a.parentNode) a.parentNode.removeChild(a); URL.revokeObjectURL(url); }, 0);
    return true;
}

function _filenameFromContentDisposition(headerValue, fallback) {
    if (!headerValue) return fallback;
    const star = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
    if (star) {
        try { return decodeURIComponent(star[1]); } catch (e) { /* fallthrough */ }
    }
    const m = headerValue.match(/filename="([^"]+)"/i);
    if (m) return m[1];
    return fallback;
}

async function exportCharacterCard(catgirlName) {
    let mode;
    try {
        mode = await showExportOptionsModal(catgirlName);
    } catch (e) {
        return;
    }
    if (!mode) return;

    const url = mode === 'settings-only'
        ? `/api/characters/catgirl/${encodeURIComponent(catgirlName)}/export-settings`
        : `/api/characters/catgirl/${encodeURIComponent(catgirlName)}/export`;
    const fallbackName = mode === 'settings-only'
        ? `${catgirlName}_设定.nekocfg`
        : `${catgirlName}.png`;
    const pickerType = mode === 'settings-only'
        ? { description: 'NEKO 设定文件', accept: { 'application/octet-stream': ['.nekocfg'] } }
        : { description: 'NEKO 角色卡 (PNG)', accept: { 'image/png': ['.png'] } };

    const loadingText = window.t ? window.t('character.exportingCard') : '正在导出...';
    showMessage(loadingText, 'info');
    try {
        const resp = await fetch(url, { method: 'GET' });
        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
            throw new Error(errData.error || `HTTP ${resp.status}`);
        }
        const blob = await resp.blob();
        const filename = _filenameFromContentDisposition(resp.headers.get('Content-Disposition'), fallbackName);
        const ok = await _downloadBlobAs(blob, filename, pickerType);
        if (ok) {
            const successText = window.t ? window.t('character.exportCardSuccess') : '导出成功';
            showMessage(successText, 'success');
        }
    } catch (error) {
        console.error('导出角色卡失败:', error);
        const errorText = window.t ? window.t('character.exportCardFailed', { error: error.message }) : `导出失败: ${error.message}`;
        showMessage(errorText, 'error');
    }
}

// 当前视图模式
let charaCardsViewMode = localStorage.getItem('charaCardsViewMode') || 'card';

// 切换视图
function switchCharaCardsView(mode) {
    if (charaCardsViewMode === mode) return;
    charaCardsViewMode = mode;
    localStorage.setItem('charaCardsViewMode', mode);
    // 更新按钮状态
    document.getElementById('chara-view-card-btn')?.classList.toggle('active', mode === 'card');
    document.getElementById('chara-view-list-btn')?.classList.toggle('active', mode === 'list');

    const container = document.getElementById('chara-cards-container');
    if (container) {
        // 退出动画
        container.style.opacity = '0';
        container.style.transform = 'scale(0.97)';
        setTimeout(function () {
            renderCharaCardsView();
            // 入场动画
            requestAnimationFrame(function () {
                container.style.opacity = '1';
                container.style.transform = 'scale(1)';
            });
        }, 200);
    } else {
        renderCharaCardsView();
    }
}
window.switchCharaCardsView = switchCharaCardsView;

// 搜索过滤
let _charaSearchQuery = '';

function filterCharaCards(query) {
    _charaSearchQuery = (query || '').trim().toLowerCase();
    renderCharaCardsView();
}
window.filterCharaCards = filterCharaCards;

// 渲染角色卡视图
function renderCharaCardsView() {
    const container = document.getElementById('chara-cards-container');
    if (!container) return;

    let cards = window.characterCards || [];

    // 应用搜索过滤
    const hiddenKeys = getHiddenCatgirlKeys();

    if (_charaSearchQuery) {
        cards = cards.filter(card => {
            const name = (card.originalName || card.name || '').toLowerCase();
            return name.includes(_charaSearchQuery);
        });
    }

    // 默认过滤掉隐藏的猫娘（除非开启显示已隐藏）
    if (!window._showHiddenCatgirls) {
        cards = cards.filter(card => !hiddenKeys.includes(card.originalName || card.name));
    }

    if (cards.length === 0) {
        const hiddenArea = container.querySelector('#hidden-catgirl-area');
        container.querySelectorAll('.chara-cards-grid, .chara-cards-list, .empty-state').forEach(el => el.remove());
        const emptyDiv = document.createElement('div');
        emptyDiv.className = 'empty-state';
        emptyDiv.innerHTML = '<p>' + (window.t ? window.t('steam.noCharacterCards') : '暂无角色卡') + '</p>';
        if (hiddenArea) {
            container.insertBefore(emptyDiv, hiddenArea);
        } else {
            container.appendChild(emptyDiv);
        }
        return;
    }

    const currentCatgirl = window._workshopCurrentCatgirl || '';

    if (charaCardsViewMode === 'card') {
        renderCharaCardsGrid(container, cards, currentCatgirl, hiddenKeys);
    } else {
        renderCharaCardsList(container, cards, currentCatgirl, hiddenKeys);
    }

    // 恢复按钮激活状态
    document.getElementById('chara-view-card-btn')?.classList.toggle('active', charaCardsViewMode === 'card');
    document.getElementById('chara-view-list-btn')?.classList.toggle('active', charaCardsViewMode === 'list');
}

// 卡片视图渲染
function renderCharaCardsGrid(container, cards, currentCatgirl, hiddenKeys) {
    const grid = document.createElement('div');
    grid.className = 'chara-cards-grid';

    cards.forEach(card => {
        const name = card.originalName || card.name;
        const isCurrent = name === currentCatgirl;
        const isHidden = (hiddenKeys || []).includes(name);

        const item = document.createElement('div');
        item.className = 'chara-card-item' + (isCurrent ? ' active' : '') + (isHidden ? ' hidden-catgirl-card' : '');
        if (isHidden) item.style.opacity = '0.6';
        item.style.cursor = 'pointer';
        item.onclick = function (e) {
            if (e.target.closest('.card-action-btn') || e.target.closest('.card-hide-corner')) return;
            openCatgirlPanel(card, item);
        };

        // 左上角隐藏/显示按钮
        if (!isCurrent) {
            const cornerBtn = document.createElement('button');
            cornerBtn.className = 'card-hide-corner';
            cornerBtn.type = 'button';
            if (isHidden) {
                cornerBtn.title = window.t ? window.t('character.show') : '显示';
                cornerBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
                cornerBtn.onclick = function (e) {
                    e.stopPropagation();
                    workshopUnhideCatgirl(name);
                };
            } else {
                cornerBtn.title = window.t ? window.t('character.hideCatgirl') : '隐藏猫娘';
                cornerBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
                cornerBtn.onclick = function (e) {
                    e.stopPropagation();
                    workshopHideCatgirl(name);
                };
            }
            item.appendChild(cornerBtn);
        }

        // 角色卡图片
        const avatar = document.createElement('div');
        avatar.className = 'card-avatar';
        const placeholderSpan = document.createElement('span');
        placeholderSpan.className = 'card-avatar-placeholder';
        placeholderSpan.textContent = window.t ? window.t('steam.noCardImage') : '暂未设置\n角色卡图片';
        avatar.appendChild(placeholderSpan);

        // 加载已有的卡面图片（仅在服务器侧确实存在时才请求，避免 404 噪声）
        if (window._cardFaceNames && window._cardFaceNames.has(name)) {
            const avatarImg = document.createElement('img');
            avatarImg.className = 'card-face-img';
            avatarImg.alt = name;
            avatarImg.onload = () => {
                placeholderSpan.style.display = 'none';
                avatar.insertBefore(avatarImg, placeholderSpan);
            };
            avatarImg.src = `/api/characters/catgirl/${encodeURIComponent(name)}/card-face?t=${Date.now()}`;
        }

        item.appendChild(avatar);

        // 名称
        const nameDiv = document.createElement('div');
        nameDiv.className = 'card-name';
        nameDiv.textContent = name;
        item.appendChild(nameDiv);

        // 当前角色卡标记（胶囊 + 肇状图标）
        if (isCurrent) {
            const badge = document.createElement('span');
            badge.className = 'card-badge';
            badge.innerHTML = '<img src="/static/icons/paw_ui.png" class="card-badge-icon" alt="">'
                + '<span>' + (window.t ? window.t('character.currentCard') : '当前角色卡') + '</span>';
            item.appendChild(badge);
        }

        // 操作按钮
        const actionsRow = document.createElement('div');
        actionsRow.className = 'card-actions-row';

        const switchBtn = document.createElement('button');
        switchBtn.className = 'card-action-btn switch-btn';
        switchBtn.title = window.t ? window.t('character.switchCard') : '切换角色卡';
        switchBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>'
            + '<span>' + (window.t ? window.t('character.switchCard') : '切换角色卡') + '</span>';
        switchBtn.disabled = isCurrent;
        switchBtn.onclick = function (e) {
            e.stopPropagation();
            workshopSwitchCatgirl(name);
        };
        actionsRow.appendChild(switchBtn);

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'card-action-btn delete-btn';
        deleteBtn.title = window.t ? window.t('character.deleteCard') : '删除角色卡';
        deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
            + '<span>' + (window.t ? window.t('character.deleteCard') : '删除角色卡') + '</span>';
        deleteBtn.onclick = function (e) {
            e.stopPropagation();
            workshopDeleteCatgirl(name);
        };
        actionsRow.appendChild(deleteBtn);

        item.appendChild(actionsRow);
        grid.appendChild(item);
    });

    const hiddenArea = container.querySelector('#hidden-catgirl-area');
    container.querySelectorAll('.chara-cards-grid, .chara-cards-list, .empty-state').forEach(el => el.remove());
    if (hiddenArea) {
        container.insertBefore(grid, hiddenArea);
    } else {
        container.appendChild(grid);
    }
}

// 列表视图渲染
function renderCharaCardsList(container, cards, currentCatgirl, hiddenKeys) {
    const list = document.createElement('div');
    list.className = 'chara-cards-list';

    cards.forEach(card => {
        const name = card.originalName || card.name;
        const isCurrent = name === currentCatgirl;
        const isHidden = (hiddenKeys || []).includes(name);

        const item = document.createElement('div');
        item.className = 'chara-list-item' + (isCurrent ? ' active' : '') + (isHidden ? ' hidden-catgirl-item' : '');
        if (isHidden) item.style.opacity = '0.6';
        item.style.cursor = 'pointer';
        item.onclick = function (e) {
            if (e.target.closest('.list-action-btn')) return;
            openCatgirlPanel(card, item);
        };

        // 头像缩略图在列表视图中已移除（列表仅展示名称/状态/操作）

        // 名称
        const nameDiv = document.createElement('div');
        nameDiv.className = 'list-name';
        nameDiv.textContent = name;
        item.appendChild(nameDiv);

        // 当前角色卡标记
        if (isCurrent) {
            const badge = document.createElement('span');
            badge.className = 'list-badge';
            badge.innerHTML = '<img src="/static/icons/paw_ui.png" class="list-badge-icon" alt="">'
                + '<span>' + (window.t ? window.t('character.currentCard') : '当前角色卡') + '</span>';
            item.appendChild(badge);
        }

        // 操作按钮
        const actions = document.createElement('div');
        actions.className = 'list-actions';

        const switchBtn = document.createElement('button');
        switchBtn.className = 'list-action-btn switch-btn';
        switchBtn.title = window.t ? window.t('character.switchCard') : '切换角色卡';
        switchBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>'
            + '<span class="list-action-label">' + (window.t ? window.t('character.switchCard') : '切换角色卡') + '</span>';
        switchBtn.disabled = isCurrent;
        switchBtn.onclick = function (e) {
            e.stopPropagation();
            workshopSwitchCatgirl(name);
        };
        actions.appendChild(switchBtn);

        if (isHidden) {
            const unhideBtn = document.createElement('button');
            unhideBtn.className = 'list-action-btn';
            unhideBtn.title = window.t ? window.t('character.show') : '显示';
            unhideBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
                + '<span class="list-action-label">' + (window.t ? window.t('character.show') : '显示') + '</span>';
            unhideBtn.onclick = function (e) {
                e.stopPropagation();
                workshopUnhideCatgirl(name);
            };
            actions.appendChild(unhideBtn);
        } else if (!isCurrent) {
            const hideBtn = document.createElement('button');
            hideBtn.className = 'list-action-btn';
            hideBtn.title = window.t ? window.t('character.hideCatgirl') : '隐藏猫娘';
            hideBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
                + '<span class="list-action-label">' + (window.t ? window.t('character.hideCatgirl') : '隐藏') + '</span>';
            hideBtn.onclick = function (e) {
                e.stopPropagation();
                workshopHideCatgirl(name);
            };
            actions.appendChild(hideBtn);
        }

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'list-action-btn delete-btn';
        deleteBtn.title = window.t ? window.t('character.deleteCard') : '删除角色卡';
        deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
            + '<span class="list-action-label">' + (window.t ? window.t('character.deleteCard') : '删除角色卡') + '</span>';
        deleteBtn.onclick = function (e) {
            e.stopPropagation();
            workshopDeleteCatgirl(name);
        };
        actions.appendChild(deleteBtn);

        item.appendChild(actions);
        list.appendChild(item);
    });

    const hiddenArea = container.querySelector('#hidden-catgirl-area');
    container.querySelectorAll('.chara-cards-grid, .chara-cards-list, .empty-state').forEach(el => el.remove());
    if (hiddenArea) {
        container.insertBefore(list, hiddenArea);
    } else {
        container.appendChild(list);
    }
}

// ===== 角色卡详情面板 =====

let _catgirlPanelOpen = false;

function openCatgirlPanel(card, originEl) {
    if (_catgirlPanelOpen) return;
    _catgirlPanelOpen = true;

    const name = card ? (card.originalName || card.name) : null;
    const rawData = card ? (card.rawData || {}) : {};
    const isNew = !name;

    // 创建遮罩层
    const overlay = document.createElement('div');
    overlay.className = 'catgirl-panel-overlay';
    overlay.onclick = function (e) {
        if (e.target === overlay) closeCatgirlPanel();
    };

    // 创建面板容器
    const wrapper = document.createElement('div');
    wrapper.className = 'catgirl-panel-wrapper card-only';
    wrapper.id = 'catgirl-panel-wrapper';

    // 设置动画起点
    if (originEl) {
        const rect = originEl.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        wrapper.style.transformOrigin = cx + 'px ' + cy + 'px';
    }

    // 左侧：卡片预览
    const leftSection = document.createElement('div');
    leftSection.className = 'catgirl-panel-left';

    const cardImage = document.createElement('div');
    cardImage.className = 'catgirl-panel-card-image';
    cardImage.setAttribute('data-edit-label', window.t ? window.t('character.editCardFace') : '✎ 编辑卡面');
    const imgPlaceholder = document.createElement('span');
    imgPlaceholder.className = 'card-avatar-placeholder';
    imgPlaceholder.textContent = window.t ? window.t('steam.noCardImage') : '暂未设置\n角色卡图片';
    cardImage.appendChild(imgPlaceholder);

    // 加载已有的卡面图片（仅在服务器侧确实存在时才请求，避免 404 噪声）
    if (name && window._cardFaceNames && window._cardFaceNames.has(name)) {
        const cardFaceUrl = `/api/characters/catgirl/${encodeURIComponent(name)}/card-face`;
        const img = document.createElement('img');
        img.className = 'card-face-img';
        img.alt = '角色卡面';
        img.onload = () => {
            imgPlaceholder.style.display = 'none';
            cardImage.insertBefore(img, imgPlaceholder);
        };
        img.src = cardFaceUrl + '?t=' + Date.now();
    }

    // 点击卡面打开角色卡制作页面
    cardImage.addEventListener('click', () => {
        // 优先使用表单中当前填写的档案名（新建猫娘可能已临时保存）
        const form = cardImage.closest('.catgirl-panel-wrapper')?.querySelector('form');
        const currentName = form?.querySelector('[name="档案名"]')?.value || name;
        if (!currentName) {
            showMessage(window.t ? window.t('character.fillProfileNameFirst') : '请先填写猫娘档案名', 'warning');
            return;
        }
        const makerUrl = `/card_maker?name=${encodeURIComponent(currentName)}&mode=maker`;
        window.open(makerUrl, '_blank', 'width=1200,height=800');
    });

    // 监听角色卡制作页面的保存消息
    const onCardFaceMessage = (event) => {
        // 获取当前实际的档案名（新建猫娘时 name 为 null，需要从表单读取）
        const form = cardImage.closest('.catgirl-panel-wrapper')?.querySelector('form');
        const currentName = form?.querySelector('[name="档案名"]')?.value || name;
        if (!currentName) return;

        if (event.data && event.data.type === 'card-face-updated' && event.data.name === currentName) {
            const ts = event.data.timestamp;
            const newSrc = `/api/characters/catgirl/${encodeURIComponent(currentName)}/card-face?t=${ts}`;
            // 更新缓存：标记该名字现在已有卡面
            if (window._cardFaceNames) window._cardFaceNames.add(currentName);

            // 更新面板卡面图片
            let panelImg = cardImage.querySelector('.card-face-img');
            if (!panelImg) {
                panelImg = document.createElement('img');
                panelImg.className = 'card-face-img';
                panelImg.alt = '角色卡面';
                cardImage.insertBefore(panelImg, imgPlaceholder);
                imgPlaceholder.style.display = 'none';
            }
            panelImg.src = newSrc;

            // 同步更新角色列表中的卡面
            document.querySelectorAll('.chara-card-item').forEach(cardItem => {
                const cardName = cardItem.querySelector('.card-name');
                if (cardName && cardName.textContent === currentName) {
                    const gridAvatar = cardItem.querySelector('.card-avatar');
                    if (gridAvatar) {
                        let gridImg = gridAvatar.querySelector('.card-face-img');
                        const gridPlaceholder = gridAvatar.querySelector('.card-avatar-placeholder');
                        if (!gridImg) {
                            gridImg = document.createElement('img');
                            gridImg.className = 'card-face-img';
                            gridImg.alt = currentName;
                            if (gridPlaceholder) {
                                gridAvatar.insertBefore(gridImg, gridPlaceholder);
                                gridPlaceholder.style.display = 'none';
                            } else {
                                gridAvatar.appendChild(gridImg);
                            }
                        }
                        gridImg.src = newSrc;
                    }
                }
            });
        }
    };
    window.addEventListener('message', onCardFaceMessage);
    // 面板关闭时清理监听器（利用MutationObserver）
    const panelCleanupObserver = new MutationObserver(() => {
        if (!document.contains(cardImage)) {
            window.removeEventListener('message', onCardFaceMessage);
            panelCleanupObserver.disconnect();
        }
    });
    panelCleanupObserver.observe(document.body, { childList: true, subtree: true });

    leftSection.appendChild(cardImage);

    // === 卡面信息 ===
    const metaBlock = document.createElement('div');
    metaBlock.className = 'card-meta-block';
    metaBlock.id = 'card-meta-block';
    leftSection.appendChild(metaBlock);
    renderCardMetaBlock(metaBlock, name, isNew, rawData);

    // === 角色卡操作按钮（仅已存在的猫娘） ===
    if (!isNew && name) {
        const actions = document.createElement('div');
        actions.className = 'card-panel-actions';

        const exportBtn = document.createElement('button');
        exportBtn.type = 'button';
        exportBtn.className = 'card-panel-action-btn export-btn';
        exportBtn.title = window.t ? window.t('character.exportCardOnly') : '导出角色卡';
        exportBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>'
            + '<span>' + (window.t ? window.t('character.exportCardOnly') : '导出') + '</span>';
        exportBtn.onclick = function (e) {
            e.stopPropagation();
            exportCharacterCard(name);
        };
        actions.appendChild(exportBtn);

        const switchBtn = document.createElement('button');
        switchBtn.type = 'button';
        switchBtn.className = 'card-panel-action-btn switch-btn';
        const isCurrentChara = (window._workshopCurrentCatgirl || '') === name;
        switchBtn.disabled = isCurrentChara;
        switchBtn.title = window.t ? window.t('character.switchCard') : '切换角色卡';
        switchBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>'
            + '<span>' + (window.t ? window.t('character.switchCard') : '切换') + '</span>';
        switchBtn.onclick = function (e) {
            e.stopPropagation();
            workshopSwitchCatgirl(name);
        };
        actions.appendChild(switchBtn);

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'card-panel-action-btn delete-btn';
        deleteBtn.disabled = isCurrentChara;
        deleteBtn.title = isCurrentChara
            ? (window.t ? window.t('character.cannotDeleteCurrentCard') : '当前正在使用的角色卡无法删除，请先切换到其他角色卡')
            : (window.t ? window.t('character.deleteCard') : '删除角色卡');
        deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
            + '<span>' + (window.t ? window.t('character.deleteCard') : '删除') + '</span>';
        deleteBtn.onclick = function (e) {
            e.stopPropagation();
            if (isCurrentChara) {
                if (typeof showMessage === 'function') {
                    showMessage(window.t ? window.t('character.cannotDeleteCurrentCard') : '当前正在使用的角色卡无法删除，请先切换到其他角色卡', 'error');
                }
                return;
            }
            workshopDeleteCatgirl(name);
            // 删除会刷新列表，关闭面板
            setTimeout(() => closeCatgirlPanel(), 300);
        };
        actions.appendChild(deleteBtn);

        leftSection.appendChild(actions);
    }

    wrapper.appendChild(leftSection);

    // 右侧：编辑表单
    const rightSection = document.createElement('div');
    rightSection.className = 'catgirl-panel-right';

    // === 面板标题栏 ===
    const headerBar = document.createElement('div');
    headerBar.className = 'panel-header-bar';

    const tabsContainer = document.createElement('div');
    tabsContainer.className = 'panel-tabs';

    // 滑动指示器
    const indicator = document.createElement('div');
    indicator.className = 'panel-tabs-indicator';
    tabsContainer.appendChild(indicator);

    // 设定标签
    const settingsTab = document.createElement('button');
    settingsTab.type = 'button';
    settingsTab.className = 'panel-tab active';
    settingsTab.dataset.tab = 'settings';
    const settingsIcon = document.createElement('img');
    settingsIcon.src = '/static/icons/set_on.png';
    settingsIcon.className = 'panel-tab-icon';
    settingsIcon.alt = '';
    settingsTab.appendChild(settingsIcon);
    settingsTab.appendChild(document.createTextNode(window.t ? window.t('character.settings') : '设定'));
    tabsContainer.appendChild(settingsTab);

    if (!isNew) {
        // Steam 标签
        const steamTab = document.createElement('button');
        steamTab.type = 'button';
        steamTab.className = 'panel-tab';
        steamTab.dataset.tab = 'steam';
        const steamIcon = document.createElement('img');
        steamIcon.src = '/static/icons/Steam_icon_logo.png';
        steamIcon.className = 'panel-tab-icon';
        steamIcon.alt = '';
        steamTab.appendChild(steamIcon);
        steamTab.appendChild(document.createTextNode('Steam'));
        tabsContainer.appendChild(steamTab);
    }

    headerBar.appendChild(tabsContainer);

    // 关闭按钮（统一样式）
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'panel-close-btn';
    closeBtn.title = window.t ? window.t('common.close') : '关闭';
    const closeBtnImg = document.createElement('img');
    closeBtnImg.src = '/static/icons/close_button.png';
    closeBtnImg.alt = window.t ? window.t('common.close') : '关闭';
    closeBtn.appendChild(closeBtnImg);
    closeBtn.onclick = closeCatgirlPanel;
    headerBar.appendChild(closeBtn);

    rightSection.appendChild(headerBar);

    // === 设定标签内容 ===
    const settingsContent = document.createElement('div');
    settingsContent.className = 'panel-tab-content panel-tab-settings active';
    buildCatgirlDetailForm(name, rawData, isNew, settingsContent);
    rightSection.appendChild(settingsContent);

    // === Steam 标签内容 ===
    if (!isNew) {
        const steamContent = document.createElement('div');
        steamContent.className = 'panel-tab-content panel-tab-steam';
        rightSection.appendChild(steamContent);

        // 标签切换逻辑（含滑动指示器 + 幕布转场）
        const updateIndicator = function () {
            const activeTab = tabsContainer.querySelector('.panel-tab.active');
            if (activeTab && indicator) {
                indicator.style.left = activeTab.offsetLeft + 'px';
                indicator.style.width = activeTab.offsetWidth + 'px';
            }
        };

        // 幕布转场特效
        const CURTAIN_SCATTER_ICONS = [
            '/static/icons/star.png',
            '/static/icons/paw_ui.png',
            '/static/icons/star.png',
            '/static/icons/paw_ui.png'
        ];
        const spawnCurtainTransition = function (targetTabName, reverse) {
            const curtain = document.createElement('div');
            curtain.className = 'panel-transition-curtain' + (reverse ? ' curtain-reverse' : '');

            // 幕布色块
            const sweep = document.createElement('div');
            sweep.className = 'curtain-sweep';
            curtain.appendChild(sweep);

            // 散落小图标（跟着幕布走）
            for (let i = 0; i < 10; i++) {
                const icon = document.createElement('img');
                icon.className = 'curtain-icon';
                icon.src = CURTAIN_SCATTER_ICONS[i % CURTAIN_SCATTER_ICONS.length];
                const size = 18 + Math.random() * 20;
                icon.style.width = size + 'px';
                icon.style.height = size + 'px';
                icon.style.top = (5 + Math.random() * 85) + '%';
                icon.style.left = (5 + Math.random() * 85) + '%';
                icon.style.animationDelay = (0.15 + i * 0.04) + 's';
                sweep.appendChild(icon);
            }

            // 中央大图标 — 根据目标标签页显示不同图标
            const centerIcon = document.createElement('img');
            centerIcon.className = 'curtain-center-icon';
            if (targetTabName === 'steam') {
                centerIcon.src = '/static/icons/Steam_icon_logo.png';
                centerIcon.style.width = '72px';
                centerIcon.style.height = '72px';
                centerIcon.style.background = 'white';
                centerIcon.style.borderRadius = '50%';
                centerIcon.style.padding = '4px';
                centerIcon.style.boxShadow = '0 4px 16px rgba(0,100,200,0.25)';
            } else {
                centerIcon.src = '/static/icons/set_on.png';
                centerIcon.style.width = '64px';
                centerIcon.style.height = '64px';
            }
            centerIcon.style.animationDelay = '0.18s';
            curtain.appendChild(centerIcon);

            rightSection.appendChild(curtain);
            setTimeout(function () { curtain.remove(); }, 900);
        };

        let _tabSwitching = false;

        // 初始化指示器位置（等 DOM 渲染后）
        requestAnimationFrame(updateIndicator);

        headerBar.querySelectorAll('.panel-tab').forEach(tab => {
            tab.addEventListener('click', function () {
                if (_tabSwitching) return;
                const targetTab = this.dataset.tab;
                const currentActive = rightSection.querySelector('.panel-tab-content.active');
                const targetClass = 'panel-tab-' + targetTab;
                const target = rightSection.querySelector('.' + targetClass);
                if (!target || target === currentActive) return;

                // 计算动画方向：点击位于当前激活 tab 左侧的则反向动画
                const allTabs = Array.from(headerBar.querySelectorAll('.panel-tab'));
                const currentActiveTabBtn = headerBar.querySelector('.panel-tab.active');
                const currentIdx = currentActiveTabBtn ? allTabs.indexOf(currentActiveTabBtn) : -1;
                const targetIdx = allTabs.indexOf(this);
                const reverseDirection = (currentIdx >= 0 && targetIdx >= 0 && targetIdx < currentIdx);

                _tabSwitching = true;
                headerBar.querySelectorAll('.panel-tab').forEach(t => t.classList.remove('active'));
                this.classList.add('active');
                updateIndicator();

                // 根据当前激活状态切换设定齿轮图标 on/off
                if (settingsIcon) {
                    settingsIcon.src = (targetTab === 'settings')
                        ? '/static/icons/set_on.png'
                        : '/static/icons/set_off.png';
                }

                // 播放幕布转场
                spawnCurtainTransition(targetTab, reverseDirection);

                // 退出当前页 — absolute定位防止撑高容器
                if (currentActive) {
                    currentActive.classList.remove('active');
                    currentActive.classList.add('tab-exit');
                    if (reverseDirection) currentActive.classList.add('tab-reverse');
                }

                // 幕布扫过中央时切入新页
                setTimeout(function () {
                    if (currentActive) {
                        currentActive.classList.remove('tab-exit');
                        currentActive.classList.remove('tab-reverse');
                    }
                    target.classList.add('active', 'tab-enter');
                    if (reverseDirection) target.classList.add('tab-reverse');

                    // Steam 标签变为可见后，强制刷新模型预览尺寸并重新计算模型位置
                    if (targetTab === 'steam') {
                        requestAnimationFrame(function () {
                            // Live2D resize + 重新应用模型设置
                            if (live2dPreviewManager && live2dPreviewManager.pixi_app) {
                                const l2dContainer = document.getElementById('live2d-preview-content');
                                if (l2dContainer && l2dContainer.clientWidth > 0 && l2dContainer.clientHeight > 0) {
                                    live2dPreviewManager.pixi_app.renderer.resize(l2dContainer.clientWidth, l2dContainer.clientHeight);
                                    // 重新计算模型缩放和位置（修复在隐藏标签中加载导致的0尺寸问题）
                                    if (live2dPreviewManager.currentModel) {
                                        live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
                                        if (live2dPreviewManager.app && live2dPreviewManager.app.renderer) {
                                            live2dPreviewManager.app.renderer.render(live2dPreviewManager.app.stage);
                                        }
                                    }
                                }
                            }
                            // VRM resize
                            const vrmContainer = document.getElementById('vrm-preview-container');
                            if (vrmContainer && workshopVrmManager && workshopVrmManager.renderer) {
                                workshopVrmManager.renderer.setSize(vrmContainer.clientWidth, vrmContainer.clientHeight);
                            }
                            // MMD resize
                            const mmdContainer = document.getElementById('mmd-preview-container');
                            if (mmdContainer && workshopMmdManager && workshopMmdManager.renderer) {
                                workshopMmdManager.renderer.setSize(mmdContainer.clientWidth, mmdContainer.clientHeight);
                            }
                        });
                    }

                    // 入场动画结束后清理class
                    setTimeout(function () {
                        target.classList.remove('tab-enter');
                        target.classList.remove('tab-reverse');
                        _tabSwitching = false;
                    }, 460);
                }, 320);
            });
        });
    } else {
        // 单标签模式也初始化指示器
        requestAnimationFrame(function () {
            if (indicator && settingsTab) {
                indicator.style.left = settingsTab.offsetLeft + 'px';
                indicator.style.width = settingsTab.offsetWidth + 'px';
            }
        });
    }

    wrapper.appendChild(rightSection);

    overlay.appendChild(wrapper);
    document.body.appendChild(overlay);

    // 动画 Phase 1: 卡面移动到中间
    requestAnimationFrame(() => {
        overlay.classList.add('active');
        wrapper.classList.add('phase-center');
        // Phase 2: 展开右侧表单
        setTimeout(() => {
            wrapper.classList.remove('phase-center');
            wrapper.classList.add('phase-expand');

            // 在展开动画刚开始时立即测量并调整 textarea 高度，
            // 这样多行内容（>3 行）的输入框在展开过程中就直接呈现出
            // 「带滚动条+左下圆角」的最终形态，不再出现展开后才变化的延迟感。
            // 因为 phase-expand 仅做 opacity / translateX 过渡（宽度已是终态），
            // textarea 的 scrollHeight 已可正确测量。
            const _resizeAllPanelTextareas = () => {
                const settingsForm = rightSection.querySelector('form');
                if (!settingsForm) return;
                settingsForm.querySelectorAll('textarea').forEach(ta => {
                    ta.style.height = 'auto';
                    const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 20;
                    const maxHeight = lineHeight * 3 + 10;
                    const scrollHeight = ta.scrollHeight;
                    ta.style.height = Math.min(scrollHeight, maxHeight) + 'px';
                    const fieldRow = ta.closest('.field-row');
                    if (fieldRow) {
                        if (scrollHeight > maxHeight) {
                            ta.style.overflowY = 'auto';
                            fieldRow.classList.add('has-scrollbar');
                        } else {
                            ta.style.overflowY = 'hidden';
                            fieldRow.classList.remove('has-scrollbar');
                        }
                    }
                });
            };
            // 双 rAF 等一次 layout flush，再做测量
            requestAnimationFrame(() => requestAnimationFrame(_resizeAllPanelTextareas));
            // 兜底：动画结束后再测量一次（处理字体延迟加载等情况）
            setTimeout(_resizeAllPanelTextareas, 500);

            // 延迟初始化 Steam 标签页内容（等待面板展开动画完成后）
            if (!isNew) {
                setTimeout(() => {
                    const steamContainer = rightSection.querySelector('.panel-tab-steam');
                    if (steamContainer && !steamContainer.dataset.initialized) {
                        steamContainer.dataset.initialized = 'true';
                        buildSteamTabContent(name, rawData, card, steamContainer);
                    }
                }, 500);
            }
        }, 500);
    });
}
window.openCatgirlPanel = openCatgirlPanel;

function openNewCatgirlPanel() {
    openCatgirlPanel(null, null);
}
window.openNewCatgirlPanel = openNewCatgirlPanel;

function closeCatgirlPanel() {
    const overlay = document.querySelector('.catgirl-panel-overlay');
    if (!overlay) return;

    // 清理模型预览资源（如果 Steam 标签页曾加载过）
    try {
        if (typeof disposeWorkshopVrm === 'function') disposeWorkshopVrm();
        if (typeof disposeWorkshopMmd === 'function') disposeWorkshopMmd();
        if (typeof clearLive2DPreview === 'function') clearLive2DPreview();
    } catch (e) {
        console.warn('[Panel] 清理预览资源时出错:', e);
    }

    const wrapper = overlay.querySelector('.catgirl-panel-wrapper');
    if (wrapper) {
        wrapper.classList.remove('phase-expand');
        wrapper.classList.add('phase-center');
    }

    setTimeout(() => {
        overlay.classList.remove('active');
        if (wrapper) wrapper.classList.remove('phase-center');
        setTimeout(() => {
            overlay.remove();
            _catgirlPanelOpen = false;
        }, 400);
    }, 300);
}
window.closeCatgirlPanel = closeCatgirlPanel;

function buildCatgirlDetailForm(name, rawData, isNew, container) {
    let cat = rawData || {};
    let form = document.createElement('form');
    form.id = name ? 'catgirl-form-' + name : 'catgirl-form-new';
    form.style.padding = '0';
    form._catgirlName = name;
    form.onsubmit = function (e) { e.preventDefault(); };

    // 档案名
    const baseWrapper = document.createElement('div');
    baseWrapper.className = 'field-row-wrapper';

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
        renameBtn.className = 'btn sm';
        renameBtn.id = 'rename-catgirl-btn';
        renameBtn.style.marginLeft = '8px';
        renameBtn.style.minWidth = '120px';
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
            try {
                const resp = await fetch('/api/characters/catgirl/' + encodeURIComponent(name) + '/rename', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_name: newName.trim() })
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
                    await showAlert(window.t ? window.t('character.renameError') : '重命名时发生错误');
                }
            }
        });
        baseWrapper.appendChild(renameBtn);
    }
    form.appendChild(baseWrapper);

    // 自定义字段
    const ALL_RESERVED = typeof getWorkshopHiddenFields === 'function' ? ['档案名', ...getWorkshopHiddenFields()] : ['档案名'];
    Object.keys(cat).forEach(k => {
        if (ALL_RESERVED.includes(k)) return;
        const val = cat[k];
        if (val === null || val === undefined) return;

        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row';

        const deleteFieldText = (window.t && typeof window.t === 'function')
            ? '<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteField">' + window.t('character.deleteField') + '</span>'
            : '<img src="/static/icons/delete.png" alt="" class="delete-icon"> 删除设定';

        const labelEl = document.createElement('label');
        _panelSetFieldLabel(labelEl, k);
        wrapper.appendChild(labelEl);

        const fr = document.createElement('div');
        fr.className = 'field-row';
        const textareaEl = document.createElement('textarea');
        textareaEl.name = k;
        textareaEl.rows = 1;
        textareaEl.placeholder = (window.t && typeof window.t === 'function')
            ? window.t('character.detailDescriptionPlaceholder')
            : '可输入详细描述';
        textareaEl.value = cat[k];
        fr.appendChild(textareaEl);
        wrapper.appendChild(fr);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn sm delete';
        delBtn.innerHTML = deleteFieldText;
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
    addFieldArea.className = 'btn-area add-field-area';
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

    const addFieldBtn = document.createElement('button');
    addFieldBtn.type = 'button';
    addFieldBtn.className = 'btn sm add';
    addFieldBtn.id = 'panel-add-catgirl-field-btn';
    addFieldBtn.style.minWidth = '120px';
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
        const FORBIDDEN = ALL_RESERVED;
        if (!key || FORBIDDEN.includes(key)) return;
        if (form.querySelector('[name="' + CSS.escape(key) + '"]')) {
            if (typeof showAlert === 'function') {
                await showAlert(window.t ? window.t('character.fieldExists') : '该设定已存在');
            } else {
                alert(window.t ? window.t('character.fieldExists') : '该设定已存在');
            }
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row';

        const deleteFieldText = (window.t && typeof window.t === 'function')
            ? '<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteField">' + window.t('character.deleteField') + '</span>'
            : '<img src="/static/icons/delete.png" alt="" class="delete-icon"> 删除设定';

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
        delBtn.className = 'btn sm delete';
        delBtn.innerHTML = deleteFieldText;
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

    // 进阶设定折叠
    const fold = document.createElement('div');
    fold.className = 'fold open';

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
    arrowSpan.style.transform = 'rotate(0deg)';
    foldToggle.appendChild(arrowSpan);
    foldToggle.appendChild(document.createTextNode(' '));
    const toggleText = document.createTextNode(window.t ? window.t('character.advancedSettings') : '进阶设定');
    foldToggle.appendChild(toggleText);
    foldToggle.onclick = function () {
        fold.classList.toggle('open');
        arrowSpan.style.transform = fold.classList.contains('open') ? 'rotate(0deg)' : 'rotate(-90deg)';
        // localStorage 持久化折叠状态
        if (name) {
            localStorage.setItem('catgirl_advanced_' + name, fold.classList.contains('open'));
        }
    };
    fold.appendChild(foldToggle);

    const foldContent = document.createElement('div');
    foldContent.className = 'fold-content';

    // 模型设定
    const modelWrapper = document.createElement('div');
    modelWrapper.className = 'field-row-wrapper';
    const modelLabel = document.createElement('label');
    modelLabel.textContent = window.t ? window.t('character.modelSettings') : '模型设定';
    modelLabel.style.fontSize = '1rem';
    modelWrapper.appendChild(modelLabel);

    const modelLink = document.createElement('span');
    modelLink.className = 'live2d-link';
    modelLink.title = window.t ? window.t('character.manageModel') : '点击管理模型';
    modelLink.style.color = '#40C5F1';
    modelLink.style.cursor = 'pointer';
    modelLink.style.textDecoration = 'underline';
    modelLink.style.display = 'flex';
    modelLink.style.alignItems = 'center';

    // 辅助函数：检查模型路径是否有效
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
    let modelDisplayText = '';

    const mmdPath = validateModelPath(cat['mmd'])
        || validateModelPath(cat['_reserved']?.avatar?.mmd?.model_path);
    const vrmPath = validateModelPath(cat['vrm'])
        || validateModelPath(cat['_reserved']?.avatar?.vrm?.model_path);
    const live2dPath = validateModelPath(cat['live2d']);

    const live3dSubType = String(
        cat['_reserved']?.avatar?.live3d_sub_type || cat['live3d_sub_type'] || ''
    ).trim().toLowerCase();

    if (normalizedModelType === 'live3d' && live3dSubType === 'mmd' && mmdPath) {
        modelDisplayText = (mmdPath.split(/[\\/]/).pop() || mmdPath).replace(/\.(pmx|pmd)$/i, '');
    } else if (normalizedModelType === 'live3d' && live3dSubType === 'vrm' && vrmPath) {
        modelDisplayText = (vrmPath.split(/[\\/]/).pop() || vrmPath).replace(/\.vrm$/i, '');
    } else if (normalizedModelType === 'live3d' && mmdPath && !vrmPath) {
        modelDisplayText = (mmdPath.split(/[\\/]/).pop() || mmdPath).replace(/\.(pmx|pmd)$/i, '');
    } else if (normalizedModelType === 'live3d' && vrmPath) {
        modelDisplayText = (vrmPath.split(/[\\/]/).pop() || vrmPath).replace(/\.vrm$/i, '');
    } else if (live2dPath) {
        modelDisplayText = live2dPath;
    } else {
        modelDisplayText = window.t ? window.t('character.modelNotSet') : '未设置';
    }

    modelLink.textContent = modelDisplayText || (window.t ? window.t('character.modelNotSet') : '未设置');
    modelWrapper.appendChild(modelLink);
    foldContent.appendChild(modelWrapper);

    // 模型设定弹窗逻辑
    modelLink.onclick = async function () {
        const catgirlName = form.querySelector('[name="档案名"]').value;
        if (!catgirlName) {
            if (typeof showAlert === 'function') {
                await showAlert(window.t ? window.t('character.fillProfileNameFirst') : '请先填写猫娘档案名，然后再设置模型');
            }
            return;
        }
        // 新建猫娘时，先临时保存（自动创建角色记录），确保模型管理器能正确关联
        if (isNew && !form._autoCreated) {
            try {
                const tmpResp = await fetch('/api/characters/catgirl', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ '档案名': catgirlName })
                });
                if (tmpResp.ok) {
                    form._autoCreated = true;
                    form._autoCreatedName = catgirlName;
                } else {
                    const errData = await tmpResp.json().catch(() => ({}));
                    showMessage((window.t ? window.t('character.tempSaveFailed', { error: errData.error || '' }) : '临时保存失败: ' + (errData.error || '')), 'error');
                    return;
                }
            } catch (e) {
                showMessage((window.t ? window.t('character.tempSaveFailed', { error: e.message }) : '临时保存失败: ' + e.message), 'error');
                return;
            }
        }
        const url = '/model_manager?lanlan_name=' + encodeURIComponent(catgirlName);
        if (!window._openSettingsWindows) window._openSettingsWindows = {};
        if (window._openSettingsWindows[url]) {
            const existingWindow = window._openSettingsWindows[url];
            if (existingWindow && !existingWindow.closed) {
                existingWindow.focus();
                return;
            } else {
                delete window._openSettingsWindows[url];
            }
        }
        const popup = window.open(url, '_blank',
            'toolbar=no,location=no,status=no,menubar=no,scrollbars=yes,resizable=yes,width=' + screen.availWidth + ',height=' + screen.availHeight + ',top=0,left=0');
        if (!popup) {
            if (typeof showAlert === 'function') await showAlert(window.t ? window.t('character.allowPopups') : '请允许弹窗！');
            return;
        }
        window._openSettingsWindows[url] = popup;
        popup.moveTo(0, 0);
        popup.resizeTo(screen.availWidth, screen.availHeight);
        const timer = setInterval(async () => {
            if (popup.closed) {
                clearInterval(timer);
                if (window._openSettingsWindows[url] === popup) delete window._openSettingsWindows[url];
                loadCharacterCards();
                // 模型管理器关闭后，重新获取角色数据并更新模型显示名称
                try {
                    const resp = await fetch('/api/characters/');
                    if (resp.ok) {
                        const allData = await resp.json();
                        const updatedCat = allData?.['猫娘']?.[catgirlName];
                        if (!updatedCat) throw new Error('catgirl not found');
                        const updModelType = updatedCat['model_type'] || 'live2d';
                        const updNormType = updModelType === 'vrm' ? 'live3d' : updModelType;
                        const updMmd = validateModelPath(updatedCat['mmd'])
                            || validateModelPath(updatedCat['_reserved']?.avatar?.mmd?.model_path);
                        const updVrm = validateModelPath(updatedCat['vrm'])
                            || validateModelPath(updatedCat['_reserved']?.avatar?.vrm?.model_path);
                        const updLive2d = validateModelPath(updatedCat['live2d']);
                        const updSubType = String(
                            updatedCat['_reserved']?.avatar?.live3d_sub_type || updatedCat['live3d_sub_type'] || ''
                        ).trim().toLowerCase();
                        let newDisplayText = '';
                        if (updNormType === 'live3d' && updSubType === 'mmd' && updMmd) {
                            newDisplayText = (updMmd.split(/[\\/]/).pop() || updMmd).replace(/\.(pmx|pmd)$/i, '');
                        } else if (updNormType === 'live3d' && updSubType === 'vrm' && updVrm) {
                            newDisplayText = (updVrm.split(/[\\/]/).pop() || updVrm).replace(/\.vrm$/i, '');
                        } else if (updNormType === 'live3d' && updMmd && !updVrm) {
                            newDisplayText = (updMmd.split(/[\\/]/).pop() || updMmd).replace(/\.(pmx|pmd)$/i, '');
                        } else if (updNormType === 'live3d' && updVrm) {
                            newDisplayText = (updVrm.split(/[\\/]/).pop() || updVrm).replace(/\.vrm$/i, '');
                        } else if (updLive2d) {
                            newDisplayText = updLive2d;
                        }
                        modelLink.textContent = newDisplayText || (window.t ? window.t('character.modelNotSet') : '未设置');
                    }
                } catch (e) {
                    console.warn('[Panel] 更新模型显示名称失败:', e);
                }
            }
        }, 500);
    };

    // 音色设定
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

    // 注册新声音按钮
    const registerVoiceBtn = document.createElement('button');
    registerVoiceBtn.type = 'button';
    registerVoiceBtn.className = 'btn sm';
    registerVoiceBtn.style.marginLeft = '8px';
    registerVoiceBtn.style.minWidth = '120px';
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
    foldContent.appendChild(voiceWrapper);

    fold.appendChild(foldContent);
    form.appendChild(fold);

    // 操作按钮区
    const btnArea = document.createElement('div');
    btnArea.className = 'btn-area';
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
    saveButton.className = 'btn sm';
    saveButton.style.minWidth = '120px';
    if (!isNew) saveButton.style.display = 'none';
    saveButton.textContent = isNew
        ? (window.t ? window.t('character.confirmNewCatgirl') : '确认新猫娘')
        : (window.t ? window.t('character.saveChanges') : '保存修改');
    saveButton.onclick = function () { saveCatgirlFromPanel(form, name, isNew); };
    btnArea.appendChild(saveButton);

    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.id = 'cancel-button';
    cancelButton.className = 'btn sm';
    cancelButton.style.minWidth = '120px';
    if (!isNew) cancelButton.style.display = 'none';
    cancelButton.textContent = window.t ? window.t('character.cancel') : '取消';
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
    const voicesLoadPromise = _loadPanelVoices(voiceSelect, String(cat['voice_id'] || '').trim());
    form._voicesLoadPromise = voicesLoadPromise;
    form._previousVoiceId = String(cat['voice_id'] || '').trim();
    form._live2dModel = live2dPath;
    form._modelType = normalizedModelType;

    // 恢复进阶设定折叠状态
    if (name) {
        setTimeout(() => {
            const savedState = localStorage.getItem('catgirl_advanced_' + name);
            if (savedState === 'false') {
                fold.classList.remove('open');
                arrowSpan.style.transform = 'rotate(-90deg)';
            }
        }, 0);
    }

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
        const translated = window.t('character.field.' + key);
        if (translated && translated !== 'character.field.' + key) {
            displayText = translated;
        }
    }
    labelEl.textContent = displayText;
    if (displayText.length > MAX_LABEL_LEN) {
        labelEl.title = displayText;
    }
}

// textarea自动调整高度（匹配原版逻辑：三行最大高度 + scrollbar类切换）
function _panelAttachTextareaAutoResize(textarea) {
    if (!textarea || textarea.dataset.autoResizeAttached) return;
    textarea.dataset.autoResizeAttached = 'true';

    function resize() {
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

    textarea.addEventListener('input', resize);
    textarea.addEventListener('focus', resize);
    resize();
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

            // 添加音色选项
            const voiceOwners = data.voice_owners || {};
            Object.entries(data.voices).forEach(function ([voiceId, voiceData]) {
                const option = document.createElement('option');
                option.value = voiceId;
                // 显示名称：优先用 voice_owners，其次 voiceData.name，最后 voiceId
                let displayName = voiceId;
                if (voiceOwners[voiceId]) {
                    displayName = voiceOwners[voiceId] + ' - ' + voiceId;
                } else if (voiceData && voiceData.name) {
                    displayName = voiceData.name;
                }
                option.textContent = displayName;
                option.title = voiceId;
                if (voiceId === currentVoiceId) option.selected = true;
                selectEl.appendChild(option);
            });

            // 免费预设音色
            if (data.free_voices && Object.keys(data.free_voices).length > 0) {
                const freeGroup = document.createElement('optgroup');
                const freeLabel = window.t ? window.t('character.freePresetVoices') : '免费预设音色';
                freeGroup.label = '── ' + freeLabel + ' ──';
                Object.entries(data.free_voices).forEach(function ([voiceKey, voiceId]) {
                    const option = document.createElement('option');
                    option.value = voiceId;
                    option.textContent = window.t ? window.t('voice.freeVoice.' + voiceKey) : voiceKey;
                    if (voiceId === currentVoiceId) option.selected = true;
                    freeGroup.appendChild(option);
                });
                selectEl.appendChild(freeGroup);
            }
        }

        // 加载 GPT-SoVITS 声音列表
        await _loadPanelGsvVoices(selectEl, currentVoiceId);
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
            const gsvLabel = window.t ? window.t('character.gptsovitsVoices') : 'GPT-SoVITS 声音';
            gsvGroup.label = '── ' + gsvLabel + ' ──';
            gsvGroup.dataset.gsvGroup = 'true';
            selectEl.appendChild(gsvGroup);
        }
        const fallbackOpt = document.createElement('option');
        fallbackOpt.value = currentVoiceId;
        fallbackOpt.textContent = currentVoiceId.substring(GSV_PREFIX.length) + ' (?)';
        gsvGroup.appendChild(fallbackOpt);
        selectEl.value = currentVoiceId;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    try {
        const resp = await fetch('/api/characters/custom_tts_voices', { signal: controller.signal });
        clearTimeout(timeoutId);
        const result = await resp.json();
        if (result.success && Array.isArray(result.voices) && result.voices.length > 0) {
            const gsvGroup = document.createElement('optgroup');
            const gsvLabel = window.t ? window.t('character.gptsovitsVoices') : 'GPT-SoVITS 声音';
            gsvGroup.label = '── ' + gsvLabel + ' ──';
            gsvGroup.dataset.gsvGroup = 'true';
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
        }
        ensureGsvFallback();
    } catch (e) {
        clearTimeout(timeoutId);
        console.debug('GPT-SoVITS voices not available:', e.message);
        ensureGsvFallback();
    }
}

async function saveCatgirlFromPanel(form, originalName, isNew) {
    // 防止重复提交
    if (form.dataset.submitting === 'true') {
        console.log('表单正在提交中，忽略重复提交');
        return;
    }
    form.dataset.submitting = 'true';

    try {
        // 等待音色加载完成
        if (form._voicesLoadPromise) {
            await form._voicesLoadPromise;
        }

        const data = {};

        // 收集表单数据
        const nameInput = form.querySelector('input[name="档案名"]');
        if (!nameInput || !nameInput.value.trim()) {
            showMessage(window.t ? window.t('character.profileNameRequired') : '请输入档案名', 'error');
            return;
        }
        data['档案名'] = nameInput.value.trim();

        // 收集已有字段（通过 FormData 统一收集，跳过voice_id）
        const fd = new FormData(form);
        const selectedVoiceId = (form.querySelector('select[name="voice_id"]')?.value ?? '').trim();
        const previousVoiceId = form._previousVoiceId || '';

        for (const [k, v] of fd.entries()) {
            if (k === 'voice_id') continue;
            const normalizedValue = typeof v === 'string' ? v.trim() : v;
            if (k && normalizedValue) {
                data[k] = normalizedValue;
            }
        }

        // 如果新建猫娘已被临时保存（自动创建），则改用 PUT 更新
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
            showMessage((window.t ? window.t('character.saveFailedWithError') : '保存失败: ') + errorMessage, 'error');
            return;
        }

        const result = await response.json();
        if (result.success === false) {
            showMessage(result.error || (window.t ? window.t('character.saveFailed') : '保存失败'), 'error');
            return;
        }

        // voice_id 通过专用接口更新
        if (selectedVoiceId !== previousVoiceId) {
            if (selectedVoiceId) {
                try {
                    const voiceResp = await fetch('/api/characters/catgirl/voice_id/' + encodeURIComponent(data['档案名']), {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ voice_id: selectedVoiceId })
                    });
                    const voiceResult = await voiceResp.json().catch(() => ({}));
                    if (!voiceResp.ok || voiceResult.success === false) {
                        const detail = (voiceResult && voiceResult.error) || (voiceResp.status + ' ' + voiceResp.statusText);
                        showMessage(
                            (window.t ? window.t('character.partialSaveVoiceFailed') : '角色已保存，但音色更新失败: ') + detail,
                            'error'
                        );
                    }
                } catch (voiceErr) {
                    showMessage(
                        (window.t ? window.t('character.partialSaveVoiceFailed') : '角色已保存，但音色更新失败: ') + (voiceErr.message || String(voiceErr)),
                        'error'
                    );
                }
            } else if (previousVoiceId) {
                try {
                    const clearResp = await fetch('/api/characters/catgirl/' + encodeURIComponent(data['档案名']) + '/unregister_voice', {
                        method: 'POST'
                    });
                    const clearResult = await clearResp.json().catch(() => ({}));
                    if (!clearResp.ok || clearResult.success === false) {
                        const detail = (clearResult && clearResult.error) || (clearResp.status + ' ' + clearResp.statusText);
                        showMessage(
                            (window.t ? window.t('character.partialSaveVoiceFailed') : '角色已保存，但音色更新失败: ') + detail,
                            'error'
                        );
                    }
                } catch (clearErr) {
                    showMessage(
                        (window.t ? window.t('character.partialSaveVoiceFailed') : '角色已保存，但音色更新失败: ') + (clearErr.message || String(clearErr)),
                        'error'
                    );
                }
            }
        }

        // 保存 Live2D 待机动作（如果当前是 Live2D 模型且动作选择器有值）
        if (!isNew && form._modelType === 'live2d' && form._live2dModel) {
            const motionSelect = document.getElementById('preview-motion-select');
            const idleAnimation = motionSelect ? (motionSelect.value || '') : '';
            try {
                const l2dResp = await fetch('/api/characters/catgirl/l2d/' + encodeURIComponent(data['档案名']), {
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

        showMessage(isNew
            ? (window.t ? window.t('character.newCatgirlSuccess') : '新猫娘创建成功')
            : (window.t ? window.t('character.saveSuccess') : '保存成功'), 'success');
        if (isNew) {
            closeCatgirlPanel();
        } else {
            const container = form.parentNode;
            const saveBtn = form.querySelector('#save-button');
            const cancelBtn = form.querySelector('#cancel-button');
            if (saveBtn) saveBtn.style.display = 'none';
            if (cancelBtn) cancelBtn.style.display = 'none';
            try {
                const freshData = await loadCharacterData();
                if (freshData && freshData['猫娘'] && freshData['猫娘'][data['档案名']]) {
                    buildCatgirlDetailForm(data['档案名'], freshData['猫娘'][data['档案名']], false, container);
                }
            } catch (e) {
                console.error('重新加载猫娘数据失败:', e);
            }
        }
        await loadCharacterCards();
    } catch (error) {
        console.error('保存猫娘失败:', error);
        showMessage(window.t ? window.t('character.saveError') : '保存时发生错误: ' + error.message, 'error');
    } finally {
        form.dataset.submitting = 'false';
    }
}

// 切换猫娘
async function workshopSwitchCatgirl(name) {
    try {
        const response = await fetch('/api/characters/current_catgirl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ catgirl_name: name })
        });
        const result = await response.json();
        if (result.success) {
            window._workshopCurrentCatgirl = name;
            renderCharaCardsView();
            showMessage(window.t ? window.t('character.switchSuccess') : '切换成功', 'success');
        } else {
            showMessage(result.error || (window.t ? window.t('character.switchFailed') : '切换失败'), 'error');
        }
    } catch (error) {
        console.error('切换猫娘失败:', error);
        showMessage(window.t ? window.t('character.switchError') : '切换猫娘时发生错误', 'error');
    }
}

// 删除猫娘
async function workshopDeleteCatgirl(name) {
    // 检查是否为当前猫娘
    if (name === window._workshopCurrentCatgirl) {
        showMessage(window.t ? window.t('character.cannotDeleteCurrentCard') : '不能删除当前正在使用的角色卡', 'error');
        return;
    }

    // 检查是否只剩一只猫娘
    try {
        const resp = await fetch('/api/characters/', { cache: 'no-store' });
        if (resp.ok) {
            const allData = await resp.json();
            const catgirls = allData?.['猫娘'] || {};
            if (Object.keys(catgirls).length <= 1) {
                showMessage(window.t ? window.t('character.onlyOneCatgirlLeft') : '只剩一只猫娘，无法删除！', 'error');
                return;
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
    if (!confirmed) return;

    try {
        await fetch('/api/characters/catgirl/' + encodeURIComponent(name), { method: 'DELETE' });
        // 重新加载角色卡列表
        await loadCharacterCards();
    } catch (error) {
        console.error('删除猫娘失败:', error);
        showMessage(window.t ? window.t('character.deleteError') : '删除猫娘时发生错误', 'error');
    }
}

// ====== 占位符环形3D文字 ======
var GLITCH_TIMINGS = [
    {dur:'4.8s',delay:'0s'},   {dur:'5.3s',delay:'1.2s'},
    {dur:'4.5s',delay:'2.7s'}, {dur:'5.7s',delay:'0.4s'},
    {dur:'4.2s',delay:'3.5s'}, {dur:'5.1s',delay:'1.8s'},
    {dur:'4.9s',delay:'2.1s'}, {dur:'5.4s',delay:'0.9s'},
    {dur:'4.6s',delay:'3.2s'},
];
var lastCustomRingText = null;

function buildPreviewRing(customText) {
    var container = document.getElementById('preview-ring-container');
    if (!container) return;
    var text;
    if (customText && typeof customText === 'string') {
        lastCustomRingText = customText;
        text = customText;
    } else if (lastCustomRingText) {
        text = lastCustomRingText;
    } else {
        var key = 'steam.selectCharaToPreview';
        var raw = (typeof window.t === 'function') ? window.t(key) : null;
        text = (raw && raw !== key) ? raw : '请选择角色进行预览';
    }
    var base = Array.from(text);
    var chars = base.concat(base).concat(base);

    var groupSize = base.length;
    var gapExtra = 0.3;
    var totalSlots = chars.length + gapExtra * 3;

    var placeholder = container.closest('.preview-placeholder');
    var availH = placeholder ? placeholder.clientHeight : 0;
    var availW = placeholder ? placeholder.clientWidth : 0;
    var nominalRadius = Math.ceil(totalSlots * 50 / (2 * Math.PI));
    var limits = [];
    if (availH > 80) limits.push((availH - 50) * 0.65);
    if (availW > 80) limits.push((availW - 50 - 42) / 2);
    var containerDriven = limits.length ? Math.max(200, Math.min.apply(null, limits)) : 200;
    var radius = Math.min(nominalRadius, containerDriven);

    var arcPerSlot = radius * 2 * Math.PI / totalSlots;
    var fontSize = Math.max(14, Math.min(42, Math.floor(arcPerSlot) - 4));
    container.style.setProperty('--ring-char-size', fontSize + 'px');

    var yComp = Math.round(radius * Math.sin(10 * Math.PI / 180) * -0.1);
    var tiltDiv = container.closest('.preview-ring-tilt');
    if (tiltDiv) {
        tiltDiv.style.transform = 'translateY(' + yComp + 'px) rotateX(-10deg)';
    }
    container.innerHTML = '';
    chars.forEach(function(ch, i) {
        var group = Math.floor(i / groupSize);
        var posInGroup = i % groupSize;
        var slotIndex = group * (groupSize + gapExtra) + posInGroup;
        var angle = (slotIndex / totalSlots) * 360;
        var span = document.createElement('span');
        span.className = 'ring-char';
        span.textContent = ch;
        span.setAttribute('data-char', ch);
        var t = GLITCH_TIMINGS[i % GLITCH_TIMINGS.length];
        span.style.setProperty('--gdur', t.dur);
        span.style.setProperty('--gdelay', t.delay);
        span.style.transform = 'rotateY(' + angle + 'deg) translateZ(' + radius + 'px)';
        container.appendChild(span);
    });
}
window.buildPreviewRing = buildPreviewRing;

// ====== Steam 标签页内容构建 ======
function buildSteamTabContent(name, rawData, card, container) {
    container.innerHTML = '';

    // 主布局容器
    const layout = document.createElement('div');
    layout.className = 'character-card-layout';
    layout.id = 'character-card-layout';
    layout.style.display = 'flex';

    // ── 上方区域：角色卡信息 + Live2D预览 ──
    const topRow = document.createElement('div');
    topRow.className = 'character-card-top-row';

    // 左上：角色卡信息
    const infoSection = document.createElement('div');
    infoSection.className = 'character-card-info-section';

    const infoLogo = document.createElement('img');
    infoLogo.src = '/static/icons/logo_show.png';
    infoLogo.className = 'card-info-logo';
    infoLogo.alt = '';
    infoSection.appendChild(infoLogo);

    // 标题区
    const headerRow = document.createElement('div');
    headerRow.className = 'card-info-header-row';
    headerRow.innerHTML = `
        <svg class="card-info-bg-hexagons" viewBox="-10 -10 370 310" xmlns="http://www.w3.org/2000/svg">
            <defs><polygon id="hex-header-shape-p" points="25,5 75,5 100,48 75,91 25,91 0,48" fill="#8cd5ff" stroke="#8cd5ff" stroke-width="8" stroke-linejoin="round"/></defs>
            <use href="#hex-header-shape-p" x="120" y="0" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="240" y="50" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="0" y="50" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="120" y="99" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="240" y="149" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="0" y="149" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="120" y="198" opacity="0.05"/>
        </svg>
        <div class="card-info-title-area">
            <div class="card-info-header-text">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 2 L14.5 9.5 L22 12 L14.5 14.5 L12 22 L9.5 14.5 L2 12 L9.5 9.5 Z" stroke="#7EC8E3" stroke-width="2" stroke-linejoin="round" fill="white"/></svg>
                <span data-i18n="steam.cardInfoPreview">${window.t ? window.t('steam.cardInfoPreview') : '角色卡信息'}</span>
            </div>
            <img src="/static/icons/paw_ui.png" class="card-info-paw" alt="">
        </div>`;
    infoSection.appendChild(headerRow);

    // 信息正文
    const infoBody = document.createElement('div');
    infoBody.className = 'card-info-body';
    infoBody.innerHTML = `
        <svg class="card-info-bg-hexagons" viewBox="-10 -10 370 310" xmlns="http://www.w3.org/2000/svg">
            <defs><polygon id="hex-body-shape-p" points="25,5 75,5 100,48 75,91 25,91 0,48" fill="#8cd5ff" stroke="#8cd5ff" stroke-width="8" stroke-linejoin="round"/></defs>
            <use href="#hex-body-shape-p" x="120" y="0" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="240" y="50" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="0" y="50" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="120" y="99" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="240" y="149" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="0" y="149" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="120" y="198" opacity="0.05"/>
        </svg>
        <div class="card-info-body-scroll">
            <svg class="card-info-bg-stars" xmlns="http://www.w3.org/2000/svg">
                <defs>
                    <linearGradient id="card-star-gradient-p" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" stop-color="#ffffff"/><stop offset="100%" stop-color="#8cd5ff"/>
                    </linearGradient>
                    <symbol id="card-rounded-star-p" viewBox="0 0 24 24">
                        <path d="M 12 3 Q 12 12 21 12 Q 12 12 12 21 Q 12 12 3 12 Q 12 12 12 3 Z" fill="#ffffff" stroke="#ffffff" stroke-width="3.5" stroke-linejoin="round"/>
                    </symbol>
                    <pattern id="card-star-pattern-p" x="0" y="0" width="80" height="80" patternUnits="userSpaceOnUse">
                        <use href="#card-rounded-star-p" x="5" y="5" width="15" height="15"/>
                        <use href="#card-rounded-star-p" x="45" y="45" width="15" height="15"/>
                    </pattern>
                    <mask id="card-stars-mask-p"><rect width="100%" height="100%" fill="url(#card-star-pattern-p)"/></mask>
                </defs>
                <rect width="100%" height="100%" fill="url(#card-star-gradient-p)" mask="url(#card-stars-mask-p)"/>
            </svg>
            <div id="card-info-preview">
                <div id="card-info-dynamic-content">
                    <p style="color: #999; text-align: center;" data-i18n="steam.selectCharacterCard">${window.t ? window.t('steam.selectCharacterCard') : '请选择一个角色卡'}</p>
                </div>
            </div>
        </div>`;
    infoSection.appendChild(infoBody);
    topRow.appendChild(infoSection);

    // 右上：模型预览
    const live2dSection = document.createElement('div');
    live2dSection.className = 'character-card-live2d-section';

    const previewTitle = document.createElement('h3');
    previewTitle.id = 'model-preview-title';
    previewTitle.setAttribute('data-i18n', 'steam.live2dPreview');
    previewTitle.textContent = 'Live2D';
    live2dSection.appendChild(previewTitle);

    const previewContainer = document.createElement('div');
    previewContainer.id = 'live2d-preview-container';

    previewContainer.innerHTML = `
        <div id="live2d-preview-content" style="flex: 1; position: relative; min-height: 0; pointer-events: none; background-color: transparent;">
            <canvas id="live2d-preview-canvas" style="display: none; width: 100%; height: 100%; position: absolute; top: 0; left: 0; pointer-events: none;"></canvas>
            <div id="vrm-preview-container" style="display: none; width: 100%; height: 100%; position: absolute; top: 0; left: 0;">
                <canvas id="vrm-preview-canvas" style="width: 100%; height: 100%;"></canvas>
            </div>
            <div id="mmd-preview-container" style="display: none; width: 100%; height: 100%; position: absolute; top: 0; left: 0;">
                <canvas id="mmd-preview-canvas" style="width: 100%; height: 100%;"></canvas>
            </div>
            <div class="preview-placeholder" style="display: flex; justify-content: center; align-items: center; height: 100%; position: relative; z-index: 1; background-color: transparent;">
                <div class="preview-ring-perspective">
                    <div class="preview-ring-tilt">
                        <div id="preview-ring-container" class="preview-ring-container"></div>
                    </div>
                </div>
            </div>
            <div id="live2d-preview-overlay" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 100; pointer-events: auto;"></div>
            <button id="live2d-refresh-btn" style="position: absolute; top: 10px; right: 10px; z-index: 101; width: 30px; height: 30px; border: none; border-radius: 50%; background-color: transparent; color: white; cursor: pointer; display: flex; justify-content: center; align-items: center; font-size: 16px; pointer-events: auto;" title="${window.t ? window.t('steam.refreshLive2DPreview') : '刷新Live2D预览'}" onclick="refreshLive2DPreview()">↻</button>
        </div>`;
    live2dSection.appendChild(previewContainer);

    // 动作/表情控件
    const controlsDiv = document.createElement('div');
    controlsDiv.id = 'live2d-preview-controls';
    controlsDiv.style.cssText = 'padding: 10px; background-color: #fff; border-top: 1px solid #e0e0e0; margin: 10px 10px 10px 10px; border-radius: 16px;';
    controlsDiv.innerHTML = `
        <div style="display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 150px;">
                <select id="preview-motion-select" class="control-input" style="width: 100%;">
                    <option value="" data-i18n="steam.selectMotion">${window.t ? window.t('steam.selectMotion') : '选择动作'}</option>
                </select>
                <div style="font-size: 11px; color: #888; margin-top: 3px; text-align: center;" data-i18n="character.idleMotionHint">${window.t ? window.t('character.idleMotionHint') : '保存角色时，当前选中的动作将被设为待机动作'}</div>
            </div>
            <div class="btn-play-wrapper">
                <button id="preview-play-motion-btn" class="btn" disabled>
                    <span data-i18n="steam.playMotion">${window.t ? window.t('steam.playMotion') : '播放动作'}</span>
                </button>
            </div>
        </div>
        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 150px;">
                <select id="preview-expression-select" class="control-input" style="width: 100%;">
                    <option value="" data-i18n="steam.selectExpression">${window.t ? window.t('steam.selectExpression') : '选择表情'}</option>
                </select>
            </div>
            <div class="btn-play-wrapper">
                <button id="preview-play-expression-btn" class="btn" disabled>
                    <span data-i18n="steam.playExpression">${window.t ? window.t('steam.playExpression') : '播放表情'}</span>
                </button>
            </div>
        </div>`;
    live2dSection.appendChild(controlsDiv);
    topRow.appendChild(live2dSection);
    layout.appendChild(topRow);

    // ── 下方区域：描述 + 标签和按钮 ──
    const bottomRow = document.createElement('div');
    bottomRow.className = 'character-card-bottom-row';

    // 左下：描述区域
    const descSection = document.createElement('div');
    descSection.className = 'character-card-description-section';

    // 描述标题栏
    const descHeader = document.createElement('div');
    descHeader.className = 'description-header-row';
    descHeader.innerHTML = `
        <div class="description-title-area">
            <div class="description-header-text">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 2 L14.5 9.5 L22 12 L14.5 14.5 L12 22 L9.5 14.5 L2 12 L9.5 9.5 Z" stroke="#7EC8E3" stroke-width="2" stroke-linejoin="round" fill="white"/></svg>
                <span data-i18n="steam.characterCardDescription">${window.t ? window.t('steam.characterCardDescription') : '描述'}</span>
                <img src="/static/icons/paw_ui.png" class="description-paw" alt="">
            </div>
        </div>`;
    descSection.appendChild(descHeader);

    // 版权警告
    const copyrightWarning = document.createElement('div');
    copyrightWarning.id = 'copyright-warning';
    copyrightWarning.style.cssText = 'display: none; padding: 8px; background-color: #f8d7da; border: 1px solid #f5c6cb; border-radius: 4px; color: #721c24; margin-bottom: 8px; margin-top: 8px;';
    copyrightWarning.innerHTML = `<strong>⚠️</strong> <span data-i18n="steam.modelCopyrightIssue">${window.t ? window.t('steam.modelCopyrightIssue') : '您的角色形象存在版权问题，无法上传'}</span>`;
    descSection.appendChild(copyrightWarning);

    // 描述输入
    const descGroup = document.createElement('div');
    descGroup.className = 'control-group description-content';
    const descTextarea = document.createElement('textarea');
    descTextarea.id = 'character-card-description';
    descTextarea.className = 'control-input';
    descTextarea.style.cssText = 'white-space: pre-wrap; min-height: 100px; resize: none; overflow-y: auto;';
    descTextarea.placeholder = window.t ? window.t('steam.placeholderCharacterDescription') : '输入角色描述...';
    descTextarea.addEventListener('input', function () {
        if (typeof updateCardPreview === 'function') updateCardPreview();
    });
    descGroup.appendChild(descTextarea);
    descSection.appendChild(descGroup);

    // Workshop 状态区域
    const statusArea = document.createElement('div');
    statusArea.id = 'workshop-status-area';
    statusArea.style.cssText = 'display: none; padding: 8px; background-color: #e7f3ff; border: 1px solid #b3d7ff; border-radius: 4px; margin-top: 8px;';
    statusArea.innerHTML = `
        <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
            <div>
                <strong style="color: #0066cc;">✅ <span data-i18n="steam.alreadyUploaded">${window.t ? window.t('steam.alreadyUploaded') : '已上传到创意工坊'}</span></strong>
                <div style="font-size: 12px; color: #666; margin-top: 4px;">
                    <span data-i18n="steam.uploadTime">${window.t ? window.t('steam.uploadTime') : '上传时间'}</span>：<span id="workshop-upload-time">-</span>
                </div>
                <div style="font-size: 12px; color: #666;">
                    <span data-i18n="steam.workshopItemId">${window.t ? window.t('steam.workshopItemId') : '物品ID'}</span>：<span id="workshop-item-id">-</span>
                </div>
            </div>
            <button class="btn btn-secondary btn-sm" onclick="showWorkshopSnapshot()" style="white-space: nowrap;">
                📋 <span data-i18n="steam.viewSnapshot">${window.t ? window.t('steam.viewSnapshot') : '查看已上传版本'}</span>
            </button>
        </div>`;
    descSection.appendChild(statusArea);
    bottomRow.appendChild(descSection);

    // 右下：标签和按钮区域
    const tagsButtonsSection = document.createElement('div');
    tagsButtonsSection.className = 'character-card-tags-buttons-section';

    // 标签区域
    const tagsArea = document.createElement('div');
    tagsArea.className = 'character-card-tags-area';

    const tagsLogo = document.createElement('img');
    tagsLogo.src = '/static/icons/logo_show.png';
    tagsLogo.className = 'card-info-logo';
    tagsLogo.alt = '';
    tagsArea.appendChild(tagsLogo);

    // 标签标题栏
    const tagsHeaderRow = document.createElement('div');
    tagsHeaderRow.className = 'tags-header-row';
    tagsHeaderRow.innerHTML = `
        <div class="tags-title-area">
            <div class="tags-header-text">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 2 L14.5 9.5 L22 12 L14.5 14.5 L12 22 L9.5 14.5 L2 12 L9.5 9.5 Z" stroke="#7EC8E3" stroke-width="2" stroke-linejoin="round" fill="white"/></svg>
                <span data-i18n="steam.characterCardTags">${window.t ? window.t('steam.characterCardTags') : '角色卡标签'}</span>
            </div>
            <img src="/static/icons/paw_ui.png" class="tags-paw" alt="">
        </div>`;
    tagsArea.appendChild(tagsHeaderRow);

    // 标签输入
    const tagsControlGroup = document.createElement('div');
    tagsControlGroup.className = 'control-group tags-content';
    const tagInput = document.createElement('input');
    tagInput.type = 'text';
    tagInput.id = 'character-card-tag-input';
    tagInput.className = 'control-input';
    tagInput.placeholder = window.t ? window.t('steam.tagsPlaceholderSpace') : '输入标签，按空格添加';

    // 标签输入事件
    tagInput.addEventListener('input', function (e) {
        if (e.target.value.endsWith(' ') && e.target.value.trim() !== '') {
            e.preventDefault();
            if (typeof addTag === 'function') addTag(e.target.value.trim(), 'character-card');
            e.target.value = '';
        }
    });
    tagInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter' && e.target.value.trim() !== '') {
            e.preventDefault();
            if (typeof addTag === 'function') addTag(e.target.value.trim(), 'character-card');
            e.target.value = '';
        }
    });
    tagsControlGroup.appendChild(tagInput);

    const tagsWrapper = document.createElement('div');
    tagsWrapper.id = 'character-card-tags-wrapper';
    tagsWrapper.style.cssText = 'display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 15px; border: 2px solid #b3e5fc; border-radius: 50px; background-color: #fff; min-height: 40px; box-sizing: border-box; margin-top: 5px;';
    const tagsContainer = document.createElement('div');
    tagsContainer.className = 'tags-container';
    tagsContainer.id = 'character-card-tags-container';
    tagsWrapper.appendChild(tagsContainer);
    tagsControlGroup.appendChild(tagsWrapper);
    tagsArea.appendChild(tagsControlGroup);
    tagsButtonsSection.appendChild(tagsArea);

    // 无可上传模型警告
    const noModelsWarning = document.createElement('div');
    noModelsWarning.id = 'no-uploadable-models-warning';
    noModelsWarning.style.cssText = 'display: none; padding: 10px; background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; color: #856404; font-size: 14px; margin-top: 15px;';
    noModelsWarning.innerHTML = `<span data-i18n="steam.noUploadableModels">${window.t ? window.t('steam.noUploadableModels') : '没有可上传的模型，请先在角色管理页面创建自定义模型'}</span>`;
    tagsButtonsSection.appendChild(noModelsWarning);

    // 按钮行
    const buttonsRow = document.createElement('div');
    buttonsRow.className = 'character-card-buttons-row';

    // 上传按钮
    const uploadWrapper = document.createElement('div');
    uploadWrapper.className = 'btn-wrapper';
    const uploadBtn = document.createElement('button');
    uploadBtn.id = 'upload-to-workshop-btn';
    uploadBtn.className = 'btn';
    uploadBtn.disabled = true;
    uploadBtn.style.cssText = 'display: flex; align-items: center; justify-content: center; gap: 6px;';
    uploadBtn.onclick = function () { if (typeof handleUploadToWorkshop === 'function') handleUploadToWorkshop(); };
    const uploadIcon = document.createElement('img');
    uploadIcon.src = '/static/icons/upload_icon.png';
    uploadIcon.style.cssText = 'width: 34px; height: 34px;';
    uploadBtn.appendChild(uploadIcon);
    const uploadText = document.createElement('span');
    uploadText.id = 'upload-btn-text';
    uploadText.setAttribute('data-i18n', 'steam.uploadToWorkshop');
    uploadText.textContent = window.t ? window.t('steam.uploadToWorkshop') : '上传到创意工坊';
    uploadBtn.appendChild(uploadText);
    uploadWrapper.appendChild(uploadBtn);
    buttonsRow.appendChild(uploadWrapper);

    // 在角色管理中编辑按钮
    const editWrapper = document.createElement('div');
    editWrapper.className = 'btn-wrapper';
    const editBtn = document.createElement('button');
    editBtn.className = 'btn';
    editBtn.style.cssText = 'display: flex; align-items: center; justify-content: center; gap: 6px;';
    editBtn.onclick = function () { window.location.href = '/character_card_manager'; };
    const editIcon = document.createElement('img');
    editIcon.src = '/static/icons/cat_icon.png';
    editIcon.style.cssText = 'width: 34px; height: 34px;';
    editBtn.appendChild(editIcon);
    const editText = document.createElement('span');
    editText.setAttribute('data-i18n', 'steam.editInCharaManager');
    editText.textContent = window.t ? window.t('steam.editInCharaManager') : '在角色管理中编辑';
    editBtn.appendChild(editText);
    editWrapper.appendChild(editBtn);
    buttonsRow.appendChild(editWrapper);

    tagsButtonsSection.appendChild(buttonsRow);
    bottomRow.appendChild(tagsButtonsSection);
    layout.appendChild(bottomRow);

    container.appendChild(layout);

    // 初始化预览环形文字
    requestAnimationFrame(function () {
        buildPreviewRing();
        requestAnimationFrame(buildPreviewRing);
        var placeholder = container.querySelector('#live2d-preview-container .preview-placeholder');
        if (placeholder && typeof ResizeObserver !== 'undefined') {
            new ResizeObserver(buildPreviewRing).observe(placeholder);
        }
    });

    // 使用 expandCharacterCardSection 填充数据
    if (card) {
        // 确保 card 有足够的信息
        const cardForExpand = {
            id: card.id || card.name || name,
            name: name,
            originalName: card.originalName || name,
            rawData: rawData,
            tags: card.tags || [],
            description: card.description || ''
        };

        // 确保角色卡列表中包含该卡
        if (window.characterCards) {
            const existingIdx = window.characterCards.findIndex(c => c.id === cardForExpand.id);
            if (existingIdx < 0) {
                window.characterCards.push(cardForExpand);
            }
        }

        expandCharacterCardSection(cardForExpand);
    }
}

// 展开角色卡区域并填充数据
function expandCharacterCardSection(card) {
    // 更新当前打开的角色卡ID
    currentCharacterCardId = card.id;

    // 立即更新角色卡预览，确保用户看到反馈
    updateCardPreview();

    // 获取原始数据，确保存在 - 兼容数据直接在card对象中的情况
    const rawData = card.rawData || card || {};

    // 提取所需信息，同时兼容中英文字段名称
    const nickname = rawData['昵称'] || rawData['档案名'] || rawData['name'] || card.name || '';
    const gender = rawData['性别'] || rawData['gender'] || '';
    const age = rawData['年龄'] || rawData['age'] || '';
    const description = rawData['描述'] || rawData['description'] || card.description || '';
    const systemPrompt = rawData['设定'] || rawData['system_prompt'] || rawData['prompt_setting'] || '';

    // 处理模型默认值 - 兼容 Live2D / VRM / MMD 三种模型类型
    let live2d = rawData['live2d'] || (rawData['model'] && rawData['model']['name']) || '';
    const modelType = rawData['model_type'] || 'live2d';
        const normalizeModelPath = value => {
            if (value && typeof value === 'object' && 'model_path' in value) {
                return String(value.model_path || '');
            }
            return String(value || '');
        };
        const vrmPath = normalizeModelPath(rawData['vrm']);
        const mmdPath = normalizeModelPath(rawData['mmd']);
    // 优先使用 live3d_sub_type（后端权威来源，含 _reserved 迁移路径）
    const explicitLive3dSubType = String(
        rawData['_reserved']?.avatar?.live3d_sub_type
        || rawData['live3d_sub_type']
        || ''
    ).trim().toLowerCase();

    // 判断实际模型类型：优先使用显式 live3d_sub_type，缺失时再根据路径区分 VRM/MMD
    let effectiveModelType = 'live2d';
    let effectiveModelPath = '';
    if (modelType === 'live3d' || modelType === 'vrm') {
        if (explicitLive3dSubType === 'mmd') {
            effectiveModelType = 'mmd';
            effectiveModelPath = mmdPath;
        } else if (explicitLive3dSubType === 'vrm') {
            effectiveModelType = 'vrm';
            effectiveModelPath = vrmPath;
        } else if (mmdPath && !vrmPath) {
            effectiveModelType = 'mmd';
            effectiveModelPath = mmdPath;
        } else if (vrmPath) {
            effectiveModelType = 'vrm';
            effectiveModelPath = vrmPath;
        }
    } else {
        effectiveModelType = 'live2d';
    }

    // 处理音色默认值
    let voiceId = rawData['voice_id'] || (rawData['voice'] && rawData['voice']['voice_id']);

    // 填充可编辑字段（Description 使用 textarea.value）
    const descEl = document.getElementById('character-card-description');
    if (descEl) descEl.value = description || '';

    // 存储当前角色卡的模型名称和类型供后续使用
    window.currentCharacterCardModel = (effectiveModelType !== 'live2d' && effectiveModelPath) ? effectiveModelPath : live2d;
    window.currentCharacterCardModelType = effectiveModelType;
    window.currentCharacterCardModelPath = effectiveModelPath;
    window._currentCardRawData = rawData;

    // 检查模型是否可上传（检查是否来自static目录）
    const uploadButton = document.getElementById('upload-to-workshop-btn');
    const copyrightWarning = document.getElementById('copyright-warning');
    const noModelsWarning = document.getElementById('no-uploadable-models-warning');

    // 根据模型类型检查是否可上传
    let isModelUploadable = false;
    let hasModel = false;
    if (effectiveModelType === 'vrm' && effectiveModelPath) {
        hasModel = true;
        // VRM：检查路径是否为用户目录（非 /static/vrm/）
        isModelUploadable = availableVrmModels.some(m => m.url === effectiveModelPath || m.path === effectiveModelPath);
        // 也可能路径匹配不上列表（例如路径格式差异），退而检查是否不在 static 目录
        if (!isModelUploadable && !effectiveModelPath.startsWith('/static/')) {
            isModelUploadable = true;
        }
    } else if (effectiveModelType === 'mmd' && effectiveModelPath) {
        hasModel = true;
        // MMD：检查路径是否为用户目录（非 /static/mmd/）
        isModelUploadable = availableMmdModels.some(m => m.url === effectiveModelPath);
        if (!isModelUploadable && !effectiveModelPath.startsWith('/static/')) {
            isModelUploadable = true;
        }
    } else if (live2d) {
        hasModel = true;
        // Live2D：原有逻辑
        const modelInfo = availableModels.find(m => m.name === live2d);
        isModelUploadable = modelInfo !== undefined;
    }

    // 同时检查系统提示词
    const hasSystemPrompt = systemPrompt && systemPrompt.trim() !== '';

    // 决定是否可以上传
    let canUpload = true;
    let disableReason = '';

    if (!hasModel) {
        // 没有模型
        canUpload = false;
        disableReason = window.t ? window.t('steam.noModelSelected') : '未选择模型';
        if (noModelsWarning) noModelsWarning.style.display = 'block';
        if (copyrightWarning) copyrightWarning.style.display = 'none';
    } else if (!isModelUploadable) {
        // 模型存在版权问题（来自static目录）
        canUpload = false;
        disableReason = window.t ? window.t('steam.modelCopyrightIssue') : '您的角色形象存在版权问题，无法上传';
        if (copyrightWarning) copyrightWarning.style.display = 'block';
        if (noModelsWarning) noModelsWarning.style.display = 'none';
    } else {
        // 可以上传
        if (copyrightWarning) copyrightWarning.style.display = 'none';
        if (noModelsWarning) noModelsWarning.style.display = 'none';
    }

    // 更新上传按钮状态
    if (uploadButton) {
        uploadButton.disabled = !canUpload;
        uploadButton.style.opacity = canUpload ? '' : '0.5';
        uploadButton.style.cursor = canUpload ? '' : 'not-allowed';
        uploadButton.title = canUpload ? '' : disableReason;
    }

    // 刷新预览
    if (effectiveModelType === 'vrm' && effectiveModelPath) {
        // 加载 VRM 3D 模型预览
        loadVrmPreview(effectiveModelPath, rawData);
    } else if (effectiveModelType === 'mmd' && effectiveModelPath) {
        // 加载 MMD 3D 模型预览
        loadMmdPreview(effectiveModelPath, rawData);
    } else if (live2d && live2d !== '') {
        // 清理可能残留的 3D 预览
        disposeWorkshopVrm();
        disposeWorkshopMmd();
        hideAll3DPreviews();
        // 恢复 Live2D 标题和控件
        const title = document.getElementById('model-preview-title');
        if (title) {
            title.textContent = 'Live2D';
            title.setAttribute('data-i18n', 'steam.live2dPreview');
        }
        const live2dControls = document.getElementById('live2d-preview-controls');
        if (live2dControls) live2dControls.style.display = '';
        const modelInfoForPreview = availableModels.find(model => model.name === live2d);
        loadLive2DModelByName(live2d, modelInfoForPreview);
    } else {
        // 角色未设置模型，清除现有预览并显示提示
        clearAllModelPreviews(true); // true 表示使用"未设置模型"的提示而非"请选择模型"
    }

    // 更新标签
    const tagsContainer = document.getElementById('character-card-tags-container');
    if (tagsContainer) {
        tagsContainer.innerHTML = '';
        if (card.tags && card.tags.length > 0) {
            card.tags.forEach(tag => {
                const tagElement = document.createElement('span');
                tagElement.className = 'tag';
                tagElement.textContent = tag;
                tagsContainer.appendChild(tagElement);
            });
        }
    }

    // 显示角色卡区域
    const characterCardLayout = document.getElementById('character-card-layout');
    if (characterCardLayout) {
        characterCardLayout.style.display = 'flex';
        requestAnimationFrame(() => {
            updateCharacterCardTagScrollControls();
        });

        // 仅在非面板上下文中滚动到角色卡区域
        if (!_catgirlPanelOpen) {
            characterCardLayout.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    // 获取并显示 Workshop 状态
    fetchWorkshopStatus(card.name);
}

// 存储当前角色卡的 Workshop 元数据
let currentWorkshopMeta = null;

// 获取 Workshop 状态
async function fetchWorkshopStatus(characterName) {
    const statusArea = document.getElementById('workshop-status-area');
    const uploadBtn = document.getElementById('upload-to-workshop-btn');
    const uploadBtnText = document.getElementById('upload-btn-text');

    // 重置状态
    statusArea.style.display = 'none';
    currentWorkshopMeta = null;
    if (uploadBtnText) {
        uploadBtnText.textContent = window.t ? window.t('steam.uploadToWorkshop') : '上传到创意工坊';
        uploadBtnText.setAttribute('data-i18n', 'steam.uploadToWorkshop');
    }

    try {
        const response = await fetch(`/api/steam/workshop/meta/${encodeURIComponent(characterName)}`);
        const data = await response.json();

        if (data.success && data.has_uploaded && data.meta) {
            currentWorkshopMeta = data.meta;

            // 显示状态区域
            statusArea.style.display = 'block';

            // 更新显示内容
            const uploadTime = document.getElementById('workshop-upload-time');
            const itemId = document.getElementById('workshop-item-id');

            if (uploadTime && data.meta.last_update) {
                const date = new Date(data.meta.last_update);
                uploadTime.textContent = date.toLocaleString();
            }

            if (itemId && data.meta.workshop_item_id) {
                itemId.textContent = data.meta.workshop_item_id;
            }

            // 修改按钮文字为"更新"
            if (uploadBtnText) {
                uploadBtnText.textContent = window.t ? window.t('steam.updateToWorkshop') : '更新到创意工坊';
                uploadBtnText.setAttribute('data-i18n', 'steam.updateToWorkshop');
            }

        }
    } catch (error) {
        console.error('获取 Workshop 状态失败:', error);
    }
}

// 显示 Workshop 快照
function showWorkshopSnapshot() {
    if (!currentWorkshopMeta || !currentWorkshopMeta.uploaded_snapshot) {
        showMessage(window.t ? window.t('steam.noSnapshotData') : '没有快照数据', 'warning');
        return;
    }

    const snapshot = currentWorkshopMeta.uploaded_snapshot;
    const modal = document.getElementById('workshopSnapshotModal');

    // 填充描述
    const descriptionEl = document.getElementById('snapshot-description');
    descriptionEl.textContent = snapshot.description || (window.t ? window.t('steam.noDescription') : '无描述');

    // 填充标签
    const tagsContainer = document.getElementById('snapshot-tags-container');
    tagsContainer.innerHTML = '';
    if (snapshot.tags && snapshot.tags.length > 0) {
        snapshot.tags.forEach(tag => {
            const tagEl = document.createElement('span');
            tagEl.className = 'tag';
            tagEl.style.cssText = `background-color: #e0e0e0; color: inherit; padding: 4px 8px; border-radius: 4px; font-size: 12px;`;
            tagEl.textContent = tag;
            tagsContainer.appendChild(tagEl);
        });
    } else {
        tagsContainer.textContent = window.t ? window.t('steam.noTags') : '无标签';
    }

    // 填充模型名称
    const modelEl = document.getElementById('snapshot-model');
    modelEl.textContent = snapshot.model_name || (window.t ? window.t('steam.unknownModel') : '未知模型');

    // 计算差异
    const diffArea = document.getElementById('snapshot-diff-area');
    const diffList = document.getElementById('snapshot-diff-list');
    diffList.innerHTML = '';

    let hasDiff = false;

    // 比较描述
    const currentDescription = document.getElementById('character-card-description')?.value.trim() || '';
    if (currentDescription !== (snapshot.description || '')) {
        const li = document.createElement('li');
        li.textContent = window.t ? window.t('steam.descriptionChanged') : '描述已修改';
        diffList.appendChild(li);
        hasDiff = true;
    }

    // 比较标签
    const currentTagElements = document.querySelectorAll('#character-card-tags-container .tag');
    const currentTags = Array.from(currentTagElements).map(el => el.textContent.replace('×', '').trim()).filter(t => t);
    const snapshotTags = snapshot.tags || [];
    if (JSON.stringify(currentTags.sort()) !== JSON.stringify(snapshotTags.sort())) {
        const li = document.createElement('li');
        li.textContent = window.t ? window.t('steam.tagsChanged') : '标签已修改';
        diffList.appendChild(li);
        hasDiff = true;
    }

    // 比较模型
    const currentModel = window.currentCharacterCardModel || '';
    if (currentModel && snapshot.model_name && currentModel !== snapshot.model_name) {
        const li = document.createElement('li');
        li.textContent = window.t ? window.t('steam.modelChanged') : '模型已修改';
        diffList.appendChild(li);
        hasDiff = true;
    }

    diffArea.style.display = hasDiff ? 'block' : 'none';

    // 显示模态框
    modal.style.display = 'flex';
}

// 关闭快照模态框
function closeWorkshopSnapshotModal(event) {
    const modal = document.getElementById('workshopSnapshotModal');
    if (!event || event.target === modal) {
        modal.style.display = 'none';
    }
}

// 加载角色卡
function loadCharacterCard() {
    // 这里将实现加载角色卡的逻辑
    showMessage(window.t ? window.t('steam.characterCardLoaded') : '角色卡已加载', 'info');
}

// 存储临时上传目录路径，供上传时使用
let currentUploadTempFolder = null;
// 标记是否已上传成功
let isUploadCompleted = false;

// 清理临时目录
function cleanupTempFolder(tempFolder, shouldDelete) {
    if (shouldDelete) {
        // 调用API删除临时目录
        fetch('/api/steam/workshop/cleanup-temp-folder', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                temp_folder: tempFolder
            })
        })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(data => {
                        throw new Error(data.error || `HTTP错误，状态码: ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(result => {
                if (result.success) {
                    showMessage(window.t ? window.t('steam.tempFolderDeleted') : '临时目录已删除', 'success');
                } else {
                    console.error('删除临时目录失败:', result.error);
                    showMessage(window.t ? window.t('steam.deleteTempDirectoryFailed', { error: result.error }) : `删除临时目录失败: ${result.error}`, 'error');
                }
                // 清除临时目录路径和上传状态
                currentUploadTempFolder = null;
                isUploadCompleted = false;
            })
            .catch(error => {
                console.error('删除临时目录失败:', error);
                showMessage(window.t ? window.t('steam.deleteTempDirectoryFailed', { error: error.message }) : `删除临时目录失败: ${error.message}`, 'error');
                // 即使删除失败，也清除临时目录路径和上传状态
                currentUploadTempFolder = null;
                isUploadCompleted = false;
            });
    } else {
        showMessage(window.t ? window.t('steam.tempFolderRetained') : '临时目录已保留', 'info');
        // 清除临时目录路径和上传状态
        currentUploadTempFolder = null;
        isUploadCompleted = false;
    }
}

async function handleUploadToWorkshop() {
    try {
        await ensureReservedFieldsLoaded();
        // 检查是否为默认模型
        if (isDefaultModel()) {
            showMessage(window.t ? window.t('steam.defaultModelCannotUpload') : '默认模型无法上传到创意工坊', 'error');
            return;
        }

        // 从已加载的角色卡列表中获取当前角色卡数据
        if (!currentCharacterCardId || !window.characterCards) {
            showMessage(window.t ? window.t('steam.noCharacterCardSelected') : '请先选择一个角色卡', 'error');
            return;
        }

        const currentCard = window.characterCards.find(card => card.id === currentCharacterCardId);
        if (!currentCard) {
            showMessage(window.t ? window.t('steam.characterCardNotFound') : '找不到当前角色卡数据', 'error');
            return;
        }

        // 从角色卡数据中提取信息
        // 现在角色使用的是 rawData 中的数据，只有 description 和 tag 需要从界面获取
        const rawData = currentCard.rawData || currentCard || {};
        // name 是 characters.json 中的唯一 key（如 "小天"、"小九"），直接从 currentCard.name 获取
        const name = currentCard.name;
        // description 可以从界面获取或从 rawData 中获取
        const description = document.getElementById('character-card-description').value.trim() || rawData['描述'] || rawData['description'] || '';
        const currentModelType = window.currentCharacterCardModelType || 'live2d';
        const currentModelPath = window.currentCharacterCardModelPath || '';
        let selectedModelName = window.currentCharacterCardModel || rawData['live2d'] || (rawData['model'] && rawData['model']['name']) || '';
        // VRM/MMD 模型使用路径而非 Live2D 模型名称
        if ((currentModelType === 'vrm' || currentModelType === 'mmd') && currentModelPath) {
            selectedModelName = currentModelPath;
        }
        const voiceId = rawData['voice_id'] || (rawData['voice'] && rawData['voice']['voice_id']) || '';

        // 验证必填字段 - 只验证 description
        const missingFields = [];
        if (!description) {
            missingFields.push(window.t ? window.t('steam.characterCardDescription') : '角色卡描述');
        }

        // 如果有未填写的必填字段，阻止上传并提示
        if (missingFields.length > 0) {
            const fieldsList = missingFields.join(window.t ? window.t('common.fieldSeparator') || '、' : '、');
            showMessage(window.t ? window.t('steam.requiredFieldsMissing', { fields: fieldsList }) : `请先填写以下必填字段：${fieldsList}`, 'error');
            return;
        }

        // 获取当前语言（需要在保存前获取）
        const currentLanguage = typeof i18next !== 'undefined' ? i18next.language : 'zh-CN';

        // 获取角色卡标签（需要在保存前获取）
        const characterCardTags = [];
        const tagElements = document.querySelectorAll('#character-card-tags-container .tag');
        if (tagElements && tagElements.length > 0) {
            tagElements.forEach(tagElement => {
                const tagText = tagElement.textContent.replace('×', '').trim();
                if (tagText) {
                    characterCardTags.push(tagText);
                }
            });
        }

        // 在上传前，先保存角色卡数据到文件
        // 构建完整的角色卡数据对象：直接使用 rawData 作为基础
        // 现在角色使用的是 rawData 中的数据，只覆盖 description 和 tags
        const fullCharaData = { ...rawData };

        // 重要：清理系统保留字段，防止恶意数据或循环引用被上传到工坊
        // 这些字段是下载时由系统添加的元数据，不应该出现在工坊角色卡中
        // description/tags 及其中文版本是工坊上传时自动生成的，不属于角色卡原始数据
        // live2d_item_id 是系统自动管理的，不应该上传
        const SYSTEM_RESERVED_FIELDS = getWorkshopReservedFields();
        for (const field of SYSTEM_RESERVED_FIELDS) {
            delete fullCharaData[field];
        }

        // 重要：添加"档案名"字段，这是下载后解析为 characters.json key 的必需字段
        // name 是 characters.json 中的唯一 key（如 "小天"、"小九"）
        fullCharaData['档案名'] = name;

        // 只覆盖 description 和 tags（这些是从界面获取的）
        if (currentLanguage === 'zh-CN') {
            fullCharaData['描述'] = description;
            fullCharaData['关键词'] = characterCardTags;
        } else {
            fullCharaData['description'] = description;
            fullCharaData['tags'] = characterCardTags;
        }

        // 根据模型类型设置正确的字段
        if (currentModelType === 'vrm' || currentModelType === 'mmd') {
            // VRM/MMD 模型：清除可能残留的旧 live2d 字段，防止元数据冲突
            delete fullCharaData.live2d;
        } else {
            fullCharaData.live2d = selectedModelName;
        }

        // 使用从角色卡数据中提取的voice_id（如果有）
        if (voiceId) {
            fullCharaData['voice_id'] = voiceId;
        }

        // 设置默认模型（排除mao_pro）- 仅限 Live2D 模型类型
        if (currentModelType === 'live2d' && (!selectedModelName || selectedModelName === 'mao_pro')) {
            const validModels = availableModels.filter(model => model.name !== 'mao_pro');
            if (validModels.length > 0) {
                selectedModelName = validModels[0].name;
            } else if (availableModels.length > 0) {
                selectedModelName = availableModels[0].name;
            } else {
                showMessage(window.t ? window.t('steam.noAvailableModelsError') : '没有可用的模型', 'error');
                return;
            }
        } else if ((currentModelType === 'vrm' || currentModelType === 'mmd') && !selectedModelName) {
            showMessage(window.t ? window.t('steam.noAvailableModelsError') : '没有可用的模型', 'error');
            return;
        }

        // 构建猫娘数据对象（用于上传，使用已保存的完整数据）
        const catgirlData = Object.assign({}, fullCharaData);

        // 构建角色卡文件名
        const charaFileName = `${name}.chara.json`;

        // 构建上传数据
        const uploadData = {
            fullCharaData: fullCharaData,
            catgirlData: catgirlData,
            name: name,
            selectedModelName: selectedModelName,
            modelType: currentModelType,
            charaFileName: charaFileName,
            characterCardTags: characterCardTags
        };

        // 直接进行上传（不再需要保存确认，因为使用的是 rawData 中的原始数据）
        await performUpload(uploadData);
    } catch (error) {
        console.error('handleUploadToWorkshop执行出错:', error);
        showMessage(window.t ? window.t('steam.prepareUploadError', { error: error.message }) : `上传准备出错: ${error.message}`, 'error');
    }
}

// 执行上传
async function performUpload(data) {
    // 显示准备上传状态
    showMessage(window.t ? window.t('steam.preparingUpload') : '正在准备上传...', 'info');

    try {
        // 步骤1: 调用API创建临时目录并复制文件
        // 保存上传数据的名称，供错误处理使用（避免回调中的参数覆盖）
        const uploadDataName = data.name;
        await fetch('/api/steam/workshop/prepare-upload', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                charaData: data.catgirlData,
                modelName: data.selectedModelName,
                modelType: data.modelType || 'live2d',
                fileName: data.charaFileName,
                character_card_name: data.name  // 传递角色卡名称，用于读取 .workshop_meta.json
            })
        })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(data => {
                        // 如果是已上传的错误，显示modal提示
                        if (data.error && (data.error.includes('已上传') || data.error.includes('已存在') || data.error.includes('already been uploaded'))) {
                            // 使用i18n构建错误消息
                            let errorMessage;
                            if (data.workshop_item_id && window.t) {
                                // 从上传数据中获取角色卡名称
                                const cardName = uploadDataName || '未知角色卡';
                                errorMessage = window.t('steam.characterCardAlreadyUploadedWithId', {
                                    name: cardName,
                                    itemId: data.workshop_item_id
                                });
                            } else {
                                errorMessage = data.message || data.error;
                            }
                            // 显示错误消息
                            showMessage(errorMessage, 'error', 10000);
                            // 显示modal提示
                            openDuplicateUploadModal(errorMessage);
                            throw new Error(errorMessage);
                        }
                        throw new Error(data.error || `HTTP错误，状态码: ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(result => {
                if (result.success) {
                    // 不再显示"上传准备完成"消息，模态框弹出本身就表明准备工作已完成

                    // 保存临时目录路径
                    currentUploadTempFolder = result.temp_folder;
                    // 重置上传完成标志
                    isUploadCompleted = false;

                    // 步骤2: 填充上传表单并打开填写信息窗口
                    const itemTitle = document.getElementById('item-title');
                    const itemDescription = document.getElementById('item-description');
                    const contentFolder = document.getElementById('content-folder');
                    const tagsContainer = document.getElementById('tags-container');


                    // 从data中获取名称和描述
                    const cardName = data.name || '';
                    const cardDescription = data.catgirlData?.['描述'] || data.catgirlData?.['description'] || '';

                    // Title 和 Description 现在是 div 元素，使用 textContent
                    if (itemTitle) itemTitle.textContent = cardName;
                    if (itemDescription) {
                        itemDescription.textContent = cardDescription;
                    }
                    // 使用临时目录路径（隐藏字段）
                    if (contentFolder) contentFolder.value = result.temp_folder;
                    resetWorkshopVoiceReferenceFields(cardName);

                    // 添加角色卡标签到上传标签（允许用户编辑）
                    if (tagsContainer) {
                        tagsContainer.innerHTML = '';

                        // 检查是否包含system_prompt（自定义模板）
                        const catgirlData = data.catgirlData || {};
                        const hasSystemPrompt = catgirlData['设定'] || catgirlData['system_prompt'] || catgirlData['prompt_setting'];

                        // 如果包含system_prompt，先添加锁定的"自定义模板"标签
                        if (hasSystemPrompt && String(hasSystemPrompt).trim() !== '') {
                            const customTemplateTagText = window.t ? window.t('steam.customTemplateTag') : '自定义模板';
                            addTag(customTemplateTagText, '', true); // locked = true
                        }

                        // 从角色卡标签容器中读取当前标签
                        const characterCardTagElements = document.querySelectorAll('#character-card-tags-container .tag');
                        const currentCharacterCardTags = Array.from(characterCardTagElements).map(tag =>
                            tag.textContent.replace('×', '').replace('🔒', '').trim()
                        ).filter(tag => tag);

                        // 如果有角色卡标签，使用它们；否则使用传入的标签
                        const tagsToAdd = currentCharacterCardTags.length > 0 ? currentCharacterCardTags : (data.characterCardTags || []);
                        tagsToAdd.forEach(tag => {
                            // 使用addTag函数，会自动添加删除按钮，允许用户编辑
                            addTag(tag);
                        });

                        // 确保标签输入框可编辑
                        const tagInput = document.getElementById('item-tags');
                        if (tagInput) {
                            tagInput.disabled = false;
                            tagInput.style.opacity = '';
                            tagInput.style.cursor = '';
                            tagInput.style.backgroundColor = '';
                            tagInput.placeholder = window.t ? window.t('steam.tagsPlaceholderInput') : '输入标签，按空格添加';
                        }
                    }

                    // 步骤3: 打开填写信息窗口（modal）
                    toggleUploadSection();
                } else {
                    showMessage(window.t ? window.t('steam.prepareUploadFailedMessage', { error: result.error || (window.t ? window.t('common.unknownError') : '未知错误') }) : `准备上传失败: ${result.error || '未知错误'}`, 'error');
                }
            })
            .catch(error => {
                console.error('准备上传失败:', error);
                showMessage(window.t ? window.t('steam.prepareUploadFailed', { error: error.message }) : `准备上传失败: ${error.message}`, 'error');
            });
    } catch (error) {
        console.error('performUpload执行出错:', error);
        showMessage(window.t ? window.t('steam.uploadExecutionError', { message: error.message }) : `上传执行出错: ${error.message}`, 'error');
    }
}

// 从模态框中编辑角色卡
function editCharacterCardModal() {
    if (currentCharacterCardId) {
        // 展开角色卡编辑区域
        toggleCharacterCardSection();

        // 调用编辑角色卡函数
        editCharacterCard(currentCharacterCardId);
    } else {
        showMessage(window.t ? window.t('steam.noCharacterCardSelectedForEdit') : '未选择要编辑的角色卡', 'error');
    }
}

// 扫描Live2D模型
async function scanModels() {
    showMessage(window.t ? window.t('steam.scanningModels') : '正在扫描模型...', 'info');

    try {
        // 并行获取 Live2D、VRM、MMD 模型列表
        const [live2dResponse, vrmResponse, mmdResponse] = await Promise.all([
            fetch('/api/live2d/models'),
            fetch('/api/model/vrm/models').catch(() => null),
            fetch('/api/model/mmd/models').catch(() => null)
        ]);

        // 处理 Live2D 模型
        if (!live2dResponse.ok) {
            throw new Error(`HTTP错误，状态码: ${live2dResponse.status}`);
        }
        const models = await live2dResponse.json();

        // 存储所有模型到全局变量（用于角色卡加载，包括static目录的模型）
        window.allModels = models;

        // 过滤掉来自static目录的模型（如mao_pro），只保留用户文档目录中的模型
        // 这是为了防止上传版权Live2D模型
        const uploadableModels = models.filter(model => model.source !== 'static');
        // 存储可上传模型列表到全局变量（用于上传检查）
        availableModels = uploadableModels;

        // 处理 VRM 模型
        try {
            if (vrmResponse && vrmResponse.ok) {
                const vrmData = await vrmResponse.json();
                if (vrmData.success && vrmData.models) {
                    window.allVrmModels = vrmData.models;
                    availableVrmModels = vrmData.models.filter(m => m.location !== 'project');
                }
            }
        } catch (e) {
            console.warn('处理VRM模型列表失败:', e);
        }

        // 处理 MMD 模型
        try {
            if (mmdResponse && mmdResponse.ok) {
                const mmdData = await mmdResponse.json();
                if (mmdData.success && mmdData.models) {
                    window.allMmdModels = mmdData.models;
                    availableMmdModels = mmdData.models.filter(m => m.location !== 'project');
                }
            }
        } catch (e) {
            console.warn('处理MMD模型列表失败:', e);
        }

        // 触发模型扫描完成事件，通知其他组件刷新 UI（具有容错能力）
        try {
            window.dispatchEvent(new CustomEvent('modelsScanned', { detail: { models, uploadableModels } }));
        } catch (e) {
            console.warn('触发 modelsScanned 事件失败:', e);
        }

        // 如果存在 model_manager.js 中的更新函数，也尝试调用（具有容错能力）
        try {
            if (typeof window.updateLive2DModelDropdown === 'function') {
                window.updateLive2DModelDropdown();
            }
        } catch (e) {
            console.warn('更新 Live2D 模型下拉菜单失败:', e);
        }

        try {
            if (typeof window.updateLive2DModelSelectButtonText === 'function') {
                window.updateLive2DModelSelectButtonText();
            }
        } catch (e) {
            console.warn('更新 Live2D 模型选择按钮文字失败:', e);
        }


    } catch (error) {
        console.error('扫描模型失败:', error);
        showMessage(window.t ? window.t('steam.modelScanError') : '扫描模型失败', 'error');
    }
}

// 全局变量：当前选择的模型信息
let selectedModelInfo = null;

// 初始化模型选择功能
// 音色相关函数（功能暂未实现）
// 加载音色列表
async function loadVoices() {
    // 显示扫描开始提示
    showMessage(window.t ? window.t('steam.scanningVoices') : '正在扫描音色...', 'info');

    try {
        const response = await fetch('/api/characters/voices');
        const data = await response.json();
        const voiceSelect = document.getElementById('voice-select');
        if (voiceSelect) {
            // 保存完整的音色数据到全局变量
            window.availableVoices = data.voices;

            // 音色数据已加载，用于后续显示音色名称
            const voiceCount = Object.keys(data.voices).length;

            // 显示扫描完成提示
            const successMessage = window.t ? window.t('steam.scanComplete', { count: voiceCount }) : `扫描完成，共找到 ${voiceCount} 个音色`;

            showToast(successMessage);
        }
    } catch (error) {
        console.error('加载音色列表失败:', error);
        showMessage(window.t ? window.t('steam.voiceScanError') : '扫描音色失败', 'error');
    }
}

// 扫描音色功能
function scanVoices() {
    loadVoices();
}

// 更新文件选择显示
function updateFileDisplay() {
    const fileInput = document.getElementById('audioFile');
    const fileNameDisplay = document.getElementById('fileNameDisplay');

    // 检查必要的DOM元素是否存在
    if (!fileInput || !fileNameDisplay) {
        return;
    }

    if (fileInput.files.length > 0) {
        fileNameDisplay.textContent = fileInput.files[0].name;
    } else {
        fileNameDisplay.textContent = window.t ? window.t('steam.voiceReferenceNoFileSelected') : '未选择文件';
    }
}

// 页面加载时获取 lanlan_name
(async function initLanlanName() {
    try {
        // 优先从 URL 获取 lanlan_name
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanName = urlParams.get('lanlan_name') || "";

        // 如果 URL 中没有，从 API 获取
        if (!lanlanName) {
            const response = await fetch('/api/config/page_config');
            const data = await response.json();
            if (data.success) {
                lanlanName = data.lanlan_name || "";
            }
        }

        // 设置到隐藏字段
        if (!document.getElementById('lanlan_name')) {
            const hiddenInput = document.createElement('input');
            hiddenInput.type = 'hidden';
            hiddenInput.id = 'lanlan_name';
            hiddenInput.value = lanlanName;
            document.body.appendChild(hiddenInput);
        } else {
            document.getElementById('lanlan_name').value = lanlanName;
        }
    } catch (error) {
        console.error('获取 lanlan_name 失败:', error);
        if (!document.getElementById('lanlan_name')) {
            const hiddenInput = document.createElement('input');
            hiddenInput.type = 'hidden';
            hiddenInput.id = 'lanlan_name';
            hiddenInput.value = '';
            document.body.appendChild(hiddenInput);
        }
    }
})();

function setFormDisabled(disabled) {
    const audioFileInput = document.getElementById('audioFile');
    const prefixInput = document.getElementById('prefix');
    const registerBtn = document.querySelector('button[onclick="registerVoice()"]');

    if (audioFileInput) audioFileInput.disabled = disabled;
    if (prefixInput) prefixInput.disabled = disabled;
    if (registerBtn) registerBtn.disabled = disabled;
}

function registerVoice() {
    const fileInput = document.getElementById('audioFile');
    const prefix = document.getElementById('prefix').value.trim();
    const resultDiv = document.getElementById('voice-register-result');

    resultDiv.innerHTML = '';
    resultDiv.className = 'result';

    if (!fileInput.files.length) {
        resultDiv.innerHTML = window.t ? window.t('voice.pleaseUploadFile') : '请选择音频文件';
        resultDiv.className = 'result error';
        resultDiv.style.color = 'red';
        return;
    }

    if (!prefix) {
        resultDiv.innerHTML = window.t ? window.t('voice.pleaseEnterPrefix') : '请填写自定义前缀';
        resultDiv.className = 'result error';
        resultDiv.style.color = 'red';
        return;
    }

    // 验证前缀格式
    const prefixRegex = /^[a-zA-Z0-9]{1,10}$/;
    if (!prefixRegex.test(prefix)) {
        resultDiv.innerHTML = window.t ? window.t('voice.prefixFormatError') : '前缀格式错误：不超过10个字符，只支持数字和英文字母';
        resultDiv.className = 'result error';
        resultDiv.style.color = 'red';
        return;
    }

    setFormDisabled(true);
    resultDiv.innerHTML = window.t ? window.t('voice.registering') : '正在注册声音，请稍后！';
    resultDiv.style.color = 'green';

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('prefix', prefix);

    fetch('/api/characters/voice_clone', {
        method: 'POST',
        body: formData
    })
        .then(res => res.json())
        .then(data => {
            if (data.voice_id) {
                if (data.reused) {
                    resultDiv.innerHTML = window.t ? window.t('voice.reusedExisting', { voiceId: data.voice_id }) : '已复用现有音色，跳过上传。voice_id: ' + data.voice_id;
                } else {
                    resultDiv.innerHTML = window.t ? window.t('voice.registerSuccess', { voiceId: data.voice_id }) : '注册成功！voice_id: ' + data.voice_id;
                }
                resultDiv.style.color = 'green';

                // 自动更新voice_id到后端
                const lanlanName = document.getElementById('lanlan_name').value;
                if (lanlanName) {
                    fetch(`/api/characters/catgirl/voice_id/${encodeURIComponent(lanlanName)}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ voice_id: data.voice_id })
                    }).then(resp => resp.json()).then(res => {
                        if (!res.success) {
                            const errorMsg = res.error || (window.t ? window.t('common.unknownError') : '未知错误');
                            resultDiv.innerHTML += '<br><span class="error" style="color: red;">' + (window.t ? window.t('voice.voiceIdSaveFailed', { error: errorMsg }) : 'voice_id自动保存失败: ' + errorMsg) + '</span>';
                        } else {
                            resultDiv.innerHTML += '<br>' + (window.t ? window.t('voice.voiceIdSaved') : 'voice_id已自动保存到角色');
                            // 如果session被结束，页面会自动刷新
                            if (res.session_restarted) {
                                resultDiv.innerHTML += '<br><span style="color: blue;">' + (window.t ? window.t('voice.pageWillRefresh') : '当前页面即将自动刷新以应用新语音') + '</span>';
                                setTimeout(() => {
                                    location.reload();
                                }, 2000);
                            } else {
                                resultDiv.innerHTML += '<br><span style="color: blue;">' + (window.t ? window.t('voice.voiceWillTakeEffect') : '新语音将在下次对话时生效') + '</span>';
                            }
                        }
                    }).catch(e => {
                        resultDiv.innerHTML += '<br><span class="error" style="color: red;">' + (window.t ? window.t('voice.voiceIdSaveRequestError') : 'voice_id自动保存请求出错') + '</span>';
                    });
                }

                // 重新扫描音色以更新列表
                setTimeout(() => {
                    loadVoices();
                }, 1000);
            } else {
                const errorMsg = data.error || (window.t ? window.t('common.unknownError') : '未知错误');
                resultDiv.innerHTML = window.t ? window.t('voice.registerFailed', { error: errorMsg }) : '注册失败：' + errorMsg;
                resultDiv.className = 'result error';
                resultDiv.style.color = 'red';
            }
            setFormDisabled(false);
        })
        .catch(err => {
            const errorMsg = err?.message || err?.toString() || (window.t ? window.t('common.unknownError') : '未知错误');
            resultDiv.textContent = window.t ? window.t('voice.requestError', { error: errorMsg }) : '请求出错：' + errorMsg;
            resultDiv.className = 'result error';
            resultDiv.style.color = 'red';
            setFormDisabled(false);
        });
}

// 页面加载时初始化文件选择显示
window.addEventListener('load', () => {
    // 监听文件选择变化
    const audioFileInput = document.getElementById('audioFile');
    if (audioFileInput) {
        audioFileInput.addEventListener('change', updateFileDisplay);
    }

    // 如果 i18next 已经初始化完成，立即更新
    if (window.i18n && window.i18n.isInitialized) {
        updateFileDisplay();
    } else {
        // 延迟更新，等待 i18next 初始化
        setTimeout(updateFileDisplay, 500);
    }
});

// ====================== VRM/MMD 3D 模型预览 ======================

// 工坊预览专用的 VRM/MMD 管理器实例
let workshopVrmManager = null;
let workshopMmdManager = null;
let _workshopVrmModulesLoaded = false;
let _workshopMmdModulesLoaded = false;
let _workshopVrmModulesLoading = false;
let _workshopMmdModulesLoading = false;

// 按需加载 VRM 模块
async function ensureVrmModulesLoaded() {
    if (_workshopVrmModulesLoaded) return true;
    if (_workshopVrmModulesLoading) {
        // 等待加载完成，带超时和失败检测
        return new Promise((resolve) => {
            let elapsed = 0;
            const check = () => {
                if (_workshopVrmModulesLoaded) resolve(true);
                else if (!_workshopVrmModulesLoading || elapsed >= 30000) resolve(false);
                else { elapsed += 100; setTimeout(check, 100); }
            };
            check();
        });
    }
    _workshopVrmModulesLoading = true;

    // 等待 THREE 就绪
    if (typeof window.THREE === 'undefined') {
        await new Promise(resolve => {
            window.addEventListener('three-ready', resolve, { once: true });
        });
    }

    const vrmModules = [
        '/static/vrm-orientation.js',
        '/static/vrm-core.js',
        '/static/vrm-expression.js',
        '/static/vrm-animation.js',
        '/static/vrm-interaction.js',
        '/static/vrm-cursor-follow.js',
        '/static/vrm-manager.js'
    ];

    for (const moduleSrc of vrmModules) {
        // 检查是否已通过其他途径加载
        if (moduleSrc.includes('vrm-manager') && typeof window.VRMManager !== 'undefined') continue;
        if (moduleSrc.includes('vrm-core') && typeof window.VRMCore !== 'undefined') continue;

        const script = document.createElement('script');
        script.src = `${moduleSrc}?v=${Date.now()}`;
        await new Promise((resolve) => {
            script.onload = resolve;
            script.onerror = () => {
                console.error(`[Workshop VRM] 模块加载失败: ${moduleSrc}`);
                resolve();
            };
            document.body.appendChild(script);
        });
    }

    _workshopVrmModulesLoaded = typeof window.VRMManager !== 'undefined';
    _workshopVrmModulesLoading = false;
    return _workshopVrmModulesLoaded;
}

// 按需加载 MMD 模块
async function ensureMmdModulesLoaded() {
    if (_workshopMmdModulesLoaded) return true;
    if (_workshopMmdModulesLoading) {
        return new Promise((resolve) => {
            let elapsed = 0;
            const check = () => {
                if (_workshopMmdModulesLoaded) resolve(true);
                else if (!_workshopMmdModulesLoading || elapsed >= 30000) resolve(false);
                else { elapsed += 100; setTimeout(check, 100); }
            };
            check();
        });
    }
    _workshopMmdModulesLoading = true;

    if (typeof window.THREE === 'undefined') {
        await new Promise(resolve => {
            window.addEventListener('three-ready', resolve, { once: true });
        });
    }

    const mmdModules = [
        '/static/mmd-core.js',
        '/static/mmd-animation.js',
        '/static/mmd-expression.js',
        '/static/mmd-interaction.js',
        '/static/mmd-cursor-follow.js',
        '/static/mmd-manager.js'
    ];

    for (const moduleSrc of mmdModules) {
        if (moduleSrc.includes('mmd-manager') && typeof window.MMDManager !== 'undefined') continue;
        if (moduleSrc.includes('mmd-core') && typeof window.MMDCore !== 'undefined') continue;

        const script = document.createElement('script');
        script.src = `${moduleSrc}?v=${Date.now()}`;
        await new Promise((resolve) => {
            script.onload = resolve;
            script.onerror = () => {
                console.error(`[Workshop MMD] 模块加载失败: ${moduleSrc}`);
                resolve();
            };
            document.body.appendChild(script);
        });
    }

    _workshopMmdModulesLoaded = typeof window.MMDManager !== 'undefined';
    _workshopMmdModulesLoading = false;
    return _workshopMmdModulesLoaded;
}

// 隐藏所有 3D 预览容器
function hideAll3DPreviews() {
    const vrmContainer = document.getElementById('vrm-preview-container');
    const mmdContainer = document.getElementById('mmd-preview-container');
    if (vrmContainer) vrmContainer.style.display = 'none';
    if (mmdContainer) mmdContainer.style.display = 'none';
}

// 清理工坊 VRM 预览实例
async function disposeWorkshopVrm() {
    if (workshopVrmManager) {
        try {
            if (typeof workshopVrmManager.dispose === 'function') {
                await workshopVrmManager.dispose();
            }
        } catch (e) {
            console.warn('[Workshop VRM] dispose 失败:', e);
        }
        workshopVrmManager = null;
    }
    hideAll3DPreviews();
}

// 清理工坊 MMD 预览实例
async function disposeWorkshopMmd() {
    if (workshopMmdManager) {
        try {
            if (typeof workshopMmdManager.stopAnimation === 'function') {
                workshopMmdManager.stopAnimation();
            }
            if (typeof workshopMmdManager.dispose === 'function') {
                await workshopMmdManager.dispose();
            }
        } catch (e) {
            console.warn('[Workshop MMD] dispose 失败:', e);
        }
        workshopMmdManager = null;
    }
    hideAll3DPreviews();
}

// 加载 VRM 模型预览
async function loadVrmPreview(modelPath, rawData) {
    try {
        // 先清理之前的 3D 预览
        await disposeWorkshopVrm();
        await disposeWorkshopMmd();

        // 清理 Live2D 预览（如果有）
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel(true);
            currentPreviewModel = null;
        }

        // 隐藏 Live2D canvas 和占位符
        const live2dCanvas = document.getElementById('live2d-preview-canvas');
        const placeholder = document.querySelector('#live2d-preview-content .preview-placeholder');
        if (live2dCanvas) live2dCanvas.style.display = 'none';
        if (placeholder) placeholder.style.display = 'none';

        // 更新标题
        const title = document.getElementById('model-preview-title');
        if (title) title.textContent = 'VRM';

        // 隐藏 Live2D 控件
        const live2dControls = document.getElementById('live2d-preview-controls');
        if (live2dControls) live2dControls.style.display = 'none';

        // 确保 VRM 模块已加载
        const loaded = await ensureVrmModulesLoaded();
        if (!loaded) {
            console.error('[Workshop VRM] 模块加载失败');
            showMessage(window.t ? window.t('steam.vrmModuleLoadFailed') || 'VRM 模块加载失败' : 'VRM 模块加载失败', 'error');
            return;
        }

        // 显示 VRM 容器
        const vrmContainer = document.getElementById('vrm-preview-container');
        if (vrmContainer) vrmContainer.style.display = 'block';

        // 创建 VRM 管理器实例
        workshopVrmManager = new window.VRMManager();

        // 获取光照配置
        const lighting = rawData?.['lighting'] || null;

        // 初始化 Three.js 场景
        await workshopVrmManager.initThreeJS('vrm-preview-canvas', 'vrm-preview-container', lighting);

        // 修正容器样式：VRMCore.init 会设置 position:fixed 覆盖全屏，
        // 这里覆盖为 absolute 使其嵌入预览区域内
        const vrmContainerEl = document.getElementById('vrm-preview-container');
        if (vrmContainerEl) {
            vrmContainerEl.style.position = 'absolute';
            vrmContainerEl.style.top = '0';
            vrmContainerEl.style.left = '0';
            vrmContainerEl.style.width = '100%';
            vrmContainerEl.style.height = '100%';
            vrmContainerEl.style.zIndex = '10';
        }

        // 按预览区域实际尺寸重设渲染器大小
        const previewContent = document.getElementById('live2d-preview-content');
        if (previewContent && workshopVrmManager.renderer) {
            const w = previewContent.clientWidth;
            const h = previewContent.clientHeight;
            if (w > 0 && h > 0) {
                workshopVrmManager.renderer.setSize(w, h);
                if (workshopVrmManager.camera) {
                    workshopVrmManager.camera.aspect = w / h;
                    workshopVrmManager.camera.updateProjectionMatrix();
                }
            }
        }

        // 允许 3D 交互：临时启用预览区域的 pointer-events
        if (previewContent) previewContent.style.pointerEvents = 'auto';
        const overlay = document.getElementById('live2d-preview-overlay');
        if (overlay) overlay.style.display = 'none';

        // 获取 idle 动画路径
        const idleAnimation = rawData?.['idleAnimation'] || '/static/vrm/animation/wait03.vrma';

        // 加载模型
        const result = await workshopVrmManager.loadModel(modelPath, {
            canvasId: 'vrm-preview-canvas',
            containerId: 'vrm-preview-container',
            addShadow: true,
            idleAnimation: idleAnimation
        });

        if (result) {
            console.log('[Workshop VRM] 模型预览加载成功');
            showMessage(window.t ? window.t('steam.vrmPreviewLoaded') || 'VRM 模型预览已加载' : 'VRM 模型预览已加载', 'success');
        }
    } catch (error) {
        console.error('[Workshop VRM] 加载预览失败:', error);
        showMessage(window.t ? window.t('steam.vrmPreviewFailed') || 'VRM 模型预览加载失败' : 'VRM 模型预览加载失败', 'error');
    }
}

// 加载 MMD 模型预览
async function loadMmdPreview(modelPath, rawData) {
    try {
        // 先清理之前的 3D 预览
        await disposeWorkshopVrm();
        await disposeWorkshopMmd();

        // 清理 Live2D 预览（如果有）
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel(true);
            currentPreviewModel = null;
        }

        // 隐藏 Live2D canvas 和占位符
        const live2dCanvas = document.getElementById('live2d-preview-canvas');
        const placeholder = document.querySelector('#live2d-preview-content .preview-placeholder');
        if (live2dCanvas) live2dCanvas.style.display = 'none';
        if (placeholder) placeholder.style.display = 'none';

        // 更新标题
        const title = document.getElementById('model-preview-title');
        if (title) title.textContent = 'MMD';

        // 隐藏 Live2D 控件
        const live2dControls = document.getElementById('live2d-preview-controls');
        if (live2dControls) live2dControls.style.display = 'none';

        // 确保 MMD 模块已加载
        const loaded = await ensureMmdModulesLoaded();
        if (!loaded) {
            console.error('[Workshop MMD] 模块加载失败');
            showMessage(window.t ? window.t('steam.mmdModuleLoadFailed') || 'MMD 模块加载失败' : 'MMD 模块加载失败', 'error');
            return;
        }

        // 显示 MMD 容器
        const mmdContainer = document.getElementById('mmd-preview-container');
        if (mmdContainer) mmdContainer.style.display = 'block';

        // 创建 MMD 管理器实例
        workshopMmdManager = new window.MMDManager();

        // 初始化
        await workshopMmdManager.init('mmd-preview-canvas', 'mmd-preview-container');

        // 修正容器样式：MMDCore.init 会设置 position:fixed 覆盖全屏，
        // 这里覆盖为 absolute 使其嵌入预览区域内
        const mmdContainerEl = document.getElementById('mmd-preview-container');
        if (mmdContainerEl) {
            mmdContainerEl.style.position = 'absolute';
            mmdContainerEl.style.top = '0';
            mmdContainerEl.style.left = '0';
            mmdContainerEl.style.width = '100%';
            mmdContainerEl.style.height = '100%';
            mmdContainerEl.style.zIndex = '10';
        }

        // 按预览区域实际尺寸重设渲染器大小
        const previewContent = document.getElementById('live2d-preview-content');
        if (previewContent && workshopMmdManager.renderer) {
            const w = previewContent.clientWidth;
            const h = previewContent.clientHeight;
            if (w > 0 && h > 0) {
                workshopMmdManager.renderer.setSize(w, h);
                if (workshopMmdManager.camera) {
                    workshopMmdManager.camera.aspect = w / h;
                    workshopMmdManager.camera.updateProjectionMatrix();
                }
            }
        }

        // 允许 3D 交互：临时启用预览区域的 pointer-events
        if (previewContent) previewContent.style.pointerEvents = 'auto';
        const overlay = document.getElementById('live2d-preview-overlay');
        if (overlay) overlay.style.display = 'none';

        // 加载模型
        const modelInfo = await workshopMmdManager.loadModel(modelPath);

        if (modelInfo) {
            // 如果有 idle 动画，尝试加载
            const idleAnimation = rawData?.['mmd_idle_animation'] || '';
            if (idleAnimation && typeof workshopMmdManager.loadAnimation === 'function') {
                try {
                    await workshopMmdManager.loadAnimation(idleAnimation);
                    workshopMmdManager.playAnimation();
                } catch (e) {
                    console.warn('[Workshop MMD] idle 动画加载失败:', e);
                }
            }
            console.log('[Workshop MMD] 模型预览加载成功');
            showMessage(window.t ? window.t('steam.mmdPreviewLoaded') || 'MMD 模型预览已加载' : 'MMD 模型预览已加载', 'success');
        }
    } catch (error) {
        console.error('[Workshop MMD] 加载预览失败:', error);
        showMessage(window.t ? window.t('steam.mmdPreviewFailed') || 'MMD 模型预览加载失败' : 'MMD 模型预览加载失败', 'error');
    }
}

// 清除所有模型预览（Live2D + VRM + MMD）
async function clearAllModelPreviews(showModelNotSetMessage = false) {
    await disposeWorkshopVrm();
    await disposeWorkshopMmd();
    hideAll3DPreviews();

    // 恢复 Live2D 预览区域的 pointer-events 和 overlay
    const previewContent = document.getElementById('live2d-preview-content');
    if (previewContent) previewContent.style.pointerEvents = 'none';
    const overlay = document.getElementById('live2d-preview-overlay');
    if (overlay) overlay.style.display = '';

    // 恢复 Live2D 标题和控件
    const title = document.getElementById('model-preview-title');
    if (title) {
        title.textContent = 'Live2D';
        title.setAttribute('data-i18n', 'steam.live2dPreview');
    }
    const live2dControls = document.getElementById('live2d-preview-controls');
    if (live2dControls) live2dControls.style.display = '';

    await clearLive2DPreview(showModelNotSetMessage);
}

// 清除Live2D预览并显示占位符
async function clearLive2DPreview(showModelNotSetMessage = false) {
    try {
        // 如果有模型加载，先移除它
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel(true);
            currentPreviewModel = null;
        }

        // 隐藏canvas，显示占位符
        const canvas = document.getElementById('live2d-preview-canvas');
        const placeholder = document.querySelector('#live2d-preview-content .preview-placeholder');

        if (canvas) {
            canvas.style.display = 'none';
        }

        if (placeholder) {
            placeholder.style.display = 'flex';
            // 根据参数显示不同的提示文本
            const span = placeholder.querySelector('span');
            const getText = (key, fallback) => {
                if (!window.t) return fallback;
                const raw = window.t(key);
                return (raw && typeof raw === 'string' && raw !== key) ? raw : fallback;
            };
            const modelNotSetText = getText('steam.characterModelNotSet', '当前角色未设置模型');
            const selectCharText = getText('steam.selectCharaToPreview', '请选择角色进行预览');
            const isModelNotSet = showModelNotSetMessage === true;
            if (span) {
                if (isModelNotSet) {
                    span.textContent = modelNotSetText;
                    span.setAttribute('data-i18n', 'steam.characterModelNotSet');
                } else {
                    span.textContent = selectCharText;
                    span.setAttribute('data-i18n', 'steam.selectCharaToPreview');
                }
            }
            // 同步更新环形文字
            if (typeof buildPreviewRing === 'function') {
                buildPreviewRing(isModelNotSet ? modelNotSetText : selectCharText);
            }
        }

    } catch (error) {
        console.error('清除Live2D预览失败:', error);
    }
}

// 通过模型名称加载Live2D模型
async function loadLive2DModelByName(modelName, modelInfo = null) {
    try {
        // 确保live2dPreviewManager已初始化
        if (!live2dPreviewManager || !live2dPreviewManager.pixi_app) {
            await initLive2DPreview();
        }

        // 强制resize PIXI应用，确保canvas尺寸正确
        // 这是必要的，因为当容器最初是隐藏的(display:none)时，PIXI的尺寸会是0
        if (live2dPreviewManager && live2dPreviewManager.pixi_app) {
            const container = document.getElementById('live2d-preview-content');
            if (container && container.clientWidth > 0 && container.clientHeight > 0) {
                live2dPreviewManager.pixi_app.renderer.resize(container.clientWidth, container.clientHeight);
            }
        }

        // 如果已经有模型加载，先移除它
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel(true);
            // 重置当前预览模型引用
            currentPreviewModel = null;
        }

        // 如果没有传入modelInfo，则从API获取模型列表
        if (!modelInfo) {
            // 调用API获取模型列表，找到对应模型的信息
            const response = await fetch('/api/live2d/models');
            if (!response.ok) {
                throw new Error(`HTTP错误，状态码: ${response.status}`);
            }

            const models = await response.json();
            modelInfo = models.find(model => model.name === modelName);

            if (!modelInfo) {
                throw new Error(window.t('steam.modelNotFound', '模型未找到'));
            }
        }

        // 确保获取正确的steam_id，优先使用modelInfo中的item_id
        let finalSteamId = modelInfo.item_id;
        showMessage((window.t && window.t('live2d.loadingModel', { model: modelName })) || `正在加载模型: ${modelName}...`, 'info');

        // 1. Fetch files list
        let filesRes;
        // 根据modelInfo的source字段和finalSteamId决定使用哪个API端点
        if (modelInfo.source === 'user_mods') {
            // 对于用户mod模型，使用modelName构建URL
            filesRes = await fetch(`/api/live2d/model_files/${encodeURIComponent(modelName)}`);
        } else if (finalSteamId && finalSteamId !== 'undefined') {
            // 如果提供了finalSteamId，调用专门的API端点
            filesRes = await fetch(`/api/live2d/model_files_by_id/${finalSteamId}`);
        } else {
            // 否则使用原来的API端点
            filesRes = await fetch(`/api/live2d/model_files/${encodeURIComponent(modelName)}`);
        }
        const filesData = await filesRes.json();
        if (!filesData.success) throw new Error(window.t('live2d.modelFilesFetchFailed', '无法获取模型文件列表'));
        window._previewMotionFiles = filesData.motion_files || [];

        // 2. Fetch model config
        let modelJsonUrl;
        // 优先使用后端返回的model_config_url（如果有）
        if (filesData.model_config_url) {
            modelJsonUrl = filesData.model_config_url;
        } else if (modelInfo.source === 'user_mods') {
            // 对于用户mod模型，直接使用modelInfo.path（已经包含/user_mods/路径）
            modelJsonUrl = modelInfo.path;
        } else if (finalSteamId && finalSteamId !== 'undefined') {
            // 如果提供了finalSteamId但没有model_config_url，使用兼容模式构建URL
            // 注意：上传后的目录结构是 workshop/{item_id}/{model_name}/{model_name}.model3.json
            modelJsonUrl = `/workshop/${finalSteamId}/${modelName}/${modelName}.model3.json`;
        } else {
            // 否则使用原来的路径
            modelJsonUrl = modelInfo.path;
        }
        const modelConfigRes = await fetch(modelJsonUrl);
        if (!modelConfigRes.ok) throw new Error((window.t && window.t('live2d.modelConfigFetchFailed', { status: modelConfigRes.statusText })) || `无法获取模型配置: ${modelConfigRes.statusText}`);
        const modelConfig = await modelConfigRes.json();

        // 3. Add URL context for the loader
        modelConfig.url = modelJsonUrl;

        // 4. Inject PreviewAll motion group AND ensure all expressions are referenced
        if (!modelConfig.FileReferences) modelConfig.FileReferences = {};

        // Motions
        if (!modelConfig.FileReferences.Motions) modelConfig.FileReferences.Motions = {};
        // 只有当模型有动作文件时才添加PreviewAll组
        if (filesData.motion_files.length > 0) {
            modelConfig.FileReferences.Motions.PreviewAll = filesData.motion_files.map(file => ({
                File: file  // 直接使用API返回的完整路径
            }));
        }

        // Expressions: Overwrite with all available expression files for preview purposes.
        modelConfig.FileReferences.Expressions = filesData.expression_files.map(file => ({
            Name: file.split('/').pop().replace('.exp3.json', ''),  // 从路径中提取文件名作为名称
            File: file  // 直接使用API返回的完整路径
        }));

        // 5. Load preferences (如果需要)
        // const preferences = await live2dPreviewManager.loadUserPreferences();
        // const modelPreferences = preferences.find(p => p && p.model_path === modelInfo.path) || null;

        // 6. Load model FROM THE MODIFIED OBJECT
        await live2dPreviewManager.loadModel(modelConfig, {
            loadEmotionMapping: true,
            dragEnabled: true,
            wheelEnabled: true,
            skipCloseWindows: true  // 创意工坊页面不需要关闭其他窗口
        });

        // 设置当前预览模型引用，用于播放动作和表情
        currentPreviewModel = live2dPreviewManager.currentModel;

        // 清除模型路径，防止拖动预览时自动保存到preference
        live2dPreviewManager._lastLoadedModelPath = null;

        // 更新预览控件
        await updatePreviewControlsAfterModelLoad(filesData);

        // 模型加载完成后，确保它在容器中正确显示
        setTimeout(() => {
            if (live2dPreviewManager && live2dPreviewManager.currentModel) {
                live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
                // 确保canvas正确显示，占位符被隐藏
                document.getElementById('live2d-preview-canvas').style.display = '';
                document.querySelector('.preview-placeholder').style.display = 'none';
                // 强制重绘canvas
                if (live2dPreviewManager.app && live2dPreviewManager.app.renderer) {
                    live2dPreviewManager.app.renderer.render(live2dPreviewManager.app.stage);
                }
            }
        }, 100);

        // 更新全局selectedModelInfo变量
        selectedModelInfo = modelInfo;
        showMessage((window.t && window.t('live2d.modelLoadSuccess', { model: modelName })) || `模型 ${modelName} 加载成功`, 'success');
    } catch (error) {
        console.error('Failed to load Live2D model by name:', error);
        showMessage((window.t && window.t('live2d.modelLoadFailed', { model: modelName })) || `加载模型 ${modelName} 失败`, 'error');

        // 在加载失败时隐藏预览控件
        hidePreviewControls();
    }
}

// 刷新Live2D预览
async function refreshLive2DPreview() {
    // 检查当前角色是否有设置模型
    if (!selectedModelInfo || !selectedModelInfo.name) {
        showMessage(window.t('characterModelNotSet', '当前角色未设置模型'), 'warning');
        return;
    }

    // 重新加载当前模型
    await loadLive2DModelByName(selectedModelInfo.name, selectedModelInfo);
}

// 模型加载后更新预览控件
async function updatePreviewControlsAfterModelLoad(filesData) {
    if (!live2dPreviewManager) {
        return;
    }

    // 检查filesData是否存在
    if (!filesData || !filesData.motion_files || !filesData.expression_files) {
        console.error('Invalid filesData object:', filesData);
        return;
    }

    // 显示Canvas，隐藏占位符
    const canvas = document.getElementById('live2d-preview-canvas');
    const placeholder = document.querySelector('.preview-placeholder');
    if (canvas) canvas.style.display = '';
    if (placeholder) placeholder.style.display = 'none';

    // 启用预览控件
    const motionSelect = document.getElementById('preview-motion-select');
    const expressionSelect = document.getElementById('preview-expression-select');
    const playMotionBtn = document.getElementById('preview-play-motion-btn');
    const playExpressionBtn = document.getElementById('preview-play-expression-btn');

    if (motionSelect) motionSelect.disabled = false;
    if (expressionSelect) expressionSelect.disabled = false;
    if (playMotionBtn) playMotionBtn.disabled = false;
    if (playExpressionBtn) playExpressionBtn.disabled = false;

    // 显示预览控件区域
    const previewControls = document.getElementById('live2d-preview-controls');
    if (previewControls) {
        previewControls.style.display = 'block';
    }

    // 更新动作和表情列表
    try {
        updatePreviewControls(filesData.motion_files, filesData.expression_files);
    } catch (error) {
        console.error('Failed to update preview controls:', error);
    }

    // 恢复已保存的待机动作（如果存在）
    const rawData = window._currentCardRawData || {};
    const savedIdleAnimation = rawData._reserved?.avatar?.live2d?.idle_animation
        || rawData.avatar?.live2d?.idle_animation
        || rawData.live2d_idle_animation;
    if (savedIdleAnimation && motionSelect) {
        const motionFiles = window._previewMotionFiles || [];
        if (motionFiles.includes(savedIdleAnimation)) {
            motionSelect.value = savedIdleAnimation;
        }
    }
}

// 更新角色卡信息预览（动态渲染所有属性）
function updateCardPreview() {
    const container = document.getElementById('card-info-dynamic-content');
    if (!container) return;

    // 从已加载的角色卡列表中获取当前角色卡数据
    if (!currentCharacterCardId || !window.characterCards) {
        container.innerHTML = `<p style="color: #999; text-align: center;">` +
            (window.t ? window.t('steam.selectCharacterCard') : '请选择一个角色卡') + '</p>';
        return;
    }

    const currentCard = window.characterCards.find(card => card.id === currentCharacterCardId);
    if (!currentCard) {
        container.innerHTML = `<p style="color: #999; text-align: center;">` +
            (window.t ? window.t('steam.characterCardNotFound') : '找不到角色卡数据') + '</p>';
        return;
    }

    // 获取角色卡原始数据
    const rawData = currentCard.rawData || currentCard || {};

    // 保留字段（不显示）
    // 系统保留字段 + 工坊保留字段
    const hiddenFields = getWorkshopHiddenFields();

    // 清空容器
    container.innerHTML = '';

    // 遍历所有属性并动态生成显示
    for (const [key, value] of Object.entries(rawData)) {
        // 跳过保留字段
        if (hiddenFields.includes(key)) continue;

        // 跳过空值
        if (value === null || value === undefined || value === '') continue;

        // 创建属性行
        const row = document.createElement('div');
        row.style.cssText = `color: #000; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1.5px solid #d5efff; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%;`;

        // 格式化值
        let displayValue = '';
        if (Array.isArray(value)) {
            // 数组：用逗号分隔显示
            displayValue = value.join('、');
        } else if (typeof value === 'object') {
            // 对象：显示为 JSON（但跳过复杂嵌套对象）
            try {
                displayValue = JSON.stringify(value, null, 0);
            } catch (e) {
                displayValue = '[复杂对象]';
            }
        } else {
            displayValue = String(value);
        }

        // 构建HTML - 使用黑色文字，添加自动换行
        row.innerHTML = '<strong style="color: #000;">' + escapeHtml(key) + ':</strong> <span style="font-weight: normal; color: #000; word-wrap: break-word; overflow-wrap: break-word; display: inline-block; max-width: 100%;">' + escapeHtml(displayValue) + '</span>';
        container.appendChild(row);
    }

    // 如果没有任何属性显示，显示提示
    if (container.children.length === 0) {
        container.innerHTML = `<p style="color: #999; text-align: center;">` +
            (window.t ? window.t('steam.noCardProperties') : '暂无属性信息') + '</p>';
    }
}


// 为输入字段添加事件监听器，自动更新预览
document.addEventListener('DOMContentLoaded', function () {
    // 只有 description 输入框仍然存在，为其添加事件监听器
    const descriptionInput = document.getElementById('character-card-description');
    const characterCardTagsWrapper = document.getElementById('character-card-tags-wrapper');

    // 页面加载完成后自动加载音色列表
    loadVoices();

    if (descriptionInput) {
        descriptionInput.addEventListener('input', updateCardPreview);
    }

    if (characterCardTagsWrapper) {
        characterCardTagsWrapper.addEventListener('scroll', updateCharacterCardTagScrollControls, { passive: true });
        if (typeof ResizeObserver !== 'undefined') {
            const tagsResizeObserver = new ResizeObserver(() => {
                updateCharacterCardTagScrollControls();
            });
            tagsResizeObserver.observe(characterCardTagsWrapper);
        }
    }

    window.addEventListener('resize', updateCharacterCardTagScrollControls);
    window.setTimeout(updateCharacterCardTagScrollControls, 0);
});

// 添加标签（角色卡用）
function addCharacterCardTag(type, tagValue) {
    const tagText = String(tagValue || '').trim();
    if (!tagText) return;
    addTag(tagText, type);
}

// 清除所有标签
function clearTags(type) {
    const tagsContainer = document.getElementById(`${type}-tags-container`);
    tagsContainer.innerHTML = '';
    if (type === 'character-card') {
        updateCharacterCardTagScrollControls();
    }
}

// Live2D预览相关功能
let live2dPreviewManager = null;
let currentPreviewModel = null;

// 初始化Live2D预览环境
async function initLive2DPreview() {
    try {
        // 检查Live2DManager是否已定义
        if (typeof Live2DManager === 'undefined') {
            throw new Error('Live2DManager class not found');
        }

        // 避免重复初始化
        if (live2dPreviewManager && live2dPreviewManager.pixi_app) {
            return; // 已经正常初始化，不需要重新初始化
        }

        // 清理可能存在的残缺实例
        live2dPreviewManager = null;

        // 创建一个新的Live2DManager实例
        live2dPreviewManager = new Live2DManager();
        await live2dPreviewManager.initPIXI('live2d-preview-canvas', 'live2d-preview-content');

        // 覆盖applyModelSettings方法，为预览模式实现专门的显示逻辑
        const originalApplyModelSettings = live2dPreviewManager.applyModelSettings;
        live2dPreviewManager.applyModelSettings = function (model, options) {
            // 获取预览容器的尺寸
            const container = document.getElementById('live2d-preview-content');
            if (!container) {
                return originalApplyModelSettings(model, options);
            }

            const containerWidth = container.clientWidth;
            const containerHeight = container.clientHeight;

            // 先设置临时缩放和锚点以便获取实际边界
            model.anchor.set(0.5, 0.5);
            model.scale.set(0.1); // 临时缩放值
            model.x = 0;
            model.y = 0;

            // 获取模型的实际边界
            const bounds = model.getBounds();
            const modelWidth = bounds.width / 0.1; // 还原原始宽度
            const modelHeight = bounds.height / 0.1; // 还原原始高度

            // 计算适合容器的缩放比例
            const padding = 30;
            const availableWidth = Math.max(50, containerWidth - padding * 2);
            const availableHeight = Math.max(50, containerHeight - padding * 2);

            // 基于实际模型尺寸计算缩放
            const scaleX = availableWidth / modelWidth;
            const scaleY = availableHeight / modelHeight;

            // 取较小值确保完整显示
            let defaultScale = Math.min(scaleX, scaleY);
            defaultScale = Math.max(0.01, Math.min(defaultScale, 1.0));

            model.scale.set(defaultScale);

            // 将模型居中显示在容器中央
            model.x = containerWidth * 0.5;
            model.y = containerHeight * 0.5;

            // 锚点保持中心，确保模型居中缩放
            model.anchor.set(0.5, 0.5);
        };

        // 添加窗口大小变化的监听，当预览区域大小变化时重新计算模型缩放和位置
        function resizePreviewModel() {
            const container = document.getElementById('live2d-preview-content');
            if (live2dPreviewManager && live2dPreviewManager.pixi_app && container &&
                container.clientWidth > 0 && container.clientHeight > 0) {
                live2dPreviewManager.pixi_app.renderer.resize(container.clientWidth, container.clientHeight);
            }
            if (live2dPreviewManager && live2dPreviewManager.currentModel) {
                // 调用我们覆盖的applyModelSettings方法，重新计算模型缩放和位置
                live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
            }
        }

        // 添加removeModel方法的fallback，防止调用时出错
        if (!live2dPreviewManager.removeModel) {
            live2dPreviewManager.removeModel = async function (force) {
                try {
                    if (this.currentModel && this.app && this.app.stage) {
                        // 移除当前模型
                        this.app.stage.removeChild(this.currentModel);
                        this.currentModel = null;

                        // 如果有清理资源的方法，调用它
                        if (this.disposeCurrentModel) {
                            await this.disposeCurrentModel();
                        }
                    }
                } catch (error) {
                    console.error('Error removing model:', error);
                }
            };
        }

        // 添加窗口大小变化监听
        window.addEventListener('resize', resizePreviewModel);

    } catch (error) {
        console.error('Failed to initialize Live2D preview:', error);
        live2dPreviewManager = null;
        showMessage(window.t('steam.live2dInitFailed'), 'error');
    }
}

// 从文件夹加载Live2D模型
async function loadLive2DModelFromFolder(files) {
    try {
        if (!live2dPreviewManager || !live2dPreviewManager.pixi_app) {
            await initLive2DPreview();
        }

        // 获取第一个文件夹的名称
        const firstFolder = files[0].webkitRelativePath.split('/')[0];

        // 查找模型配置文件
        const modelConfigFile = files.find(file =>
            file.name.toLowerCase().endsWith('.model3.json') &&
            file.webkitRelativePath.startsWith(firstFolder + '/')
        );

        if (!modelConfigFile) {
            throw new Error(window.t('steam.modelConfigNotFound', '模型配置文件未找到'));
        }

        // 读取模型配置文件内容
        const modelConfigContent = await modelConfigFile.text();
        const modelConfig = JSON.parse(modelConfigContent);

        // 创建一个临时的模型加载环境
        const modelFiles = {};

        // 收集所有模型相关文件
        const motionFiles = [];
        const expressionFiles = [];

        for (const file of files) {
            if (file.webkitRelativePath.startsWith(firstFolder + '/')) {
                const relativePath = file.webkitRelativePath.substring(firstFolder.length + 1);
                modelFiles[relativePath] = file;

                // 收集动作文件
                if (file.name.toLowerCase().endsWith('.motion3.json')) {
                    motionFiles.push(relativePath);
                }
                // 收集表情文件
                if (file.name.toLowerCase().endsWith('.exp3.json')) {
                    expressionFiles.push(relativePath);
                }
            }
        }

        // 添加PreviewAll动作组到模型配置
        if (!modelConfig.FileReferences) modelConfig.FileReferences = {};
        if (!modelConfig.FileReferences.Motions) modelConfig.FileReferences.Motions = {};

        if (motionFiles.length > 0) {
            modelConfig.FileReferences.Motions.PreviewAll = motionFiles.map(file => ({
                File: file
            }));
        }

        // 更新表情引用
        if (expressionFiles.length > 0) {
            modelConfig.FileReferences.Expressions = expressionFiles.map(file => ({
                Name: file.split('/').pop().replace('.exp3.json', ''),
                File: file
            }));
        }

        // 加载模型 - 禁用所有交互功能
        currentPreviewModel = await live2dPreviewManager.loadModelFromFiles(modelConfig, modelFiles, {
            onProgress: (progress) => {
            },
            dragEnabled: false,
            wheelEnabled: false,
            touchZoomEnabled: false,
            mouseTracking: false
        });

        // 显示Canvas，隐藏占位符
        document.getElementById('live2d-preview-canvas').style.display = '';
        document.querySelector('.preview-placeholder').style.display = 'none';

        // 更新预览控件
        updatePreviewControls(motionFiles, expressionFiles);

        // 禁用所有交互功能
        live2dPreviewManager.setLocked(true, { updateFloatingButtons: false });
        // 直接禁用canvas的pointerEvents，确保点击拖动无效
        const previewCanvas = document.getElementById('live2d-preview-canvas');
        if (previewCanvas) {
            previewCanvas.style.pointerEvents = 'none';
        }

        // 确保覆盖层处于激活状态，阻挡所有鼠标事件
        const previewOverlay = document.getElementById('live2d-preview-overlay');
        if (previewOverlay) {
            previewOverlay.style.pointerEvents = 'auto';
        }

        showMessage(window.t('steam.live2dPreviewLoaded'), 'success');

    } catch (error) {
        console.error('Failed to load Live2D model:', error);
        showMessage(window.t('steam.live2dPreviewLoadFailed', { error: error.message }), 'error');

        // 在加载失败时隐藏预览控件
        hidePreviewControls();
    }
}

// 隐藏预览控件
function hidePreviewControls() {
    // 隐藏预览控件
    const previewControls = document.getElementById('live2d-preview-controls');
    if (previewControls) {
        previewControls.style.display = 'none';
    }

    // 显示占位符
    document.querySelector('.preview-placeholder').style.display = '';

    // 清空并禁用动作和表情选择器
    const motionSelect = document.getElementById('preview-motion-select');
    const expressionSelect = document.getElementById('preview-expression-select');
    const playMotionBtn = document.getElementById('preview-play-motion-btn');
    const playExpressionBtn = document.getElementById('preview-play-expression-btn');

    if (motionSelect) {
        motionSelect.innerHTML = '<option value="">' + window.t('live2d.pleaseLoadModel', '请先加载模型') + '</option>';
        motionSelect.disabled = true;
    }

    if (expressionSelect) {
        expressionSelect.innerHTML = '<option value="">' + window.t('live2d.pleaseLoadModel', '请先加载模型') + '</option>';
        expressionSelect.disabled = true;
    }

    if (playMotionBtn) {
        playMotionBtn.disabled = true;
    }

    if (playExpressionBtn) {
        playExpressionBtn.disabled = true;
    }
}

// 更新预览控件
function updatePreviewControls(motionFiles, expressionFiles) {
    const motionSelect = document.getElementById('preview-motion-select');
    const expressionSelect = document.getElementById('preview-expression-select');
    const playMotionBtn = document.getElementById('preview-play-motion-btn');
    const playExpressionBtn = document.getElementById('preview-play-expression-btn');
    const previewControls = document.getElementById('live2d-preview-controls');

    // 检查必要的DOM元素是否存在
    if (!motionSelect || !expressionSelect || !playMotionBtn || !playExpressionBtn) {
        console.error('Missing required DOM elements for preview controls');
        return;
    }

    // 清空现有选项
    motionSelect.innerHTML = '';
    expressionSelect.innerHTML = '';

    // 更新动作选择框
    if (motionFiles.length > 0) {
        motionSelect.disabled = false;
        playMotionBtn.disabled = false;

        // 添加动作选项（value 使用文件名，便于直接作为 live2d_idle_animation）
        motionFiles.forEach((motionFile) => {
            const option = document.createElement('option');
            option.value = motionFile;
            option.textContent = motionFile;
            motionSelect.appendChild(option);
        });
    } else {
        motionSelect.disabled = true;
        playMotionBtn.disabled = true;

        const option = document.createElement('option');
        option.value = '';
        option.textContent = window.t('live2d.noMotionFiles', '没有动作文件');
        motionSelect.appendChild(option);
    }

    // 更新表情选择框
    if (expressionFiles.length > 0) {
        expressionSelect.disabled = false;
        playExpressionBtn.disabled = false;

        // 添加表情选项
        expressionFiles.forEach(expressionFile => {
            const expressionName = expressionFile.split('/').pop().replace('.exp3.json', '');
            const option = document.createElement('option');
            option.value = expressionName;
            option.textContent = expressionName;
            expressionSelect.appendChild(option);
        });
    } else {
        expressionSelect.disabled = true;
        playExpressionBtn.disabled = true;

        const option = document.createElement('option');
        option.value = '';
        option.textContent = window.t('live2d.noExpressionFiles', '没有表情文件');
        expressionSelect.appendChild(option);
    }

    // 显示预览控件
    previewControls.style.display = '';
}

// 播放预览动作
const playMotionBtn = document.getElementById('preview-play-motion-btn');
if (playMotionBtn) {
    playMotionBtn.addEventListener('click', () => {
        if (!currentPreviewModel) return;

        const motionSelect = document.getElementById('preview-motion-select');
        const motionFile = motionSelect.value;
        if (!motionFile) return;

        const motionIndex = (window._previewMotionFiles || []).indexOf(motionFile);
        if (motionIndex < 0) return;

        try {
            currentPreviewModel.motion('PreviewAll', motionIndex, 3);
        } catch (error) {
            console.error('Failed to play motion:', error);
            showMessage(window.t('live2d.playMotionFailed', { motion: motionFile }), 'error');
        }
    });
}

// 播放预览表情
const playExpressionBtn = document.getElementById('preview-play-expression-btn');
if (playExpressionBtn) {
    playExpressionBtn.addEventListener('click', () => {
        if (!currentPreviewModel) return;

        const expressionSelect = document.getElementById('preview-expression-select');
        const expressionName = expressionSelect.value;

        if (!expressionName) return;

        try {
            currentPreviewModel.expression(expressionName);
        } catch (error) {
            console.error('Failed to play expression:', error);
            showMessage(window.t('live2d.playExpressionFailed', { expression: expressionName }), 'error');
        }
    });
}

// 页面加载完成后初始化Live2D预览环境
document.addEventListener('DOMContentLoaded', function () {
    // 延迟初始化，确保其他资源已加载
    setTimeout(initLive2DPreview, 1000);
});

// 注意事项标签功能
(function () {
    const tagsContainer = document.getElementById('notes-tags-container');
    const notesInput = document.getElementById('workshop-notes-input');
    let notesTags = [];

    // 渲染标签
    function renderTags() {
        tagsContainer.innerHTML = '';
        const removeTagTitle = window.t ? window.t('steam.removeTag') : '删除标签';
        notesTags.forEach((tag, index) => {
            const tagElement = document.createElement('span');
            tagElement.className = 'tag';

            const tagText = document.createElement('span');
            tagText.textContent = tag;

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'tag-remove';
            removeButton.title = removeTagTitle;
            removeButton.setAttribute('aria-label', removeTagTitle);
            removeButton.setAttribute('data-i18n-title', 'steam.removeTag');
            removeButton.setAttribute('data-i18n-aria', 'steam.removeTag');
            removeButton.addEventListener('click', () => removeNotesTag(index));

            const removeIcon = document.createElement('span');
            removeIcon.textContent = '×';
            removeButton.appendChild(removeIcon);

            tagElement.appendChild(tagText);
            tagElement.appendChild(removeButton);
            tagsContainer.appendChild(tagElement);
        });
        if (window.updatePageTexts) {
            window.updatePageTexts();
        }
        updateNotesPreview(); // 更新预览，移到循环外部确保无论是否有标签都会执行
    }

    // 添加标签
    function addNotesTag(tagValue) {
        if (tagValue && tagValue.trim()) {
            const tag = tagValue.trim();

            // 检查标签数量是否超过限制（最多4个）
            if (notesTags.length >= 4) {
                alert(window.t ? window.t('steam.tagLimitReached') : '标签数量不能超过4个！');
                return;
            }

            // 检查标签字数是否超过限制（最多30字）
            if (tag.length > 30) {
                alert(window.t ? window.t('steam.tagTooLong') : '标签字数不能超过30字！');
                return;
            }

            // 去重
            if (!notesTags.includes(tag)) {
                notesTags.push(tag);
                renderTags();
            }
        }
    }

    // 删除标签
    function removeNotesTag(index) {
        notesTags.splice(index, 1);
        renderTags();
    }

    window.removeNotesTag = removeNotesTag;

    // 处理输入框变化
    function handleInput() {
        const inputValue = notesInput.value;

        // 当输入空格时添加标签
        if (inputValue.endsWith(' ')) {
            const tagValue = inputValue.trim();
            addNotesTag(tagValue);
            notesInput.value = '';
        }
    }

    // 监听输入变化，按空格添加标签
    if (notesInput) {
        notesInput.addEventListener('input', handleInput);
    }

    // 导出addNotesTag函数供外部使用
    window.addNotesTag = addNotesTag;
})();

// 预览图片选择功能
function selectPreviewImage() {
    // 创建文件选择事件监听
    const fileInput = document.getElementById('preview-image-file');

    // 清除之前的事件监听
    fileInput.onchange = null;

    // 添加新的事件监听
    fileInput.onchange = function (e) {
        if (e.target.files && e.target.files[0]) {
            const file = e.target.files[0];
            const hintElement = document.getElementById('preview-image-size-hint');

            // 校验文件大小（1MB = 1024 * 1024 字节）
            const maxSize = 1024 * 1024; // 1MB
            if (file.size > maxSize) {
                // 文件超过1MB，将提示文字变为红色
                if (hintElement) {
                    hintElement.style.color = 'red';
                }
                showMessage(window.t ? window.t('steam.previewImageSizeExceeded') : '预览图片大小超过1MB，请选择较小的图片', 'error');
                // 清空文件选择
                e.target.value = '';
                return;
            } else {
                // 文件大小符合要求，将提示文字恢复为默认色
                if (hintElement) {
                    hintElement.style.color = '#333';
                }
            }

            // 创建FormData对象，用于上传文件
            const formData = new FormData();
            // 获取原始文件扩展名
            const fileExtension = file.name.split('.').pop().toLowerCase();
            // 创建新的File对象，使用统一的文件名"preview.扩展名"
            const renamedFile = new File([file], `preview.${fileExtension}`, {
                type: file.type,
                lastModified: file.lastModified
            });
            formData.append('file', renamedFile);

            // 获取内容文件夹路径（如果已选择）
            const contentFolder = document.getElementById('content-folder').value.trim();
            if (contentFolder) {
                formData.append('content_folder', contentFolder);
            }

            // 显示上传进度
            showMessage(window.t ? window.t('steam.uploadingPreviewImage') : '正在上传预览图片...', 'info');

            // 上传文件到服务器
            fetch('/api/steam/workshop/upload-preview-image', {
                method: 'POST',
                body: formData
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 设置服务器返回的临时文件路径
                        document.getElementById('preview-image').value = data.file_path;
                        showMessage(window.t ? window.t('steam.previewImageUploaded') : '预览图片上传成功', 'success');
                    } else {
                        console.error("上传预览图片失败:", data.message);
                        showMessage(window.t ? window.t('steam.previewImageUploadFailed', { error: data.message }) : `预览图片上传失败: ${data.message}`, 'error');
                    }
                })
                .catch(error => {
                    console.error("上传预览图片出错:", error);
                    showMessage(window.t ? window.t('steam.previewImageUploadError', { error: error.message }) : `预览图片上传出错: ${error.message}`, 'error');
                });
        }
    };

    // 触发文件选择对话框
    fileInput.click();
}


// ===================== 主人档案管理 =====================

async function loadMasterProfile() {
    try {
        const resp = await fetch('/api/characters', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        const master = data?.['主人'] || {};
        renderMasterForm(master);
    } catch (e) {
        console.error('加载主人档案失败:', e);
    }
}

function renderMasterForm(master) {
    const form = document.getElementById('master-form');
    if (!form) return;
    form.innerHTML = '';

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
    nameInput.value = master['档案名'] || '';
    nameInput.autocomplete = 'off';
    fieldRow.appendChild(nameInput);
    baseWrapper.appendChild(fieldRow);

    // 重命名按钮
    const renameBtn = document.createElement('button');
    renameBtn.type = 'button';
    renameBtn.className = 'btn sm';
    renameBtn.style.minWidth = '70px';
    const renameText = window.t ? window.t('character.rename') : '修改名称';
    renameBtn.textContent = renameText;
    renameBtn.onclick = renameMaster;
    baseWrapper.appendChild(renameBtn);

    form.appendChild(baseWrapper);

    // 自定义字段
    Object.keys(master).forEach(k => {
        if (k === '档案名') return;
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row';

        const label = document.createElement('label');
        label.textContent = k;
        wrapper.appendChild(label);

        const row = document.createElement('div');
        row.className = 'field-row';
        const textarea = document.createElement('textarea');
        textarea.name = k;
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
        attachAutoSaveListener(textarea, 'master');
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
    saveBtn.style.display = 'none';
    const saveText = window.t ? window.t('character.saveMaster') : '保存主人设定';
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

    // 为档案名输入框添加自动保存和变化监听
    attachAutoSaveListener(nameInput, 'master');
    nameInput.addEventListener('input', showMasterActionButtons);
    nameInput.addEventListener('change', showMasterActionButtons);
}

function showMasterActionButtons() {
    const form = document.getElementById('master-form');
    if (!form) return;
    const saveBtn = form.querySelector('#save-master-btn');
    const cancelBtn = form.querySelector('#cancel-master-btn');
    if (saveBtn) saveBtn.style.display = '';
    if (cancelBtn) cancelBtn.style.display = '';
}

async function saveMasterForm() {
    const form = document.getElementById('master-form');
    if (!form) return;
    const nameInput = form.querySelector('input[name="档案名"]');
    if (!nameInput || !nameInput.value.trim()) {
        showMessage(window.t ? window.t('character.profileNameRequired') : '档案名为必填项', 'error');
        return;
    }
    const data = {};
    for (const [k, v] of new FormData(form).entries()) {
        if (k && v) data[k] = v;
    }
    try {
        const resp = await fetch('/api/characters/master', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (resp.ok) {
            showMessage(window.t ? window.t('character.saveMasterSuccess') : '保存主人设定成功', 'success');
            await loadMasterProfile();
        } else {
            const err = await resp.text();
            showMessage((window.t ? window.t('character.saveMasterError') : '保存失败') + ': ' + err, 'error');
        }
    } catch (e) {
        showMessage(window.t ? window.t('character.saveMasterError') : '保存主人设定失败', 'error');
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
    const fieldName = input.name;
    if (!fieldName) return;
    if (fieldName === '档案名' && !input.value.trim()) return;
    const allData = {};
    for (const [k, v] of new FormData(form).entries()) {
        if (k && v) allData[k] = v;
    }
    if (!allData['档案名']) return;
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
    const fieldName = input.name;
    if (!fieldName || fieldName === '档案名' || fieldName === 'voice_id') return;
    const data = { '档案名': catgirlName };
    const ALL_RESERVED_FIELDS = ['档案名', ...getWorkshopHiddenFields()];
    const inputs = form.querySelectorAll('input, textarea');
    inputs.forEach(inp => {
        if (inp.name && !ALL_RESERVED_FIELDS.includes(inp.name) && inp.value) {
            data[inp.name] = inp.value;
        }
    });
    try {
        const resp = await fetch('/api/characters/catgirl/' + encodeURIComponent(catgirlName), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (resp.ok) {
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
            window.t ? window.t('character.addMasterFieldTitle') : '新增主人设定'
        );
    } else {
        key = prompt(window.t ? window.t('character.addMasterFieldPrompt') : '请输入新设定的名称（键名）');
    }
    if (!key || key === '档案名') return;
    const exists = Array.from(form.querySelectorAll('textarea, input')).some(el => el.name === key);
    if (exists) {
        showMessage(window.t ? window.t('character.fieldExists') : '该设定已存在', 'error');
        return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'field-row-wrapper custom-row';
    const label = document.createElement('label');
    label.textContent = key;
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
    attachAutoSaveListener(textarea, 'master');
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
    const oldName = nameInput?.value || '';
    let newName;
    if (typeof showPrompt === 'function') {
        newName = await showPrompt(
            window.t ? window.t('character.renamePrompt') : '请输入新的档案名',
            oldName,
            window.t ? window.t('character.renameTitle') : '修改名称'
        );
    } else {
        newName = prompt(window.t ? window.t('character.renamePrompt') : '请输入新的档案名', oldName);
    }
    if (!newName || newName.trim() === '' || newName.trim() === oldName) return;
    try {
        const resp = await fetch('/api/characters/master/' + encodeURIComponent(oldName) + '/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_name: newName.trim() })
        });
        const result = await resp.json();
        if (result.success) {
            showMessage(window.t ? window.t('character.renameSuccess') : '重命名成功', 'success');
            await loadMasterProfile();
        } else {
            showMessage(result.error || (window.t ? window.t('character.renameFailed') : '重命名失败'), 'error');
        }
    } catch (e) {
        showMessage(window.t ? window.t('character.renameError') : '重命名时发生错误', 'error');
    }
}

function toggleMasterSection() {
    const content = document.getElementById('master-profile-content');
    const header = document.getElementById('master-profile-header');
    if (!content || !header) return;
    const isHidden = content.style.display === 'none';
    content.style.display = isHidden ? 'block' : 'none';
    header.classList.toggle('open', isHidden);
    header.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
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

// ===================== 云存档同步与生命周期 =====================

function getCurrentUiLanguage() {
    if (window.i18n && typeof window.i18n.language === 'string' && window.i18n.language.trim()) {
        return window.i18n.language.trim();
    }
    const saved = localStorage.getItem('i18nextLng');
    if (typeof saved === 'string' && saved.trim()) return saved.trim();
    return '';
}

function hasUnsavedNewCatgirlDraft() {
    const form = document.getElementById('catgirl-form-new');
    if (!form) return false;
    const nameInput = form.querySelector('input[name="档案名"]');
    return !!(nameInput && nameInput.value && nameInput.value.trim());
}

const CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY = 'neko_cloudsave_character_sync';
const CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE = 'cloudsave_character_changed';
const CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME = 'neko_cloudsave_character_sync';

function handleCloudsaveCharacterSync(data) {
    if (!data || data.type !== CLOUDSAVE_CHARACTER_SYNC_MESSAGE_TYPE) return;
    if (hasUnsavedNewCatgirlDraft()) {
        console.log('[CharacterCardManager] Unsaved draft detected, deferring sync refresh');
        return;
    }
    console.log('[CharacterCardManager] Received cloudsave sync:', data.action);
    loadCharacterCards().catch(e => console.warn('Cloudsave sync refresh failed:', e));
}

(function initCloudsaveSync() {
    if (typeof BroadcastChannel === 'function') {
        try {
            const channel = new BroadcastChannel(CLOUDSAVE_CHARACTER_SYNC_CHANNEL_NAME);
            channel.onmessage = function (event) {
                handleCloudsaveCharacterSync(event.data);
            };
        } catch (e) {
            console.warn('BroadcastChannel init failed:', e);
        }
    }

    window.addEventListener('storage', function (event) {
        if (event.key !== CLOUDSAVE_CHARACTER_SYNC_EVENT_KEY) return;
        try {
            const data = JSON.parse(event.newValue);
            handleCloudsaveCharacterSync(data);
        } catch (e) {
            console.warn('localStorage sync parse failed:', e);
        }
    });
})();

// sendBeacon 生命周期
window.addEventListener('beforeunload', function () {
    try {
        navigator.sendBeacon('/api/beacon/shutdown');
    } catch (e) { /* ignore */ }
});

window.addEventListener('unload', function () {
    try {
        navigator.sendBeacon('/api/beacon/shutdown');
    } catch (e) { /* ignore */ }
});

// =========================================================================
// 清理遗留记忆（Legacy Memory Cleanup）
// -----------------------------------------------------------------------
// 流程：按钮点击 → openLegacyMemoryModal() → fetch GET /api/memory/legacy/scan
// → 填充表格 → 用户勾选 → legacyMemoryPurgeSelected() → POST /api/memory/legacy/purge
// → toast 汇报 → 重新扫描刷新弹层
// =========================================================================

// 最近一次 scan 结果缓存（用于快捷全选/只选未关联的复用）
let _legacyMemoryLastScan = null;

function _legacyMemoryI18n(key, fallback, opts) {
    try {
        if (window.t) {
            const v = window.t(key, opts || {});
            if (v && v !== key) return v;
        }
    } catch (_) { /* ignore */ }
    return fallback;
}

function _legacyFormatSize(bytes) {
    if (typeof bytes !== 'number' || bytes < 0) return '—';
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let v = bytes;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i++;
    }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function _legacyEscapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function openLegacyMemoryModal() {
    const modal = document.getElementById('legacyMemoryModal');
    if (!modal) return;
    modal.style.display = 'flex';
    // 重置状态
    const tableWrap = document.getElementById('legacy-memory-table-wrap');
    const toolbar = document.getElementById('legacy-memory-toolbar');
    const runtimeInfo = document.getElementById('legacy-memory-runtime-info');
    const deleteBtn = document.getElementById('legacy-memory-delete-btn');
    const deleteCount = document.getElementById('legacy-memory-delete-count');
    if (tableWrap) {
        tableWrap.innerHTML = `<div class="empty-state"><p>${_legacyEscapeHtml(
            _legacyMemoryI18n('steam.legacyScanLoading', '扫描中...')
        )}</p></div>`;
    }
    if (toolbar) toolbar.style.display = 'none';
    if (runtimeInfo) runtimeInfo.textContent = '';
    if (deleteBtn) deleteBtn.disabled = true;
    if (deleteCount) deleteCount.textContent = ' (0)';
    // 发起扫描
    _legacyMemoryScan();
}

function closeLegacyMemoryModal() {
    const modal = document.getElementById('legacyMemoryModal');
    if (modal) modal.style.display = 'none';
}

function closeLegacyMemoryModalOnOutsideClick(event) {
    if (event && event.target && event.target.id === 'legacyMemoryModal') {
        closeLegacyMemoryModal();
    }
}

function _legacyMemoryScan() {
    fetch('/api/memory/legacy/scan')
        .then((resp) => resp.json().then((data) => ({ resp, data })).catch(() => ({ resp, data: null })))
        .then(({ resp, data }) => {
            // 只记录状态 + 汇总计数；legacy_roots 里包含 Documents 路径，不落日志
            console.info('[legacy memory scan]', {
                status: resp.status,
                ok: resp.ok,
                success: !!(data && data.success),
                total_entries: data && data.total_entries,
                total_size_bytes: data && data.total_size_bytes,
                root_count: data && Array.isArray(data.legacy_roots) ? data.legacy_roots.length : 0,
            });
            if (!resp.ok || !data || !data.success) {
                const errMsg = (data && data.error) || `HTTP ${resp.status}`;
                const tableWrap = document.getElementById('legacy-memory-table-wrap');
                if (tableWrap) {
                    tableWrap.innerHTML = `<div class="empty-state"><p style="color:#e57373;">${_legacyEscapeHtml(
                        _legacyMemoryI18n('steam.legacyScanFailed', '扫描失败') + ': ' + errMsg
                    )}</p></div>`;
                }
                return;
            }
            _legacyMemoryLastScan = data;
            _legacyMemoryRenderTable(data);
        })
        .catch((err) => {
            console.error('[legacy memory scan] 失败:', err);
            const tableWrap = document.getElementById('legacy-memory-table-wrap');
            if (tableWrap) {
                tableWrap.innerHTML = `<div class="empty-state"><p style="color:#e57373;">${_legacyEscapeHtml(
                    _legacyMemoryI18n('steam.legacyScanFailed', '扫描失败') + ': ' + (err && err.message ? err.message : err)
                )}</p></div>`;
            }
        });
}

function _legacyMemoryRenderTable(data) {
    const tableWrap = document.getElementById('legacy-memory-table-wrap');
    const toolbar = document.getElementById('legacy-memory-toolbar');
    const runtimeInfo = document.getElementById('legacy-memory-runtime-info');
    if (!tableWrap) return;

    if (runtimeInfo) {
        const runtimePath = data.runtime_memory_dir || '-';
        runtimeInfo.textContent = _legacyMemoryI18n(
            'steam.legacyRuntimeMemory',
            `runtime memory: ${runtimePath}`,
            { path: runtimePath }
        );
    }

    // 总条目数为 0 → empty state
    if (!data.legacy_roots || data.total_entries === 0) {
        tableWrap.innerHTML = `<div class="empty-state"><p>${_legacyEscapeHtml(
            _legacyMemoryI18n('steam.legacyScanEmpty', '未发现遗留记忆，无需清理')
        )}</p></div>`;
        if (toolbar) toolbar.style.display = 'none';
        const deleteBtn = document.getElementById('legacy-memory-delete-btn');
        if (deleteBtn) deleteBtn.disabled = true;
        return;
    }

    // 构造表格
    const rows = [];
    let globalIndex = 0;
    for (const root of data.legacy_roots) {
        if (!root.entries || root.entries.length === 0) continue;
        rows.push(`
            <tr>
                <td colspan="5" style="background:#2a2a2a;color:#ccc;padding:6px 10px;font-size:12px;">
                    <strong>${_legacyEscapeHtml(root.root)}</strong>
                    <span style="color:#888;margin-left:8px;">[${_legacyEscapeHtml(root.source || '')}]</span>
                </td>
            </tr>
        `);
        for (const entry of root.entries) {
            const statusLabel = entry.is_unlinked
                ? _legacyMemoryI18n('steam.legacyStatusUnlinked', '未关联')
                : (entry.runtime_has_same_name
                    ? _legacyMemoryI18n('steam.legacyStatusDuplicate', '已有同名副本')
                    : _legacyMemoryI18n('steam.legacyStatusListed', '仍在角色列表'));
            const statusColor = entry.is_unlinked ? '#e57373' : (entry.runtime_has_same_name ? '#64b5f6' : '#9e9e9e');
            const sizeStr = _legacyFormatSize(entry.size_bytes);
            rows.push(`
                <tr data-index="${globalIndex}" data-unlinked="${entry.is_unlinked ? '1' : '0'}">
                    <td style="padding:6px 10px;width:30px;">
                        <input type="checkbox" class="legacy-memory-row-cb" data-path="${_legacyEscapeHtml(entry.path)}" onchange="_legacyMemoryUpdateDeleteCount()">
                    </td>
                    <td style="padding:6px 10px;">${_legacyEscapeHtml(entry.name)}</td>
                    <td style="padding:6px 10px;color:#888;font-size:12px;word-break:break-all;">${_legacyEscapeHtml(entry.path)}</td>
                    <td style="padding:6px 10px;text-align:right;color:#ccc;">${_legacyEscapeHtml(sizeStr)}</td>
                    <td style="padding:6px 10px;color:${statusColor};font-weight:500;">${_legacyEscapeHtml(statusLabel)}</td>
                </tr>
            `);
            globalIndex++;
        }
    }

    tableWrap.innerHTML = `
        <div style="overflow-x:auto;max-height:50vh;overflow-y:auto;border:1px solid #333;border-radius:4px;">
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead style="position:sticky;top:0;background:#1a1a1a;z-index:1;">
                    <tr>
                        <th style="padding:6px 10px;text-align:left;"></th>
                        <th style="padding:6px 10px;text-align:left;" data-i18n="steam.legacyColName">名称</th>
                        <th style="padding:6px 10px;text-align:left;" data-i18n="steam.legacyColPath">路径</th>
                        <th style="padding:6px 10px;text-align:right;" data-i18n="steam.legacyColSize">大小</th>
                        <th style="padding:6px 10px;text-align:left;" data-i18n="steam.legacyColStatus">状态</th>
                    </tr>
                </thead>
                <tbody>${rows.join('')}</tbody>
            </table>
        </div>
        <div style="margin-top:10px;color:#888;font-size:12px;">
            ${_legacyEscapeHtml(_legacyMemoryI18n(
                'steam.legacyScanFooter',
                `共 ${data.total_entries} 条，总大小约 ${_legacyFormatSize(data.total_size_bytes)}`,
                { count: data.total_entries, size: _legacyFormatSize(data.total_size_bytes) }
            ))}
        </div>
    `;
    if (toolbar) toolbar.style.display = 'flex';
    _legacyMemoryUpdateDeleteCount();
}

function _legacyMemoryUpdateDeleteCount() {
    const cbs = document.querySelectorAll('.legacy-memory-row-cb');
    let checked = 0;
    cbs.forEach((cb) => { if (cb.checked) checked++; });
    const deleteBtn = document.getElementById('legacy-memory-delete-btn');
    const deleteCount = document.getElementById('legacy-memory-delete-count');
    if (deleteBtn) deleteBtn.disabled = checked === 0;
    if (deleteCount) deleteCount.textContent = ` (${checked})`;
}

function legacyMemorySelectAll() {
    document.querySelectorAll('.legacy-memory-row-cb').forEach((cb) => { cb.checked = true; });
    _legacyMemoryUpdateDeleteCount();
}

function legacyMemorySelectNone() {
    document.querySelectorAll('.legacy-memory-row-cb').forEach((cb) => { cb.checked = false; });
    _legacyMemoryUpdateDeleteCount();
}

function legacyMemorySelectUnlinked() {
    document.querySelectorAll('tr[data-index]').forEach((tr) => {
        const cb = tr.querySelector('.legacy-memory-row-cb');
        if (!cb) return;
        cb.checked = tr.getAttribute('data-unlinked') === '1';
    });
    _legacyMemoryUpdateDeleteCount();
}

function legacyMemoryPurgeSelected() {
    const cbs = document.querySelectorAll('.legacy-memory-row-cb');
    const paths = [];
    cbs.forEach((cb) => {
        if (cb.checked) {
            const p = cb.getAttribute('data-path');
            if (p) paths.push(p);
        }
    });
    if (paths.length === 0) return;

    const confirmMsg = _legacyMemoryI18n(
        'steam.legacyDeleteConfirm',
        `确认永久删除 ${paths.length} 个目录？此操作不可撤销。`,
        { count: paths.length }
    );
    if (!window.confirm(confirmMsg)) return;

    const deleteBtn = document.getElementById('legacy-memory-delete-btn');
    if (deleteBtn) deleteBtn.disabled = true;

    fetch('/api/memory/legacy/purge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths }),
    })
        .then((resp) => resp.json().then((data) => ({ resp, data })).catch(() => ({ resp, data: null })))
        .then(({ resp, data }) => {
            // 只记录状态 + 计数；removed / errors 内容含本地路径，不落日志
            console.info('[legacy memory purge]', {
                status: resp.status,
                ok: resp.ok,
                success: !!(data && data.success),
                removed_count: data && Array.isArray(data.removed) ? data.removed.length : 0,
                error_count: data && Array.isArray(data.errors) ? data.errors.length : 0,
            });
            if (!resp.ok || !data || !data.success) {
                const errMsg = (data && data.error) || `HTTP ${resp.status}`;
                showMessage(
                    _legacyMemoryI18n('steam.legacyDeleteFailed', '清理失败') + ': ' + errMsg,
                    'error',
                    6000
                );
                if (deleteBtn) deleteBtn.disabled = false;
                return;
            }
            const okCount = Array.isArray(data.removed) ? data.removed.length : 0;
            const failCount = Array.isArray(data.errors) ? data.errors.length : 0;
            const msg = _legacyMemoryI18n(
                'steam.legacyDeleteDone',
                `已删除 ${okCount} 条，失败 ${failCount} 条`,
                { ok: okCount, failed: failCount }
            );
            showMessage(msg, failCount > 0 ? 'warning' : 'success', 5000);
            if (failCount > 0) {
                console.warn('[legacy memory purge errors]', data.errors);
            }
            // 刷新扫描
            _legacyMemoryScan();
        })
        .catch((err) => {
            console.error('[legacy memory purge] 失败:', err);
            showMessage(
                _legacyMemoryI18n('steam.legacyDeleteFailed', '清理失败') + ': ' + (err && err.message ? err.message : err),
                'error',
                6000
            );
            if (deleteBtn) deleteBtn.disabled = false;
        });
}
