
// ===== 跨页面通信系统 =====
const CHANNEL_NAME = 'neko_page_channel';
let modelManagerBroadcastChannel = null;

// 初始化 BroadcastChannel（如果支持）
try {
    if (typeof BroadcastChannel !== 'undefined') {
        modelManagerBroadcastChannel = new BroadcastChannel(CHANNEL_NAME);
        console.log('[CrossPageComm] model_manager BroadcastChannel 已初始化');
    }
} catch (e) {
    console.log('[CrossPageComm] BroadcastChannel 不可用，将使用 localStorage 后备方案');
}

// 用于页面间通信的事件处理
function sendMessageToMainPage(action, payload = {}) {
    try {
        const safePayload = {};
        if (payload && typeof payload === 'object') {
            for (const [key, value] of Object.entries(payload)) {
                if (key === 'action' || key === 'timestamp') continue;
                safePayload[key] = value;
            }
        }

        const message = {
            ...safePayload,
            action: action,
            timestamp: Date.now()
        };

        // 优先使用 BroadcastChannel
        if (modelManagerBroadcastChannel) {
            modelManagerBroadcastChannel.postMessage(message);
            console.log('[CrossPageComm] 通过 BroadcastChannel 发送消息:', action);
        }

        // 方式1: 如果是在弹出窗口中，使用 postMessage（更可靠）
        if (window.opener && !window.opener.closed) {
            console.log(`[消息发送] 使用 postMessage 发送消息: ${action}`);
            window.opener.postMessage(message, window.location.origin);
        }

        // 方式2: 使用localStorage事件机制发送消息给主页面（备用方案）
        try {
            localStorage.setItem('nekopage_message', JSON.stringify(message));
            localStorage.removeItem('nekopage_message'); // 立即移除以允许重复发送相同消息
            console.log(`[消息发送] 使用 localStorage 发送消息: ${action}`);
        } catch (e) {
            console.warn('localStorage 消息发送失败:', e);
        }
    } catch (e) {
        console.error('发送消息给主页面失败:', e);
    }
}



function isModelManagerPopupWindow() {
    return window.opener !== null;
}

// 全局变量：跟踪未保存的更改
Object.assign(window, {
    hasUnsavedChanges: false,
    _modelManagerParameterEditedSinceSave: false,
    _modelManagerParameterSaveNoticeShown: false,
});
const MODEL_MANAGER_PARAMETER_SAVE_MARK_PREFIX = 'neko_model_manager_parameter_save_pending:';
const MODEL_MANAGER_PARAMETER_SAVE_MARK_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const MODEL_MANAGER_LANLAN_NAME_SESSION_KEY = 'neko_model_manager_lanlan_name';

function normalizeModelManagerLanlanName(value) {
    return String(value || '').trim();
}

function getModelManagerLanlanNameFromUrl() {
    try {
        const urlParams = new URLSearchParams(window.location.search);
        return normalizeModelManagerLanlanName(urlParams.get('lanlan_name'));
    } catch (_) {
        return '';
    }
}

function rememberModelManagerLanlanNameFallback(lanlanName) {
    const normalizedName = normalizeModelManagerLanlanName(lanlanName);
    if (!normalizedName) return;
    try {
        sessionStorage.setItem(MODEL_MANAGER_LANLAN_NAME_SESSION_KEY, normalizedName);
    } catch (_) {}
}

function getModelManagerLanlanNameFromSession() {
    try {
        return normalizeModelManagerLanlanName(sessionStorage.getItem(MODEL_MANAGER_LANLAN_NAME_SESSION_KEY));
    } catch (_) {
        return '';
    }
}

async function resolveModelManagerParameterSaveLanlanName() {
    let lanlanName = getModelManagerLanlanNameFromUrl();
    if (lanlanName) {
        rememberModelManagerLanlanNameFallback(lanlanName);
        return lanlanName;
    }

    lanlanName = getModelManagerLanlanNameFromSession();
    if (lanlanName) return lanlanName;

    if (typeof resolveModelManagerLanlanName === 'function') {
        try {
            lanlanName = normalizeModelManagerLanlanName(await resolveModelManagerLanlanName());
        } catch (_) {
            lanlanName = '';
        }
    }
    if (lanlanName) {
        rememberModelManagerLanlanNameFallback(lanlanName);
    }
    return lanlanName;
}

async function getModelManagerParameterSaveMarkKey(lanlanName) {
    const normalizedName = normalizeModelManagerLanlanName(lanlanName || await resolveModelManagerParameterSaveLanlanName());
    if (!normalizedName) return '';
    try {
        return MODEL_MANAGER_PARAMETER_SAVE_MARK_PREFIX + encodeURIComponent(normalizedName);
    } catch (_) {
        return '';
    }
}

function getModelManagerParameterSaveStorages() {
    const storages = [];
    try {
        if (window.sessionStorage) storages.push(window.sessionStorage);
    } catch (_) {}
    try {
        if (window.localStorage) storages.push(window.localStorage);
    } catch (_) {}
    return storages;
}

function isPendingParameterEditorSaveValid(pendingSave) {
    if (!pendingSave || typeof pendingSave !== 'object') return false;
    const timestamp = Number(pendingSave.timestamp || 0);
    if (!timestamp) return true;
    return Date.now() - timestamp <= MODEL_MANAGER_PARAMETER_SAVE_MARK_TTL_MS;
}

function normalizeModelManagerModelMarkValue(value) {
    return String(value || '').trim();
}

function pendingParameterEditorSaveMatchesCurrentModel(pendingSave, modelInfo) {
    if (!pendingSave || !modelInfo) return false;
    const pendingPath = normalizeModelManagerModelMarkValue(pendingSave.modelPath);
    const currentPath = normalizeModelManagerModelMarkValue(modelInfo.path);
    if (pendingPath) {
        return pendingPath === currentPath;
    }

    const pendingName = normalizeModelManagerModelMarkValue(pendingSave.modelName);
    if (!pendingName) return true;
    return pendingName === normalizeModelManagerModelMarkValue(modelInfo.name);
}

async function readPendingParameterEditorSave() {
    const markKey = await getModelManagerParameterSaveMarkKey();
    if (!markKey) return null;
    for (const storage of getModelManagerParameterSaveStorages()) {
        try {
            const raw = storage.getItem(markKey);
            if (!raw) continue;
            const pendingSave = JSON.parse(raw);
            if (!isPendingParameterEditorSaveValid(pendingSave)) {
                storage.removeItem(markKey);
                continue;
            }
            return pendingSave;
        } catch (_) {}
    }
    return null;
}

async function clearPendingParameterEditorSaveState() {
    const markKey = await getModelManagerParameterSaveMarkKey();
    if (markKey) {
        for (const storage of getModelManagerParameterSaveStorages()) {
            try {
                storage.removeItem(markKey);
            } catch (_) {}
        }
    }
    window._modelManagerParameterEditedSinceSave = false;
    window._modelManagerParameterSaveNoticeShown = false;
}

async function restorePendingParameterEditorSaveState(saveButton, options = {}) {
    const pendingSave = await readPendingParameterEditorSave();
    if (!pendingSave) return false;
    if (!pendingParameterEditorSaveMatchesCurrentModel(pendingSave, options.currentModelInfo)) {
        await clearPendingParameterEditorSaveState();
        return false;
    }

    window._modelManagerParameterEditedSinceSave = true;
    window.hasUnsavedChanges = true;
    if (saveButton) {
        saveButton.disabled = false;
    }
    if (options.showNotice && !window._modelManagerParameterSaveNoticeShown) {
        const message = modelManagerText(
            'modelManager.parameterEditorSavedNeedsModelSave',
            '捏脸参数已保存，请点击「保存设置」同步到角色配置。'
        );
        if (typeof options.showStatus === 'function') {
            options.showStatus(message, options.statusDuration || 4000);
        }
        if (typeof options.showToast === 'function') {
            options.showToast(message, options.toastDuration || 4200, 'warning');
        }
        window._modelManagerParameterSaveNoticeShown = true;
    }
    return true;
}

// 全局辅助：从待机动作多选容器获取已勾选 URL 列表（供快照使用）
function _getSelectedIdleAnimationsGlobal(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    return Array.from(container.querySelectorAll('.idle-animation-options input[type="checkbox"]:checked'))
        .map(cb => cb.value)
        .filter(Boolean);
}

// 采集当前所有可保存设置的快照（模型选择 + 打光 + 待机动作）
function captureSettingsSnapshot() {
    const modelSelect = document.getElementById('model-select');
    const vrmModelSelect = document.getElementById('vrm-model-select');
    return {
        modelType: typeof currentModelType !== 'undefined' ? currentModelType : '',
        live2d: modelSelect ? modelSelect.value : '',
        live3d: vrmModelSelect ? vrmModelSelect.value : '',
        // VRM 打光
        ambient: document.getElementById('ambient-light-slider')?.value ?? '',
        mainLight: document.getElementById('main-light-slider')?.value ?? '',
        exposure: document.getElementById('exposure-slider')?.value ?? '',
        toneMapping: document.getElementById('tonemapping-select')?.value ?? '',
        outlineWidth: document.getElementById('vrm-outline-width-slider')?.value ?? '',
        // MMD 打光
        mmdAmbientIntensity: document.getElementById('mmd-ambient-intensity-slider')?.value ?? '',
        mmdAmbientColor: document.getElementById('mmd-ambient-color-picker')?.value ?? '',
        mmdDirectionalIntensity: document.getElementById('mmd-directional-intensity-slider')?.value ?? '',
        mmdDirectionalColor: document.getElementById('mmd-directional-color-picker')?.value ?? '',
        mmdExposure: document.getElementById('mmd-exposure-slider')?.value ?? '',
        mmdToneMapping: document.getElementById('mmd-tonemapping-select')?.value ?? '',
        mmdOutline: String(document.getElementById('mmd-outline-toggle')?.checked ?? false),
        // 待机动作（多选，序列化为 JSON 数组）
        live2dIdleAnimation: document.getElementById('motion-select')?.value ?? '',
        idleAnimation: JSON.stringify(_getSelectedIdleAnimationsGlobal('vrm-idle-animation-multiselect')),
        mmdIdleAnimation: JSON.stringify(_getSelectedIdleAnimationsGlobal('mmd-idle-animation-multiselect')),
        // VRM/MMD 手动动作选择
        vrmAnimation: document.getElementById('vrm-animation-select')?.value ?? '',
        mmdAnimation: document.getElementById('mmd-animation-select')?.value ?? '',
    };
}

// 比较两个快照是否一致
function snapshotsEqual(a, b) {
    if (!a || !b) return false;
    return Object.keys(a).every(k => String(a[k]) === String(b[k]));
}

function modelSelectionChanged(before, after) {
    if (!before || !after) return true;
    return String(before.modelType) !== String(after.modelType)
        || String(before.live2d) !== String(after.live2d)
        || String(before.live3d) !== String(after.live3d);
}

// 仅当本页确实保存过配置时，才触发主界面重载（避免退出就把主界面模型/位置”复位”）
Object.assign(window, {
    _modelManagerHasSaved: false,
    _modelManagerLanlanName: new URLSearchParams(window.location.search).get('lanlan_name') || '',
    _modelManagerModelChangedSinceSave: false,
    _modelManagerLoadedFallbackModel: false,
    _suppressModelManagerChange: false,
});

function markModelChangedForCardFacePrompt() {
    if (window._suppressModelManagerChange) return;
    window._modelManagerModelChangedSinceSave = true;
}

function isSuppressedModelManagerChangeEvent(event) {
    return !!(event && event._suppressModelManagerChange);
}

function dispatchModelManagerChange(target, options = {}) {
    if (!target) return false;
    const event = new Event('change', { bubbles: true });
    if (options.suppress || window._suppressModelManagerChange) {
        event._suppressModelManagerChange = true;
    }
    return target.dispatchEvent(event);
}

async function suppressModelManagerChange(fn) {
    const previous = window._suppressModelManagerChange;
    window._suppressModelManagerChange = true;
    try {
        return await fn();
    } finally {
        window._suppressModelManagerChange = previous;
    }
}

function modelManagerText(key, fallback, params = {}) {
    try {
        if (window.t && typeof window.t === 'function') {
            const translated = window.t(key, params);
            if (translated && translated !== key) return translated;
        }
    } catch (e) {
        console.error(`[i18n] Translation failed for key "${key}":`, e);
    }
    return fallback;
}

function setModelManagerStatusText(message) {
    const statusSpan = document.getElementById('status-text');
    if (statusSpan) statusSpan.textContent = message;
}

const MODEL_MANAGER_SETTINGS_WAITING_EVENT = 'neko-model-manager-settings-waiting-change';

function getModelManagerSettingsWaitingMessage() {
    return window._modelManagerSettingsWaitingMessage
        || modelManagerText('cardExport.autoSavingDefaultCardFace', '正在生成默认卡面...');
}

function isModelManagerSettingsWaiting() {
    return window._modelManagerSettingsWaiting === true
        || Number(window._modelManagerSettingsWaitingCount || 0) > 0;
}

function dispatchModelManagerSettingsWaitingChange() {
    const waiting = isModelManagerSettingsWaiting();
    window._modelManagerSettingsWaiting = waiting;
    try {
        window.dispatchEvent(new CustomEvent(MODEL_MANAGER_SETTINGS_WAITING_EVENT, {
            detail: {
                waiting,
                message: getModelManagerSettingsWaitingMessage()
            }
        }));
    } catch (_) {}
}

function beginModelManagerSettingsWaiting(message) {
    const waitingMessage = message || getModelManagerSettingsWaitingMessage();
    window._modelManagerSettingsWaitingCount = Number(window._modelManagerSettingsWaitingCount || 0) + 1;
    window._modelManagerSettingsWaitingMessage = waitingMessage;
    dispatchModelManagerSettingsWaitingChange();

    let finished = false;
    return () => {
        if (finished) return;
        finished = true;
        window._modelManagerSettingsWaitingCount = Math.max(
            0,
            Number(window._modelManagerSettingsWaitingCount || 0) - 1
        );
        if (window._modelManagerSettingsWaitingCount === 0) {
            window._modelManagerSettingsWaiting = false;
            window._modelManagerSettingsWaitingMessage = '';
        }
        dispatchModelManagerSettingsWaitingChange();
    };
}

async function resolveModelManagerLanlanName() {
    if (window._modelManagerLanlanName && window._modelManagerLanlanName.trim() !== '') {
        rememberModelManagerLanlanNameFallback(window._modelManagerLanlanName);
        return window._modelManagerLanlanName;
    }
    try {
        const data = await RequestHelper.fetchJson('/api/config/page_config');
        if (data && data.success && data.lanlan_name) {
            window._modelManagerLanlanName = data.lanlan_name;
            rememberModelManagerLanlanNameFallback(window._modelManagerLanlanName);
        }
    } catch (e) {
        console.warn('[模型管理] 获取 lanlan_name 失败，跳过缓存:', e);
    }
    return window._modelManagerLanlanName || '';
}

async function notifyMainPageModelReload() {
    const lanlanName = await resolveModelManagerLanlanName();
    if (lanlanName && lanlanName.trim() !== '') {
        sendMessageToMainPage('reload_model', { lanlan_name: lanlanName });
    } else {
        console.warn('[模型管理] lanlan_name 为空，跳过 reload_model 通知以避免主界面过滤失败');
    }
}

const MODEL_MANAGER_CARD_MAKER_WINDOW_NAME = 'neko_card_maker';

function openCardMakerFromModelManager(lanlanName, options = {}) {
    const params = new URLSearchParams({
        name: lanlanName,
        mode: 'maker'
    });
    if (options.fallbackDefaultOnClose) {
        params.set('fallback_default_on_close', '1');
    }
    if (options.fallbackToken) {
        params.set('fallback_token', options.fallbackToken);
    }
    const url = `/card_maker?${params.toString()}`;
    const features = 'width=1200,height=800';

    // 从角色卡管理页打开，避免卡面制作页成为模型管理页的子窗口。
    // 否则模型管理页关闭时，部分 Electron/浏览器环境会连带关闭卡面制作页。
    if (window.opener && !window.opener.closed) {
        try {
            if (typeof window.opener.openManagedPopup === 'function') {
                return window.opener.openManagedPopup(url, MODEL_MANAGER_CARD_MAKER_WINDOW_NAME, features);
            }
            if (typeof window.opener.openOrFocusWindow === 'function') {
                return window.opener.openOrFocusWindow(url, MODEL_MANAGER_CARD_MAKER_WINDOW_NAME, features);
            }
            const openedByParent = window.opener.open(url, MODEL_MANAGER_CARD_MAKER_WINDOW_NAME, features);
            if (openedByParent && typeof window.opener.requestOpenedWindowRestore === 'function') {
                window.opener.requestOpenedWindowRestore(openedByParent);
            }
            return openedByParent;
        } catch (error) {
            console.warn('[模型管理] 通过父窗口打开卡面制作页失败，回退当前窗口打开:', error);
        }
    }

    if (typeof window.openOrFocusWindow === 'function') {
        return window.openOrFocusWindow(url, MODEL_MANAGER_CARD_MAKER_WINDOW_NAME, features);
    }
    return window.open(url, MODEL_MANAGER_CARD_MAKER_WINDOW_NAME, features);
}

function notifyCardFaceUpdatedFromModelManager(name) {
    const message = {
        type: 'card-face-updated',
        name,
        timestamp: Date.now()
    };

    if (window.opener && !window.opener.closed) {
        try {
            window.opener.postMessage(message, window.location.origin);
        } catch (_) {}
        try {
            const loadCharacterCards = window.opener.loadCharacterCards;
            if (typeof loadCharacterCards === 'function') {
                const refreshResult = loadCharacterCards.call(window.opener);
                if (refreshResult && typeof refreshResult.catch === 'function') {
                    refreshResult.catch(() => {});
                }
            }
        } catch (_) {}
    }

    try {
        const channel = new BroadcastChannel('neko-card-face-events');
        channel.postMessage(message);
        channel.close();
    } catch (_) {}

    try {
        localStorage.setItem('neko_card_face_event', JSON.stringify(message));
        localStorage.removeItem('neko_card_face_event');
    } catch (_) {}
}

function createCardMakerFallbackToken() {
    try {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
    } catch (_) {}
    return `card-maker-fallback-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function getCardMakerFallbackCloseMarkKey(token, name) {
    const normalizedToken = String(token || '').trim();
    const normalizedName = String(name || '').trim();
    if (!normalizedToken || !normalizedName) return '';
    try {
        return `neko_card_maker_fallback_closed:${encodeURIComponent(normalizedToken)}:${encodeURIComponent(normalizedName)}`;
    } catch (_) {
        return '';
    }
}

function postCardMakerFallbackEvent(message) {
    try {
        const channel = new BroadcastChannel('neko-card-maker-fallback-events');
        channel.postMessage(message);
        channel.close();
    } catch (_) {}

    try {
        const closeMarkKey = getCardMakerFallbackCloseMarkKey(message?.token, message?.name);
        if (closeMarkKey) {
            localStorage.setItem(closeMarkKey, JSON.stringify({
                token: message.token,
                name: message.name,
                timestamp: Date.now()
            }));
        }
        localStorage.setItem('neko_card_maker_fallback_event', JSON.stringify(message));
        localStorage.removeItem('neko_card_maker_fallback_event');
    } catch (_) {}
}

function notifyCardMakerFallbackOwnerClosing() {
    const active = window._modelManagerActiveCardMakerFallback;
    if (!active || active.cardFaceSaved || !active.lanlanName || !active.token) return;

    const message = {
        type: 'model-manager-card-maker-fallback-owner-closing',
        name: active.lanlanName,
        token: active.token,
        timestamp: Date.now()
    };

    try {
        active.makerWindow?.postMessage(message, window.location.origin);
    } catch (_) {}
    postCardMakerFallbackEvent(message);
}

function cleanupCardMakerCloseFallbackWatcher() {
    const cleanup = window._modelManagerCardMakerFallbackCleanup;
    if (typeof cleanup === 'function') {
        try {
            cleanup();
        } catch (error) {
            console.warn('[模型管理] 清理卡面制作兜底监听失败:', error);
        }
    }
    if (window._modelManagerCardMakerFallbackCleanup === cleanup) {
        window._modelManagerCardMakerFallbackCleanup = null;
    }
    window._modelManagerActiveCardMakerFallback = null;
}

function watchCardMakerCloseForDefaultCardFace(makerWindow, lanlanName, state = {}, options = {}) {
    if (!makerWindow || !lanlanName) return;

    cleanupCardMakerCloseFallbackWatcher();

    const startedAt = Date.now();
    const fallbackToken = options.fallbackToken || '';
    let cardFaceSaved = false;
    let fallbackRunning = false;
    let closeTimer = 0;
    let closeGraceTimer = 0;
    let channel = null;
    let cachedDefaultCardFaceImage = null;
    let fallbackAbortController = null;
    const cachedDefaultCardFaceImagePromise = captureDefaultCardFaceModelImage(state, 600, 800)
        .then(image => {
            cachedDefaultCardFaceImage = image;
            if (window._modelManagerActiveCardMakerFallback?.token === fallbackToken) {
                window._modelManagerActiveCardMakerFallback.cachedDefaultCardFaceImage = image;
            }
            return image;
        })
        .catch(error => {
            console.warn('[模型管理] 卡面制作兜底快照预捕获失败，将在兜底时重新截图:', error);
            return null;
        });
    window._modelManagerActiveCardMakerFallback = {
        makerWindow,
        lanlanName,
        token: fallbackToken,
        cardFaceSaved: false,
        cachedDefaultCardFaceImage: null
    };

    const matchesCardFaceUpdate = (data) => {
        if (!data || data.type !== 'card-face-updated') return false;
        if (data.name !== lanlanName) return false;
        if (fallbackToken) {
            const eventToken = data.fallbackToken || data.fallback_token || data.token || '';
            if (eventToken !== fallbackToken) return false;
        }
        const timestamp = Number(data.timestamp || 0);
        return !Number.isFinite(timestamp) || timestamp === 0 || timestamp >= startedAt - 2000;
    };

    const cleanup = () => {
        if (closeTimer) {
            clearInterval(closeTimer);
            closeTimer = 0;
        }
        if (closeGraceTimer) {
            clearTimeout(closeGraceTimer);
            closeGraceTimer = 0;
        }
        window.removeEventListener('message', handleMessage);
        window.removeEventListener('storage', handleStorage);
        if (channel) {
            try { channel.close(); } catch (_) {}
            channel = null;
        }
        cachedDefaultCardFaceImage = null;
        if (fallbackAbortController) {
            try { fallbackAbortController.abort(); } catch (_) {}
        }
        fallbackAbortController = null;
        if (window._modelManagerCardMakerFallbackCleanup === cleanup) {
            window._modelManagerCardMakerFallbackCleanup = null;
        }
        if (window._modelManagerActiveCardMakerFallback?.token === fallbackToken) {
            window._modelManagerActiveCardMakerFallback = null;
        }
    };

    const markCardFaceSaved = (data) => {
        if (!matchesCardFaceUpdate(data)) return;
        cardFaceSaved = true;
        if (window._modelManagerActiveCardMakerFallback?.token === fallbackToken) {
            window._modelManagerActiveCardMakerFallback.cardFaceSaved = true;
        }
        if (fallbackAbortController) {
            try { fallbackAbortController.abort(); } catch (_) {}
        }
        cleanup();
    };

    function handleMessage(event) {
        if (event.origin !== window.location.origin) return;
        markCardFaceSaved(event.data);
    }

    function handleStorage(event) {
        if (event.key !== 'neko_card_face_event' || !event.newValue) return;
        try {
            markCardFaceSaved(JSON.parse(event.newValue));
        } catch (_) {}
    }

    async function generateFallbackDefaultCardFace() {
        if (fallbackRunning || cardFaceSaved) return;
        fallbackRunning = true;
        fallbackAbortController = new AbortController();
        try {
            const signal = fallbackAbortController.signal;
            const modelImage = cachedDefaultCardFaceImage || await cachedDefaultCardFaceImagePromise;
            if (cardFaceSaved || signal.aborted) return;
            await generateDefaultCardFaceFromModelManager(lanlanName, state, {
                modelImage,
                signal,
                shouldCancel: () => cardFaceSaved
            });
            if (cardFaceSaved || signal.aborted) return;
            await notifyMainPageModelReload();
        } catch (error) {
            if (error && error.name === 'AbortError') return;
            console.error('[模型管理] 卡面制作关闭后的默认卡面兜底生成失败:', error);
            setModelManagerStatusText(
                error && error.message
                    ? error.message
                    : modelManagerText('cardExport.autoSaveDefaultCardFaceFailed', '默认卡面生成失败')
            );
        } finally {
            fallbackAbortController = null;
        }
    }

    function checkClosed() {
        let isClosed = false;
        try {
            isClosed = makerWindow.closed === true;
        } catch (_) {
            isClosed = true;
        }
        if (!isClosed) return;
        if (closeTimer) {
            clearInterval(closeTimer);
            closeTimer = 0;
        }
        if (closeGraceTimer) return;

        closeGraceTimer = setTimeout(() => {
            closeGraceTimer = 0;
            if (cardFaceSaved) {
                cleanup();
                return;
            }
            generateFallbackDefaultCardFace().finally(() => cleanup());
        }, 350);
    }

    window.addEventListener('message', handleMessage);
    window.addEventListener('storage', handleStorage);
    if (typeof BroadcastChannel === 'function') {
        try {
            channel = new BroadcastChannel('neko-card-face-events');
            channel.onmessage = event => markCardFaceSaved(event.data);
        } catch (_) {
            channel = null;
        }
    }

    closeTimer = setInterval(checkClosed, 800);
    window._modelManagerCardMakerFallbackCleanup = cleanup;
}
