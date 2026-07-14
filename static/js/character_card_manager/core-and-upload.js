// Part responsibility: shared field normalization, page controls, form validation, and workshop upload setup.

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
