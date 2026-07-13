// 角色保留字段配置（优先从后端集中配置加载；失败时使用前端兜底）
// 共用工具由 reserved_fields_utils.js 提供（ReservedFieldsUtils）
let characterReservedFieldsConfig = ReservedFieldsUtils.emptyConfig();
let _reservedFieldsReady = null;

const SYSTEM_RESERVED_FIELDS_FALLBACK = ReservedFieldsUtils.SYSTEM_RESERVED_FIELDS_FALLBACK;
const WORKSHOP_RESERVED_FIELDS_FALLBACK = ReservedFieldsUtils.WORKSHOP_RESERVED_FIELDS_FALLBACK;
const FRONTEND_FORCE_HIDDEN_FIELDS = [
    'live2d_item_id',
    'live2d_idle_animation',
    '_reserved',
    '_field_order',
    'item_id',
    'idleAnimation',
    'idleAnimations',
    'mmd_idle_animation',
    'mmd_idle_animations',
];

let charaCardParticleCanvas = null;
let charaCardParticleContext = null;
let charaCardParticleFrame = 0;
let charaCardParticles = [];
let charaCardParticleResizeBound = false;
let charaCardParticleResizeHandler = null;
let charaCardDissolveRunId = 0;
const CHARA_CARD_DISSOLVE_PARTICLE_LIMIT = 144;
const CHARA_CARD_DISSOLVE_DURATION = 680;

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
    return _uniqueFields([...cfg.workshop_reserved_fields, ...FRONTEND_FORCE_HIDDEN_FIELDS]);
}

function getWorkshopHiddenFields() {
    const cfg = _getReservedConfigOrFallback();
    // 即使运行中的后端还没重启、返回了旧保留字段列表，也不要把这些兼容字段渲染成普通设定。
    return _uniqueFields([...cfg.all_reserved_fields, ...FRONTEND_FORCE_HIDDEN_FIELDS]);
}

function normalizeCharacterFieldName(fieldName) {
    return String(fieldName ?? '').trim();
}

function isCharacterReservedFieldName(fieldName) {
    const normalizedFieldName = normalizeCharacterFieldName(fieldName);
    if (!normalizedFieldName) return false;
    return getWorkshopHiddenFields().includes(normalizedFieldName);
}

function normalizeCharacterFieldValue(value, fieldName) {
    const normalizedFieldName = normalizeCharacterFieldName(fieldName);
    if (normalizedFieldName === '档案名') {
        return typeof value === 'string' ? value.trim() : value;
    }
    return value;
}

function collectCharacterFields(form, options = {}) {
    const {
        baseData = {},
        excludeFieldNames = [],
        includeProfileName = false,
    } = options;
    const data = {};
    const seen = new Set();
    const fieldOrder = [];

    Object.entries(baseData || {}).forEach(([key, value]) => {
        const normalizedKey = normalizeCharacterFieldName(key);
        if (!normalizedKey) return;
        data[normalizedKey] = value;
        seen.add(normalizedKey);
    });

    const excluded = new Set(
        (excludeFieldNames || []).map(normalizeCharacterFieldName).filter(Boolean)
    );
    if (!includeProfileName) {
        excluded.add('档案名');
    }

    for (const [rawKey, rawValue] of new FormData(form).entries()) {
        const key = normalizeCharacterFieldName(rawKey);
        if (!key || excluded.has(key) || isCharacterReservedFieldName(key)) {
            continue;
        }
        const value = normalizeCharacterFieldValue(rawValue, key);
        if (!value) {
            continue;
        }
        if (seen.has(key)) {
            return { data, duplicateKey: key, fieldOrder };
        }
        data[key] = value;
        seen.add(key);
        fieldOrder.push(key);
    }

    return { data, duplicateKey: '', fieldOrder };
}

const CHARACTER_FIELD_ORDER_PAYLOAD_KEY = '_field_order';

function attachCharacterFieldOrderPayload(data, fieldOrder) {
    if (!data || !Array.isArray(fieldOrder)) return data;
    const seen = new Set();
    data[CHARACTER_FIELD_ORDER_PAYLOAD_KEY] = fieldOrder
        .map(normalizeCharacterFieldName)
        .filter(key => {
            if (!key || seen.has(key) || isCharacterReservedFieldName(key)) return false;
            seen.add(key);
            return true;
        });
    return data;
}

function getStoredCharacterFieldOrder(rawData) {
    if (!rawData || typeof rawData !== 'object') return [];
    const reserved = rawData._reserved && typeof rawData._reserved === 'object' ? rawData._reserved : null;
    const order = reserved && Array.isArray(reserved.field_order)
        ? reserved.field_order
        : (Array.isArray(rawData[CHARACTER_FIELD_ORDER_PAYLOAD_KEY]) ? rawData[CHARACTER_FIELD_ORDER_PAYLOAD_KEY] : []);
    const seen = new Set();
    return order
        .map(normalizeCharacterFieldName)
        .filter(key => {
            if (!key || seen.has(key)) return false;
            seen.add(key);
            return true;
        });
}

function getOrderedCharacterFieldKeys(rawData, hiddenFields = [], options = {}) {
    if (!rawData || typeof rawData !== 'object') return [];
    // 渲染自定义字段时要剔除系统保留名（live2d/model_type 等）；但工坊导入 scanCharaFile 需要保留这些
    // 模型字段，只按调用方传入的 hiddenFields 过滤，故用此开关让调用方决定是否额外剔除保留名。
    const { skipReservedNames = true } = options;
    const hidden = new Set((hiddenFields || []).map(normalizeCharacterFieldName).filter(Boolean));
    const seen = new Set();
    const keys = [];
    const addKey = (rawKey) => {
        const key = normalizeCharacterFieldName(rawKey);
        if (!key || seen.has(key) || hidden.has(key)) return;
        if (skipReservedNames && isCharacterReservedFieldName(key)) return;
        const value = rawData[key];
        if (value === null || value === undefined) return;
        seen.add(key);
        keys.push(key);
    };

    // 数字形式的对象 key 会被浏览器提前枚举，优先使用后端保存的显式顺序。
    getStoredCharacterFieldOrder(rawData).forEach(addKey);
    Object.keys(rawData).forEach(addKey);
    return keys;
}

function setLocalRawDataFieldOrder(rawData, fieldOrder) {
    if (!rawData || typeof rawData !== 'object' || !Array.isArray(fieldOrder)) return rawData;
    const reserved = rawData._reserved && typeof rawData._reserved === 'object'
        ? rawData._reserved
        : (rawData._reserved = {});
    const seen = new Set();
    reserved.field_order = fieldOrder
        .map(normalizeCharacterFieldName)
        .filter(key => {
            if (!key || seen.has(key) || isCharacterReservedFieldName(key) || rawData[key] === undefined) return false;
            seen.add(key);
            return true;
        });
    return rawData;
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

function createVoiceConfigSwitchOpId(lanlanName) {
    return 'voice-config-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8) + '-' + (lanlanName || 'current');
}

function notifyVoiceConfigSwitching(lanlanName, active, opId) {
    const payload = {
        action: 'voice_config_switching',
        type: 'voice_config_switching',
        active: !!active,
        op_id: opId || '',
        lanlan_name: lanlanName || '',
        timestamp: Date.now()
    };

    if (typeof BroadcastChannel !== 'undefined') {
        try {
            const channel = new BroadcastChannel('neko_page_channel');
            channel.postMessage(payload);
            setTimeout(() => channel.close(), 1000);
        } catch (_) { /* 跨窗口同步失败时继续走 postMessage 兜底 */ }
    }

    if (window.nekoElectronVoiceConfigSwitching && typeof window.nekoElectronVoiceConfigSwitching.send === 'function') {
        try { window.nekoElectronVoiceConfigSwitching.send(payload); } catch (_) { }
    }

    if (window.parent !== window) {
        try { window.parent.postMessage(payload, window.location.origin); } catch (_) { }
    }
    if (window.opener && !window.opener.closed) {
        try { window.opener.postMessage(payload, window.location.origin); } catch (_) { }
    }
}

const WORKSHOP_VOICE_PROVIDER_REGISTRY_KEYS = Object.freeze({
    cosyvoice: 'qwen',
    cosyvoice_intl: 'qwen_intl',
    minimax: 'minimax',
    minimax_intl: 'minimax_intl',
});
const WORKSHOP_VOICE_RESTRICTED_REGISTRY_KEYS = new Set([
    'qwen_intl',
    'minimax_intl',
]);
const workshopVoiceProviderRestrictionState = {
    loaded: false,
    loadingPromise: null,
    isMainlandChinaUser: false,
    apiKeyRegistry: {},
};
const WORKSHOP_VOICE_PROVIDER_FETCH_TIMEOUT_MS = 5000;
const WORKSHOP_VOICE_PROVIDER_FETCH_ATTEMPTS = 3;
const WORKSHOP_VOICE_PROVIDER_FETCH_BACKOFF_MS = 250;

function sleepWorkshopVoiceProviderRetry(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchWorkshopVoiceProviderJson(url, options = {}) {
    let lastError = null;
    for (let attempt = 1; attempt <= WORKSHOP_VOICE_PROVIDER_FETCH_ATTEMPTS; attempt += 1) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), WORKSHOP_VOICE_PROVIDER_FETCH_TIMEOUT_MS);
        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal,
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return data;
        } catch (error) {
            lastError = error;
            if (attempt >= WORKSHOP_VOICE_PROVIDER_FETCH_ATTEMPTS) break;
            await sleepWorkshopVoiceProviderRetry(WORKSHOP_VOICE_PROVIDER_FETCH_BACKOFF_MS * attempt);
        } finally {
            clearTimeout(timeoutId);
        }
    }
    throw lastError || new Error('请求失败');
}

function getWorkshopVoiceProviderRegistryKey(provider) {
    return WORKSHOP_VOICE_PROVIDER_REGISTRY_KEYS[provider] || provider;
}

async function checkWorkshopVoiceMainlandChinaUser() {
    let data = null;
    try {
        data = await fetchWorkshopVoiceProviderJson('/api/config/steam_language', {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' }
        });
    } catch (_) {
        return true;
    }

    if (data && data.is_mainland_china === true) {
        return true;
    }

    const ipCountry = String((data && data.ip_country) || '').trim().toUpperCase();
    if (data && data.success === true && ipCountry && ipCountry !== 'CN') {
        return false;
    }

    return true;
}

async function loadWorkshopVoiceProviderRestrictionState() {
    if (workshopVoiceProviderRestrictionState.loaded) {
        return workshopVoiceProviderRestrictionState;
    }
    if (workshopVoiceProviderRestrictionState.loadingPromise) {
        return workshopVoiceProviderRestrictionState.loadingPromise;
    }

    workshopVoiceProviderRestrictionState.loadingPromise = (async () => {
        const [isMainlandChinaUser, providersResponse] = await Promise.all([
            checkWorkshopVoiceMainlandChinaUser(),
            fetchWorkshopVoiceProviderJson('/api/config/api_providers').catch(() => null)
        ]);
        let apiKeyRegistry = {};
        if (providersResponse && providersResponse.success) {
            apiKeyRegistry = providersResponse.api_key_registry || {};
        }
        workshopVoiceProviderRestrictionState.isMainlandChinaUser = !!isMainlandChinaUser;
        workshopVoiceProviderRestrictionState.apiKeyRegistry = apiKeyRegistry;
        workshopVoiceProviderRestrictionState.loaded = true;
        return workshopVoiceProviderRestrictionState;
    })().finally(() => {
        workshopVoiceProviderRestrictionState.loadingPromise = null;
    });

    return workshopVoiceProviderRestrictionState.loadingPromise;
}

async function ensureWorkshopVoiceProviderRestrictionsLoaded() {
    try {
        await loadWorkshopVoiceProviderRestrictionState();
    } catch (error) {
        console.warn('参考语音服务商地区配置加载失败，使用默认显示策略:', error);
    }
    return workshopVoiceProviderRestrictionState;
}

function isWorkshopVoiceProviderRestricted(provider) {
    if (!workshopVoiceProviderRestrictionState.isMainlandChinaUser) return false;
    const registryKey = getWorkshopVoiceProviderRegistryKey(provider);
    const entry = workshopVoiceProviderRestrictionState.apiKeyRegistry[registryKey];
    if (entry && Object.prototype.hasOwnProperty.call(entry, 'restricted')) {
        return entry.restricted === true;
    }
    return WORKSHOP_VOICE_RESTRICTED_REGISTRY_KEYS.has(registryKey);
}

function getFirstAvailableWorkshopVoiceProviderValue(providerSelect) {
    if (!providerSelect) return '';
    const options = Array.from(providerSelect.options || []);
    const availableOption = options.find(option => !option.disabled && !option.hidden && option.style.display !== 'none');
    return availableOption ? availableOption.value : '';
}

async function applyWorkshopVoiceProviderRestrictions(providerSelect) {
    await ensureWorkshopVoiceProviderRestrictionsLoaded();
    if (!providerSelect) return false;
    const previousValue = providerSelect.value;
    Array.from(providerSelect.options || []).forEach(option => {
        const restricted = isWorkshopVoiceProviderRestricted(option.value);
        option.disabled = restricted;
        option.hidden = restricted;
        option.style.display = restricted ? 'none' : '';
    });

    const selectedOption = providerSelect.options[providerSelect.selectedIndex];
    if (selectedOption && !selectedOption.disabled && !selectedOption.hidden && selectedOption.style.display !== 'none') {
        return false;
    }

    const fallbackValue = getFirstAvailableWorkshopVoiceProviderValue(providerSelect);
    if (fallbackValue) {
        providerSelect.value = fallbackValue;
    }
    return providerSelect.value !== previousValue;
}

async function initWorkshopVoiceProviderRestrictions() {
    const providerSelect = document.getElementById('voice-reference-provider-hint');
    await applyWorkshopVoiceProviderRestrictions(providerSelect);
    return workshopVoiceProviderRestrictionState;
}

// 顶部 tab 按钮初始化（旧版自定义 tooltip 因为文本与按钮文字重复且定位有误已移除）
document.addEventListener('DOMContentLoaded', function () {
    void loadCharacterReservedFieldsConfig();
    initWorkshopVoiceProviderRestrictions().catch(error => {
        console.warn('初始化参考语音服务商地区过滤失败:', error);
    });

    // 云存档管理按钮
    const openCloudsaveManagerBtn = document.getElementById('open-cloudsave-manager-btn');
    if (openCloudsaveManagerBtn) {
        setCloudsaveManagerEntryDisabled(openCloudsaveManagerBtn, true);
        openCloudsaveManagerBtn.addEventListener('click', openCloudsaveManager);
        void refreshCloudsaveManagerEntryAvailability(openCloudsaveManagerBtn);
    }
});

function setCloudsaveManagerEntryDisabled(openCloudsaveManagerBtn, disabled) {
    if (!openCloudsaveManagerBtn) return;
    const isDisabled = disabled === true;
    openCloudsaveManagerBtn.disabled = isDisabled;
    openCloudsaveManagerBtn.setAttribute('aria-disabled', isDisabled ? 'true' : 'false');
    openCloudsaveManagerBtn.classList.toggle('button-disabled', isDisabled);
}

async function refreshCloudsaveManagerEntryAvailability(openCloudsaveManagerBtn) {
    if (!openCloudsaveManagerBtn || typeof fetch !== 'function') return;

    try {
        const response = await fetch('/api/cloudsave/summary', { cache: 'no-store' });
        if (!response.ok) {
            setCloudsaveManagerEntryDisabled(openCloudsaveManagerBtn, false);
            return;
        }
        const summary = await response.json();
        const steamAutoCloud = summary && summary.steam_autocloud && typeof summary.steam_autocloud === 'object'
            ? summary.steam_autocloud
            : {};
        const disabled = summary.provider_available === false || steamAutoCloud.disabled === true;
        setCloudsaveManagerEntryDisabled(openCloudsaveManagerBtn, disabled);
    } catch (error) {
        console.warn('刷新云存档入口状态失败:', error);
        setCloudsaveManagerEntryDisabled(openCloudsaveManagerBtn, false);
    }
}

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
    const openCloudsaveManagerBtn = document.getElementById('open-cloudsave-manager-btn');
    if (!openCloudsaveManagerBtn) {
        return;
    }
    if (openCloudsaveManagerBtn.disabled) {
        return;
    }

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
            if (typeof window.requestOpenedWindowRestore === 'function') {
                window.requestOpenedWindowRestore(existingWindow);
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

// 检查当前模型是否为默认模型（yui-origin）
function isDefaultModel() {
    // 使用保存的角色卡模型名称
    const currentModel = window.currentCharacterCardModel || '';
    return isStaticDefaultLive2DModel(currentModel, window._currentCardRawData || {});
}

function getLive2DModelInfo(modelName) {
    if (!modelName) {
        return null;
    }
    const allModels = Array.isArray(window.allModels) ? window.allModels : [];
    const matches = allModels.filter(model => model && model.name === modelName);
    return matches.length === 1 ? matches[0] : null;
}

function hasStaticModelFlag(metadata) {
    if (!metadata || typeof metadata !== 'object') {
        return false;
    }
    return metadata.source === 'static'
        || metadata.isStatic === true
        || metadata.is_static === true
        || metadata.isDefault === true
        || metadata.is_default === true;
}

function isLegacyDefaultLive2DModel(modelName) {
    return modelName === 'yui_default' || modelName === 'yui-default';
}

function isStaticDefaultLive2DModel(modelName, rawData = {}) {
    if (isLegacyDefaultLive2DModel(modelName)) {
        return true;
    }

    if (modelName !== 'yui-origin') {
        return false;
    }

    if (window.currentCharacterCardModel === modelName && window.currentCharacterCardModelSource) {
        return window.currentCharacterCardModelSource === 'static';
    }

    const modelInfo = getLive2DModelInfo(modelName);
    if (hasStaticModelFlag(modelInfo) || hasStaticModelFlag(modelInfo && modelInfo.modelMetadata)) {
        return true;
    }

    const rawModel = rawData && typeof rawData.model === 'object' ? rawData.model : null;
    return hasStaticModelFlag(rawData && rawData.modelMetadata)
        || hasStaticModelFlag(rawData && rawData._reserved && rawData._reserved.modelMetadata)
        || hasStaticModelFlag(rawModel);
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

function lockWorkshopTabLayoutForSwitch() {
    const tabContents = document.querySelector('.tab-contents');
    const scrollContainer = document.querySelector('.layout-container');
    if (!tabContents) return () => {};

    const previousMinHeight = tabContents.style.minHeight;
    const currentHeight = Math.ceil(tabContents.getBoundingClientRect().height);
    const scrollTop = scrollContainer ? scrollContainer.scrollTop : window.scrollY;

    if (currentHeight > 0) {
        tabContents.style.minHeight = currentHeight + 'px';
    }

    return () => {
        const restoreScroll = () => {
            if (scrollContainer) {
                scrollContainer.scrollTop = scrollTop;
            } else {
                window.scrollTo(window.scrollX, scrollTop);
            }
        };

        restoreScroll();
        requestAnimationFrame(() => {
            restoreScroll();
            requestAnimationFrame(() => {
                tabContents.style.minHeight = previousMinHeight;
                restoreScroll();
            });
        });
    };
}

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
    const unlockTabLayout = lockWorkshopTabLayoutForSwitch();

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

    // 找到当前激活视图。切换时不叠放、不位移，避免两个面板短暂覆盖或抖动。
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
        unlockTabLayout();
        _viewSwitching = false;
    };

    if (leavingTab && leavingTab !== selectedTab) {
        leavingTab.classList.remove('active', 'tab-leaving', 'tab-entering');
        leavingTab.style.display = '';
        selectedTab.classList.remove('tab-leaving', 'tab-entering');
        selectedTab.classList.add('active');
        if (window.updatePageTexts) window.updatePageTexts();
        finalize();
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
    const controls = ensureCharacterCardTagScrollControls();
    if (!controls) return;

    const { wrapper, leftButton, rightButton } = controls;

    const hasOverflow = (wrapper.scrollWidth - wrapper.clientWidth) > 2;
    const atStart = wrapper.scrollLeft <= 2;
    const atEnd = (wrapper.scrollLeft + wrapper.clientWidth) >= (wrapper.scrollWidth - 2);

    leftButton.classList.toggle('is-hidden', !hasOverflow);
    rightButton.classList.toggle('is-hidden', !hasOverflow);
    leftButton.disabled = !hasOverflow || atStart;
    rightButton.disabled = !hasOverflow || atEnd;
}

function createCharacterCardTagScrollButton(direction) {
    const isLeft = direction < 0;
    const button = document.createElement('button');
    const labelKey = isLeft ? 'steam.scrollTagsLeftAriaLabel' : 'steam.scrollTagsRightAriaLabel';
    const fallbackLabel = isLeft ? '向左滚动标签' : '向右滚动标签';

    button.type = 'button';
    button.id = isLeft ? 'character-card-tags-scroll-left' : 'character-card-tags-scroll-right';
    button.className = 'tag-scroll-button is-hidden';
    button.textContent = isLeft ? '<' : '>';
    button.setAttribute('data-i18n-title', labelKey);
    button.setAttribute('data-i18n-aria', labelKey);
    button.setAttribute('title', window.t ? window.t(labelKey) : fallbackLabel);
    button.setAttribute('aria-label', window.t ? window.t(labelKey) : fallbackLabel);
    button.addEventListener('click', () => {
        scrollCharacterCardTags(isLeft ? -1 : 1);
    });

    return button;
}

function ensureCharacterCardTagScrollControls() {
    const wrapper = document.getElementById('character-card-tags-wrapper');
    if (!wrapper) return null;

    let shell = wrapper.parentElement && wrapper.parentElement.classList.contains('character-card-tags-scroll-shell')
        ? wrapper.parentElement
        : null;

    if (!shell && wrapper.parentNode) {
        shell = document.createElement('div');
        shell.className = 'character-card-tags-scroll-shell';
        wrapper.parentNode.insertBefore(shell, wrapper);
        shell.appendChild(createCharacterCardTagScrollButton(-1));
        shell.appendChild(wrapper);
        shell.appendChild(createCharacterCardTagScrollButton(1));
    }

    if (!shell) return null;

    let leftButton = shell.querySelector('#character-card-tags-scroll-left');
    if (!leftButton) {
        leftButton = createCharacterCardTagScrollButton(-1);
        shell.insertBefore(leftButton, shell.firstChild || null);
    }

    let rightButton = shell.querySelector('#character-card-tags-scroll-right');
    if (!rightButton) {
        rightButton = createCharacterCardTagScrollButton(1);
        shell.appendChild(rightButton);
    }

    if (wrapper.dataset.scrollControlsBound !== 'true') {
        wrapper.addEventListener('scroll', updateCharacterCardTagScrollControls, { passive: true });

        if (typeof ResizeObserver !== 'undefined') {
            const tagsContainer = document.getElementById('character-card-tags-container');
            const tagsResizeObserver = new ResizeObserver(() => {
                updateCharacterCardTagScrollControls();
            });
            tagsResizeObserver.observe(wrapper);
            if (tagsContainer) {
                tagsResizeObserver.observe(tagsContainer);
            }
            wrapper._tagScrollResizeObserver = tagsResizeObserver;
        }

        wrapper.dataset.scrollControlsBound = 'true';
    }

    return { wrapper, leftButton, rightButton };
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
        requestAnimationFrame(updateCharacterCardTagScrollControls);
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
        requestAnimationFrame(updateCharacterCardTagScrollControls);
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

async function resetWorkshopVoiceReferenceFields(defaultTitle = '') {
    const displayNameInput = document.getElementById('voice-reference-display-name');
    const prefixInput = document.getElementById('voice-reference-prefix');
    const languageSelect = document.getElementById('voice-reference-language');
    const providerSelect = document.getElementById('voice-reference-provider-hint');

    clearReferenceAudioSelection();
    if (displayNameInput) displayNameInput.value = defaultTitle || '';
    if (prefixInput) prefixInput.value = sanitizeWorkshopVoicePrefix(defaultTitle, 'voice');
    if (languageSelect) languageSelect.value = 'ch';
    if (providerSelect) {
        providerSelect.value = 'cosyvoice';
        await applyWorkshopVoiceProviderRestrictions(providerSelect);
    }
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
    await applyWorkshopVoiceProviderRestrictions(providerSelect);
    formData.append('provider_hint', providerSelect?.value || getFirstAvailableWorkshopVoiceProviderValue(providerSelect) || 'cosyvoice');

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
        .then(async data => {
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
                await applyWorkshopVoiceProviderRestrictions(document.getElementById('voice-reference-provider-hint'));

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
                        <button class="button button-primary" data-item-id="${formattedItem.id}" data-item-name="${formattedItem.name}" onclick="addWorkshopCharacterCardFromSubscription(this)" style="margin-bottom: 8px;">${window.t ? window.t('steam.workshopAddCharacterCard') : '加入角色卡'}</button>
                        <button class="button button-danger" data-item-id="${formattedItem.id}" data-item-name="${formattedItem.name}" onclick="unsubscribeItem(this.dataset.itemId, this.dataset.itemName)">${window.t ? window.t('steam.unsubscribe') : '取消订阅'}</button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function formatWorkshopCharacterNameList(names) {
    const list = Array.isArray(names)
        ? names.map(name => String(name || '').trim()).filter(Boolean)
        : [];
    return list.length > 0 ? list.join('、') : (window.t ? window.t('steam.unknownCharacterCard') : '未知角色卡');
}

async function showWorkshopCharacterAddAlert(message, type = 'info') {
    if (typeof showAlertDialog === 'function') {
        const title = type === 'info'
            ? (window.t ? window.t('steam.characterCardAlreadyExistsTitle') : '角色卡已存在')
            : (window.t ? window.t('common.warning') : '提示');
        await showAlertDialog(message, {
            type,
            title,
        });
        return;
    }
    window.alert(message);
}

async function addWorkshopCharacterCardFromSubscription(button) {
    const itemId = button?.dataset?.itemId || '';
    if (!itemId) return;

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = window.t ? window.t('steam.workshopAddingCharacterCard') : '正在加入...';

    try {
        const response = await fetch(`/api/steam/workshop/sync-character/${encodeURIComponent(itemId)}`, {
            method: 'POST',
        });
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            data = {};
        }

        if (data.code === 'WORKSHOP_CHARACTER_ALREADY_EXISTS') {
            const namesText = formatWorkshopCharacterNameList(data.existing_character_names);
            const message = window.t
                ? window.t('steam.characterCardAlreadyExistsMessage', { names: namesText })
                : `角色卡已存在：${namesText}`;
            await showWorkshopCharacterAddAlert(message, 'info');
            return;
        }

        if (!response.ok || !data.success) {
            const fallbackError = data.error || data.message || (window.t ? window.t('common.unknownError') : 'Unknown error');
            const key = data.code === 'WORKSHOP_CHARACTER_NOT_FOUND'
                ? 'steam.workshopCharacterNotFound'
                : 'steam.workshopCharacterAddFailed';
            const message = window.t
                ? window.t(key, { error: fallbackError })
                : (data.code === 'WORKSHOP_CHARACTER_NOT_FOUND'
                    ? '此订阅内容中未找到可加入的角色卡，请确认内容已下载完成。'
                    : `加入角色卡失败: ${fallbackError}`);
            await showWorkshopCharacterAddAlert(message, 'warning');
            return;
        }

        const namesText = formatWorkshopCharacterNameList(data.added_character_names);
        const successMessage = window.t
            ? window.t('steam.workshopCharacterAdded', { names: namesText })
            : `已加入角色卡：${namesText}`;
        showMessage(successMessage, 'success');
        try {
            await loadCharacterCards();
        } catch (refreshError) {
            console.warn('刷新角色卡列表失败:', refreshError);
            const refreshMessage = window.t
                ? window.t('steam.characterCardsRefreshFailed', { error: refreshError.message })
                : `刷新列表失败: ${refreshError.message}`;
            showMessage(refreshMessage, 'warning');
        }
    } catch (error) {
        const message = window.t
            ? window.t('steam.workshopCharacterAddFailed', { error: error.message })
            : `加入角色卡失败: ${error.message}`;
        showMessage(message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
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
                showMessage(window.t ? window.t('steam.unsubscribeFailed', { error: errorMsg }) : `取消订阅失败: ${errorMsg}`, 'error');
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
                showMessage(window.t ? window.t('steam.unsubscribeFailed', { error: errorMsg }) : `取消订阅失败: ${errorMsg}`, 'error');
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
                    const backfilledFaces = Number(syncResult.backfilled_faces || 0);
                    if (syncResult.added > 0 || backfilledFaces > 0) {
                        console.log(`[工坊同步] 服务端同步完成：新增 ${syncResult.added} 个角色卡，回填 ${backfilledFaces} 个封面，跳过 ${syncResult.skipped} 个已存在`);
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

            const fieldOrder = [];
            // 工坊导入要保留 live2d/model_type/vrm 等模型字段（仅靠 skipKeys 过滤工坊元数据），
            // 不能套用渲染路径对系统保留名的剔除，否则导入卡会丢失模型绑定、开成错误或缺失的模型。
            getOrderedCharacterFieldKeys(charaData, skipKeys, { skipReservedNames: false }).forEach(key => {
                const value = charaData[key];
                if (value !== undefined && value !== null && value !== '') {
                    catgirlFormat[key] = value;
                    fieldOrder.push(key);
                }
            });
            attachCharacterFieldOrderPayload(catgirlFormat, fieldOrder);

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
        const resp = await fetch('/api/characters', { cache: 'no-store' });
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
        }
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

const CHARACTER_CARD_MODEL_SCAN_RENDER_BUDGET_MS = 2500;
let characterCardLoadSequence = 0;

function getCharacterCardDescriptionFromData(data) {
    if (!data || typeof data !== 'object') {
        return window.t ? window.t('steam.noDescription') : '暂无描述';
    }
    if (data['description']) return data['description'];
    if (data['描述']) return data['描述'];
    if (data['角色卡描述']) return data['角色卡描述'];
    return window.t ? window.t('steam.noDescription') : '暂无描述';
}

function getCharacterCardTagsFromData(data) {
    if (!data || typeof data !== 'object') {
        return [];
    }
    return Array.isArray(data['关键词']) ? data['关键词'] : [];
}

function buildCharacterCardEntry(name, data, id) {
    return {
        id: id,
        name: name,
        description: getCharacterCardDescriptionFromData(data),
        tags: getCharacterCardTagsFromData(data),
        rawData: data || {},
        originalName: name
    };
}

function findCharacterCardIndexByName(name) {
    const cards = Array.isArray(window.characterCards) ? window.characterCards : [];
    return cards.findIndex(card => String(card?.originalName || card?.name || '') === String(name));
}

function getNextCharacterCardId() {
    const cards = Array.isArray(window.characterCards) ? window.characterCards : [];
    let maxId = 0;
    cards.forEach(card => {
        const numericId = Number(card && card.id);
        if (Number.isFinite(numericId)) {
            maxId = Math.max(maxId, numericId);
        }
    });
    return maxId + 1;
}

function buildLocalCatgirlRawData(catgirlName, submittedData, fieldOrder) {
    const cards = Array.isArray(window.characterCards) ? window.characterCards : [];
    const existingIdx = findCharacterCardIndexByName(catgirlName);
    const previousRawData = existingIdx >= 0 && cards[existingIdx]?.rawData && typeof cards[existingIdx].rawData === 'object'
        ? cards[existingIdx].rawData
        : {};
    const allReservedFields = ['档案名', ...getWorkshopHiddenFields()];
    const nextRawData = {};

    // 通用编辑接口会保留系统字段，但会用本次提交的普通字段整体替换旧普通字段。
    Object.keys(previousRawData).forEach(key => {
        if (allReservedFields.includes(key)) {
            nextRawData[key] = previousRawData[key];
        }
    });
    Object.entries(submittedData || {}).forEach(([key, value]) => {
        if (!key || key === '档案名' || allReservedFields.includes(key)) {
            return;
        }
        if (value !== null && value !== undefined && String(value).trim() !== '') {
            nextRawData[key] = value;
        }
    });
    if (Array.isArray(fieldOrder)) {
        setLocalRawDataFieldOrder(nextRawData, fieldOrder);
    }
    return nextRawData;
}

function mergeFreshCatgirlRawDataWithLocal(freshRawData, localRawData) {
    const allReservedFields = ['档案名', ...getWorkshopHiddenFields()];
    const merged = {};

    // 本轮刚保存的普通字段优先；重新拉取的数据只用于补回模型、音色等保留字段。
    Object.entries(localRawData || {}).forEach(([key, value]) => {
        if (!allReservedFields.includes(key)) {
            merged[key] = value;
        }
    });
    Object.entries(localRawData || {}).forEach(([key, value]) => {
        if (allReservedFields.includes(key)) {
            merged[key] = value;
        }
    });
    Object.entries(freshRawData || {}).forEach(([key, value]) => {
        if (allReservedFields.includes(key)) {
            merged[key] = value;
        }
    });
    return merged;
}

function applyLocalVoiceIdToRawData(rawData, voiceId) {
    if (!rawData || typeof rawData !== 'object') return rawData;
    const normalizedVoiceId = (voiceId == null ? '' : String(voiceId)).trim();
    rawData.voice_id = normalizedVoiceId;
    if (rawData.voice && typeof rawData.voice === 'object') {
        rawData.voice.voice_id = normalizedVoiceId;
    }
    return rawData;
}

function syncCharacterCardCache(catgirlName, rawData, options = {}) {
    if (!catgirlName) return;
    if (!Array.isArray(window.characterCards)) {
        window.characterCards = [];
    }

    const existingIdx = findCharacterCardIndexByName(catgirlName);
    const existingCard = existingIdx >= 0 ? window.characterCards[existingIdx] : null;
    const cardId = existingCard?.id ?? getNextCharacterCardId();
    const updatedCard = buildCharacterCardEntry(catgirlName, rawData || {}, cardId);

    if (existingIdx >= 0) {
        window.characterCards[existingIdx] = updatedCard;
    } else {
        window.characterCards.push(updatedCard);
    }
    globalCharacterCards = window.characterCards || [];

    refreshCharacterCardSelectOptions();
    if (options.render !== false) {
        renderCharaCardsView();
    }
}

function waitForCharacterCardModelScanBudget(scanPromise) {
    const eventual = Promise.resolve(scanPromise)
        .then(scanCompleted => scanCompleted === true)
        .catch(error => {
            console.warn('角色卡模型扫描失败，先渲染角色列表:', error);
            return false;
        });

    return new Promise(resolve => {
        let settled = false;
        const finish = inTime => {
            if (settled) return;
            settled = true;
            resolve({ inTime, eventual });
        };

        window.setTimeout(() => finish(false), CHARACTER_CARD_MODEL_SCAN_RENDER_BUDGET_MS);
        eventual.then(scanCompleted => finish(scanCompleted === true));
    });
}

async function collectCharacterSettingsCardsFromModels(idCounter, loadSequence) {
    let nextId = idCounter;
    const newCards = [];
    for (const model of availableModels) {
        // 每个模型外层 fetch 前先校验序列号；旧轮被新一轮 loadCharacterCards 抢占后立刻早退，
        // 避免在大目录下继续打 model_files / *.chara.json 的废请求拖慢最新一轮 I/O
        if (loadSequence !== undefined && loadSequence !== characterCardLoadSequence) {
            return { cards: newCards, nextId };
        }
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
                        if (loadSequence !== undefined && loadSequence !== characterCardLoadSequence) {
                            return { cards: newCards, nextId };
                        }
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
                                    newCards.push({
                                        id: nextId++,
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
    return { cards: newCards, nextId };
}

function mergeCharacterSettingsCardsFromModels(loadSequence, discovered) {
    const cards = discovered?.cards || [];
    if (loadSequence !== characterCardLoadSequence || cards.length === 0) {
        return;
    }
    window.characterCards = (window.characterCards || []).concat(cards);
    globalCharacterCards = window.characterCards || [];
    refreshCharacterCardSelectOptions();
    // 主列表视图也要同步刷新，否则晚到的旧格式兼容卡得等下次整页刷新才会出现
    renderCharaCardsView();
}

function refreshExpandedCardAfterScan(loadSequence) {
    if (loadSequence !== characterCardLoadSequence) return;
    if (!currentCharacterCardId) return;
    const card = (window.characterCards || []).find(c => String(c.id) === String(currentCharacterCardId));
    if (card) {
        // availableModels 在扫描完成后才落地，重跑 expand 让上传/预览按钮基于最新模型列表渲染
        expandCharacterCardSection(card);
    }
}

function refreshCharacterCardSelectOptions() {
    const characterCardSelect = document.getElementById('character-card-select');

    if (!characterCardSelect) {
        return;
    }

    // 保留当前选中值，重建后再恢复，避免异步补卡时把用户已选项清掉
    const previousValue = characterCardSelect.value;

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

        if (previousValue && Array.from(characterCardSelect.options).some(option => option.value === previousValue)) {
            characterCardSelect.value = previousValue;
        }
    }
}

// 加载角色卡列表
async function loadCharacterCards() {
    const loadSequence = ++characterCardLoadSequence;

    // 新一轮加载先失效上一轮的模型扫描缓存：scanModels 现在 fire-and-forget，
    // 若新一轮扫描卡住/失败，本应基于过期清单判断上传可用性会出现假阳性。
    // 清空后扫描完成前 UI 会显示"无可用模型"，是诚实的 loading 信号；
    // refreshExpandedCardAfterScan 在扫描完成后会按当前展开卡重渲染恢复正常状态。
    availableModels = [];
    availableVrmModels = [];
    availableMmdModels = [];
    window.allModels = [];
    window.allVrmModels = [];
    window.allMmdModels = [];

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

    // 模型扫描可能受 Linux 新存储根、创意工坊目录或 Steam 状态影响变慢。
    // 角色列表不应被模型扫描阻塞；扫描完成后再用于预览/上传等增强能力。
    const modelScanPromise = scanModels(loadSequence);

    // 转换角色数据为角色卡格式（定义为全局变量，供其他函数使用）
    window.characterCards = [];
    let idCounter = 1;

    // 只处理猫娘数据，忽略其他角色类型（包括主人）
    const catgirls = characterData['猫娘'] || {};
    for (const [name, data] of Object.entries(catgirls)) {
        window.characterCards.push(buildCharacterCardEntry(name, data, idCounter++));
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

    // 扫描模型文件夹中的 character_settings JSON 文件仅用于旧格式兼容，不能阻塞角色管理主列表。
    const characterSettingsStartId = idCounter;

    // 渲染角色卡列表（改为下拉选单）
    refreshCharacterCardSelectOptions();

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

    // 同步加载我的档案和已隐藏猫娘列表
    loadMasterProfile();
    renderHiddenCatgirls();

    waitForCharacterCardModelScanBudget(modelScanPromise)
        .then(scanBudget => {
            const appendAfterScan = () => collectCharacterSettingsCardsFromModels(characterSettingsStartId, loadSequence)
                .then(discovered => mergeCharacterSettingsCardsFromModels(loadSequence, discovered));

            scanBudget.eventual.then(scanCompleted => {
                if (scanCompleted) {
                    // 扫描成功后回补当前展开角色卡的上传/预览状态，避免用户先点开卡片时停留在旧/空 availableModels
                    refreshExpandedCardAfterScan(loadSequence);
                }
                if (scanBudget.inTime || !scanCompleted) {
                    return null;
                }
                return appendAfterScan();
            }).catch(error => {
                console.warn('角色卡旧格式兼容延迟扫描失败，已保留主列表:', error);
            });

            if (!scanBudget.inTime) {
                return null;
            }
            return appendAfterScan();
        })
        .catch(error => {
            console.warn('角色卡旧格式兼容扫描失败，已保留主列表:', error);
        });
}

// ===== 角色卡 卡片/列表 视图 =====

// 已设置卡面的猫娘名集合（避免无卡面的 404 控制台噪声）
window._cardFaceNames = window._cardFaceNames || new Set();
const CHARACTER_MANAGER_CARD_MAKER_WINDOW_NAME = 'neko_card_maker';
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

function openManagedPopup(url, windowName, features) {
    window._openWindows = window._openWindows || {};
    const existingWindow = window._openWindows[windowName];
    if (existingWindow && !existingWindow.closed) {
        const replacementName = `${windowName}_${Date.now()}_${Math.random().toString(36).slice(2)}`;
        const replacementWindow = window.open(url, replacementName, features);
        if (replacementWindow) {
            try { existingWindow.close(); } catch (_) {}
            try {
                // 随机名只用于绕开旧窗口复用；新窗口接管后恢复固定名称，方便其他上下文继续定位。
                replacementWindow.name = windowName;
            } catch (error) {
                console.warn('更新弹窗名称失败:', error);
            }
            window._openWindows[windowName] = replacementWindow;
            try { replacementWindow.focus(); } catch (_) {}
            return replacementWindow;
        }

        try {
            // 新窗口被拦截时才复用旧窗口，仍然保证内容跟随最后一次打开。
            existingWindow.location.href = new URL(url, window.location.origin).toString();
        } catch (error) {
            console.warn('更新弹窗地址失败:', error);
        }
        if (typeof window.requestOpenedWindowRestore === 'function') {
            window.requestOpenedWindowRestore(existingWindow);
        }
        existingWindow.focus();
        return existingWindow;
    }
    delete window._openWindows[windowName];

    const popup = window.open(url, windowName, features);
    if (popup) {
        window._openWindows[windowName] = popup;
        try { popup.focus(); } catch (_) {}
    }
    return popup;
}
window.openManagedPopup = openManagedPopup;

function refreshOpenCardMetaBlock(name) {
    const panelWrapper = document.getElementById('catgirl-panel-wrapper');
    if (!panelWrapper || !name) return;
    const formName = panelWrapper.querySelector('form [name="档案名"]')?.value;
    if (formName !== name) return;
    const metaBlock = panelWrapper.querySelector('#card-meta-block');
    if (metaBlock && typeof renderCardMetaBlock === 'function') {
        renderCardMetaBlock(metaBlock, name, false);
    }
}

function updateCardMetaAfterFaceChange(name, timestamp) {
    if (!name) return;
    window._cardMetas = window._cardMetas || {};
    const existing = window._cardMetas[name] || {};
    const updatedAt = new Date(timestamp || Date.now()).toISOString();
    window._cardMetas[name] = {
        author: existing.author || '',
        origin: 'self',
        created_at: existing.created_at || updatedAt,
        updated_at: updatedAt
    };
    refreshOpenCardMetaBlock(name);
}

function applyCardFaceUpdated(name, timestamp) {
    if (!name) return;
    const ts = timestamp || Date.now();
    const newSrc = `/api/characters/catgirl/${encodeURIComponent(name)}/card-face?t=${ts}`;
    if (window._cardFaceNames) window._cardFaceNames.add(name);
    updateCardMetaAfterFaceChange(name, ts);

    const panelWrapper = document.getElementById('catgirl-panel-wrapper');
    if (panelWrapper) {
        const formName = panelWrapper.querySelector('form [name="档案名"]')?.value;
        if (formName === name) {
            const cardImage = panelWrapper.querySelector('.catgirl-panel-card-image');
            const placeholder = cardImage?.querySelector('.card-avatar-placeholder');
            if (cardImage) {
                let panelImg = cardImage.querySelector('.card-face-img');
                if (!panelImg) {
                    panelImg = document.createElement('img');
                    panelImg.className = 'card-face-img';
                    panelImg.alt = '角色卡面';
                    cardImage.insertBefore(panelImg, placeholder || cardImage.firstChild);
                }
                panelImg.onload = () => {
                    if (placeholder) placeholder.style.display = 'none';
                };
                panelImg.onerror = () => {
                    if (placeholder) placeholder.style.display = '';
                };
                panelImg.src = newSrc;
            }
        }
    }

    document.querySelectorAll('.chara-card-item').forEach(cardItem => {
        const cardName = cardItem.querySelector('.card-name');
        if (!cardName || cardName.textContent !== name) return;
        const gridAvatar = cardItem.querySelector('.card-avatar');
        if (!gridAvatar) return;
        let gridImg = gridAvatar.querySelector('.card-face-img');
        const gridPlaceholder = gridAvatar.querySelector('.card-avatar-placeholder');
        if (!gridImg) {
            gridImg = document.createElement('img');
            gridImg.className = 'card-face-img';
            gridImg.alt = name;
            if (gridPlaceholder) {
                gridAvatar.insertBefore(gridImg, gridPlaceholder);
            } else {
                gridAvatar.appendChild(gridImg);
            }
        }
        gridImg.onload = () => {
            if (gridPlaceholder) gridPlaceholder.style.display = 'none';
        };
        gridImg.onerror = () => {
            if (gridPlaceholder) gridPlaceholder.style.display = '';
        };
        gridImg.src = newSrc;
    });
}

function handleExternalCardFaceUpdated(data) {
    if (!data || data.type !== 'card-face-updated') return;
    applyCardFaceUpdated(data.name, data.timestamp);
}

(function initCardFaceUpdateEvents() {
    window.addEventListener('message', event => {
        if (event.origin !== window.location.origin) return;
        handleExternalCardFaceUpdated(event.data);
    });
    if (typeof BroadcastChannel === 'function') {
        try {
            const channel = new BroadcastChannel('neko-card-face-events');
            channel.onmessage = event => {
                if (event.origin !== window.location.origin) return;
                handleExternalCardFaceUpdated(event.data);
            };
        } catch (_) {}
    }
    window.addEventListener('storage', event => {
        if (event.key !== 'neko_card_face_event' || !event.newValue) return;
        try {
            handleExternalCardFaceUpdated(JSON.parse(event.newValue));
        } catch (_) {}
    });
})();

async function openModelManagerForCharacterForm(form, fallbackName) {
    let catgirlName = getProfileNameFromCharacterForm(form, fallbackName);
    if (!catgirlName) {
        await showProfileNameRequiredDialog();
        return;
    }
    const nameInput = form?.querySelector?.('[name="档案名"]');
    const shouldCreateCharacter = form && form._isNew && (!form._autoCreated || form._autoCreatedName !== catgirlName);
    if (shouldCreateCharacter) {
        if (form._autoCreated && form._autoCreatedName !== catgirlName) {
            form._autoCreatedDetachedName = form._autoCreatedName;
            form._autoCreated = false;
            form._autoCreatedName = '';
        }
        if (!(await ensureValidCharacterProfileName(catgirlName, nameInput))) {
            return;
        }
    } else if (!(await ensureSafeExistingCharacterPathName(catgirlName, nameInput))) {
        return;
    }

    if (shouldCreateCharacter) {
        try {
            const tmpResp = await fetch('/api/characters/catgirl', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ '档案名': catgirlName })
            });
            if (tmpResp.ok) {
                const tmpResult = await tmpResp.json().catch(() => ({}));
                const createdName = tmpResult.character_name || catgirlName;
                const nameInput = form.querySelector?.('[name="档案名"]');
                if (nameInput) nameInput.value = createdName;
                catgirlName = createdName;
                form._autoCreated = true;
                form._autoCreatedName = createdName;
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
    const existingWindow = window._openSettingsWindows[url];
    if (existingWindow && !existingWindow.closed) {
        if (form && form._autoCreated) form._autoCreatedDependentPopup = existingWindow;
        existingWindow.focus();
        return;
    }
    delete window._openSettingsWindows[url];

    const popup = window.open(url, '_blank',
        'toolbar=no,location=no,status=no,menubar=no,scrollbars=yes,resizable=yes,width=' + screen.availWidth + ',height=' + screen.availHeight + ',top=0,left=0');
    if (!popup) {
        if (typeof showAlert === 'function') await showAlert(window.t ? window.t('character.allowPopups') : '请允许弹窗！');
        // 弹窗被拦截：回滚本次及此前重命名遗留的 detached 临时角色，避免用户直接刷新/关页时残留空记录
        if (form && (form._autoCreated || form._autoCreatedDetachedName)) {
            await rollbackAutoCreatedCatgirl(form);
        }
        return;
    }

    window._openSettingsWindows[url] = popup;
    if (form && form._autoCreated) form._autoCreatedDependentPopup = popup;
    popup.moveTo(0, 0);
    popup.resizeTo(screen.availWidth, screen.availHeight);
    const timer = setInterval(() => {
        if (!popup.closed) {
            if (form && popup._modelManagerHasSaved) form._autoCreatedDependentPopupSaved = true;
            return;
        }
        clearInterval(timer);
        if (window._openSettingsWindows[url] === popup) delete window._openSettingsWindows[url];
        if (form && popup._modelManagerHasSaved) form._autoCreatedDependentPopupSaved = true;
        if (form && form._autoCreatedDependentPopup === popup) form._autoCreatedDependentPopup = null;
        if (form && form._autoCreatedRollbackWhenDependentCloses && !form._autoCreatedDependentPopupSaved) {
            rollbackAutoCreatedCatgirl(form).catch(e => console.warn('[角色面板] 延迟回滚临时角色失败:', e));
        }
        if (typeof loadCharacterCards === 'function') {
            loadCharacterCards().catch(e => console.warn('刷新角色列表失败:', e));
        }
    }, 500);
}

function getProfileNameFromCharacterForm(form, fallbackName) {
    return String(form?.querySelector?.('[name="档案名"]')?.value || fallbackName || '').trim();
}

const CHARACTER_PROFILE_RESERVED_ROUTE_NAMES = new Set([
    'l2d',
    'model_manager',
    'live2d_parameter_editor',
    'live2d_emotion_manager',
    'vrm_emotion_manager',
    'mmd_emotion_manager',
    'voice_clone',
    'api_key',
    'character_card_manager',
    'cloudsave_manager',
    'memory_browser',
    'cookies_login',
    'chat',
    'subtitle',
    'agenthud',
    'toast',
    'card_maker',
    'soccer_demo',
    'badminton_demo',
    'jukebox',
    'static',
    'user_live2d',
    'user_live2d_local',
    'user_vrm',
    'user_mmd',
    'user_mods',
    'workshop',
    'api',
    'ws',
    'health',
]);
const CHARACTER_PROFILE_RESERVED_DEVICE_RE = /^(con|prn|aux|nul|clock\$|com[1-9]|lpt[1-9])$/i;
const CHARACTER_PROFILE_ALLOWED_PUNCTUATION = new Set([' ', '_', '-', '(', ')', '（', '）', '·', '・', "'", '’']);
const CHARACTER_PROFILE_NAME_MAX_UNITS = 60;

function countCharacterProfileNameUnits(name) {
    return Array.from(String(name || '')).reduce((total, ch) => total + (ch.codePointAt(0) <= 0x7F ? 1 : 2), 0);
}

function getCharacterProfileNameError(name) {
    const value = String(name || '').trim();
    if (!value) return window.t ? window.t('character.profileNameRequired') : '档案名为必填项';
    if (value.includes('/') || value.includes('\\')) return window.t ? window.t('character.profileNameContainsSlash') : '档案名不能包含路径分隔符(/或\\)';
    if (value.includes('..')) return window.t ? window.t('character.profileNameDotSequence') : '档案名不能包含连续点号(..)';
    if (value === '.' || value.endsWith('.')) return window.t ? window.t('character.profileNameUnsafeDot') : '档案名不能仅由点号组成或以点号结尾';
    if (value.includes('.')) return window.t ? window.t('character.profileNameContainsDot') : '档案名不能包含点号(.)';
    if (CHARACTER_PROFILE_RESERVED_DEVICE_RE.test(value.split('.', 1)[0])) return window.t ? window.t('character.profileNameReservedDevice') : '档案名不能使用 Windows 保留设备名';
    if (CHARACTER_PROFILE_RESERVED_ROUTE_NAMES.has(value)) return window.t ? window.t('character.profileNameReservedRoute') : '此名称是系统保留的路由名称，不能用作档案名';
    for (const ch of value) {
        if (/[\u0000-\u001F\u007F]/.test(ch)) return window.t ? window.t('character.profileNameInvalidChars') : '档案名只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号';
        if (/[\p{L}\p{N}]/u.test(ch) || CHARACTER_PROFILE_ALLOWED_PUNCTUATION.has(ch)) continue;
        return window.t ? window.t('character.profileNameInvalidChars') : '档案名只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号';
    }
    if (countCharacterProfileNameUnits(value) > CHARACTER_PROFILE_NAME_MAX_UNITS) return window.t ? window.t('character.profileNameTooLong') : '档案名过长';
    return '';
}

async function showCharacterProfileNameInvalidDialog(message) {
    const text = message || (window.t ? window.t('character.profileNameInvalid') : '档案名无效');
    showMessage(text, 'warning', 6000);
    if (typeof showAlertDialog === 'function') {
        await showAlertDialog(text, { type: 'warning' });
    }
}

async function ensureValidCharacterProfileName(name, input) {
    const error = getCharacterProfileNameError(name);
    if (!error) return true;
    if (input && typeof input.focus === 'function') input.focus();
    await showCharacterProfileNameInvalidDialog(error);
    return false;
}

function getExistingCharacterPathNameError(name) {
    const value = String(name || '').trim();
    if (!isUnsafeCharacterPathSegment(value)) return '';
    if (!value) return window.t ? window.t('character.profileNameRequired') : '档案名为必填项';
    if (value.includes('/') || value.includes('\\')) return window.t ? window.t('character.profileNameContainsSlash') : '档案名不能包含路径分隔符(/或\\)';
    if (value.includes('..')) return window.t ? window.t('character.profileNameDotSequence') : '档案名不能包含连续点号(..)';
    if (value === '.' || value.endsWith('.')) return window.t ? window.t('character.profileNameUnsafeDot') : '档案名不能仅由点号组成或以点号结尾';
    return window.t ? window.t('character.profileNameInvalid') : '档案名无效';
}

async function ensureSafeExistingCharacterPathName(name, input) {
    const error = getExistingCharacterPathNameError(name);
    if (!error) return true;
    if (input && typeof input.focus === 'function') input.focus();
    await showCharacterProfileNameInvalidDialog(error);
    return false;
}

async function showProfileNameRequiredDialog(key = 'character.fillProfileNameFirst', fallback = '请先填写猫娘档案名，然后再设置模型') {
    const message = window.t ? window.t(key) : fallback;
    if (typeof showAlertDialog === 'function') {
        await showAlertDialog(message, { type: 'warning' });
        return;
    }
    showMessage(message, 'warning');
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
                    const errorMessage = e.message || String(e);
                    showMessage(window.t ? window.t('character.cardAuthorUpdateFailed', { error: errorMessage }) : '更新作者失败: ' + errorMessage, 'error');
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

// ===== API 设置窗口 =====
function buildApiKeySettingsWindowFeatures(width = 1240, height = 940) {
    const availableWidth = Math.max(1, Number(window.screen && (window.screen.availWidth || window.screen.width)) || width);
    const availableHeight = Math.max(1, Number(window.screen && (window.screen.availHeight || window.screen.height)) || height);
    const windowWidth = Math.min(width, Math.max(720, availableWidth - 80));
    const windowHeight = Math.min(height, Math.max(560, availableHeight - 80));
    // 居中走 core 公共 helper：多显示器下叠加当前屏幕偏移，避免副屏弹窗跳回主屏。
    if (typeof window.buildCenteredPopupFeatures === 'function') {
        return window.buildCenteredPopupFeatures(windowWidth, windowHeight);
    }
    const left = Math.max(0, Math.floor((availableWidth - windowWidth) / 2));
    const top = Math.max(0, Math.floor((availableHeight - windowHeight) / 2));
    return `width=${windowWidth},height=${windowHeight},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;
}

function openApiKeySettings() {
    const url = '/api_key';
    const windowName = 'neko_api_key';
    const features = buildApiKeySettingsWindowFeatures();
    let childWin = null;

    if (typeof window.openOrFocusWindow === 'function') {
        childWin = window.openOrFocusWindow(url, windowName, features);
    } else {
        childWin = window.open(url, windowName, features);
    }

    if (childWin && typeof childWin.focus === 'function') {
        try {
            childWin.focus();
        } catch (error) {
            // 部分浏览器环境不允许主动聚焦，忽略即可。
        }
    }
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
        container.style.opacity = '1';
        container.style.transform = 'none';
        renderCharaCardsView();
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

function _ensureCharaCardParticleCanvas() {
    if (charaCardParticleCanvas) return;
    charaCardParticleCanvas = document.createElement('canvas');
    charaCardParticleCanvas.id = 'chara-card-particle-canvas';
    charaCardParticleCanvas.className = 'chara-card-particle-canvas';
    charaCardParticleCanvas.setAttribute('aria-hidden', 'true');
    document.body.appendChild(charaCardParticleCanvas);
    charaCardParticleContext = charaCardParticleCanvas.getContext('2d');

    if (!charaCardParticleResizeBound) {
        charaCardParticleResizeHandler = function () {
            if (!charaCardParticleCanvas || !charaCardParticleContext) return;
            const dpr = window.devicePixelRatio || 1;
            charaCardParticleCanvas.width = Math.max(1, Math.floor(window.innerWidth * dpr));
            charaCardParticleCanvas.height = Math.max(1, Math.floor(window.innerHeight * dpr));
            charaCardParticleCanvas.style.width = `${window.innerWidth}px`;
            charaCardParticleCanvas.style.height = `${window.innerHeight}px`;
            charaCardParticleContext.setTransform(dpr, 0, 0, dpr, 0, 0);
        };
        window.addEventListener('resize', charaCardParticleResizeHandler);
        charaCardParticleResizeBound = true;
        charaCardParticleResizeHandler();
    }
}

function _teardownCharaCardParticleCanvas() {
    if (!charaCardParticleCanvas) return;
    if (charaCardParticleResizeBound && charaCardParticleResizeHandler) {
        window.removeEventListener('resize', charaCardParticleResizeHandler);
    }
    window.cancelAnimationFrame(charaCardParticleFrame);
    charaCardParticleFrame = 0;
    if (charaCardParticleContext) {
        charaCardParticleContext.clearRect(0, 0, window.innerWidth, window.innerHeight);
    }
    if (charaCardParticleCanvas.parentNode) {
        charaCardParticleCanvas.parentNode.removeChild(charaCardParticleCanvas);
    }
    charaCardParticleCanvas = null;
    charaCardParticleContext = null;
    charaCardParticles = [];
    charaCardParticleResizeBound = false;
    charaCardParticleResizeHandler = null;
}

function _randomBetween(min, max) {
    return min + Math.random() * (max - min);
}

function _createCharaCardParticle(x, y, color, delay) {
    const angle = _randomBetween(-Math.PI * 0.9, -Math.PI * 0.1);
    const speed = _randomBetween(1, 3.8);
    charaCardParticles.push({
        x,
        y,
        vx: Math.cos(angle) * speed + _randomBetween(-0.4, 0.4),
        vy: Math.sin(angle) * speed - _randomBetween(0.2, 1.2),
        rotation: _randomBetween(0, Math.PI),
        spin: _randomBetween(-0.18, 0.18),
        size: _randomBetween(2.8, 6.4),
        life: 0,
        maxLife: _randomBetween(42, 76),
        delay: delay || 0,
        color,
        alpha: 1,
    });
}

function _spawnCharaCardParticles(target) {
    const rect = target.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const palette = ['#40c5f1', '#7fd9ff', '#ffffff', '#ff9eb5', '#ff6f8b', '#e3f4ff'];
    const particleCount = Math.min(CHARA_CARD_DISSOLVE_PARTICLE_LIMIT, Math.max(18, Math.floor(rect.width * rect.height / 180)));

    for (let i = 0; i < particleCount; i++) {
        _createCharaCardParticle(
            _randomBetween(rect.left + rect.width * 0.07, rect.right - rect.width * 0.07),
            _randomBetween(rect.top + rect.height * 0.05, rect.bottom - rect.height * 0.05),
            palette[Math.floor(Math.random() * palette.length)],
            _randomBetween(0, 22)
        );
    }
}

function _animateCharaCardParticles() {
    if (!charaCardParticleContext) return;
    charaCardParticleContext.clearRect(0, 0, window.innerWidth, window.innerHeight);

    charaCardParticles = charaCardParticles.filter(particle => {
        if (particle.delay > 0) {
            particle.delay -= 1;
            return true;
        }

        particle.life += 1;
        const progress = particle.life / particle.maxLife;
        particle.vy += 0.018;
        particle.vx *= 0.992;
        particle.x += particle.vx;
        particle.y += particle.vy;
        particle.rotation += particle.spin;
        particle.alpha = Math.max(0, 1 - progress);

        charaCardParticleContext.save();
        charaCardParticleContext.globalAlpha = particle.alpha;
        charaCardParticleContext.translate(particle.x, particle.y);
        charaCardParticleContext.rotate(particle.rotation);
        charaCardParticleContext.fillStyle = particle.color;
        charaCardParticleContext.shadowColor = 'rgba(64, 197, 241, 0.35)';
        charaCardParticleContext.shadowBlur = 7 * particle.alpha;
        charaCardParticleContext.fillRect(-particle.size / 2, -particle.size / 2, particle.size, particle.size);
        charaCardParticleContext.restore();

        return particle.life < particle.maxLife;
    });

    if (charaCardParticles.length) {
        charaCardParticleFrame = requestAnimationFrame(_animateCharaCardParticles);
    } else {
        cancelAnimationFrame(charaCardParticleFrame);
        charaCardParticleFrame = 0;
        _teardownCharaCardParticleCanvas();
    }
}

function _startCharaCardParticles() {
    if (!charaCardParticleCanvas) _ensureCharaCardParticleCanvas();
    if (!charaCardParticleFrame) {
        charaCardParticleFrame = requestAnimationFrame(_animateCharaCardParticles);
    }
}

function _wait(duration) {
    return new Promise(resolve => window.setTimeout(resolve, duration));
}

async function _dissolveCharaCardElement(target) {
    if (!(target && target.classList)) {
        return;
    }
    const runId = ++charaCardDissolveRunId;
    const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    target.style.pointerEvents = 'none';

    if (reduceMotion) {
        target.style.opacity = '0';
        target.style.visibility = 'hidden';
        return;
    }

    target.classList.add('is-dissolving');
    _ensureCharaCardParticleCanvas();
    _spawnCharaCardParticles(target);
    _startCharaCardParticles();

    await _wait(CHARA_CARD_DISSOLVE_DURATION);
    if (runId !== charaCardDissolveRunId) {
        target.classList.remove('is-dissolving');
        target.style.opacity = '0';
        target.style.visibility = 'hidden';
        target.style.pointerEvents = 'none';
        return;
    }
    target.classList.remove('is-dissolving');
    target.style.opacity = '0';
    target.style.visibility = 'hidden';
    target.style.pointerEvents = '';
}

async function _deleteCharaCardWithParticle(name, targetCardElement, triggerButton) {
    try {
        const deleted = await workshopDeleteCatgirl(name, { skipReload: true });
        if (!deleted) {
            if (triggerButton) triggerButton.disabled = false;
            return false;
        }

        if (targetCardElement) {
            await _dissolveCharaCardElement(targetCardElement);
        }

        try {
            await loadCharacterCards();
        } catch (error) {
            console.error('刷新角色卡列表失败:', error);
            if (targetCardElement && targetCardElement.parentNode) {
                targetCardElement.parentNode.removeChild(targetCardElement);
            } else if (triggerButton) {
                triggerButton.disabled = false;
            }
        }
        return true;
    } catch (error) {
        console.error('删除角色卡粒子消散流程失败:', error);
        if (triggerButton) triggerButton.disabled = false;
        return false;
    }
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
        const translatedNoCardImage = window.t && window.t('steam.noCardImage');
        placeholderSpan.textContent = translatedNoCardImage && translatedNoCardImage !== 'steam.noCardImage'
            ? translatedNoCardImage
            : '暂未设置卡面';
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
        switchBtn.title = window.t ? window.t('character.switchCard') : '切换该角色';
        switchBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>'
            + '<span>' + (window.t ? window.t('character.switchCard') : '切换该角色') + '</span>';
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
        deleteBtn.onclick = async function (e) {
            e.stopPropagation();
            if (deleteBtn.disabled) return;
            deleteBtn.disabled = true;
            const deleted = await _deleteCharaCardWithParticle(name, item, deleteBtn);
            if (!deleted) deleteBtn.disabled = false;
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
        switchBtn.title = window.t ? window.t('character.switchCard') : '切换该角色';
        switchBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>'
            + '<span class="list-action-label">' + (window.t ? window.t('character.switchCard') : '切换该角色') + '</span>';
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
        deleteBtn.onclick = async function (e) {
            e.stopPropagation();
            if (deleteBtn.disabled) return;
            deleteBtn.disabled = true;
            const deleted = await _deleteCharaCardWithParticle(name, item, deleteBtn);
            if (!deleted) deleteBtn.disabled = false;
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
const CATGIRL_PANEL_STEAM_COMPACT_WIDTH = 1280;
let _catgirlPanelSteamLayoutRaf = null;

function isCatgirlPanelSteamCompactWindow() {
    const width = window.innerWidth || document.documentElement.clientWidth || 0;
    return width > 0 && width < CATGIRL_PANEL_STEAM_COMPACT_WIDTH;
}

function refreshSteamPreviewAfterPanelLayoutChange() {
    requestAnimationFrame(function () {
        if (typeof buildPreviewRing === 'function') buildPreviewRing();
        if (live2dPreviewManager && live2dPreviewManager.pixi_app) {
            const l2dContainer = document.getElementById('live2d-preview-content');
            if (l2dContainer && l2dContainer.clientWidth > 0 && l2dContainer.clientHeight > 0) {
                live2dPreviewManager.pixi_app.renderer.resize(l2dContainer.clientWidth, l2dContainer.clientHeight);
                if (live2dPreviewManager.currentModel) {
                    live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
                    live2dPreviewManager.pixi_app.renderer.render(live2dPreviewManager.pixi_app.stage);
                }
            }
        }
        syncWorkshop3DPreviewSize(workshopVrmManager, 'vrm-preview-canvas');
        syncWorkshop3DPreviewSize(workshopMmdManager, 'mmd-preview-canvas');
    });
}

function updateCatgirlPanelSteamCardLayout(wrapper) {
    const panel = wrapper || document.getElementById('catgirl-panel-wrapper');
    if (!panel) return;

    const activeTab = panel.querySelector('.panel-tab.active');
    const shouldHideCardFace = !!(
        activeTab
        && activeTab.dataset.tab === 'steam'
        && isCatgirlPanelSteamCompactWindow()
    );
    const wasHidden = panel.classList.contains('steam-compact-card-hidden');
    panel.classList.toggle('steam-compact-card-hidden', shouldHideCardFace);
    const indicator = panel.querySelector('.panel-tabs-indicator');
    if (activeTab && indicator) {
        indicator.style.left = activeTab.offsetLeft + 'px';
        indicator.style.width = activeTab.offsetWidth + 'px';
    }
    const changed = wasHidden !== shouldHideCardFace;
    if (changed) {
        setTimeout(refreshSteamPreviewAfterPanelLayoutChange, 430);
    }
}

function scheduleCatgirlPanelSteamCardLayoutUpdate() {
    if (_catgirlPanelSteamLayoutRaf) cancelAnimationFrame(_catgirlPanelSteamLayoutRaf);
    _catgirlPanelSteamLayoutRaf = requestAnimationFrame(function () {
        _catgirlPanelSteamLayoutRaf = null;
        updateCatgirlPanelSteamCardLayout();
    });
}

window.addEventListener('resize', scheduleCatgirlPanelSteamCardLayoutUpdate);

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
    if (name) wrapper.dataset.catgirlName = name;

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
    const imgPlaceholder = document.createElement('span');
    imgPlaceholder.className = 'card-avatar-placeholder';
    const translatedNoCardImage = window.t && window.t('steam.noCardImage');
    imgPlaceholder.textContent = translatedNoCardImage && translatedNoCardImage !== 'steam.noCardImage'
        ? translatedNoCardImage
        : '暂未设置卡面';
    cardImage.appendChild(imgPlaceholder);

    const cardActionOverlay = document.createElement('div');
    cardActionOverlay.className = 'catgirl-panel-card-actions';
    const modelSettingsAction = document.createElement('button');
    modelSettingsAction.type = 'button';
    modelSettingsAction.className = 'catgirl-panel-card-action';
    modelSettingsAction.textContent = window.t ? window.t('character.cardFaceModelSettings') : '模型设置';
    const editCardFaceAction = document.createElement('button');
    editCardFaceAction.type = 'button';
    editCardFaceAction.className = 'catgirl-panel-card-action';
    editCardFaceAction.textContent = window.t ? window.t('character.editCardFace') : '编辑卡面';
    cardActionOverlay.appendChild(modelSettingsAction);
    cardActionOverlay.appendChild(editCardFaceAction);
    cardImage.appendChild(cardActionOverlay);

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

    const openCardMaker = async () => {
        // 优先使用表单中当前填写的档案名（新建猫娘可能已临时保存）
        const form = cardImage.closest('.catgirl-panel-wrapper')?.querySelector('form');
        const currentName = getProfileNameFromCharacterForm(form, name);
        if (!currentName) {
            await showProfileNameRequiredDialog(
                'character.fillProfileNameFirstForCardFace',
                '请先填写猫娘档案名，然后再设置卡面'
            );
            return;
        }
        const nameInput = form?.querySelector?.('[name="档案名"]');
        if (form && form._isNew && !form._autoCreated) {
            if (!(await ensureValidCharacterProfileName(currentName, nameInput))) {
                return;
            }
        } else if (!(await ensureSafeExistingCharacterPathName(currentName, nameInput))) {
            return;
        }
        const makerUrl = `/card_maker?name=${encodeURIComponent(currentName)}&mode=maker`;
        openManagedPopup(makerUrl, CHARACTER_MANAGER_CARD_MAKER_WINDOW_NAME, 'width=1200,height=800');
    };

    const openCardModelManager = async () => {
        const form = cardImage.closest('.catgirl-panel-wrapper')?.querySelector('form');
        await openModelManagerForCharacterForm(form, name);
    };

    // 点击卡面主体打开模型管理；编辑卡面按钮仍进入角色卡制作页面。
    cardImage.addEventListener('click', async (event) => {
        if (event.target.closest('.catgirl-panel-card-action')) return;
        await openCardModelManager();
    });
    editCardFaceAction.addEventListener('click', (event) => {
        event.stopPropagation();
        openCardMaker();
    });
    modelSettingsAction.addEventListener('click', async (event) => {
        event.stopPropagation();
        await openCardModelManager();
    });

    // 监听角色卡制作页面的保存消息
    const onCardFaceMessage = (event) => {
        if (event.origin !== window.location.origin) return;
        // 获取当前实际的档案名（新建猫娘时 name 为 null，需要从表单读取）
        const form = cardImage.closest('.catgirl-panel-wrapper')?.querySelector('form');
        const currentName = form?.querySelector('[name="档案名"]')?.value || name;
        if (!currentName) return;

        if (event.data && event.data.type === 'card-face-updated' && event.data.name === currentName) {
            applyCardFaceUpdated(currentName, event.data.timestamp);
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

        const isCurrentChara = (window._workshopCurrentCatgirl || '') === name;

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'card-panel-action-btn delete-btn' + (isCurrentChara ? ' disabled' : '');
        deleteBtn.title = isCurrentChara
            ? (window.t ? window.t('character.cannotDeleteCurrentCard') : '当前正在使用的角色卡无法删除，请先切换到其他角色卡')
            : (window.t ? window.t('character.deleteCard') : '删除角色卡');
        deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
            + '<span>' + (window.t ? window.t('character.deleteCard') : '删除') + '</span>';
        deleteBtn.onclick = async function (e) {
            e.stopPropagation();
            // 不再用打开面板时快照的 isCurrentChara 拦截——workshopDeleteCatgirl 内部会用权威当前角色名做判断，
            // 这样跨窗口切换、用户取消、后端拒绝等情况都会被正确处理，且只有真正删除成功后才关面板，避免提前关掉丢未保存改动
            const deleted = await workshopDeleteCatgirl(name);
            if (deleted) {
                closeCatgirlPanel();
            }
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
    closeBtnImg.draggable = false;
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
        const spawnCurtainTransition = function (targetTabName, reverse, fullPanel) {
            const curtain = document.createElement('div');
            curtain.className = 'panel-transition-curtain'
                + (reverse ? ' curtain-reverse' : '')
                + (fullPanel ? ' full-panel' : '');

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

            const curtainHost = fullPanel ? wrapper : rightSection;
            curtainHost.appendChild(curtain);
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
                const needsFullPanelCurtain = wrapper.classList.contains('steam-compact-card-hidden')
                    || (targetTab === 'steam' && isCatgirlPanelSteamCompactWindow());

                _tabSwitching = true;
                headerBar.querySelectorAll('.panel-tab').forEach(t => t.classList.remove('active'));
                this.classList.add('active');
                updateIndicator();

                // 小窗口 Steam 切换会联动左侧卡面，幕布先覆盖整个面板再更新布局。
                spawnCurtainTransition(targetTab, reverseDirection, needsFullPanelCurtain);
                updateCatgirlPanelSteamCardLayout(wrapper);

                // 根据当前激活状态切换设定齿轮图标 on/off
                if (settingsIcon) {
                    settingsIcon.src = (targetTab === 'settings')
                        ? '/static/icons/set_on.png'
                        : '/static/icons/set_off.png';
                }

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
                                        if (live2dPreviewManager.pixi_app && live2dPreviewManager.pixi_app.renderer) {
                                            live2dPreviewManager.pixi_app.renderer.render(live2dPreviewManager.pixi_app.stage);
                                        }
                                    }
                                }
                            }
                            // VRM / MMD resize：同步 renderer、camera 和 OutlineEffect，避免只改 canvas 尺寸导致 3D 预览横向变形。
                            syncWorkshop3DPreviewSize(workshopVrmManager, 'vrm-preview-canvas');
                            syncWorkshop3DPreviewSize(workshopMmdManager, 'mmd-preview-canvas');
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
    updateCatgirlPanelSteamCardLayout(wrapper);

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
            // 用 overlay 持有 timer id，关闭时统一 clearTimeout，避免在 closing 期间重建预览
            if (!isNew) {
                const steamInitTimer = setTimeout(() => {
                    overlay._steamTabInitTimer = null;
                    if (overlay.dataset.closing === 'true' || !overlay.isConnected) return;
                    const steamContainer = rightSection.querySelector('.panel-tab-steam');
                    if (steamContainer && !steamContainer.dataset.initialized) {
                        steamContainer.dataset.initialized = 'true';
                        buildSteamTabContent(name, rawData, card, steamContainer);
                    }
                }, 500);
                overlay._steamTabInitTimer = steamInitTimer;
            }
        }, 500);
    });
}
window.openCatgirlPanel = openCatgirlPanel;

function openNewCatgirlPanel() {
    openCatgirlPanel(null, null);
}
window.openNewCatgirlPanel = openNewCatgirlPanel;

function buildCreatedCatgirlPanelActions(name) {
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

    const isCurrentChara = (window._workshopCurrentCatgirl || '') === name;

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'card-panel-action-btn delete-btn' + (isCurrentChara ? ' disabled' : '');
    deleteBtn.title = isCurrentChara
        ? (window.t ? window.t('character.cannotDeleteCurrentCard') : '当前正在使用的角色卡无法删除，请先切换到其他角色卡')
        : (window.t ? window.t('character.deleteCard') : '删除角色卡');
    deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>'
        + '<span>' + (window.t ? window.t('character.deleteCard') : '删除') + '</span>';
    deleteBtn.onclick = async function (e) {
        e.stopPropagation();
        const deleted = await workshopDeleteCatgirl(name);
        if (deleted) {
            closeCatgirlPanel();
        }
    };
    actions.appendChild(deleteBtn);

    return actions;
}

async function rollbackAutoCreatedCatgirl(form, targetName = '') {
    if (!form) return;
    const tempNames = Array.from(new Set(
        (targetName
            ? [targetName]
            : [form._autoCreatedName, form._autoCreatedDetachedName]
        ).filter(Boolean)
    ));
    if (!tempNames.length) return;
    const deletedNames = [];
    try {
        for (const tempName of tempNames) {
            const resp = await fetch('/api/characters/catgirl/' + encodeURIComponent(tempName), {
                method: 'DELETE'
            });
            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({}));
                console.warn('[角色面板] 回滚临时角色失败:', tempName, errData.error || resp.statusText);
                continue;
            }
            deletedNames.push(tempName);
            if (window._cardFaceNames) window._cardFaceNames.delete(tempName);
            if (window._cardMetas) delete window._cardMetas[tempName];
        }
        if (!deletedNames.length) return;
        if (deletedNames.includes(form._autoCreatedName)) {
            form._autoCreated = false;
            form._autoCreatedName = '';
        }
        if (deletedNames.includes(form._autoCreatedDetachedName)) {
            form._autoCreatedDetachedName = '';
        }
        if (!form._autoCreatedName && !form._autoCreatedDetachedName) {
            form._autoCreatedRollbackWhenDependentCloses = false;
            form._autoCreatedDependentPopupSaved = false;
        }
        if (typeof loadCharacterCards === 'function') {
            loadCharacterCards().catch(e => console.warn('刷新角色列表失败:', e));
        }
    } catch (e) {
        console.warn('[角色面板] 回滚临时角色请求失败:', tempNames.join(', '), e);
    }
}

function hasOpenAutoCreatedDependentPopup(form) {
    const popup = form && form._autoCreatedDependentPopup;
    return !!(popup && !popup.closed);
}

async function closeCatgirlPanel() {
    const overlay = document.querySelector('.catgirl-panel-overlay');
    if (!overlay) return;
    if (overlay.dataset.closing === 'true') return;
    overlay.dataset.closing = 'true';

    // 详情面板被显式关闭：companion 是绑在「这一次卡片编辑会话」上的助手，会话结束就
    // 跟着收掉。关键安全点（Codex #3328901017）：打开/切换到「别的卡」必须先走到这里把
    // 当前面板关掉（openCatgirlPanel 顶部有 _catgirlPanelOpen 互斥 guard，开新面板前
    // 一定先 closeCatgirlPanel）。所以在这里直接 teardown+destroy companion，就从根上
    // 杜绝了「A 的聊天被 _companionEnsureLiveForm 的选择器回退误绑到下一张打开的卡 B、
    // 后续 action/autosave 改错卡」。合法的 in-place rebuild（改档案名字段后
    // saveCatgirlFromPanel 重建、新卡首存 popup 被拦走 rebuildSavedCatgirlPanel）都不经过
    // closeCatgirlPanel，companion 不受影响、照常跟随。
    if (window._cardCompanion) {
        _companionTeardown(window._cardCompanion);
        _companionDestroy(window._cardCompanion);
        window._cardCompanion = null;
    }

    const currentForm = overlay.querySelector('form');
    if (currentForm && currentForm._voiceSelectCleanup) {
        currentForm._voiceSelectCleanup();
        delete currentForm._voiceSelectCleanup;
    }
    if (currentForm && currentForm._characterPersonalityUpdateHandler) {
        window.removeEventListener('neko:character-personality-updated', currentForm._characterPersonalityUpdateHandler);
        delete currentForm._characterPersonalityUpdateHandler;
    }
    // _autoCreatedDependentPopupSaved 由 500ms 轮询置位，存在 popup 已关到 timer 下次触发之间的窗口；
    // 同时直接读 popup._modelManagerHasSaved 兜底，避免误把刚保存好的临时角色回滚掉
    const dependentPopupForCheck = currentForm && currentForm._autoCreatedDependentPopup;
    const dependentSaved = !!(currentForm && (
        currentForm._autoCreatedDependentPopupSaved
        || (dependentPopupForCheck && dependentPopupForCheck._modelManagerHasSaved)
    ));
    if (!dependentSaved && hasOpenAutoCreatedDependentPopup(currentForm)) {
        currentForm._autoCreatedRollbackWhenDependentCloses = true;
    } else if (!dependentSaved) {
        await rollbackAutoCreatedCatgirl(currentForm);
    }

    // 取消所有预览加载：包括尚未完成的 Live2D/VRM/MMD 异步加载，避免清理后又把预览建回来
    if (typeof cancelWorkshopPreviewLoads === 'function') {
        cancelWorkshopPreviewLoads();
    } else if (typeof cancelPendingLive2DPreviewLoads === 'function') {
        cancelPendingLive2DPreviewLoads();
    }

    // 取消尚未触发的 Steam 标签页延迟初始化，避免在清理后又把预览建回来
    if (overlay._steamTabInitTimer) {
        clearTimeout(overlay._steamTabInitTimer);
        overlay._steamTabInitTimer = null;
    }

    // 清理模型预览资源（如果 Steam 标签页曾加载过）；清理完成后再执行收起动画
    try {
        const cleanupTasks = [];
        if (typeof disposeWorkshopVrm === 'function') cleanupTasks.push(disposeWorkshopVrm());
        if (typeof disposeWorkshopMmd === 'function') cleanupTasks.push(disposeWorkshopMmd());
        if (typeof destroyLive2DPreviewContext === 'function') cleanupTasks.push(destroyLive2DPreviewContext());
        await Promise.allSettled(cleanupTasks);
    } catch (e) {
        console.warn('[Panel] 清理预览资源时出错:', e);
    }

    const wrapper = overlay.querySelector('.catgirl-panel-wrapper');
    if (wrapper) {
        wrapper.classList.remove('phase-expand');
        wrapper.classList.add('phase-center');
    }

    await new Promise(resolve => setTimeout(resolve, 300));
    overlay.classList.remove('active');
    if (wrapper) wrapper.classList.remove('phase-center');
    await new Promise(resolve => setTimeout(resolve, 400));
    overlay.remove();
    _catgirlPanelOpen = false;
}
window.closeCatgirlPanel = closeCatgirlPanel;

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
            <button id="live2d-refresh-btn" style="position: absolute; top: 10px; right: 10px; z-index: 101; width: 30px; height: 30px; border: none; border-radius: 50%; background-color: transparent; color: white; cursor: pointer; display: none; justify-content: center; align-items: center; font-size: 16px; pointer-events: auto;" title="${window.t ? window.t('steam.refreshLive2DPreview') : '刷新Live2D预览'}" onclick="refreshLive2DPreview()">↻</button>
        </div>`;
    live2dSection.appendChild(previewContainer);

    // 动作/表情控件
    const controlsDiv = document.createElement('div');
    controlsDiv.id = 'live2d-preview-controls';
    controlsDiv.style.cssText = 'padding: 10px; background-color: #fff; border-top: 1px solid #e0e0e0; margin: 10px 10px 10px 10px; border-radius: 16px;';
    controlsDiv.innerHTML = `
        <div style="display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 150px;">
                <select id="preview-motion-select" class="control-input" style="width: 100%;"></select>
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
                <select id="preview-expression-select" class="control-input" style="width: 100%;"></select>
            </div>
            <div class="btn-play-wrapper">
                <button id="preview-play-expression-btn" class="btn" disabled>
                    <span data-i18n="steam.playExpression">${window.t ? window.t('steam.playExpression') : '播放表情'}</span>
                </button>
            </div>
        </div>`;
    live2dSection.appendChild(controlsDiv);
    ensurePreviewPlaybackBindings();
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
    const tagsContainer = document.createElement('div');
    tagsContainer.className = 'tags-container';
    tagsContainer.id = 'character-card-tags-container';
    tagsWrapper.appendChild(tagsContainer);
    tagsControlGroup.appendChild(tagsWrapper);
    ensureCharacterCardTagScrollControls();
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
    const currentLive2DModelInfo = effectiveModelType === 'live2d' ? getLive2DModelInfo(live2d) : null;
    window.currentCharacterCardModelSource = currentLive2DModelInfo && currentLive2DModelInfo.source ? currentLive2DModelInfo.source : '';
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
        requestAnimationFrame(updateCharacterCardTagScrollControls);
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

        // 字段顺序是展示属性，先在删保留字段前抓住它，删完再以顶层 _field_order 挂回。
        // 否则数字 key 的自定义字段名会被下载方按对象枚举顺序提前，复现本次修复要解决的乱序问题。
        const workshopFieldOrder = getStoredCharacterFieldOrder(rawData);

        // 重要：清理系统保留字段，防止恶意数据或循环引用被上传到工坊
        // 这些字段是下载时由系统添加的元数据，不应该出现在工坊角色卡中
        // description/tags 及其中文版本是工坊上传时自动生成的，不属于角色卡原始数据
        // live2d_item_id 是系统自动管理的，不应该上传
        const SYSTEM_RESERVED_FIELDS = getWorkshopReservedFields();
        for (const field of SYSTEM_RESERVED_FIELDS) {
            delete fullCharaData[field];
        }
        // 顺序元数据本身被当作系统保留字段删掉了，这里按显式顺序重新挂回，供下载方按创建顺序渲染。
        if (workshopFieldOrder.length) {
            attachCharacterFieldOrderPayload(fullCharaData, workshopFieldOrder);
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

        // 设置默认模型（排除yui-origin）- 仅限 Live2D 模型类型
        if (currentModelType === 'live2d' && (!selectedModelName || isStaticDefaultLive2DModel(selectedModelName, rawData))) {
            const validModels = availableModels.filter(model =>
                model
                && model.name
                && !hasStaticModelFlag(model)
                && !hasStaticModelFlag(model.modelMetadata)
            );
            if (validModels.length > 0) {
                selectedModelName = validModels[0].name;
            } else {
                showMessage(window.t ? window.t('steam.noAvailableModelsError') : '没有可用的模型', 'error');
                return;
            }
            fullCharaData.live2d = selectedModelName;
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
            .then(async result => {
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
                    const previewImageInput = document.getElementById('preview-image');
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
                    // 若后端成功从角色卡卡面复制出预览图，则默认带入；没有卡面时不改动用户当前预览图输入。
                    if (previewImageInput && result.preview_image) {
                        previewImageInput.value = result.preview_image;
                        previewImageInput.classList.remove('error');
                    }
                    await resetWorkshopVoiceReferenceFields(cardName);

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
async function scanModels(loadSequence) {
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

        // 过滤掉来自static目录的模型（如默认/版权Live2D），只保留用户文档目录中的模型
        // 这是为了防止上传版权Live2D模型
        const uploadableModels = models.filter(model => model.source !== 'static');

        // 处理 VRM 模型（先收集到局部变量，避免旧轮扫描晚到时回滚新轮结果）
        let scannedAllVrmModels = null;
        let nextAvailableVrmModels = null;
        try {
            if (vrmResponse && vrmResponse.ok) {
                const vrmData = await vrmResponse.json();
                if (vrmData.success && vrmData.models) {
                    scannedAllVrmModels = vrmData.models;
                    nextAvailableVrmModels = vrmData.models.filter(m => m.location !== 'project');
                }
            }
        } catch (e) {
            console.warn('处理VRM模型列表失败:', e);
        }

        // 处理 MMD 模型
        let scannedAllMmdModels = null;
        let nextAvailableMmdModels = null;
        try {
            if (mmdResponse && mmdResponse.ok) {
                const mmdData = await mmdResponse.json();
                if (mmdData.success && mmdData.models) {
                    scannedAllMmdModels = mmdData.models;
                    nextAvailableMmdModels = mmdData.models.filter(m => m.location !== 'project');
                }
            }
        } catch (e) {
            console.warn('处理MMD模型列表失败:', e);
        }

        // 序列号校验：若已被新一轮 loadCharacterCards 触发，丢弃本轮结果，防止旧扫描回滚新数据
        if (loadSequence !== undefined && loadSequence !== characterCardLoadSequence) {
            return false;
        }

        // 提交到全局变量（用于角色卡加载，包括static目录的模型）
        // 注意：6 个全局必须无条件覆写到本轮结果，VRM/MMD 子扫描失败时落 [] 而非沿用旧值；
        // 否则 tab 切换路径里如果 VRM/MMD 端点偶发失败，会保留上一轮的 stale 列表造成假阳性
        window.allModels = models;
        availableModels = uploadableModels;
        window.allVrmModels = scannedAllVrmModels || [];
        availableVrmModels = nextAvailableVrmModels || [];
        window.allMmdModels = scannedAllMmdModels || [];
        availableMmdModels = nextAvailableMmdModels || [];

        // 触发模型扫描完成事件，通知其他组件刷新 UI（具有容错能力）
        try {
            window.dispatchEvent(new CustomEvent('modelsScanned', { detail: { models, uploadableModels } }));
        } catch (e) {
            console.warn('触发 modelsScanned 事件失败:', e);
        }

        // 如果存在 model_manager 分片中的更新函数，也尝试调用（具有容错能力）
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

        return true;

    } catch (error) {
        console.error('扫描模型失败:', error);
        showMessage(window.t ? window.t('steam.modelScanError') : '扫描模型失败', 'error');
        return false;
    }
}

// 全局变量：当前选择的模型信息
let selectedModelInfo = null;

function setLive2DPreviewRefreshButtonState(visible, enabled = visible) {
    const refreshButton = document.getElementById('live2d-refresh-btn');
    if (!refreshButton) return;

    refreshButton.style.display = visible ? 'flex' : 'none';
    refreshButton.disabled = !enabled;
    refreshButton.style.cursor = enabled ? 'pointer' : 'default';
    refreshButton.setAttribute('aria-hidden', visible ? 'false' : 'true');
}

function fitLive2DPreviewModelToContainer(model) {
    if (!live2dPreviewManager || !live2dPreviewManager.pixi_app || !model) return;

    const renderer = live2dPreviewManager.pixi_app.renderer;
    const screenWidth = Number(renderer?.screen?.width) || 0;
    const screenHeight = Number(renderer?.screen?.height) || 0;
    if (screenWidth <= 0 || screenHeight <= 0) return;

    model.anchor.set(0.5, 0.5);
    if (!Number.isFinite(model.scale?.x) || model.scale.x <= 0 || !Number.isFinite(model.scale?.y) || model.scale.y <= 0) {
        model.scale.set(0.18);
    }

    model.x = screenWidth * 0.5;
    model.y = screenHeight * 0.5;

    // Live2DManager 在 addChild 之前会先调用 applyModelSettings。
    // 这时直接依赖 getBounds() 做精确 fitting 并不稳定，先做保守居中，
    // 等模型真正挂到 stage 上后再用 bounds 做二次校正。
    if (!model.parent || typeof model.getBounds !== 'function') return;

    let bounds = null;
    try {
        bounds = model.getBounds();
    } catch (error) {
        console.warn('[CharacterCard] 获取 Live2D 预览 bounds 失败:', error);
        return;
    }

    const initialWidth = Number(bounds?.width) || 0;
    const initialHeight = Number(bounds?.height) || 0;
    if (initialWidth <= 1 || initialHeight <= 1) return;

    const padding = 30;
    const availableWidth = Math.max(80, screenWidth - padding * 2);
    const availableHeight = Math.max(80, screenHeight - padding * 2);
    const scaleRatio = Math.min(availableWidth / initialWidth, availableHeight / initialHeight);

    if (Number.isFinite(scaleRatio) && scaleRatio > 0) {
        const nextScaleX = Math.max(0.02, Math.min(model.scale.x * scaleRatio, 2.5));
        const nextScaleY = Math.max(0.02, Math.min(model.scale.y * scaleRatio, 2.5));
        model.scale.set(nextScaleX, nextScaleY);
    }

    try {
        const fittedBounds = model.getBounds();
        const fittedWidth = Number(fittedBounds?.width) || 0;
        const fittedHeight = Number(fittedBounds?.height) || 0;
        if (fittedWidth > 1 && fittedHeight > 1) {
            const currentCenterX = (Number(fittedBounds.x) || 0) + fittedWidth * 0.5;
            const currentCenterY = (Number(fittedBounds.y) || 0) + fittedHeight * 0.5;
            model.x += (screenWidth * 0.5) - currentCenterX;
            model.y += (screenHeight * 0.5) - currentCenterY;
        }
    } catch (error) {
        console.warn('[CharacterCard] 校正 Live2D 预览位置失败:', error);
    }
}

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

async function registerVoice() {
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
    const providerSelect = document.getElementById('voice-reference-provider-hint');
    await applyWorkshopVoiceProviderRestrictions(providerSelect);
    const providerValue = providerSelect && providerSelect.value ? providerSelect.value.trim() : '';
    formData.append('provider', providerValue || getFirstAvailableWorkshopVoiceProviderValue(providerSelect) || 'cosyvoice');

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
                    const voiceSwitchOpId = createVoiceConfigSwitchOpId(lanlanName);
                    notifyVoiceConfigSwitching(lanlanName, true, voiceSwitchOpId);
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
                    }).finally(() => {
                        notifyVoiceConfigSwitching(lanlanName, false, voiceSwitchOpId);
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
let _workshopPreviewGeneration = 0;

function cancelWorkshopPreviewLoads() {
    _workshopPreviewGeneration += 1;
    cancelPendingLive2DPreviewLoads();
}

function isWorkshopPreviewLoadCurrent(generation) {
    return generation === _workshopPreviewGeneration && !!document.getElementById('live2d-preview-content');
}

async function disposeStaleWorkshopPreviewManager(manager, type) {
    if (!manager) return;
    try {
        if (type === 'mmd' && typeof manager.stopAnimation === 'function') {
            manager.stopAnimation();
        }
        if (typeof manager.dispose === 'function') {
            await manager.dispose();
        }
    } catch (e) {
        console.warn(`[Workshop ${String(type || '').toUpperCase()}] 清理过期预览实例失败:`, e);
    } finally {
        if (type === 'vrm' && workshopVrmManager === manager) {
            workshopVrmManager = null;
        }
        if (type === 'mmd' && workshopMmdManager === manager) {
            workshopMmdManager = null;
        }
    }
}

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
        '/static/vrm/vrm-orientation.js',
        '/static/vrm/vrm-core.js',
        '/static/vrm/vrm-expression.js',
        '/static/vrm/vrm-animation.js',
        '/static/vrm/vrm-interaction.js',
        '/static/vrm/vrm-cursor-follow.js',
        '/static/vrm/vrm-manager.js'
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
        '/static/mmd/mmd-core.js',
        '/static/mmd/mmd-animation.js',
        '/static/mmd/mmd-expression.js',
        '/static/mmd/mmd-interaction.js',
        '/static/mmd/mmd-cursor-follow.js',
        '/static/mmd/mmd-manager.js'
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

function syncWorkshop3DPreviewSize(manager, canvasId) {
    if (!manager || !manager.renderer) return false;

    const previewContent = document.getElementById('live2d-preview-content');
    const canvas = canvasId ? document.getElementById(canvasId) : (manager.renderer.domElement || null);
    const rect = previewContent ? previewContent.getBoundingClientRect() : null;
    const w = Math.max(1, Math.round(rect?.width || previewContent?.clientWidth || canvas?.clientWidth || 0));
    const h = Math.max(1, Math.round(rect?.height || previewContent?.clientHeight || canvas?.clientHeight || 0));
    if (w <= 1 || h <= 1) return false;

    if (canvas) {
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.width = Math.round(w * (window.devicePixelRatio || 1));
        canvas.height = Math.round(h * (window.devicePixelRatio || 1));
    }

    manager.renderer.setSize(w, h, false);
    if (manager.camera) {
        manager.camera.aspect = w / h;
        manager.camera.updateProjectionMatrix();
    }
    if (manager.effect && typeof manager.effect.setSize === 'function') {
        manager.effect.setSize(w, h);
    }
    return true;
}

function scheduleWorkshop3DPreviewResize(manager, canvasId) {
    requestAnimationFrame(() => {
        syncWorkshop3DPreviewSize(manager, canvasId);
        requestAnimationFrame(() => syncWorkshop3DPreviewSize(manager, canvasId));
    });
}

// 加载 VRM 模型预览
async function loadVrmPreview(modelPath, rawData) {
    const previewGeneration = ++_workshopPreviewGeneration;
    let localVrmManager = null;
    try {
        cancelPendingLive2DPreviewLoads();
        selectedModelInfo = null;
        setLive2DPreviewRefreshButtonState(false, false);

        // 先清理之前的 3D 预览
        await disposeWorkshopVrm();
        await disposeWorkshopMmd();
        if (!isWorkshopPreviewLoadCurrent(previewGeneration)) return;

        // 清理 Live2D 预览（如果有）
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel({ skipCloseWindows: true });
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
        if (!isWorkshopPreviewLoadCurrent(previewGeneration)) return;
        if (!loaded) {
            console.error('[Workshop VRM] 模块加载失败');
            showMessage(window.t ? window.t('steam.vrmModuleLoadFailed') || 'VRM 模块加载失败' : 'VRM 模块加载失败', 'error');
            return;
        }

        // 显示 VRM 容器
        const vrmContainer = document.getElementById('vrm-preview-container');
        if (vrmContainer) vrmContainer.style.display = 'block';

        // 创建 VRM 管理器实例
        localVrmManager = new window.VRMManager();
        workshopVrmManager = localVrmManager;

        // 获取光照配置
        const lighting = rawData?.['lighting'] || null;

        // 初始化 Three.js 场景
        await localVrmManager.initThreeJS('vrm-preview-canvas', 'vrm-preview-container', lighting);
        if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopVrmManager !== localVrmManager) {
            await disposeStaleWorkshopPreviewManager(localVrmManager, 'vrm');
            return;
        }

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

        // 按预览区域实际尺寸同步 renderer / camera / effect，避免 CSS 尺寸和 WebGL 后备尺寸不一致。
        const previewContent = document.getElementById('live2d-preview-content');
        syncWorkshop3DPreviewSize(localVrmManager, 'vrm-preview-canvas');

        // 允许 3D 交互：临时启用预览区域的 pointer-events
        if (previewContent) previewContent.style.pointerEvents = 'auto';
        const overlay = document.getElementById('live2d-preview-overlay');
        if (overlay) overlay.style.display = 'none';

        // 获取 idle 动画路径
        const idleAnimation = rawData?.['idleAnimation'] || '/static/vrm/animation/wait03.vrma';

        // 加载模型
        const result = await localVrmManager.loadModel(modelPath, {
            canvasId: 'vrm-preview-canvas',
            containerId: 'vrm-preview-container',
            addShadow: true,
            idleAnimation: idleAnimation
        });
        if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopVrmManager !== localVrmManager) {
            await disposeStaleWorkshopPreviewManager(localVrmManager, 'vrm');
            return;
        }

        if (result) {
            scheduleWorkshop3DPreviewResize(localVrmManager, 'vrm-preview-canvas');
            console.log('[Workshop VRM] 模型预览加载成功');
            showMessage(window.t ? window.t('steam.vrmPreviewLoaded') || 'VRM 模型预览已加载' : 'VRM 模型预览已加载', 'success');
        }
    } catch (error) {
        console.error('[Workshop VRM] 加载预览失败:', error);
        await disposeStaleWorkshopPreviewManager(localVrmManager, 'vrm');
        currentPreviewModel = null;
        showMessage(window.t ? window.t('steam.vrmPreviewFailed') || 'VRM 模型预览加载失败' : 'VRM 模型预览加载失败', 'error');
    }
}

// 加载 MMD 模型预览
async function loadMmdPreview(modelPath, rawData) {
    const previewGeneration = ++_workshopPreviewGeneration;
    let localMmdManager = null;
    try {
        cancelPendingLive2DPreviewLoads();
        selectedModelInfo = null;
        setLive2DPreviewRefreshButtonState(false, false);

        // 先清理之前的 3D 预览
        await disposeWorkshopVrm();
        await disposeWorkshopMmd();
        if (!isWorkshopPreviewLoadCurrent(previewGeneration)) return;

        // 清理 Live2D 预览（如果有）
        if (live2dPreviewManager && live2dPreviewManager.currentModel) {
            await live2dPreviewManager.removeModel({ skipCloseWindows: true });
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
        if (!isWorkshopPreviewLoadCurrent(previewGeneration)) return;
        if (!loaded) {
            console.error('[Workshop MMD] 模块加载失败');
            showMessage(window.t ? window.t('steam.mmdModuleLoadFailed') || 'MMD 模块加载失败' : 'MMD 模块加载失败', 'error');
            return;
        }

        // 显示 MMD 容器
        const mmdContainer = document.getElementById('mmd-preview-container');
        if (mmdContainer) mmdContainer.style.display = 'block';

        // 创建 MMD 管理器实例
        localMmdManager = new window.MMDManager();
        workshopMmdManager = localMmdManager;

        // 初始化
        await localMmdManager.init('mmd-preview-canvas', 'mmd-preview-container');
        if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopMmdManager !== localMmdManager) {
            await disposeStaleWorkshopPreviewManager(localMmdManager, 'mmd');
            return;
        }

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

        // 按预览区域实际尺寸同步 renderer / camera / effect，避免 CSS 尺寸和 WebGL 后备尺寸不一致。
        const previewContent = document.getElementById('live2d-preview-content');
        syncWorkshop3DPreviewSize(localMmdManager, 'mmd-preview-canvas');

        // 允许 3D 交互：临时启用预览区域的 pointer-events
        if (previewContent) previewContent.style.pointerEvents = 'auto';
        const overlay = document.getElementById('live2d-preview-overlay');
        if (overlay) overlay.style.display = 'none';

        // 加载模型
        const modelInfo = await localMmdManager.loadModel(modelPath);
        if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopMmdManager !== localMmdManager) {
            await disposeStaleWorkshopPreviewManager(localMmdManager, 'mmd');
            return;
        }

        if (modelInfo) {
            scheduleWorkshop3DPreviewResize(localMmdManager, 'mmd-preview-canvas');
            // 如果有 idle 动画，尝试加载
            const idleAnimation = rawData?.['mmd_idle_animation'] || '';
            if (idleAnimation && typeof localMmdManager.loadAnimation === 'function') {
                try {
                    await localMmdManager.loadAnimation(idleAnimation);
                    if (!isWorkshopPreviewLoadCurrent(previewGeneration) || workshopMmdManager !== localMmdManager) {
                        await disposeStaleWorkshopPreviewManager(localMmdManager, 'mmd');
                        return;
                    }
                    localMmdManager.playAnimation();
                } catch (e) {
                    console.warn('[Workshop MMD] idle 动画加载失败:', e);
                }
            }
            console.log('[Workshop MMD] 模型预览加载成功');
            showMessage(window.t ? window.t('steam.mmdPreviewLoaded') || 'MMD 模型预览已加载' : 'MMD 模型预览已加载', 'success');
        }
    } catch (error) {
        console.error('[Workshop MMD] 加载预览失败:', error);
        await disposeStaleWorkshopPreviewManager(localMmdManager, 'mmd');
        currentPreviewModel = null;
        showMessage(window.t ? window.t('steam.mmdPreviewFailed') || 'MMD 模型预览加载失败' : 'MMD 模型预览加载失败', 'error');
    }
}

// 清除所有模型预览（Live2D + VRM + MMD）
async function clearAllModelPreviews(showModelNotSetMessage = false) {
    cancelWorkshopPreviewLoads();
    selectedModelInfo = null;
    setLive2DPreviewRefreshButtonState(false, false);
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
        cancelPendingLive2DPreviewLoads();
        selectedModelInfo = null;
        window._previewMotionFiles = [];
        setLive2DPreviewRefreshButtonState(false, false);

        // 如果有模型加载，先移除它
        if (live2dPreviewManager && typeof live2dPreviewManager.removeModel === 'function') {
            await live2dPreviewManager.removeModel({ skipCloseWindows: true });
        }
        currentPreviewModel = null;

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

async function destroyLive2DPreviewContext() {
    const manager = live2dPreviewManager;
    cancelPendingLive2DPreviewLoads();
    selectedModelInfo = null;
    currentPreviewModel = null;
    window._previewMotionFiles = [];
    setLive2DPreviewRefreshButtonState(false, false);

    if (!manager) {
        return;
    }

    if (typeof manager._activeLoadToken === 'number') {
        manager._activeLoadToken += 1;
    }

    try {
        await clearLive2DPreview();
    } finally {
        manager._isLoadingModel = false;
        manager._modelLoadState = 'idle';
        manager._isModelReadyForInteraction = false;

        if (manager._canvasRevealTimer) {
            clearTimeout(manager._canvasRevealTimer);
            manager._canvasRevealTimer = null;
        }

        try {
            if (manager.pixi_app && manager.pixi_app.view && manager.pixi_app.view.style) {
                manager.pixi_app.view.style.transition = '';
                manager.pixi_app.view.style.opacity = '';
            }
        } catch (_) {}

        if (manager._previewResizeHandlerBound && manager._previewResizeHandler) {
            window.removeEventListener('resize', manager._previewResizeHandler);
        }
        manager._previewResizeHandlerBound = false;
        manager._previewResizeHandler = null;

        if (manager._screenChangeHandler) {
            window.removeEventListener('resize', manager._screenChangeHandler);
            manager._screenChangeHandler = null;
        }
        if (manager._displayChangeHandler) {
            window.removeEventListener('electron-display-changed', manager._displayChangeHandler);
            manager._displayChangeHandler = null;
        }

        if (typeof manager._stopIdleFpsGovernor === 'function') {
            manager._stopIdleFpsGovernor();
        }

        if (manager.pixi_app && typeof manager.pixi_app.destroy === 'function') {
            try {
                manager.pixi_app.destroy(true);
            } catch (destroyError) {
                console.warn('[CharacterCard] 销毁 Live2D 预览 PIXI 实例失败:', destroyError);
            }
        }

        manager.pixi_app = null;
        manager.currentModel = null;
        manager.isInitialized = false;
        manager._lastPIXIContext = { canvasId: null, containerId: null };
        live2dPreviewManager = null;
    }
}

// 通过模型名称加载Live2D模型
async function loadLive2DModelByName(modelName, modelInfo = null) {
    const loadGeneration = beginLive2DPreviewLoadGeneration();
    let loadedModel = null;
    setLive2DPreviewRefreshButtonState(false, false);
    const ensureCurrentLoad = async () => {
        if (isCurrentLive2DPreviewLoad(loadGeneration)) {
            return;
        }

        if (loadedModel && live2dPreviewManager?.currentModel === loadedModel) {
            try {
                await live2dPreviewManager.removeModel({ skipCloseWindows: true });
            } catch (cleanupError) {
                console.warn('[CharacterCard] 清理过期 Live2D 预览失败:', cleanupError);
            }
        }

        const staleError = new Error('Stale Live2D preview load');
        staleError.code = 'STALE_LIVE2D_PREVIEW_LOAD';
        throw staleError;
    };

    try {
        // 每次加载前都重新校验预览上下文。
        // Steam 详情面板会动态销毁并重建 canvas，仅凭 manager 是否存在
        // 无法判断它是否还绑定在当前这次打开的预览节点上。
        await initLive2DPreview();
        await ensureCurrentLoad();
        if (!live2dPreviewManager || !live2dPreviewManager.pixi_app) {
            throw new Error('Live2D preview is not ready');
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
            await live2dPreviewManager.removeModel({ skipCloseWindows: true });
            // 重置当前预览模型引用
            currentPreviewModel = null;
        }
        await ensureCurrentLoad();

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
        await ensureCurrentLoad();

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
        await ensureCurrentLoad();
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
        await ensureCurrentLoad();

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
        loadedModel = live2dPreviewManager.currentModel || null;
        await ensureCurrentLoad();

        // 设置当前预览模型引用，用于播放动作和表情
        currentPreviewModel = loadedModel;

        // 清除模型路径，防止拖动预览时自动保存到preference
        live2dPreviewManager._lastLoadedModelPath = null;

        // 更新预览控件
        await updatePreviewControlsAfterModelLoad(filesData);
        await ensureCurrentLoad();

        // 模型加载完成后，确保它在容器中正确显示
        setTimeout(() => {
            if (!isCurrentLive2DPreviewLoad(loadGeneration)) {
                return;
            }

            const canvas = document.getElementById('live2d-preview-canvas');
            if (live2dPreviewManager && live2dPreviewManager.currentModel && canvas) {
                fitLive2DPreviewModelToContainer(live2dPreviewManager.currentModel);
                // 确保canvas正确显示，占位符被隐藏
                canvas.style.display = '';
                const placeholder = document.querySelector('#live2d-preview-content .preview-placeholder');
                if (placeholder) placeholder.style.display = 'none';
                // 强制重绘canvas
                if (live2dPreviewManager.pixi_app && live2dPreviewManager.pixi_app.renderer) {
                    live2dPreviewManager.pixi_app.renderer.render(live2dPreviewManager.pixi_app.stage);
                }
            }
        }, 100);

        // 更新全局selectedModelInfo变量
        selectedModelInfo = modelInfo;
        setLive2DPreviewRefreshButtonState(true, true);
        showMessage((window.t && window.t('live2d.modelLoadSuccess', { model: modelName })) || `模型 ${modelName} 加载成功`, 'success');
    } catch (error) {
        if (error && error.code === 'STALE_LIVE2D_PREVIEW_LOAD') {
            return;
        }

        setLive2DPreviewRefreshButtonState(false, false);
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

    // 恢复已保存的待机动作（如果存在）。显式保留空值，避免“无动作”被浏览器默认选中第一个 option。
    const rawData = window._currentCardRawData || {};
    const savedIdleAnimation = rawData._reserved?.avatar?.live2d?.idle_animation
        || rawData.avatar?.live2d?.idle_animation
        || rawData.live2d_idle_animation
        || '';
    const savedIdleAnimationBaseName = savedIdleAnimation
        ? String(savedIdleAnimation).split('/').pop()
        : '';
    const availableMotionFiles = window._previewMotionFiles || [];
    let initialMotionToPlay = '';
    if (motionSelect) {
        motionSelect.value = '';
    }
    if (savedIdleAnimationBaseName && motionSelect) {
        const matchingSavedMotion = availableMotionFiles.find(file => {
            const normalizedFile = String(file || '');
            return normalizedFile === savedIdleAnimation
                || normalizedFile.split('/').pop() === savedIdleAnimationBaseName;
        });
        if (matchingSavedMotion) {
            motionSelect.value = matchingSavedMotion;
            initialMotionToPlay = matchingSavedMotion;
        }
    }

    const previewModelToAutoplay = currentPreviewModel;

    if (live2dPreviewManager) {
        live2dPreviewManager._userIdleAnimations = initialMotionToPlay
            ? [String(initialMotionToPlay).split('/').pop()]
            : [];
    }

    const scheduledMotionSelection = motionSelect ? motionSelect.value : '';

    if (initialMotionToPlay && previewModelToAutoplay) {
        requestAnimationFrame(() => {
            if (
                currentPreviewModel === previewModelToAutoplay
                && live2dPreviewManager?.currentModel === previewModelToAutoplay
                && motionSelect
                && motionSelect.value === scheduledMotionSelection
            ) {
                handlePreviewMotionPlay();
            }
        });
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
    for (const key of getOrderedCharacterFieldKeys(rawData, hiddenFields)) {
        const value = rawData[key];
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

    // 页面加载完成后自动加载音色列表
    loadVoices();

    if (descriptionInput) {
        descriptionInput.addEventListener('input', updateCardPreview);
    }

    window.addEventListener('resize', updateCharacterCardTagScrollControls);
    ensureCharacterCardTagScrollControls();
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
let live2dPreviewLoadGeneration = 0;

function beginLive2DPreviewLoadGeneration() {
    live2dPreviewLoadGeneration += 1;
    return live2dPreviewLoadGeneration;
}

function cancelPendingLive2DPreviewLoads() {
    live2dPreviewLoadGeneration += 1;
}

function isCurrentLive2DPreviewLoad(loadGeneration) {
    return loadGeneration === live2dPreviewLoadGeneration;
}

// 初始化Live2D预览环境
async function initLive2DPreview() {
    try {
        // 检查Live2DManager是否已定义
        if (typeof Live2DManager === 'undefined') {
            throw new Error('Live2DManager class not found');
        }

        const canvasId = 'live2d-preview-canvas';
        const containerId = 'live2d-preview-content';
        const canvas = document.getElementById(canvasId);
        const container = document.getElementById(containerId);

        // Steam 预览区域是动态创建的；在 DOM 尚未生成时静默跳过，
        // 避免页面初始加载阶段提前报错并污染后续初始化状态。
        if (!canvas || !container) {
            return;
        }

        if (!live2dPreviewManager) {
            live2dPreviewManager = new Live2DManager();
        }

        const existingView = live2dPreviewManager.pixi_app?.view || null;
        const needsPixiRebuild = !!(
            existingView && (
                existingView !== canvas ||
                !existingView.isConnected
            )
        );

        if (needsPixiRebuild && typeof live2dPreviewManager.rebuildPIXI === 'function') {
            await live2dPreviewManager.rebuildPIXI(canvasId, containerId);
        } else if (typeof live2dPreviewManager.ensurePIXIReady === 'function') {
            await live2dPreviewManager.ensurePIXIReady(canvasId, containerId);
        } else if (!live2dPreviewManager.pixi_app) {
            await live2dPreviewManager.initPIXI(canvasId, containerId);
        }

        // 覆盖applyModelSettings方法，为预览模式实现专门的显示逻辑
        if (!live2dPreviewManager._previewApplyModelSettingsPatched) {
            const originalApplyModelSettings = live2dPreviewManager.applyModelSettings;
            live2dPreviewManager.applyModelSettings = function (model, options) {
                // 获取预览容器的尺寸
                const previewContainer = document.getElementById(containerId);
                if (!previewContainer || !this.pixi_app || !this.pixi_app.renderer) {
                    return originalApplyModelSettings.call(this, model, options);
                }
                fitLive2DPreviewModelToContainer(model);
            };
            live2dPreviewManager._previewApplyModelSettingsPatched = true;
        }

        // 添加窗口大小变化的监听，当预览区域大小变化时重新计算模型缩放和位置
        if (!live2dPreviewManager._previewResizeHandlerBound) {
            function resizePreviewModel() {
                const previewContainer = document.getElementById(containerId);
                if (live2dPreviewManager && live2dPreviewManager.pixi_app && previewContainer &&
                    previewContainer.clientWidth > 0 && previewContainer.clientHeight > 0) {
                    live2dPreviewManager.pixi_app.renderer.resize(previewContainer.clientWidth, previewContainer.clientHeight);
                }
                if (live2dPreviewManager && live2dPreviewManager.currentModel) {
                    // 调用我们覆盖的applyModelSettings方法，重新计算模型缩放和位置
                    live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
                    if (live2dPreviewManager.pixi_app && live2dPreviewManager.pixi_app.renderer) {
                        live2dPreviewManager.pixi_app.renderer.render(live2dPreviewManager.pixi_app.stage);
                    }
                }
            }
            live2dPreviewManager._previewResizeHandler = resizePreviewModel;
            live2dPreviewManager._previewResizeHandlerBound = true;
            window.addEventListener('resize', resizePreviewModel);
        }

        // 添加removeModel方法的fallback，防止调用时出错
        if (!live2dPreviewManager.removeModel) {
            live2dPreviewManager.removeModel = async function (force) {
                try {
                    if (this.currentModel && this.pixi_app && this.pixi_app.stage) {
                        // 移除当前模型
                        this.pixi_app.stage.removeChild(this.currentModel);
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

    } catch (error) {
        console.error('Failed to initialize Live2D preview:', error);
        live2dPreviewManager = null;
        showMessage(window.t('steam.live2dInitFailed'), 'error');
    }
}

// 从文件夹加载Live2D模型
async function loadLive2DModelFromFolder(files) {
    try {
        await initLive2DPreview();
        if (!live2dPreviewManager || !live2dPreviewManager.pixi_app) {
            throw new Error('Live2D preview is not ready');
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

    // 更新动作选择框：始终提供空选项，允许保存“无待机动作”。
    const emptyMotionOption = document.createElement('option');
    emptyMotionOption.value = '';
    emptyMotionOption.textContent = (window.t && window.t('character.noIdleMotion', '无动作')) || '无动作';
    motionSelect.appendChild(emptyMotionOption);

    if (motionFiles.length > 0) {
        motionSelect.disabled = false;
        playMotionBtn.disabled = false;
        motionSelect.value = '';

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
        emptyMotionOption.textContent = (window.t && window.t('live2d.noMotionFiles', '没有动作文件')) || '没有动作文件';
    }

    // 更新表情选择框：始终提供空选项，避免默认选中第一个表情。
    const emptyExpressionOption = document.createElement('option');
    emptyExpressionOption.value = '';
    emptyExpressionOption.textContent = (window.t && window.t('character.noExpression', '无表情')) || '无表情';
    expressionSelect.appendChild(emptyExpressionOption);

    if (expressionFiles.length > 0) {
        expressionSelect.disabled = false;
        playExpressionBtn.disabled = false;
        expressionSelect.value = '';

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
        emptyExpressionOption.textContent = (window.t && window.t('live2d.noExpressionFiles', '没有表情文件')) || '没有表情文件';
    }

    // 显示预览控件
    previewControls.style.display = '';

    ensurePreviewPlaybackBindings();
}

function handlePreviewMotionPlay() {
    if (!currentPreviewModel) return;

    const motionSelect = document.getElementById('preview-motion-select');
    const motionFile = motionSelect ? motionSelect.value : '';
    if (!motionFile) return;

    const motionIndex = (window._previewMotionFiles || []).indexOf(motionFile);
    if (motionIndex < 0) return;

    try {
        currentPreviewModel.motion('PreviewAll', motionIndex, 3);
    } catch (error) {
        console.error('Failed to play motion:', error);
        showMessage(window.t('live2d.playMotionFailed', { motion: motionFile }), 'error');
    }
}

function handlePreviewExpressionPlay() {
    if (!currentPreviewModel) return;

    const expressionSelect = document.getElementById('preview-expression-select');
    const expressionName = expressionSelect ? expressionSelect.value : '';
    if (!expressionName) return;

    try {
        currentPreviewModel.expression(expressionName);
    } catch (error) {
        console.error('Failed to play expression:', error);
        showMessage(window.t('live2d.playExpressionFailed', { expression: expressionName }), 'error');
    }
}

function ensurePreviewPlaybackBindings() {
    const playMotionBtn = document.getElementById('preview-play-motion-btn');
    if (playMotionBtn && playMotionBtn.dataset.previewMotionBound !== 'true') {
        playMotionBtn.addEventListener('click', handlePreviewMotionPlay);
        playMotionBtn.dataset.previewMotionBound = 'true';
    }

    const playExpressionBtn = document.getElementById('preview-play-expression-btn');
    if (playExpressionBtn && playExpressionBtn.dataset.previewExpressionBound !== 'true') {
        playExpressionBtn.addEventListener('click', handlePreviewExpressionPlay);
        playExpressionBtn.dataset.previewExpressionBound = 'true';
    }
}

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


// ===================== 我的档案管理 =====================

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
function _cardAssistIsReservedKey(key) {
    const k = String(key == null ? '' : key).trim();
    if (!k) return false;
    if (k === '档案名') return true;
    try {
        if (typeof isCharacterReservedFieldName === 'function') {
            return isCharacterReservedFieldName(k);
        }
    } catch (_) { /* 极端兜底：helper 不可用就只挡 '档案名' */ }
    return false;
}

// 当前用作"开发猫"占位的猫娘 profile name。/api/characters/catgirl/{name}/card-face
// 命中就用真实卡面，不命中走 fallback 圆圈。未来替换开发猫只需要改这里。
const CARD_COMPANION_DEV_CAT_NAME = 'YUI';

function _cardAssistT(key, fallback, vars) {
    if (window.t && typeof window.t === 'function') {
        try {
            const v = window.t(key, vars || undefined);
            if (typeof v === 'string' && v && v !== key) return v;
        } catch (_) { /* fall through */ }
    }
    return fallback;
}

function _cardAssistCurrentLocale() {
    try {
        const lang = (typeof getCurrentUiLanguage === 'function') ? getCurrentUiLanguage() : '';
        return lang || 'en';
    } catch (_) { return 'en'; }
}

// 收集表单上所有用户可见的字段 name（textarea + input），保留出现顺序、去重、
// 跳过保留 key。Apply 时是按 `textarea[name=...]` 精确匹配的，所以必须把
// 模板真实使用的 key（en 模板用 "Gender"/"Age" 之类、zh 用 "性别"/"年龄" 之类）
// 喂给 LLM，否则生成出来的中文 key 会以"新增字段"形式平行插入，旧字段不会被覆盖。
function _cardAssistCollectFieldKeys(form) {
    const keys = [];
    if (!form) return keys;
    const seen = new Set();
    form.querySelectorAll('textarea[name], input[name]').forEach(function (el) {
        const k = el.getAttribute('name');
        if (!k || _cardAssistIsReservedKey(k)) return;
        if (seen.has(k)) return;
        seen.add(k);
        keys.push(k);
    });
    return keys;
}

function _cardAssistCollectCurrentFormData(form) {
    const data = {};
    if (!form) return data;
    const fd = new FormData(form);
    for (const [k, v] of fd.entries()) {
        if (!k || _cardAssistIsReservedKey(k)) continue;
        const val = typeof v === 'string' ? v.trim() : v;
        if (val) data[k] = val;
    }
    return data;
}

// card-assist 的 4 个端点会真去打 LLM、花用户配额，后端按统一守卫（issue #1479）
// 要求带本地 Origin/CSRF 头，挡掉恶意网页用 no-cors 伪造 JSON 偷跑配额（Codex
// #3328998416）。本页（character_card_manager.html，独立页）不加载 app-prompt-shared.js
// → 没有 window.nekoLocalMutationSecurity，所以这里自包含地拿 X-CSRF-Token：
//   1) 主 app 上下文里若已有统一安全助手就直接用（带刷新逻辑）；
//   2) 独立页兜底：从 /api/config/page_config 取 autostart_csrf_token（与本页已加载的
//      tutorial/core/universal-manager.js 同一套来源），缓存一次即可（per-instance 常量）。
// 取不到就返回空头——后端会 403，_cardAssistFetch 下面的错误通路照常当失败处理，不会
// 静默成功。Origin 头由浏览器对同源 POST 自动带上，与本页 tutorial 上报走的是同一条路。
let _cardAssistCsrfToken = null;
async function _cardAssistCsrfHeaders() {
    try {
        const sec = window.nekoLocalMutationSecurity;
        if (sec && typeof sec.getMutationHeaders === 'function') {
            const h = await sec.getMutationHeaders();
            if (h && typeof h === 'object') return h;
        }
    } catch (_) { /* fall through to page_config */ }
    if (_cardAssistCsrfToken) return { 'X-CSRF-Token': _cardAssistCsrfToken };
    try {
        const r = await fetch('/api/config/page_config', { cache: 'no-store' });
        if (r.ok) {
            const d = await r.json();
            if (d && typeof d.autostart_csrf_token === 'string' && d.autostart_csrf_token) {
                _cardAssistCsrfToken = d.autostart_csrf_token;
                return { 'X-CSRF-Token': _cardAssistCsrfToken };
            }
        }
    } catch (_) { /* 取不到 → 空头，后端 403 由错误通路兜住 */ }
    return {};
}

async function _cardAssistFetch(path, payload) {
    const csrfHeaders = await _cardAssistCsrfHeaders();
    const resp = await fetch(path, {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, csrfHeaders),
        body: JSON.stringify(payload || {}),
    });
    let body = null;
    try { body = await resp.json(); } catch (_) { body = null; }
    if (!resp.ok || !body || body.success === false) {
        const err = (body && (body.message || body.error)) || ('HTTP ' + resp.status);
        const e = new Error(err);
        // 后端目前用 {success, error: "<machine_code>", message: "..."} 形状，
        // body.error 就是机器码；兼容性预留 body.code（其他接口可能这么写）。
        e.code = body && (body.code || body.error);
        throw e;
    }
    return body;
}

// ========== 入口：打开/复用 companion 面板 ==========

function openCardAssistCompanion(form, originalName, isNew) {
    if (window._cardCompanion) {
        const existing = window._cardCompanion;
        if (existing.form === form) {
            // 同一只猫娘 → 把已有面板拉回前台
            _companionSetMinimized(existing, false);
            if (existing.inputEl) {
                try { existing.inputEl.focus(); } catch (_) {}
            }
            return;
        }
        // 切换到不同的猫娘 → 销毁旧面板再开新的
        _companionTeardown(existing);
        _companionDestroy(existing);
        window._cardCompanion = null;
    }
    const state = _companionCreate(form, originalName, isNew);
    window._cardCompanion = state;
    document.body.appendChild(state.overlay);
    _companionAttachFormWatchers(state);
    _companionGreet(state);
    setTimeout(() => { if (state.inputEl) state.inputEl.focus(); }, 80);
}

function _companionCreate(form, originalName, isNew) {
    const state = {
        form: form,
        originalName: originalName,
        isNew: !!isNew,
        devCatName: CARD_COMPANION_DEV_CAT_NAME,
        // 状态机：
        //   awaiting_description → 还在等用户给一句话描述
        //   asking_questions     → AI 抛出了 N 道澄清问题，正在轮流回答
        //   generating           → 正在调 /generate 写草稿（极短瞬态）
        //   chat                 → 草稿已应用，自由对话 + 局部 patch
        mode: 'awaiting_description',
        description: '',
        pendingQuestions: [],
        currentQuestionIdx: 0,
        collectedAnswers: {},
        // /chat 调用时发回去的对话历史（OpenAI 格式）
        chatHistory: [],
        // 表单监视：detach 列表 + 上次快照
        formWatchHandlers: [],
        formWatchSnapshot: {},
        // DOM refs
        overlay: null,
        threadEl: null,
        inputEl: null,
        sendBtnEl: null,
        quickRowEl: null,
        avatarToggleEl: null,
        dragCleanup: null,
        expandedPanelRect: null,
        minimizeTransitionTimer: null,
        minimizedClickSuppressTimer: null,
        suppressNextMinimizedClick: false,
        minimized: false,
        busy: false,
    };
    state.overlay = _companionBuildPanel(state);
    return state;
}

function _companionDestroy(state) {
    if (state.overlay && state.overlay.parentNode) {
        state.overlay.parentNode.removeChild(state.overlay);
    }
}

function _companionSetMinimized(state, minimized) {
    if (!state || !state.overlay) return;
    const overlay = state.overlay;
    if (state.minimizeTransitionTimer) {
        clearTimeout(state.minimizeTransitionTimer);
        state.minimizeTransitionTimer = null;
    }
    if (state.minimizedClickSuppressTimer) {
        clearTimeout(state.minimizedClickSuppressTimer);
        state.minimizedClickSuppressTimer = null;
    }
    overlay.classList.remove('card-companion-collapsing', 'card-companion-expanding');
    const shouldMinimize = !!minimized;
    const currentlyMinimized = !!state.minimized;
    if (shouldMinimize === currentlyMinimized) return;
    state.minimized = shouldMinimize;

    if (shouldMinimize) {
        const panelRect = overlay.getBoundingClientRect();
        const avatarRect = state.avatarToggleEl
            ? state.avatarToggleEl.getBoundingClientRect()
            : panelRect;
        state.expandedPanelRect = {
            left: panelRect.left,
            top: panelRect.top,
            width: panelRect.width,
            height: panelRect.height,
        };
        overlay.style.left = panelRect.left + 'px';
        overlay.style.top = panelRect.top + 'px';
        overlay.style.width = panelRect.width + 'px';
        overlay.style.height = panelRect.height + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
        overlay.style.minWidth = '0px';
        overlay.style.maxWidth = 'none';
        overlay.style.minHeight = '0px';
        overlay.style.maxHeight = 'none';
        overlay.getBoundingClientRect();
        overlay.classList.add('card-companion-collapsing');
        overlay.style.left = avatarRect.left + 'px';
        overlay.style.top = avatarRect.top + 'px';
        overlay.style.width = avatarRect.width + 'px';
        overlay.style.height = avatarRect.height + 'px';
        state.minimizeTransitionTimer = setTimeout(function () {
            overlay.classList.remove('card-companion-collapsing');
            overlay.classList.add('card-companion-minimized');
            state.minimizeTransitionTimer = null;
        }, 260);
    } else {
        const currentRect = overlay.getBoundingClientRect();
        const targetRect = state.expandedPanelRect || currentRect;
        overlay.style.left = currentRect.left + 'px';
        overlay.style.top = currentRect.top + 'px';
        overlay.style.width = currentRect.width + 'px';
        overlay.style.height = currentRect.height + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
        overlay.classList.add('card-companion-expanding');
        overlay.classList.remove('card-companion-minimized');
        overlay.getBoundingClientRect();
        overlay.style.left = targetRect.left + 'px';
        overlay.style.top = targetRect.top + 'px';
        overlay.style.width = targetRect.width + 'px';
        overlay.style.height = targetRect.height + 'px';
        state.minimizeTransitionTimer = setTimeout(function () {
            overlay.classList.remove('card-companion-expanding');
            state.minimizeTransitionTimer = null;
        }, 260);
    }
    if (state.avatarToggleEl) {
        const title = shouldMinimize
            ? _cardAssistT('character.aiCompanionExpand', '展开')
            : _cardAssistT('character.aiCompanionMinimize', '收起');
        state.avatarToggleEl.title = title;
        state.avatarToggleEl.setAttribute('aria-label', title);
        state.avatarToggleEl.setAttribute('aria-expanded', shouldMinimize ? 'false' : 'true');
    }
}

function _companionTeardown(state) {
    if (!state) return;
    // ⚠ 先把 _companionSetBusy 给「未落库新卡」禁掉的 Save 无条件恢复：用户在 LLM 请求
    // 还在飞时点 × 关掉 companion = 主动结束 AI 流程，但 teardown 只置 closed / 摘监听、
    // 迟到响应的 guard 又会直接 return，于是 Save 会一直灰着直到那次请求超时（最多 60s）。
    // 这里在关闭时强制放开，避免表单还在页面上却存不了（Codex #3331627614 / CR #3331629488）。
    try {
        if (state.form) {
            const saveBtn = state.form.querySelector('#save-button');
            if (saveBtn) saveBtn.disabled = false;
        }
    } catch (_) { /* form 可能已 detach，忽略 */ }
    // closed flag：所有 in-flight 的 await 拿到 response 后会 check 这个，
    // 避免 companion 已经关掉/切到别只猫娘了，迟到的 LLM 结果还在静默改表单。
    state.closed = true;
    if (state.minimizeTransitionTimer) {
        clearTimeout(state.minimizeTransitionTimer);
        state.minimizeTransitionTimer = null;
    }
    if (state.minimizedClickSuppressTimer) {
        clearTimeout(state.minimizedClickSuppressTimer);
        state.minimizedClickSuppressTimer = null;
    }
    if (typeof state.dragCleanup === 'function') {
        try { state.dragCleanup(); } catch (_) {}
        state.dragCleanup = null;
    }
    if (state.form && state.formWatchHandlers) {
        state.formWatchHandlers.forEach(function (pair) {
            try { state.form.removeEventListener(pair[0], pair[1]); } catch (_) {}
        });
        state.formWatchHandlers = [];
    }
}

function _companionBuildPanel(state) {
    const overlay = document.createElement('aside');
    overlay.className = 'card-companion-panel';
    overlay.setAttribute('role', 'complementary');
    // 阻止面板上的点击冒泡到外层的"点击外部关闭"之类的逻辑（虽然没有，但
    // 防御一下）
    overlay.addEventListener('click', function (e) { e.stopPropagation(); });
    overlay.addEventListener('click', function (e) {
        if (!state.minimized) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        if (state.suppressNextMinimizedClick) {
            state.suppressNextMinimizedClick = false;
            if (state.minimizedClickSuppressTimer) {
                clearTimeout(state.minimizedClickSuppressTimer);
                state.minimizedClickSuppressTimer = null;
            }
            return;
        }
        _companionSetMinimized(state, false);
    }, true);

    // --- header ---
    const header = document.createElement('div');
    header.className = 'card-companion-header';
    header.title = _cardAssistT('character.aiCompanionDragHint', '拖动窗口');

    const avatar = document.createElement('div');
    avatar.className = 'card-companion-avatar';
    avatar.title = _cardAssistT('character.aiCompanionMinimize', '收起');
    avatar.setAttribute('role', 'button');
    avatar.setAttribute('tabindex', '0');
    avatar.setAttribute('aria-label', avatar.title);
    avatar.setAttribute('aria-expanded', 'true');
    avatar.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        _companionSetMinimized(state, !state.minimized);
    });
    avatar.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        e.stopPropagation();
        _companionSetMinimized(state, !state.minimized);
    });
    state.avatarToggleEl = avatar;
    const avatarImg = document.createElement('img');
    avatarImg.alt = state.devCatName;
    // 不加 ?t=Date.now() cache-bust：companion avatar 是个稳定的静态图，让
    // 浏览器 HTTP cache + 后端 ETag 接管 —— 多次开关 companion / 切换猫娘
    // 不用每次都拉一次图。如果未来要在 card-face 改了之后立刻刷新，应该用
    // 一个稳定的 cache key（如卡面文件 mtime / hash）而不是 Date.now()。
    avatarImg.src = '/api/characters/catgirl/' + encodeURIComponent(state.devCatName) + '/card-face';
    avatarImg.onerror = function () {
        avatarImg.remove();
        const fallback = document.createElement('div');
        fallback.className = 'card-companion-avatar-fallback';
        fallback.textContent = (state.devCatName || 'AI').slice(0, 2);
        avatar.appendChild(fallback);
    };
    avatar.appendChild(avatarImg);

    const titleWrap = document.createElement('div');
    titleWrap.className = 'card-companion-title';
    const nameEl = document.createElement('div');
    nameEl.className = 'card-companion-name';
    nameEl.textContent = state.devCatName;
    const subEl = document.createElement('div');
    subEl.className = 'card-companion-sub';
    subEl.textContent = _cardAssistT('character.aiCompanionSub', '设定捏人助手 · 暂代开发猫');
    titleWrap.appendChild(nameEl);
    titleWrap.appendChild(subEl);

    const headerPaw = document.createElement('img');
    headerPaw.className = 'card-companion-header-paw';
    headerPaw.src = '/static/icons/paw_ui.png';
    headerPaw.alt = '';
    headerPaw.draggable = false;

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'card-companion-close';
    closeBtn.title = _cardAssistT('character.aiCompanionClose', '关闭');
    closeBtn.innerHTML = '&times;';
    closeBtn.addEventListener('click', function () {
        _companionTeardown(state);
        _companionDestroy(state);
        if (window._cardCompanion === state) window._cardCompanion = null;
    });

    header.appendChild(avatar);
    header.appendChild(titleWrap);
    header.appendChild(headerPaw);
    header.appendChild(closeBtn);
    overlay.appendChild(header);
    _companionAttachWindowDrag(state, overlay, header);

    // --- thread ---
    const thread = document.createElement('div');
    thread.className = 'card-companion-thread';
    overlay.appendChild(thread);
    state.threadEl = thread;

    // --- input bar ---
    const inputBar = document.createElement('div');
    inputBar.className = 'card-companion-input-bar';

    const inputRow = document.createElement('div');
    inputRow.className = 'card-companion-input-row';

    const input = document.createElement('textarea');
    input.className = 'card-companion-input';
    input.rows = 1;
    input.placeholder = _cardAssistT('character.aiCompanionPlaceholder',
        '说点什么…（Enter 发送、Shift+Enter 换行）');
    input.addEventListener('input', function () { _cardAssistAutoResize(input); });
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            _companionSubmit(state);
        }
    });
    inputRow.appendChild(input);
    state.inputEl = input;

    const sendBtn = document.createElement('button');
    sendBtn.type = 'button';
    sendBtn.className = 'card-companion-send';
    sendBtn.textContent = _cardAssistT('character.aiCompanionSend', '发送');
    sendBtn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        _companionSubmit(state);
    });
    inputRow.appendChild(sendBtn);
    state.sendBtnEl = sendBtn;

    inputBar.appendChild(inputRow);

    // 一些预设的快捷指令，方便用户不用手动输入
    const quickRow = document.createElement('div');
    quickRow.className = 'card-companion-quick-row';
    const quickActions = [
        { label: _cardAssistT('character.aiCompanionQuickAdvice', '💡 给点建议'),
          send: _cardAssistT('character.aiCompanionQuickAdviceMsg',
                '看一下当前的角色设定，给我几条具体的改进建议吧。'),
          requireMode: 'chat', adviceOnly: true },
        { label: _cardAssistT('character.aiCompanionQuickCheck', '🔍 帮我审一下'),
          send: _cardAssistT('character.aiCompanionQuickCheckMsg',
                '审一下角色设定有没有矛盾、空泛或者重复的地方。'),
          requireMode: 'chat', adviceOnly: true },
        { label: _cardAssistT('character.aiCompanionQuickRegen', '🎲 重写整张卡'),
          send: _cardAssistT('character.aiCompanionQuickRegenMsg',
                '把所有可见字段都按原本的角色定位重新写一遍。'),
          requireMode: 'chat', fullRewrite: true },
    ];
    quickActions.forEach(function (qa) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'card-companion-quick-chip';
        chip.textContent = qa.label;
        chip.dataset.requireMode = qa.requireMode || '';
        chip.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (chip.disabled) return;
            // 「重写整张卡」用 locale 无关的 flag 标明全量重写意图，别让后端去正则匹配本地化
            // 文案——ja/ko/pt/ru/es/zh-TW 的「重写」措辞匹配不到，后端 _complete_full_rewrite_actions
            // 补全通路就不会触发，部分 action 列表会被当部分重写存下去（Codex #3333137718）。
            state._pendingFullRewrite = !!qa.fullRewrite;
            state._pendingAdviceOnly = !!qa.adviceOnly;
            input.value = qa.send;
            _companionSubmit(state);
        });
        quickRow.appendChild(chip);
    });
    inputBar.appendChild(quickRow);
    state.quickRowEl = quickRow;

    overlay.appendChild(inputBar);

    return overlay;
}

function _companionAttachWindowDrag(state, overlay, handle) {
    if (!overlay || !handle) return;
    let dragging = false;
    let startClientX = 0;
    let startClientY = 0;
    let startLeft = 0;
    let startTop = 0;
    let dragLeft = 0;
    let dragTop = 0;
    let activePointerId = null;
    let movedEnoughToDrag = false;

    function suppressMinimizedClickOnce() {
        state.suppressNextMinimizedClick = true;
        if (state.minimizedClickSuppressTimer) {
            clearTimeout(state.minimizedClickSuppressTimer);
        }
        state.minimizedClickSuppressTimer = setTimeout(function () {
            state.suppressNextMinimizedClick = false;
            state.minimizedClickSuppressTimer = null;
        }, 600);
    }

    function clampWindow(left, top) {
        const rect = overlay.getBoundingClientRect();
        const margin = 8;
        const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
        const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
        return {
            left: Math.min(Math.max(left, margin), maxLeft),
            top: Math.min(Math.max(top, margin), maxTop),
        };
    }

    function placeWindow(left, top) {
        const next = clampWindow(left, top);
        overlay.style.left = next.left + 'px';
        overlay.style.top = next.top + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
    }

    function onPointerMove(e) {
        if (!dragging) return;
        if (activePointerId !== null && e.pointerId !== undefined && e.pointerId !== activePointerId) return;
        const deltaX = e.clientX - startClientX;
        const deltaY = e.clientY - startClientY;
        if (state.minimized && !movedEnoughToDrag && Math.hypot(deltaX, deltaY) > 5) {
            movedEnoughToDrag = true;
        }
        e.preventDefault();
        const next = clampWindow(startLeft + deltaX, startTop + deltaY);
        dragLeft = next.left;
        dragTop = next.top;
        overlay.style.left = dragLeft + 'px';
        overlay.style.top = dragTop + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
    }

    function stopDrag() {
        if (!dragging) return;
        const wasMinimizedDrag = state.minimized && movedEnoughToDrag;
        dragging = false;
        activePointerId = null;
        overlay.style.left = dragLeft + 'px';
        overlay.style.top = dragTop + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
        overlay.style.transform = '';
        movedEnoughToDrag = false;
        overlay.classList.remove('card-companion-dragging');
        window.requestAnimationFrame(function () {
            if (!dragging) overlay.style.transition = '';
        });
        if (wasMinimizedDrag) {
            suppressMinimizedClickOnce();
        }
        window.removeEventListener('pointermove', onPointerMove);
        window.removeEventListener('pointerup', stopDrag);
        window.removeEventListener('pointercancel', stopDrag);
    }

    function onPointerDown(e) {
        if (e.button !== undefined && e.button !== 0) return;
        const interactive = e.target && e.target.closest && e.target.closest('button, a, input, textarea, select, [role="button"]');
        if (interactive && !state.minimized) return;
        const rect = overlay.getBoundingClientRect();
        dragging = true;
        activePointerId = e.pointerId;
        movedEnoughToDrag = false;
        startClientX = e.clientX;
        startClientY = e.clientY;
        startLeft = rect.left;
        startTop = rect.top;
        dragLeft = startLeft;
        dragTop = startTop;
        overlay.style.transition = 'none';
        overlay.style.left = startLeft + 'px';
        overlay.style.top = startTop + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
        overlay.style.transform = '';
        overlay.classList.add('card-companion-dragging');
        if (handle.setPointerCapture && activePointerId !== null) {
            try { handle.setPointerCapture(activePointerId); } catch (_) {}
        }
        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', stopDrag);
        window.addEventListener('pointercancel', stopDrag);
        e.preventDefault();
    }

    function onResize() {
        const rect = overlay.getBoundingClientRect();
        placeWindow(rect.left, rect.top);
    }

    handle.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('resize', onResize);
    state.dragCleanup = function () {
        stopDrag();
        handle.removeEventListener('pointerdown', onPointerDown);
        window.removeEventListener('resize', onResize);
    };
}

function _companionGreet(state) {
    // 入口分流：
    //   - 空白卡（profile name 之外没有任何已填字段）→ 走多轮 Asking 流程
    //     （awaiting_description → clarify → generate → chat）
    //   - 已经有 tag 的卡 → 跳过 Asking，直接进入 chat（AI Design）。
    //     问候里列出现有字段做摘要，提示用 "💡/🔍" quick chip 让 AI 基于
    //     已有 tag 给建议；不预先消耗一次 LLM 调用。
    //
    // _cardAssistCollectCurrentFormData 已经用 _cardAssistIsReservedKey 过滤掉保留字段
    // （档案名/voice_id/lighting 等），所以只剩"真·角色设定字段"。空对象 → 空白卡。
    const existingData = _cardAssistCollectCurrentFormData(state.form);
    const filledKeys = Object.keys(existingData);
    if (!filledKeys.length) {
        _companionAppendAssistant(state,
            _cardAssistT('character.aiCompanionGreeting',
                '喵～我是设定捏人助手，先告诉我你想要一只什么样的猫娘呀？一句话描述就好，我会再问几个细节，然后帮你把整张卡写好喵。'));
        // mode 默认就是 awaiting_description（_companionCreate 里初始化的）
    } else {
        _companionEnterDesignMode(state, existingData, filledKeys);
    }
    _companionUpdateQuickAvailability(state);
}

// 已有 tag 的卡：跳过 Asking，直接进 chat。第一条 assistant 气泡把现有字段
// 列一下，让用户和 AI 都对得上号，再提示一下后续怎么交互。
function _companionEnterDesignMode(state, existingData, filledKeys) {
    state.mode = 'chat';
    // 太多字段时只列前 12 条，剩下的折叠成"…还有 N 项"，避免气泡铺一屏
    const MAX_LIST = 12;
    const head = filledKeys.slice(0, MAX_LIST);
    const lines = head.map(function (k) {
        return '• ' + k + '：' + _companionTruncate(String(existingData[k] || ''), 30);
    });
    if (filledKeys.length > MAX_LIST) {
        // i18next 风格的 {{n}} 占位符，跟 repo 里其它 60+ 处 {{var}} 一致。
        // _cardAssistT 把 vars 透传给 window.t(key, vars) → i18next 标准插值。
        // fallback 字符串里把数字直接内联，避免 i18next 没加载时 {{n}} 字面量
        // 漏出给用户。
        const remaining = filledKeys.length - MAX_LIST;
        lines.push('• ' + _cardAssistT('character.aiCompanionDesignMore',
            '…（还有 ' + remaining + ' 项）',
            { n: remaining }));
    }
    const greeting = _cardAssistT('character.aiCompanionDesignGreeting',
        '喵～我是设定捏人助手。看到你这只猫娘已经有点雏形啦，我先看看你已经填了什么：') +
        '\n\n' + lines.join('\n') +
        '\n\n' + _cardAssistT('character.aiCompanionDesignAsk',
        '想让我帮你做点啥呢？直接告诉我就行（比如「让她更傲娇一点」、「招牌台词换一句」），也可以点下面的「🔍 帮我审一下」让我整体看看~');
    _companionAppendAssistant(state, greeting);
    // 注：不预先塞 chatHistory，让 quick chip 触发的"帮我审一下"消息成为第一条
    // user 输入，对话上下文更自然；current_card / target_field_keys 在每次
    // /chat 调用前都重新收集，AI 永远看得到最新表单状态。
}

function _companionUpdateQuickAvailability(state) {
    // companion 已 teardown（state.closed）后绝不再碰表单控件：teardown 已无条件把未落库新卡
    // 的 Save 恢复了，迟到的 in-flight finally（如 _companionRunClarify 的 _companionSetBusy(false)）
    // 不能借这里按「未落库新卡 + 非 chat 模式」规则把它又禁回去——否则 companion 已销毁、详情
    // 面板还开着，用户再也点不动 Save（Codex #3333702549）。
    if (!state || state.closed) return;
    // 详情表单 Save 的禁用集中在这里（busy 变化 + 每次 mode 切换都会调到，是唯一同步点）。
    // 防竞态：**未落库新卡**在「打 LLM（busy）」或「澄清问答流程（asking_questions，答最后一题
    // 就触发生成）」时禁掉 Save——堵住「用户在草稿还没生成完的窗口里手动 Save 把新卡建出来」与
    // 生成竞态：那一下会用旧快照建卡，且若走 popup / 有卡面分支会 closeCatgirlPanel 把面板连同
    // AI 字段一起带走、事后救不回（Codex #3329022313 / #3329817833 / #3333137733）。
    // ⚠ awaiting_description（首启 + 澄清失败回退）**不禁**：此刻没有任何在途生成，禁 Save 是过宽
    // 的——零竞态收益，只会把用户困住：澄清失败（如没配 API）后想放弃 AI、手动建卡却点不动，得先
    // 关 companion 才行（Codex #3333683160）。手动 Save 真撞上后续生成的竞态，本就由 busy +
    // _companionTryAutoSave 的 wait/replay 兜底，不靠在这里禁 awaiting_description 的 Save。进入
    // chat 模式（草稿已落表单）同样放开，save-then-chat 由 _companionRunChat 的 dataset.submitting
    // 短路守住。已落库卡完全不禁。关 companion 时 _companionTeardown 会无条件恢复，不会卡死。
    try {
        if (state.form) {
            const sb = state.form.querySelector('#save-button');
            if (sb) {
                const unsavedNewCard = state.isNew === true && !state.form._autoCreated;
                sb.disabled = unsavedNewCard && (!!state.busy || state.mode === 'asking_questions');
            }
        }
    } catch (_) { /* form 可能已 detach，忽略 */ }
    if (!state.quickRowEl) return;
    state.quickRowEl.querySelectorAll('.card-companion-quick-chip').forEach(function (chip) {
        const req = chip.dataset.requireMode || '';
        const ok = !req || state.mode === req;
        chip.disabled = !ok || state.busy;
        chip.classList.toggle('card-companion-quick-chip-disabled', !ok);
    });
}

function _companionSetBusy(state, busy) {
    state.busy = !!busy;
    if (state.sendBtnEl) state.sendBtnEl.disabled = !!busy;
    if (state.inputEl) state.inputEl.disabled = !!busy;
    _companionUpdateQuickAvailability(state);  // 内含详情表单 Save 的禁用同步
}

async function _companionSubmit(state) {
    if (state.busy) return;
    const txt = (state.inputEl && state.inputEl.value ? state.inputEl.value : '').trim();
    if (!txt) return;
    state.inputEl.value = '';
    _cardAssistAutoResize(state.inputEl);
    await _companionHandleUserText(state, txt);
}

async function _companionHandleUserText(state, text) {
    _companionAppendUser(state, text);
    if (state.mode === 'awaiting_description') {
        state.description = text;
        await _companionRunClarify(state);
    } else if (state.mode === 'asking_questions') {
        // 用户没点 chip，而是在输入框敲了自定义答案 → 当作当前问题的回答
        const q = state.pendingQuestions[state.currentQuestionIdx];
        if (q) {
            state.collectedAnswers[q.id] = text;
            state.currentQuestionIdx++;
            _companionRenderNextQuestion(state);
        }
    } else {
        // chat 模式：进 /api/card-assist/chat
        state.chatHistory.push({ role: 'user', content: text });
        await _companionRunChat(state);
    }
}

async function _companionRunClarify(state) {
    _companionSetBusy(state, true);
    const typing = _companionAppendTyping(state);
    try {
        // form 找不到（用户切走了详情面板 / 关掉了）→ 早 return，不要白白吃一次
        // LLM 调用。同样的 short-circuit 在 _companionRunGenerate / _companionRunChat
        // 也加了，否则即使后端把 reply + actions 返回回来，前端 apply 阶段也只能
        // 弹「⚠ 角色表单不在屏幕上了」，钱白花、用户体验冲突。
        if (!_companionEnsureLiveForm(state)) {
            typing.remove();
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionFormGone',
                    '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。'));
            return;
        }
        const resp = await _cardAssistFetch('/api/card-assist/clarify', {
            description: state.description,
            current_card: _cardAssistCollectCurrentFormData(state.form),
            target_field_keys: _cardAssistCollectFieldKeys(state.form),
            locale: _cardAssistCurrentLocale(),
        });
        // 用户在 in-flight 期间关掉了 companion → 静默丢掉迟到的结果，绝不
        // 静默地把字段写进 form（form 还活着，但 user intent 已是"取消"）。
        if (state.closed) return;
        typing.remove();
        state.pendingQuestions = resp.questions || [];
        state.currentQuestionIdx = 0;
        if (!state.pendingQuestions.length) {
            // 没出问题就直接跳到 generate
            await _companionRunGenerate(state);
            return;
        }
        state.mode = 'asking_questions';
        _companionUpdateQuickAvailability(state);
        _companionRenderNextQuestion(state);
    } catch (err) {
        typing.remove();
        _companionAppendError(state, err);
        state.mode = 'awaiting_description';
        _companionUpdateQuickAvailability(state);
    } finally {
        _companionSetBusy(state, false);
    }
}

function _companionRenderNextQuestion(state) {
    if (state.currentQuestionIdx >= state.pendingQuestions.length) {
        _companionRunGenerate(state);
        return;
    }
    const q = state.pendingQuestions[state.currentQuestionIdx];
    const total = state.pendingQuestions.length;
    const prefix = '【' + (state.currentQuestionIdx + 1) + '/' + total + ' · ' + (q.header || '') + '】';
    // 捕获 chip 创建时的「这是第几题」snapshot；用户后续通过输入框回答 / 点更新
    // 的 chip 推进进度后，老 bubble 上的 chip 仍可见可点。stale chip 点击如果
    // 不防一手，会把旧答案塞进 collectedAnswers 并再次 ++currentQuestionIdx —
    // 跳过下一题、覆盖原本写好的答案。
    const ownIdx = state.currentQuestionIdx;
    const ownQid = q.id;
    _companionAppendAssistant(state, q.label, {
        prefix: prefix,
        chips: (q.options || []).map(function (opt) {
            return {
                label: opt,
                onClick: function () {
                    // 已经被其他途径推进过 → stale chip，no-op
                    if (state.currentQuestionIdx !== ownIdx) return;
                    state.collectedAnswers[ownQid] = opt;
                    _companionAppendUser(state, opt);
                    state.currentQuestionIdx++;
                    _companionRenderNextQuestion(state);
                }
            };
        }),
        allowCustom: q.allowCustom !== false,
        // 同 chip 的 ownIdx 防 race —— 自定义输入框按 Enter 也要先确认这条
        // bubble 还对应当前题，否则老 bubble 的输入会把答案塞给"现在的题"
        // 并再次 ++idx 跳过下一题。
        customSubmit: function (v) {
            if (state.currentQuestionIdx !== ownIdx) return;
            state.collectedAnswers[ownQid] = v;
            _companionAppendUser(state, v);
            state.currentQuestionIdx++;
            _companionRenderNextQuestion(state);
        },
    });
}

async function _companionRunGenerate(state) {
    _companionSetBusy(state, true);
    const typing = _companionAppendTyping(state,
        _cardAssistT('character.aiCompanionGenerating', '正在帮你写草稿…'));
    try {
        if (!_companionEnsureLiveForm(state)) {
            typing.remove();
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionFormGone',
                    '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。'));
            return;
        }
        const resp = await _cardAssistFetch('/api/card-assist/generate', {
            description: state.description,
            answers: state.collectedAnswers,
            current_card: _cardAssistCollectCurrentFormData(state.form),
            target_field_keys: _cardAssistCollectFieldKeys(state.form),
            locale: _cardAssistCurrentLocale(),
        });
        // closed-companion guard：用户在 in-flight 期间关掉了 companion → 绝
        // 不静默地往 form 写字段并 autoSave，关闭即取消。
        if (state.closed) return;
        typing.remove();
        const fields = resp.fields || {};
        const fieldKeys = Object.keys(fields);
        if (!fieldKeys.length) {
            _companionAppendAssistant(state,
                _cardAssistT('character.aiCompanionEmptyDraft',
                    '草稿空空的喵，我们再聊几句吧～'));
            state.mode = 'chat';
            _companionUpdateQuickAvailability(state);
            return;
        }
        // 直接应用到表单
        // ⚠ /generate 的 await 期间 state.form 可能被 rebuild（用户改名/保存先完成、旧
        // form detach）。这里 apply 前再 ensure 一次，把 draft 写进**当前活着**的 form；
        // 接不上就报 form-gone，绝不写进 detached DOM——否则字段写了个寂寞，紧接着的
        // _companionTryAutoSave rebind 到新 form 又没有 in-flight replay 通路，会把不带
        // draft 的表单存下去、助手却报「已应用」（Codex #3332998069）。
        if (!_companionEnsureLiveForm(state)) {
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionFormGone',
                    '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。'));
            return;
        }
        const applyRes = _cardAssistApplyToForm(state.form, fields, fieldKeys,
                                                 state.originalName, state.isNew);
        _companionRefreshFormSnapshot(state);
        await _companionTryAutoSave(state);
        // 用一条 assistant 气泡总结。区分 update vs create，让用户能立即看出
        // LLM 用的 key 是不是和表单里的字段对上号了 —— 如果 created 一大堆，
        // 说明 LLM 没听话用错 key，老字段没被覆盖。
        const lines = [];
        if (applyRes.updated.length) {
            lines.push(_cardAssistT('character.aiCompanionDraftUpdated', '✎ 改写') + '：' +
                applyRes.updated.map(function (k) { return k; }).join('、'));
        }
        if (applyRes.created.length) {
            lines.push(_cardAssistT('character.aiCompanionDraftCreated', '+ 新增') + '：' +
                applyRes.created.map(function (k) { return k; }).join('、'));
        }
        if (applyRes.skipped.length) {
            lines.push(_cardAssistT('character.aiCompanionDraftSkipped', '⤬ 跳过') + '：' +
                applyRes.skipped.map(function (k) { return k; }).join('、'));
        }
        const msg = _cardAssistT('character.aiCompanionDraftReady',
            '草稿写好啦，已经填进表单了喵～你随时改、随时跟我说要调啥都行：') +
            (lines.length ? '\n' + lines.join('\n') : '');
        _companionAppendAssistant(state, msg);
        // 进入聊天模式，把 description + 生成结果当上下文塞进 chatHistory。
        // seed 通过 i18n 走当前 locale —— 之前硬编码中文会让英文 locale 用户
        // 走完澄清问答后被 LLM 镜像成中文回复。
        state.mode = 'chat';
        const seedDescribe = _cardAssistT('character.aiCompanionSeedDescribe',
            'Generate a catgirl card based on this description: ');
        const seedGenerated = _cardAssistT('character.aiCompanionSeedGenerated',
            'Generated and filled into the form: ');
        state.chatHistory.push({ role: 'user',
            content: seedDescribe + state.description });
        state.chatHistory.push({ role: 'assistant',
            content: seedGenerated + JSON.stringify(fields) });
        _companionUpdateQuickAvailability(state);
    } catch (err) {
        typing.remove();
        _companionAppendError(state, err);
        state.mode = 'chat';
        _companionUpdateQuickAvailability(state);
    } finally {
        _companionSetBusy(state, false);
    }
}

async function _companionRunChat(state) {
    _companionSetBusy(state, true);
    // ⚠ full_rewrite 一次性消费：在任何 early-return（form-gone 等）**之前**就读出并清掉，
    // 否则这次点「重写整张卡」若撞上 form rebuild、没接上 live form 而提前 return，标记会
    // 残留、被下一条普通聊天消息误当成整卡重写（CodeRabbit #3333410664）。
    const fullRewrite = state._pendingFullRewrite === true;
    state._pendingFullRewrite = false;
    // 「给建议 / 帮我审一下」属于只读分析，不该顺手自动改表单；和 full_rewrite 一样做一次性消费，
    // 避免某次 advice 请求 early-return 后把标记泄漏到下一条普通聊天消息（本次回归）。
    const adviceOnly = state._pendingAdviceOnly === true;
    state._pendingAdviceOnly = false;
    const typing = _companionAppendTyping(state);
    try {
        if (!_companionEnsureLiveForm(state)) {
            typing.remove();
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionFormGone',
                    '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。'));
            return;
        }
        // 新卡首存正在飞行中（用户在 chat 模式点了 Save、又紧接着发消息）：别在它收尾前打
        // LLM / 改表单。否则 saveCatgirlFromPanel 已用「编辑前的快照」序列化，这次应用的 chat
        // 编辑会在「首存成功关面板 / 开卡面」分支里随面板一起没掉、_companionTryAutoSave 又
        // rebind 不上（Codex #3333457418）。短路并提示等保存收尾——用户消息还在 chatHistory 里，
        // 存好后再发一次即可（生成/问答流程下 Save 本就被禁，这里只兜 chat 模式这条路）。
        if (state.isNew && !state.form._autoCreated
                && state.form.dataset.submitting === 'true') {
            typing.remove();
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionSaveInProgress',
                    '⏳ 正在保存这张新卡，存好了我再帮你改喵～稍等一下再发一次吧。'));
            return;
        }
        // full_rewrite 已在函数开头一次性读出并清掉（见上），这里直接透传（Codex #3333137718）。
        const resp = await _cardAssistFetch('/api/card-assist/chat', {
            messages: state.chatHistory,
            current_card: _cardAssistCollectCurrentFormData(state.form),
            target_field_keys: _cardAssistCollectFieldKeys(state.form),
            dev_cat_name: state.devCatName,
            locale: _cardAssistCurrentLocale(),
            advice_only: adviceOnly,
            full_rewrite: fullRewrite,
        });
        // closed-companion guard：同 clarify/generate，关掉 companion 之后
        // 迟到的 reply + actions 都丢弃，不再静默改 form。
        if (state.closed) return;
        typing.remove();
        const reply = (resp.reply || '').trim();
        if (reply) {
            _companionAppendAssistant(state, reply);
            state.chatHistory.push({ role: 'assistant', content: reply });
        }
        const actions = Array.isArray(resp.actions) ? resp.actions : [];
        const summary = _companionApplyActions(state, actions);
        if (summary) {
            _companionAppendSystem(state, summary);
            _companionRefreshFormSnapshot(state);
            await _companionTryAutoSave(state);
        }
    } catch (err) {
        typing.remove();
        _companionAppendError(state, err);
    } finally {
        _companionSetBusy(state, false);
    }
}

function _companionApplyActions(state, actions) {
    if (!actions || !actions.length) return '';
    // form 可能已经被 buildCatgirlDetailForm 重新渲染过 → 尝试按 id 重新接上当前
    // 活着的同名表单。接不上才真的报错。
    if (!_companionEnsureLiveForm(state)) {
        return _cardAssistT('character.aiCompanionFormGone',
            '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。');
    }
    const updatedTags = [];
    const createdTags = [];
    const removedTags = [];
    const skippedTags = [];
    actions.forEach(function (a) {
        if (!a || !a.type || !a.field_key) return;
        if (_cardAssistIsReservedKey(a.field_key)) {
            skippedTags.push(a.field_key);
            return;
        }
        if (a.type === 'remove_field') {
            const ta = _findFieldTextareaByName(state.form, a.field_key);
            if (ta) {
                const wrapper = ta.closest('.field-row-wrapper');
                if (wrapper) wrapper.remove();
                removedTags.push(a.field_key);
            } else {
                skippedTags.push(a.field_key);
            }
            return;
        }
        // refine_field 严格要求字段已存在 —— LLM 偶尔会把目标字段名打错 / 大
        // 小写漂移（"Personality archetype" vs "Personality Archetype"），
        // 如果直接走 ApplyToForm 那条「找不到就创建」分支，会静默新建一条
        // 重复字段然后被 autoSave 持久化，留下脏 schema。把这种 typo case
        // 当 skipped 处理，让用户能看见。
        // add_field 反过来：本意就是新增，找不到才正常。
        if (a.type === 'refine_field') {
            if (!_findFieldTextareaByName(state.form, a.field_key)) {
                skippedTags.push(a.field_key);
                return;
            }
        }
        const single = {};
        single[a.field_key] = a.value;
        const res = _cardAssistApplyToForm(state.form, single, [a.field_key],
                                           state.originalName, state.isNew);
        res.updated.forEach(function (k) { updatedTags.push(k); });
        res.created.forEach(function (k) { createdTags.push(k); });
        res.skipped.forEach(function (k) { skippedTags.push(k); });
    });
    // 把刚 apply 出来的 4 类结果挂到 state，供 _companionTryAutoSave 在 wait→
    // rebuild 路径上 replay 时区分"该重写的字段"和"该重新删除的字段" —— snapshot
    // 自己只记得到值，不记得到"故意删除"这种 intent，必须显式传过去。
    state._lastApplyResult = {
        updated: updatedTags.slice(),
        created: createdTags.slice(),
        removed: removedTags.slice(),
        skipped: skippedTags.slice(),
    };
    // remove_field 这条分支直接删 DOM 行，不像 _cardAssistApplyToForm 末尾那样会把
    // Save / Cancel 亮出来。已保存卡这两个按钮默认 display:none（见 buildCatgirlDetailForm
    // 里 `if (!isNew) ...style.display = 'none'`），一旦后面 _companionTryAutoSave 失败、
    // 系统气泡提示「请手动点 Save 重试」时，按钮却还藏着 → 用户无从重试，被删字段在
    // reload 后复活。所以只要真发生了删除（纯 remove、没有 update/create 顺带亮按钮的场景）
    // 就把 Save / Cancel 显式亮出来，让那条 fallback 提示是可操作的。
    if (removedTags.length && state.form) {
        const sb = state.form.querySelector('#save-button');
        const cb = state.form.querySelector('#cancel-button');
        if (sb) sb.style.display = '';
        if (cb) cb.style.display = '';
    }
    const parts = [];
    if (updatedTags.length) parts.push('✎ ' + updatedTags.join(', '));
    if (createdTags.length) parts.push('+ ' + createdTags.join(', '));
    if (removedTags.length) parts.push('🗑 ' + removedTags.join(', '));
    if (skippedTags.length) parts.push('⤬ ' + skippedTags.join(', ') +
        '（' + _cardAssistT('character.aiCompanionSkipped', '未匹配/已保留') + '）');
    if (!parts.length) return '';
    return _cardAssistT('character.aiCompanionAppliedPrefix', '已应用：') + parts.join('  ·  ');
}

async function _companionTryAutoSave(state) {
    // 任何"对 form 动手"的入口前都先确保 form 还活着，否则保存的是个 detached
    // 表单 → FormData 拿到空值 / PUT 把字段全清光。
    if (!_companionEnsureLiveForm(state)) return;

    // ⚠ 关键：空白卡（state.isNew === true 且后端还没收到过 POST）下，绝对不能
    // 调 saveCatgirlFromPanel —— 它的"首次保存成功"分支会触发 closeCatgirlPanel()
    // （见 character_card_manager.js ~6331/6334），把整个详情面板收起来，用户跟
    // companion 聊到一半画面被甩走。
    //
    // 解决方案：新卡用户必须先**手动**点一次 Save 把卡建出来；之后 state.isNew
    // 翻成 false（或者 form._autoCreated 标记起来），auto-save 就可以接管走 PUT。
    // 手动 Save 后老的 saveCatgirlFromPanel 流程会 buildCatgirlDetailForm
    // 重新渲染表单，companion 会在下一次 _companionEnsureLiveForm 时自动跟过去。
    // ⚠ 例外：若此刻**已有一次手动 Save 在飞行中**（dataset.submitting === 'true'），说明用户
    // 正在把这张新卡建出来。这时绝不能直接 return —— 否则在「用户点 Save 时 /generate 还在飞」
    // 的竞态里，那次 Save 已经用「AI 写字段**之前**」的旧快照序列化好了，保存成功后会用旧快照
    // rebuild / 关面板，把 AI 刚写进去、用户已看到「已应用」的字段静默丢掉（Codex #3329022313）。
    // 所以这种情况要落到下面的 wait/replay：等那次 Save 收尾、把 AI 字段 replay 到 rebuild 出来
    // 的已保存卡表单上再存一遍。等待之后会再确认卡是否真落库（见 saveCatgirlFromPanel 前的二次 guard）。
    if (state.isNew && !state.form._autoCreated
            && state.form.dataset.submitting !== 'true') {
        // 一只新卡里只提示一次，避免 AI 改几次就刷几条 toast。
        if (!state._warnedNewCardSaveHint) {
            state._warnedNewCardSaveHint = true;
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionNewCardSaveHint',
                    '💡 新卡片要先点一下下面的 Save 才能让我自动保存，' +
                    '现在字段都已经写进表单了喵～'));
        }
        return;
    }
    if (typeof saveCatgirlFromPanel === 'function') {
        // saveCatgirlFromPanel 自己已经 toast 了错误（HTTP 非 2xx / success:false
        // / 网络异常都会经 showMessage(... ,'error')），但它**不抛**而是 `return false`。
        // 之前 catch{} 把 return value 丢了 —— 用户在 toast 之外看到 companion 的
        // 系统气泡仍然显示「✎ 已应用」，体感是「字段改了 + 已保存」，实际上后端
        // 拒绝了。这里读回 ok 值，false 时再补一条 system 错误气泡兜底。
        //
        // ⚠ saveCatgirlFromPanel 的 `return false` 有**两种**语义混用：
        //   (1) form.dataset.submitting === 'true' 的 debounce skip —— 表示有
        //       另一个 save 正在飞行中；
        //   (2) HTTP / validation / 网络异常的真·失败。
        //
        // 上一轮 (3bf0b171) 把 (1) 当失败误报；上一轮的 fix (722ada87) 简单粗暴
        // 改成 "in-flight 就 return"。但**那条捷径会丢数据**：
        //   T0: 用户手动 Save → saveCatgirlFromPanel 用 T0 的 form 数据起 POST
        //   T1: companion 把 AI 的新字段写进**同一个**form 的 textarea
        //   T2: companion tryAutoSave → 看到 dataset.submitting='true' → return
        //   T3: 后端 success → buildCatgirlDetailForm() 用 server 返回的数据
        //       (T0 的快照) 重建 form → companion 写进去的 T1 字段被抹掉
        //   T4: 既没存进后端、也不在 form 里、companion 也不知道要重试 —— 静默丢失
        //
        // 修法：不再 return，改成**等 in-flight save 收尾**（轮询 dataset.submitting
        // 翻 'false'），然后 _companionEnsureLiveForm 接上可能 rebuild 出来的新
        // form。如果 form 实例真的换了，比对 formWatchSnapshot（这次 tryAutoSave
        // 之前刚 refresh 过、代表 companion 期望的状态）和新 form 的实际字段值，
        // 把丢失/被抹掉的字段 replay 一遍，然后再调 saveCatgirlFromPanel 把
        // companion 的修改真正落盘。
        // ⚠ snapshot 和 lastApplyResult 必须在 wait/rebind 之前**defensive 拷贝**：
        // wait loop 里的每次 _companionEnsureLiveForm 在切到新 form 后会调
        // _companionAttachFormWatchers，那个函数会重写 state.formWatchSnapshot
        // = _cardAssistCollectCurrentFormData(<新 form>)，把"companion 期望状态"
        // 直接覆盖成"后端刚 rebuild 出来的旧值"。等下面的 diff 拿到时 snapshot
        // 已经和当前 form 一模一样、永远比不出差异、replay 哑火 → 数据丢失。
        // lastApplyResult 同理在某些重入路径上可能被覆盖，也先快照下来。
        const formBeforeWait = state.form;
        const expectedSnapshot = Object.assign({}, state.formWatchSnapshot || {});
        const lastApply = state._lastApplyResult || {};
        const expectedRemovals = (lastApply.removed || []).slice();
        const WAIT_TIMEOUT_MS = 8000;
        const POLL_MS = 100;
        let waited = 0;
        // ⚠ 必须盯 **formBeforeWait 自己** 的 dataset.submitting 清掉，而不是 state.form：
        // 那次手动保存的 PUT 一旦成功就会触发 buildCatgirlDetailForm 重建，旧 form 随之
        // detach、_companionEnsureLiveForm 会把 state.form 重绑到**新** form，新 form 的
        // submitting 从没被置位 → 若拿 state.form 当条件，循环会在重建一发生就**提前 break**。
        // 但此时手动保存还没收尾（saveCatgirlFromPanel 的 finally 清 submitting 之前，已保存卡
        // 分支还要 await loadCharacterData + 再跑一次 buildCatgirlDetailForm）。companion 抢在
        // 它收尾前 replay + 自存，就会被那次后续重建覆盖 → 退回这条 wait/replay 本来要消灭的
        // 静默丢失（Codex #3328951294 P1）。saveCatgirlFromPanel 的 finally 清的是原始 form
        // 引用（== formBeforeWait）的 submitting，detach 之后该 dataset 仍可读，所以这里安全。
        while (waited < WAIT_TIMEOUT_MS && formBeforeWait.dataset.submitting === 'true') {
            await new Promise(function (r) { setTimeout(r, POLL_MS); });
            waited += POLL_MS;
        }
        // 超时仍在 submitting 就放弃，避免 hang 死 —— 但**不能静默退出**：那次慢保存收尾时
        // 会用较旧的请求快照重建表单，把 companion 刚写进去的改动/删除覆盖掉，而用户只看到
        // 之前那条「已应用」气泡、误以为存好了（Codex #3328963563）。所以补一条失败气泡讲清楚，
        // 并尽量把 Save/Cancel 亮出来给一个手动兜底入口（若那次保存最终失败、没重建表单，这俩
        // 按钮就是真正的重试路径）。
        if (formBeforeWait.dataset.submitting === 'true') {
            console.warn('[card-companion] auto-save waited 8s for in-flight save, giving up');
            const tsb = formBeforeWait.querySelector('#save-button');
            const tcb = formBeforeWait.querySelector('#cancel-button');
            if (tsb) tsb.style.display = '';
            if (tcb) tcb.style.display = '';
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionAutoSaveFailed',
                    '⚠ 自动保存失败了喵——表单里的字段已经写好，请看下弹出的错误提示再手动点 Save 重试。'));
            return;
        }
        // 手动保存确认收尾后，再接上它可能 rebuild 出来的新 form（接不上 = 面板没了 → 放弃）
        if (!_companionEnsureLiveForm(state)) return;
        // form 实例换过 → 用 BEFORE-WAIT 那份 snapshot + removed 名单把 companion
        // 期望的状态重新灌进新 form。两条独立通道：
        //   1) 字段值 replay：跳过那些「companion 故意删除」的 key，避免把刚删
        //      掉的字段又写回去；只 apply 真正有 diff 的 key（避免误覆盖 server）。
        //   2) 删除 replay：对 server rebuild 后又冒出来的 removed 字段重新执行
        //      DOM 删除。snapshot 自己不携带"我删过它"的信息，所以必须靠
        //      expectedRemovals 显式记下。
        if (state.form !== formBeforeWait) {
            const removalSet = expectedRemovals.length
                ? new Set(expectedRemovals) : null;
            if (Object.keys(expectedSnapshot).length) {
                const replayValues = {};
                const replayKeys = [];
                Object.keys(expectedSnapshot).forEach(function (k) {
                    if (removalSet && removalSet.has(k)) return;
                    const ta = _findFieldTextareaByName(state.form, k);
                    const cur = ((ta && ta.value) || '').trim();
                    const want = (expectedSnapshot[k] || '').trim();
                    if (want && cur !== want) {
                        replayValues[k] = expectedSnapshot[k];
                        replayKeys.push(k);
                    }
                });
                if (replayKeys.length) {
                    _cardAssistApplyToForm(state.form, replayValues, replayKeys,
                        state.originalName, state.isNew);
                }
            }
            expectedRemovals.forEach(function (k) {
                const ta = _findFieldTextareaByName(state.form, k);
                if (!ta) return;
                const wrapper = ta.closest('.field-row-wrapper');
                if (wrapper) wrapper.remove();
            });
            // replay 里若含「删除」：rebuilt 的已保存卡表单 Save/Cancel 默认是藏着的
            //（手动 save 成功后又被隐藏）。若只 replay 了删除、没 replay 任何字段值，
            // 上面的 _cardAssistApplyToForm 不会被调到、不会顺带亮按钮；紧接着的
            // autosave 一旦失败、提示用户「手动点 Save 重试」时按钮却不可见 → 删除丢失
            //（Codex #3328942158）。跟直连 remove_field 路径一样，这里把 Save/Cancel 亮出。
            if (expectedRemovals.length) {
                const rsb = state.form.querySelector('#save-button');
                const rcb = state.form.querySelector('#cancel-button');
                if (rsb) rsb.style.display = '';
                if (rcb) rcb.style.display = '';
            }
            // 重新刷一遍 watch snapshot 把 "我们刚 replay 完的状态" 当成新的
            // baseline，避免后面 form-watch listener 把 replay 误判成"用户手改"
            // 弹一堆系统气泡。
            _companionRefreshFormSnapshot(state);
        }
        // 一次性消耗掉 lastApplyResult，避免下次 tryAutoSave 误用过期数据
        state._lastApplyResult = null;
        // 二次 guard（配合上面「新卡 + in-flight save 时不 return」的放行）：等那次手动 Save
        // 收尾后，若卡**仍未落库**（state 还是 isNew 且非 _autoCreated，说明那次 Save 失败 /
        // 没建成），绝不能调 saveCatgirlFromPanel —— 它会 POST 建卡 + closeCatgirlPanel 甩走
        // 面板，正是新卡 guard 要避免的。这种情况 AI 字段已 replay 在表单里，提示用户手动 Save 即可。
        if (state.isNew && !state.form._autoCreated) {
            if (!state._warnedNewCardSaveHint) {
                state._warnedNewCardSaveHint = true;
                _companionAppendSystem(state,
                    _cardAssistT('character.aiCompanionNewCardSaveHint',
                        '💡 新卡片要先点一下下面的 Save 才能让我自动保存，' +
                        '现在字段都已经写进表单了喵～'));
            }
            return;
        }
        let ok = true;
        try {
            // _autoCreated 的卡其实已经 POST 到后端了 → 对它来说这次只是 PUT 更新。但若仍
            // 把 isNew=true 传进去，saveCatgirlFromPanel 的**保存成功后 UI 分支**会按原始
            // isNew 去 closeCatgirlPanel / 开卡面制作弹窗，把正在进行的 companion 聊天打断
            //（Codex #3328942156）。它的请求方法本就按内部 effectiveIsNew(=isNew && !_autoCreated)
            // 走 PUT、不受这里影响；这里按"是否已落库"把 _autoCreated 当作已保存卡传进去，
            // 让 post-save 走原地刷新而不是甩走面板。走到这一步：要么本就是已保存卡 / _autoCreated，
            // 要么是「新卡 + in-flight save」竞态等完后卡已落库（未落库的已被上面的二次 guard 拦掉）。
            const effectiveIsNew = state.isNew && !state.form._autoCreated;
            const ret = await saveCatgirlFromPanel(state.form, state.originalName, effectiveIsNew);
            if (ret === false) ok = false;
        } catch (e) {
            console.warn('[card-companion] auto-save after action failed:', e);
            ok = false;
        }
        if (!ok) {
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionAutoSaveFailed',
                    '⚠ 自动保存失败了喵——表单里的字段已经写好，请看下弹出的错误提示再手动点 Save 重试。'));
        }
    }
}

// ========== 表单监视：用户在面板外手改字段时给个 system 提示 ==========

function _companionAttachFormWatchers(state) {
    if (!state.form) return;
    // 如果之前 attach 过、但 form 实例换了，先把旧的 listener 清掉，避免旧表单
    // 还在 DOM 树里时双触发。
    if (state.formWatchHandlers && state.formWatchHandlers.length) {
        state.formWatchHandlers.forEach(function (pair) {
            try { state.form.removeEventListener(pair[0], pair[1]); } catch (_) {}
        });
        state.formWatchHandlers = [];
    }
    state.formWatchSnapshot = _cardAssistCollectCurrentFormData(state.form);
    const inputHandler = function (e) {
        const t = e.target;
        if (!t || !t.name) return;
        if (t.tagName !== 'TEXTAREA' && t.tagName !== 'INPUT') return;
        if (_cardAssistIsReservedKey(t.name)) return;
        // 防抖：用户停手 600ms 才看是不是真的改了
        clearTimeout(t._companionWatchTimer);
        t._companionWatchTimer = setTimeout(function () {
            const newVal = (t.value || '').trim();
            const oldVal = (state.formWatchSnapshot[t.name] || '').trim();
            if (newVal === oldVal) return;  // companion 自己改的 → snapshot 已同步，会在这里跳过
            state.formWatchSnapshot[t.name] = newVal;
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionUserEdited', '你刚改了') +
                ' 「' + t.name + '」' +
                (oldVal
                    ? '：' + _companionTruncate(oldVal, 20) + ' → ' + _companionTruncate(newVal, 20)
                    : '：' + _companionTruncate(newVal, 40)));
        }, 600);
    };
    state.form.addEventListener('input', inputHandler);
    state.formWatchHandlers.push(['input', inputHandler]);
}

function _companionRefreshFormSnapshot(state) {
    if (!state.form) return;
    state.formWatchSnapshot = _cardAssistCollectCurrentFormData(state.form);
}

// 切换猫娘 / 关掉再开详情面板 / 重命名 / 任何让 buildCatgirlDetailForm 重新跑过
// 的操作，都会让 state.form 指向一个 detach 掉的旧 DOM 实例。companion 自己
// 是侧栏，活得比 form 长。每次要"动 form"之前调一下这个 helper，会按
//   1) state.form 还在 DOM 里 → 直接用
//   2) document.getElementById('catgirl-form-' + originalName) → 重新绑定 + 重挂监听
//   3) 都没有 → 返回 false，调用方据此让 companion 给个明确提示
// 实现这套自动跟随，用户不用关掉 companion 再开一次。
function _companionEnsureLiveForm(state) {
    if (!state) return false;
    if (state.form && state.form.isConnected) return true;
    // 找 live form 的两条路（按顺序回退）：
    //   1) 有 originalName → 按 `catgirl-form-${originalName}` 精确查（已保存卡
    //      常态：切猫娘 / 关再开）
    //   2) 上一步失败 / originalName 为空 → 用 DOM 选择器在当前 catgirl panel 里
    //      找那个唯一 form。详情面板同时只能有一个 form，所以选择器命中唯一。
    //      这一支专门覆盖两个场景：
    //        a. 「空白新卡 → 填档案名 → 手动 Save → form id 从 catgirl-form-new
    //           变成 catgirl-form-<actualName>」（originalName='', id 漂移）
    //        b. **重命名**：用户在 companion 开着的情况下改了档案名，
    //           saveCatgirlFromPanel 用新名 rebuild 表单，旧 id 找不到，但新
    //           form 已经挂在 panel 里、companion 应该顺势跟过去
    //      然后下面的 sync 逻辑会把 state.originalName 回填成新 form 的真实名字。
    let liveForm = null;
    if (state.originalName) {
        liveForm = document.getElementById('catgirl-form-' + state.originalName);
    }
    if (!liveForm) {
        // path-2 选择器回退：在当前 catgirl panel 里找那个唯一 form。
        // 安全性（Codex #3328901017）：打开/切换到「别的卡」必须先 closeCatgirlPanel
        //（openCatgirlPanel 顶部 _catgirlPanelOpen 互斥），而 closeCatgirlPanel 会直接
        // teardown+destroy 掉 companion。所以能走到这一支时 companion 必然还活着 = 详情
        // 面板从未被关过 = 当前可见的唯一 form 一定是「同一张卡」的 in-place rebuild
        //（改档案名字段后 saveCatgirlFromPanel 重建、新卡首存 popup 被拦走
        // rebuildSavedCatgirlPanel），绝不会抓到另一张卡 → 不会误绑。
        liveForm = document.querySelector('.catgirl-panel-right form[id^="catgirl-form-"]');
    }
    if (!liveForm) return false;
    // 拿到了"现行"的同名表单。把 state.form 换过去并重挂 watcher。
    // 注意：旧 form 上的 listener 已随 DOM 卸载消失，无需手动 remove —— 但
    // _companionAttachFormWatchers 内部已经做了 defensive removeEventListener。
    state.form = liveForm;
    // ⚠ 必须同步 isNew / originalName ——`buildCatgirlDetailForm` 重建表单时
    // 会把 `_isNew` / `_catgirlName` 设到最新值（比如用户首次保存新卡后表单
    // 以 isNew=false 重建、或者用户做了重命名）。companion 这边如果继续用
    // 创建时的旧 `state.isNew`：
    //   - `_companionTryAutoSave` 里的 `if (state.isNew && !state.form._autoCreated)`
    //     永久命中 → 自动保存永远 bail，新卡保存提示反复弹
    //   - 一旦走到 saveCatgirlFromPanel，`effectiveIsNew=true` 会触发 POST 而
    //     不是 PUT，造成同名 catgirl 409 / 重复
    const liveIsNew = liveForm._isNew === true;
    const wasNew = state.isNew;
    state.isNew = liveIsNew;
    if (liveForm._catgirlName) state.originalName = liveForm._catgirlName;
    // 新卡变成已保存卡的瞬间清掉 "先点 Save" 一次性提示标记；万一未来某次状态
    // 再翻回新卡（实操路径几乎不可能但便宜），提示能再次出现。
    if (wasNew && !liveIsNew) state._warnedNewCardSaveHint = false;
    state.formWatchHandlers = [];
    _companionAttachFormWatchers(state);
    return true;
}

function _companionTruncate(s, n) {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n) + '…' : s;
}

function _cardAssistNormalizeDisplayText(text) {
    let s = String(text == null ? '' : text);
    // Companion bubbles render plain text, so stray markdown markers look broken.
    // Strip the common emphasis markers and normalize markdown bullet prefixes.
    s = s.replace(/^\s{0,3}#{1,6}\s+/gm, '');
    s = s.replace(/^\s*[*-]\s+/gm, '• ');
    s = s.replace(/\*\*([^*]+)\*\*/g, '$1');
    s = s.replace(/__([^_]+)__/g, '$1');
    s = s.replace(/(^|[^\w])\*([^*\n]+)\*(?=[^\w]|$)/g, '$1$2');
    s = s.replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, '$1$2');
    return s;
}

// ========== Bubble 工厂 ==========

function _companionScrollToBottom(state) {
    if (!state.threadEl) return;
    // 用 microtask 让 DOM commit 完再算 scrollHeight，否则会拿到 stale 值
    setTimeout(function () {
        state.threadEl.scrollTop = state.threadEl.scrollHeight;
    }, 0);
}

function _companionAppendAssistant(state, text, opts) {
    opts = opts || {};
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble card-companion-bubble-assistant';

    if (opts.prefix) {
        const tag = document.createElement('div');
        tag.className = 'card-companion-bubble-prefix';
        tag.textContent = opts.prefix;
        bubble.appendChild(tag);
    }

    const body = document.createElement('div');
    body.className = 'card-companion-bubble-body';
    body.textContent = _cardAssistNormalizeDisplayText(text);
    body.style.whiteSpace = 'pre-wrap';
    bubble.appendChild(body);

    if (opts.chips && opts.chips.length) {
        const row = document.createElement('div');
        row.className = 'card-companion-bubble-chips';
        opts.chips.forEach(function (c) {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'card-companion-chip';
            chip.textContent = c.label;
            chip.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                // 一次性 chip：点完整行禁用
                row.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
                const customInput = bubble.querySelector('.card-companion-bubble-custom-input');
                if (customInput) customInput.disabled = true;
                if (typeof c.onClick === 'function') c.onClick();
            });
            row.appendChild(chip);
        });
        bubble.appendChild(row);
    }

    if (opts.allowCustom) {
        const customRow = document.createElement('div');
        customRow.className = 'card-companion-bubble-custom';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'card-companion-bubble-custom-input';
        input.placeholder = _cardAssistT('character.aiCompanionInlineCustom', '或者自己填一个…');
        input.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            const v = (input.value || '').trim();
            if (!v) return;
            input.disabled = true;
            bubble.querySelectorAll('.card-companion-chip').forEach(function (c) { c.disabled = true; });
            // opts.customSubmit 由调用方提供时优先用它（_companionRenderNextQuestion
            // 走这一支以便施加 stale-bubble ownIdx 防 race —— 否则用户在老 bubble
            // 的自定义输入框里按 Enter 会走通用 _companionHandleUserText，把答案
            // 塞给当前题、再 ++ idx 跳过下一题。chip 那一支也是这个思路）。
            if (typeof opts.customSubmit === 'function') {
                opts.customSubmit(v);
            } else {
                _companionHandleUserText(state, v);
            }
        });
        customRow.appendChild(input);
        bubble.appendChild(customRow);
    }

    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

function _companionAppendUser(state, text) {
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble card-companion-bubble-user';
    const body = document.createElement('div');
    body.className = 'card-companion-bubble-body';
    body.textContent = text || '';
    body.style.whiteSpace = 'pre-wrap';
    bubble.appendChild(body);
    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

function _companionAppendSystem(state, text) {
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble-system';
    bubble.textContent = _cardAssistNormalizeDisplayText(text);
    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

function _companionAppendTyping(state, label) {
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble card-companion-bubble-assistant card-companion-typing';
    const body = document.createElement('div');
    body.className = 'card-companion-bubble-body';
    body.innerHTML = (label ? _cardAssistEscapeHtml(label) + ' ' : '') +
        '<span class="card-companion-typing-dot"></span>' +
        '<span class="card-companion-typing-dot"></span>' +
        '<span class="card-companion-typing-dot"></span>';
    bubble.appendChild(body);
    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

function _companionAppendError(state, err) {
    let msg = (err && err.message) || String(err || '');
    if (err && err.code === 'assist_api_not_configured') {
        msg = _cardAssistT('character.aiAssistApiMissing',
            '辅助 API 尚未配置。请在「API Key 设置」里完成配置后再试。');
    }
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble-system card-companion-error';
    bubble.textContent = '⚠ ' + msg;
    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

// 在表单里查找名为 `key` 的字段 textarea。
//   1. 精确 [name=key] 命中
//   2. trim 后命中（应对 characters.json 里手抖留下的首尾空格）
//   3. 全表扫描 + trimmed 小写对比（应对 zh/en locale 漂移、大小写不一致）
// 命中返回 textarea，未命中返回 null —— 调用方据此决定 update vs create。
function _findFieldTextareaByName(form, key) {
    if (!form || !key) return null;
    const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s);
    let ta = form.querySelector('textarea[name="' + esc(key) + '"]');
    if (ta) return ta;
    const trimmed = String(key).trim();
    if (trimmed && trimmed !== key) {
        ta = form.querySelector('textarea[name="' + esc(trimmed) + '"]');
        if (ta) return ta;
    }
    const lower = trimmed.toLowerCase();
    const all = form.querySelectorAll('textarea[name]');
    for (let i = 0; i < all.length; i++) {
        const el = all[i];
        const n = (el.getAttribute('name') || '').trim();
        if (n === trimmed) return el;
        if (n.toLowerCase() === lower) return el;
    }
    return null;
}

// 给一个 field-row-wrapper 闪一下绿色渐变 + 自动滚到视野中央，让用户能立刻
// 跟上 companion 改了哪一行。
// opts:
//   scrollIntoView (bool, default false)
//     true 时把 row 平滑滚到容器中央。批量 apply 时只对"第一行"传 true，
//     避免视野被多次 yank 来 yank 去
//   focusTextarea (HTMLElement | null, default null)
//     传一个 textarea 进来，闪烁结束前若用户没在其它输入框打字，会顺手把光标
//     落在它上面 —— 对"AI 改了这个字段、你想接着调"场景挺顺手；
//     用户正在 companion 输入框里打字的话不抢焦点。
function _cardAssistFlashRow(wrapperEl, opts) {
    if (!wrapperEl) return;
    opts = opts || {};
    wrapperEl.classList.remove('card-assist-row-flash');
    // force reflow so re-applying class re-triggers the animation
    void wrapperEl.offsetWidth;
    wrapperEl.classList.add('card-assist-row-flash');
    setTimeout(function () {
        wrapperEl.classList.remove('card-assist-row-flash');
    }, 1500);

    if (opts.scrollIntoView) {
        // 用 microtask 让 DOM 把新插入的 row 算进 layout，再 scrollIntoView 才
        // 不会拿到 0 高度的 stale rect。
        setTimeout(function () {
            try {
                wrapperEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            } catch (_) {
                // 老浏览器 fallback：没有 smooth 也照样要滚到位
                wrapperEl.scrollIntoView();
            }
        }, 0);
    }

    if (opts.focusTextarea) {
        // 用户正在 companion 输入框 / 其它表单输入框里打字 → 不抢焦点
        const active = document.activeElement;
        const userIsTypingElsewhere = active &&
            (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT') &&
            active !== opts.focusTextarea;
        if (!userIsTypingElsewhere) {
            try { opts.focusTextarea.focus({ preventScroll: true }); }
            catch (_) { try { opts.focusTextarea.focus(); } catch (__) {} }
        }
    }
}

// 将一组 generated[key] = value 写到表单。返回 {updated: [key,...], created: [key,...]}
// 让上层能区分"改了已有"和"插了新行"，给用户更准确的反馈。
function _cardAssistApplyToForm(form, generated, selectedKeys, originalName, isNew) {
    const result = { updated: [], created: [], skipped: [] };
    if (!form || !selectedKeys || !selectedKeys.length) return result;
    // 防御：form 已经从 DOM 卸载（用户切了猫娘 / 关掉了详情面板）的情况下，
    // 写值不会有任何视觉效果。给上层一个明确信号。
    if (!form.isConnected) {
        selectedKeys.forEach((k) => result.skipped.push(k));
        return result;
    }
    const addFieldArea = form.querySelector('.add-field-area');
    // 批量应用时只对"第一个真正落到 form 上的字段"做 scrollIntoView，避免一次
    // generate 写 9 个字段把视野往下连甩 9 次。
    let didScroll = false;
    selectedKeys.forEach(function (key) {
        if (!key || _cardAssistIsReservedKey(key)) {
            result.skipped.push(key);
            return;
        }
        const value = String(generated[key] == null ? '' : generated[key]);
        const textarea = _findFieldTextareaByName(form, key);
        if (textarea) {
            textarea.value = value;
            if (typeof _panelRequestTextareaAutoResize === 'function') {
                _panelRequestTextareaAutoResize(textarea);
            }
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            textarea.dispatchEvent(new Event('change', { bubbles: true }));
            const row = textarea.closest('.field-row-wrapper') || textarea.parentNode;
            _cardAssistFlashRow(row, {
                scrollIntoView: !didScroll,
                focusTextarea: didScroll ? null : textarea,
            });
            didScroll = true;
            result.updated.push(textarea.getAttribute('name') || key);
            return;
        }
        // 字段不存在 → 复用「新增设定」分支的 DOM 构造
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row';

        const labelEl = document.createElement('label');
        if (typeof _panelSetFieldLabel === 'function') {
            _panelSetFieldLabel(labelEl, key);
        } else {
            labelEl.textContent = key;
        }
        wrapper.appendChild(labelEl);

        const fr = document.createElement('div');
        fr.className = 'field-row';
        const textareaEl = document.createElement('textarea');
        textareaEl.name = key;
        textareaEl.rows = 1;
        textareaEl.value = value;
        fr.appendChild(textareaEl);
        wrapper.appendChild(fr);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn sm delete';
        const delLabel = (window.t && typeof window.t === 'function')
            ? window.t('character.deleteField')
            : '删除设定';
        delBtn.innerHTML = '<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteField">' + delLabel + '</span>';
        delBtn.addEventListener('click', function () {
            wrapper.remove();
            // 镜像普通自定义字段删除路径（见 buildCatgirlDetailForm 里 ~5041）：删掉 AI
            // 新建的字段后也要把 Save / Cancel 亮出来。否则已保存卡上这次删除既不触发
            // autosave、也没有可见的手动保存入口，reload 后字段复活（Codex #3328901018）。
            const sBtn = form.querySelector('#save-button');
            const cBtn = form.querySelector('#cancel-button');
            if (sBtn) sBtn.style.display = '';
            if (cBtn) cBtn.style.display = '';
        });
        wrapper.appendChild(delBtn);

        if (addFieldArea && addFieldArea.parentNode === form) {
            form.insertBefore(wrapper, addFieldArea);
        } else {
            form.appendChild(wrapper);
        }
        if (typeof _panelAttachTextareaAutoResize === 'function') {
            _panelAttachTextareaAutoResize(textareaEl);
        }
        if (typeof _panelRequestTextareaAutoResize === 'function') {
            _panelRequestTextareaAutoResize(textareaEl);
        }
        if (!isNew && originalName && typeof panelAttachAutoSaveListener === 'function') {
            panelAttachAutoSaveListener(textareaEl, originalName);
        }
        textareaEl.dispatchEvent(new Event('input', { bubbles: true }));
        _cardAssistFlashRow(wrapper, {
            scrollIntoView: !didScroll,
            focusTextarea: didScroll ? null : textareaEl,
        });
        didScroll = true;
        result.created.push(key);
    });
    // 让用户看到 Save / Cancel
    const sb = form.querySelector('#save-button');
    const cb = form.querySelector('#cancel-button');
    if (sb) sb.style.display = '';
    if (cb) cb.style.display = '';
    return result;
}

function _cardAssistAutoResize(textarea) {
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = (textarea.scrollHeight + 2) + 'px';
}

function _cardAssistEscapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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
