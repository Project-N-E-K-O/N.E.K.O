/**
 * N.E.K.O 通用新手引导系统
 * 支持所有页面的引导配置
 */

// 引导页面列表常量 - 包含所有页面类型及子类型的存储键集合
// 注意：此列表包含 localStorage 使用的存储子键（如 model_manager_*），
// 并不完全等同于 detectPage() 返回的逻辑页面集合。
const TUTORIAL_PAGES = Object.freeze(['home', 'model_manager', 'model_manager_live2d', 'model_manager_vrm', 'model_manager_mmd', 'model_manager_common', 'parameter_editor', 'emotion_manager', 'chara_manager', 'settings', 'voice_clone', 'steam_workshop', 'memory_browser']);
const TUTORIAL_STORAGE_KEY_PREFIX = 'neko_tutorial_';
const TUTORIAL_PROMPT_FLOW_PREFIX = '[TutorialPromptFlow]';

function getTutorialStorageKeyForPage(pageKey) {
    return TUTORIAL_STORAGE_KEY_PREFIX + pageKey;
}

function getTutorialManualIntentKeyForPage(pageKey) {
    return getTutorialStorageKeyForPage(pageKey) + '_manual_intent';
}

function logTutorialPromptFlow(step, details = {}) {
    console.log(TUTORIAL_PROMPT_FLOW_PREFIX + ' ' + step, details);
}

window.getTutorialStorageKeyForPage = getTutorialStorageKeyForPage;
window.getTutorialManualIntentKeyForPage = getTutorialManualIntentKeyForPage;
window.logTutorialPromptFlow = logTutorialPromptFlow;

class UniversalTutorialManager {
    constructor() {
        // 立即设置全局引用，以便在 getter 中使用
        window.universalTutorialManager = this;

        this.STORAGE_KEY_PREFIX = TUTORIAL_STORAGE_KEY_PREFIX;
        this.driver = null;
        this.isInitialized = false;
        this.isTutorialRunning = false; // 防止重复启动
        this.currentPage = UniversalTutorialManager.detectPage();
        this.currentStep = 0;
        this.nextButtonGuardTimer = null;
        this.nextButtonGuardActive = false;
        this.tutorialPadding = 8;
        this.tutorialControlledElements = new Set();
        this.tutorialInteractionStates = new Map();
        this.tutorialMarkerDisplayCache = null;
        this.tutorialRollbackActive = false;
        this._applyingInteractionState = false;
        this._stepChanging = false;
        this._pendingStepChange = false;
        this._lastOnHighlightedStepIndex = null;
        this._lastAppliedStateKey = null;
        this.cachedValidSteps = null;
        this._refreshTimers = [];
        this._pendingI18nStart = false;
        this.pendingTutorialStartSource = null;
        this.currentTutorialStartSource = 'auto';
        this._modelManagerTutorialRecheckTimer = null;
        this._modelManagerModeListenerAttached = false;
        this._modelManagerTutorialDebounceTimer = null;
        this._modelManagerBootstrapFallbackTimer = null;
        this._modelManagerReceivedModeEvent = false;
        this.yuiGuideDirector = null;
        this._yuiGuideHandoffToken = null;
        this._yuiGuideLastSceneId = null;
        this._yuiGuideLifecycleActive = false;
        this._tutorialEndReason = null;
        this._tutorialEndRawReason = null;
        this._tutorialEndHandled = false;

        // 刷新延迟常量
        this.LAYOUT_REFRESH_DELAY = 100;
        this.DYNAMIC_REFRESH_DELAYS = [200, 600, 1000];

        // 用于追踪在引导中修改过的元素及其原始样式
        this.modifiedElementsMap = new Map();

        console.log('[Tutorial] 当前页面:', this.currentPage);

        // 必须在 waitForDriver 之前注册：model_manager 里 await switchModelDisplay 常在 initDriver（可能 setTimeout 延后）之前就结束并派发 neko-model-manager-mode-set，否则首屏已是 MMD 时会丢事件。
        if (this.currentPage.startsWith('model_manager')) {
            this.setupModelManagerModeListener();
            this.scheduleModelManagerBootstrapFallback();
        }

        // 等待 driver.js 库加载
        this.waitForDriver();
    }

    logPromptFlow(step, details = {}) {
        logTutorialPromptFlow(step, details);
    }

    /**
     * 获取翻译文本的辅助函数
     * @param {string} key - 翻译键，格式: tutorial.{page}.step{n}.{title|desc}
     * @param {string} fallback - 备用文本（如果翻译不存在）
     */
    t(key, fallback = '') {
        if (window.t && typeof window.t === 'function') {
            return window.t(key, fallback);
        }
        return fallback;
    }

    getYuiGuideRegistry() {
        try {
            if (typeof window.getYuiGuideStepsRegistry === 'function') {
                return window.getYuiGuideStepsRegistry() || null;
            }
        } catch (error) {
            console.error('[Tutorial] 获取 Yui Guide 注册表失败:', error);
        }

        return window.YuiGuideStepsRegistry || null;
    }

    isYuiGuideAvailable() {
        return !!this.getYuiGuideRegistry();
    }

    getYuiGuideHandoffApi() {
        return window.YuiGuidePageHandoff || null;
    }

    getYuiGuidePageKey(page = this.currentPage) {
        const path = window.location.pathname || '';
        const normalizedPage = typeof page === 'string' ? page : '';

        if (normalizedPage === 'settings' && path.includes('api_key')) {
            return 'api_key';
        }

        if (
            normalizedPage === 'plugin_dashboard' ||
            path.includes('/api/agent/user_plugin/dashboard') ||
            path === '/ui' ||
            path.startsWith('/ui/')
        ) {
            return 'plugin_dashboard';
        }

        return normalizedPage;
    }

    getYuiGuidePageOrder(page = this.currentPage) {
        const registry = this.getYuiGuideRegistry();
        if (!registry || !registry.sceneOrder) {
            return [];
        }

        const pageKey = this.getYuiGuidePageKey(page);
        const pageOrder = Array.isArray(registry.sceneOrder[pageKey]) ? registry.sceneOrder[pageKey] : [];
        return pageOrder.slice();
    }

    getPendingYuiGuideResumeScene(page = this.currentPage) {
        const token = this._yuiGuideHandoffToken;
        if (!token) {
            return null;
        }

        const resumeScene = typeof token.resume_scene === 'string'
            ? token.resume_scene.trim()
            : '';
        if (!resumeScene) {
            return null;
        }

        const guideStep = this.getYuiGuideStepDefinition(resumeScene);
        if (!guideStep) {
            console.warn('[Tutorial] Yui Guide handoff resume_scene 未注册:', resumeScene);
            return null;
        }

        const expectedPage = this.getYuiGuidePageKey(page);
        if (guideStep.page && guideStep.page !== expectedPage) {
            console.warn('[Tutorial] Yui Guide handoff resume_scene 页面不匹配:', resumeScene, guideStep.page, expectedPage);
            return null;
        }

        return resumeScene;
    }

    applyYuiGuideResumeScene(validSteps) {
        if (!Array.isArray(validSteps) || validSteps.length === 0) {
            return validSteps;
        }

        const pageKey = this.getYuiGuidePageKey();
        if (!pageKey || pageKey === 'home') {
            return validSteps;
        }

        const resumeSceneId = this.getPendingYuiGuideResumeScene(pageKey);
        if (!resumeSceneId) {
            return validSteps;
        }

        const resumeIndex = validSteps.findIndex(stepConfig => (
            this.getYuiGuideSceneIdForStep(stepConfig) === resumeSceneId
        ));

        if (resumeIndex < 0) {
            console.warn('[Tutorial] 当前页面步骤中未找到 handoff resume_scene，保留原始顺序:', pageKey, resumeSceneId);
            return validSteps;
        }

        if (resumeIndex === 0) {
            return validSteps;
        }

        console.log('[Tutorial] 根据 handoff resume_scene 恢复教程步骤:', pageKey, resumeSceneId, resumeIndex);
        return validSteps.slice(resumeIndex);
    }

    getYuiGuideHandoffExpectedPages() {
        const pageKey = this.getYuiGuidePageKey();

        if (pageKey === 'api_key') {
            return ['api_key', 'settings'];
        }

        if (pageKey === 'memory_browser') {
            return ['memory_browser'];
        }

        if (pageKey === 'steam_workshop') {
            return ['steam_workshop'];
        }

        if (pageKey === 'plugin_dashboard') {
            return ['plugin_dashboard'];
        }

        return [];
    }

    consumePendingYuiGuideHandoffToken() {
        if (this._yuiGuideHandoffToken) {
            return this._yuiGuideHandoffToken;
        }

        const handoffApi = this.getYuiGuideHandoffApi();
        if (!handoffApi || typeof handoffApi.consumeHandoffToken !== 'function') {
            return null;
        }

        const expectedPages = this.getYuiGuideHandoffExpectedPages();
        if (!Array.isArray(expectedPages) || expectedPages.length === 0) {
            return null;
        }

        for (const expectedPage of expectedPages) {
            try {
                const token = handoffApi.consumeHandoffToken(expectedPage);
                if (token) {
                    this._yuiGuideHandoffToken = token;
                    console.log('[Tutorial] 已消费 Yui Guide handoff token:', expectedPage, token);
                    return token;
                }
            } catch (error) {
                console.error('[Tutorial] 消费 Yui Guide handoff token 失败:', expectedPage, error);
            }
        }

        return null;
    }

    isYuiGuideEnabledForPage(page = this.currentPage) {
        const pageKey = this.getYuiGuidePageKey(page);
        const pageOrder = this.getYuiGuidePageOrder(pageKey);
        if (pageOrder.length === 0) {
            return false;
        }

        if (pageKey === 'home') {
            return true;
        }

        if (pageKey === 'plugin_dashboard') {
            return false;
        }

        if (!this._yuiGuideHandoffToken) {
            return false;
        }

        return pageOrder.length > 0;
    }

    getYuiGuideMappedSceneIds(validSteps = this.cachedValidSteps) {
        if (!Array.isArray(validSteps) || validSteps.length === 0) {
            return [];
        }

        const mappedSceneIds = new Set();
        validSteps.forEach(stepConfig => {
            const sceneId = this.getYuiGuideSceneIdForStep(stepConfig);
            if (sceneId) {
                mappedSceneIds.add(sceneId);
            }
        });

        return Array.from(mappedSceneIds);
    }

    getYuiGuideStepDefinition(stepId) {
        if (!stepId) {
            return null;
        }

        const registry = this.getYuiGuideRegistry();
        if (!registry || typeof registry.getStep !== 'function') {
            return null;
        }

        return registry.getStep(stepId) || null;
    }

    getYuiGuidePreludeSceneIds(page = this.currentPage, validSteps = this.cachedValidSteps) {
        const pageOrder = this.getYuiGuidePageOrder(page);
        const mappedSceneIds = new Set(this.getYuiGuideMappedSceneIds(validSteps));

        return pageOrder.filter(stepId => (
            typeof stepId === 'string' &&
            stepId.startsWith('intro_') &&
            !mappedSceneIds.has(stepId)
        ));
    }

    getYuiGuideSceneIdForStep(stepConfig) {
        if (!stepConfig || typeof stepConfig !== 'object') {
            return null;
        }

        const sceneId = typeof stepConfig.yuiGuideSceneId === 'string'
            ? stepConfig.yuiGuideSceneId.trim()
            : '';

        if (!sceneId) {
            return null;
        }

        const registry = this.getYuiGuideRegistry();
        if (!registry) {
            return sceneId;
        }

        if (typeof registry.hasStep === 'function' && !registry.hasStep(sceneId)) {
            console.warn(`[Tutorial] Yui Guide 场景未注册: ${sceneId}`);
            return null;
        }

        const guideStep = typeof registry.getStep === 'function' ? registry.getStep(sceneId) : null;
        const expectedPage = this.getYuiGuidePageKey();
        if (guideStep && guideStep.page && guideStep.page !== expectedPage) {
            console.warn(`[Tutorial] Yui Guide 场景页面不匹配: ${sceneId} -> ${guideStep.page} (expected ${expectedPage})`);
        }

        return sceneId;
    }

    ensureYuiGuideDirector() {
        if (this.yuiGuideDirector) {
            return this.yuiGuideDirector;
        }

        if (!this.isYuiGuideEnabledForPage()) {
            return null;
        }

        if (typeof window.createYuiGuideDirector !== 'function') {
            return null;
        }

        try {
            let homeInteractionApi = null;
            if (typeof window.getYuiGuideHomeInteractionApi === 'function') {
                try {
                    homeInteractionApi = window.getYuiGuideHomeInteractionApi() || null;
                } catch (error) {
                    console.warn('[Tutorial] 获取首页交互 API 失败，改用兜底实现:', error);
                }
            }
            if (!homeInteractionApi) {
                homeInteractionApi = window.YuiGuideHomeInteractionApi || window.YuiGuidePageHandoff || null;
            }

            const director = window.createYuiGuideDirector({
                tutorialManager: this,
                page: this.getYuiGuidePageKey(),
                registry: this.getYuiGuideRegistry(),
                homeInteractionApi: homeInteractionApi
            });

            if (director && typeof director === 'object') {
                this.yuiGuideDirector = director;
                return director;
            }

            console.warn('[Tutorial] createYuiGuideDirector 返回了无效对象');
        } catch (error) {
            console.error('[Tutorial] 创建 Yui Guide Director 失败:', error);
        }

        return null;
    }

    dispatchYuiGuideEvent(name, detail = {}) {
        if (!this.isYuiGuideEnabledForPage()) {
            return;
        }

        if (typeof window.dispatchEvent !== 'function' || typeof CustomEvent === 'undefined') {
            return;
        }

        const payload = Object.assign({
            currentPage: this.currentPage,
            yuiGuidePage: this.getYuiGuidePageKey(),
            tutorialManager: this,
            timestamp: Date.now()
        }, detail);

        window.dispatchEvent(new CustomEvent(`neko:yui-guide:${name}`, {
            detail: payload
        }));
    }

    buildYuiGuideStepContext(stepConfig, stepIndex, source = 'tutorial') {
        const sceneId = this.getYuiGuideSceneIdForStep(stepConfig);

        return {
            page: this.getYuiGuidePageKey(),
            runtimePage: this.currentPage,
            source: source,
            sceneId: sceneId,
            stepIndex: stepIndex,
            totalSteps: Array.isArray(this.cachedValidSteps) ? this.cachedValidSteps.length : 0,
            driverStep: stepConfig || null,
            guideStep: sceneId ? this.getYuiGuideStepDefinition(sceneId) : null
        };
    }

    callYuiGuideDirector(methodName, ...args) {
        const director = this.ensureYuiGuideDirector();
        if (!director || typeof director[methodName] !== 'function') {
            return;
        }

        try {
            const result = director[methodName](...args);
            if (result && typeof result.then === 'function') {
                Promise.resolve(result).catch(error => {
                    console.error(`[Tutorial] Yui Guide Director.${methodName} 执行失败:`, error);
                });
            }
        } catch (error) {
            console.error(`[Tutorial] Yui Guide Director.${methodName} 调用失败:`, error);
        }
    }

    notifyYuiGuidePreludeStart(validSteps) {
        if (!this.isYuiGuideEnabledForPage()) {
            return;
        }

        this._yuiGuideLifecycleActive = true;
        this._yuiGuideLastSceneId = null;

        const detail = {
            page: this.getYuiGuidePageKey(),
            runtimePage: this.currentPage,
            validSteps: Array.isArray(validSteps) ? validSteps : [],
            preludeSceneIds: this.getYuiGuidePreludeSceneIds(this.currentPage, validSteps)
        };

        this.dispatchYuiGuideEvent('prelude-start', detail);
        this.callYuiGuideDirector('startPrelude');
    }

    notifyYuiGuideStepEnter(stepConfig, stepIndex, source = 'step-change') {
        if (!this.isYuiGuideEnabledForPage()) {
            return;
        }

        const sceneId = this.getYuiGuideSceneIdForStep(stepConfig);
        if (!sceneId || this._yuiGuideLastSceneId === sceneId) {
            return;
        }

        const context = this.buildYuiGuideStepContext(stepConfig, stepIndex, source);
        this.dispatchYuiGuideEvent('step-enter', context);
        this.callYuiGuideDirector('enterStep', sceneId, context);
        this._yuiGuideLastSceneId = sceneId;
    }

    notifyYuiGuideStepLeave(stepConfig, stepIndex, source = 'step-change') {
        if (!this.isYuiGuideEnabledForPage()) {
            return;
        }

        const sceneId = this.getYuiGuideSceneIdForStep(stepConfig) || this._yuiGuideLastSceneId;
        if (!sceneId || this._yuiGuideLastSceneId !== sceneId) {
            return;
        }

        const detail = {
            page: this.getYuiGuidePageKey(),
            runtimePage: this.currentPage,
            source: source,
            sceneId: sceneId,
            stepIndex: stepIndex,
            driverStep: stepConfig || null,
            guideStep: this.getYuiGuideStepDefinition(sceneId)
        };

        this.dispatchYuiGuideEvent('step-leave', detail);
        this.callYuiGuideDirector('leaveStep', sceneId);
        this._yuiGuideLastSceneId = null;
    }

    notifyYuiGuideTutorialEnd(reason = 'destroy') {
        const normalizedReason = this.normalizeTutorialEndReason(reason);
        const rawReason = this.normalizeTutorialEndRawReason(reason);

        if (!this.isYuiGuideEnabledForPage()) {
            this.yuiGuideDirector = null;
            this._yuiGuideLastSceneId = null;
            this._yuiGuideLifecycleActive = false;
            return;
        }

        if (!this._yuiGuideLifecycleActive && !this._yuiGuideLastSceneId && !this.yuiGuideDirector) {
            return;
        }

        this.dispatchYuiGuideEvent('tutorial-end', {
            page: this.getYuiGuidePageKey(),
            runtimePage: this.currentPage,
            reason: normalizedReason,
            rawReason: rawReason
        });
        this.callYuiGuideDirector('destroy');
        this.yuiGuideDirector = null;
        this._yuiGuideLastSceneId = null;
        this._yuiGuideLifecycleActive = false;
        if (this.getYuiGuidePageKey() !== 'home') {
            this._yuiGuideHandoffToken = null;
        }
    }

    normalizeTutorialEndRawReason(reason) {
        const normalized = typeof reason === 'string' ? reason.trim().toLowerCase() : '';
        return normalized || 'destroy';
    }

    normalizeTutorialEndReason(reason) {
        const normalized = this.normalizeTutorialEndRawReason(reason);

        if (normalized === 'complete') {
            return 'complete';
        }

        if (normalized === 'skip' || normalized === 'escape' || normalized === 'angry_exit') {
            return 'skip';
        }

        return 'destroy';
    }

    setTutorialEndReason(reason) {
        if (this._tutorialEndRawReason) {
            return this._tutorialEndReason || 'destroy';
        }

        const rawReason = this.normalizeTutorialEndRawReason(reason);
        this._tutorialEndRawReason = rawReason;
        this._tutorialEndReason = this.normalizeTutorialEndReason(rawReason);
        return this._tutorialEndReason;
    }

    resolveTutorialEndMeta(finalSteps = this.cachedValidSteps || []) {
        if (this._tutorialEndReason || this._tutorialEndRawReason) {
            return {
                reason: this._tutorialEndReason || 'destroy',
                rawReason: this._tutorialEndRawReason || this._tutorialEndReason || 'destroy'
            };
        }

        if (Array.isArray(finalSteps) && finalSteps.length > 0 && this.currentStep >= finalSteps.length - 1) {
            return {
                reason: 'complete',
                rawReason: 'complete'
            };
        }

        return {
            reason: 'destroy',
            rawReason: 'destroy'
        };
    }

    requestTutorialDestroy(reason = 'destroy') {
        this.setTutorialEndReason(reason);

        if (this.driver) {
            this.driver.destroy();
            return;
        }

        this.onTutorialEnd();
    }

    /**
     * 检查 i18n 是否已准备好（window.t 可用且 i18next 已初始化）
     */
    isI18nReady() {
        const i18nInstance = window.i18n || (typeof i18next !== 'undefined' ? i18next : null);
        return typeof window.t === 'function' && !!(i18nInstance && i18nInstance.isInitialized);
    }

    /**
     * 等待 i18n 就绪后再启动引导，避免回退到硬编码文案
     */
    startTutorialWhenI18nReady(delayMs = 0) {
        if (this.isTutorialRunning || window.isInTutorial) {
            // 已在引导中：消耗掉本次启动意图，避免遗留到下次刷新
            this.consumeTutorialStartSource();
            return;
        }

        if (this._pendingI18nStart) {
            return;
        }

        const launchTutorial = () => {
            setTimeout(() => {
                this._pendingI18nStart = false;
                this.startTutorial();
            }, delayMs);
        };

        if (this.isI18nReady()) {
            launchTutorial();
            return;
        }

        this._pendingI18nStart = true;

        let pollTimer = null;
        let timeoutTimer = null;

        const cleanup = () => {
            if (pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
            if (timeoutTimer) {
                clearTimeout(timeoutTimer);
                timeoutTimer = null;
            }
            window.removeEventListener('localechange', onLocaleReady);
        };

        const onLocaleReady = () => {
            if (!this.isI18nReady()) {
                return;
            }
            cleanup();
            launchTutorial();
        };

        window.addEventListener('localechange', onLocaleReady);
        pollTimer = setInterval(onLocaleReady, 100);

        // 容错：如果语言系统异常，超时后仍允许教程启动
        timeoutTimer = setTimeout(() => {
            cleanup();
            launchTutorial();
        }, 5000);
    }

    /**
     * HTML转义辅助函数 - 用于在HTML属性或内容中安全使用翻译文本
     * @param {string} text - 要转义的文本
     * @returns {string} 转义后的HTML安全文本
     */
    safeEscapeHtml(text) {
        if (typeof text !== 'string') {
            return String(text);
        }
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * 检测当前激活的模型类型前缀（live2d / vrm / mmd）
     * 浮动按钮等 UI 元素的 ID 以此前缀命名，如 vrm-floating-buttons、mmd-btn-mic。
     */
    static detectModelPrefix() {
        // 1. 检查 DOM 中实际存在哪种浮动按钮容器
        if (document.getElementById('vrm-floating-buttons')) return 'vrm';
        if (document.getElementById('mmd-floating-buttons')) return 'mmd';
        if (document.getElementById('live2d-floating-buttons')) return 'live2d';

        // 2. 回退到配置
        const cfg = window.lanlan_config && window.lanlan_config.model_type;
        if (cfg === 'vrm') return 'vrm';
        if (cfg === 'mmd') return 'mmd';
        if (cfg === 'live3d') {
            if (window.mmdManager && window.mmdManager.currentModel) return 'mmd';
            if (window.vrmManager && window.vrmManager.currentModel) return 'vrm';
        }

        return 'live2d';
    }

    /**
     * 检测当前页面类型
     */
    static detectPage() {
        const path = window.location.pathname;
        const hash = window.location.hash;

        // 主页
        if (path === '/' || path === '/index.html') {
            return 'home';
        }

        // 模型管理 - 区分 Live2D 和 VRM
        if (path.includes('model_manager') || path.includes('l2d')) {
            return 'model_manager';
        }

        // Live2D 捏脸系统
        if (path.includes('parameter_editor')) {
            return 'parameter_editor';
        }

        // Live2D 情感管理
        if (path.includes('emotion_manager')) {
            return 'emotion_manager';
        }

        // 角色管理
        if (path.includes('chara_manager')) {
            return 'chara_manager';
        }

        // 设置页面
        if (path.includes('api_key') || path.includes('settings')) {
            return 'settings';
        }

        // 语音克隆
        if (path.includes('voice_clone')) {
            return 'voice_clone';
        }

        // Steam Workshop
        if (path.includes('steam_workshop')) {
            return 'steam_workshop';
        }

        // 内存浏览器
        if (path.includes('memory_browser')) {
            return 'memory_browser';
        }

        return 'unknown';
    }

    /**
     * 模型管理页当前展示模式（与 #model-type-select、localStorage live3dSubType 一致）
     * @returns {'live2d'|'vrm'|'mmd'}
     */
    static getModelManagerDisplayMode() {
        const typeSelect = document.getElementById('model-type-select');
        let val = typeSelect && typeSelect.value;

        // model_manager 初始化时先 await PIXI/列表，#model-type-select 仍为 HTML 默认 live2d，
        // 教程的 checkAndStartTutorial 若此时跑在 switchModelDisplay 之前，会与完成时写入的键（如 mmd）不一致。
        if (typeSelect && val === 'live2d') {
            try {
                let saved = (localStorage.getItem('modelType') || 'live2d').toLowerCase();
                if (saved === 'vrm') saved = 'live3d';
                if (saved === 'live3d') {
                    val = 'live3d';
                }
            } catch (e) { /* ignore */ }
        }

        if (val === 'live3d') {
            let sub = '';
            try {
                sub = (localStorage.getItem('live3dSubType') || '').toLowerCase();
            } catch (e) {
                sub = '';
            }
            if (sub === 'mmd') return 'mmd';
            const mmdSec = document.getElementById('mmd-settings-section');
            if (mmdSec) {
                try {
                    const cs = window.getComputedStyle(mmdSec);
                    if (cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0') {
                        return 'mmd';
                    }
                } catch (e) { /* ignore */ }
            }
            return 'vrm';
        }
        return 'live2d';
    }

    /**
     * 模型管理页展示模式 → localStorage 页键（与 getStorageKey 一致）
     * @param {'live2d'|'vrm'|'mmd'} mode
     */
    static modelManagerModeToPageKey(mode) {
        if (mode === 'mmd') return 'model_manager_mmd';
        if (mode === 'vrm') return 'model_manager_vrm';
        return 'model_manager_live2d';
    }

    /**
     * 等待 driver.js 库加载
     */
    waitForDriver() {
        if (typeof window.driver !== 'undefined') {
            this.initDriver();
            return;
        }

        let attempts = 0;
        const maxAttempts = 100;

        const checkDriver = () => {
            attempts++;

            if (typeof window.driver !== 'undefined') {
                console.log('[Tutorial] driver.js 已加载');
                this.initDriver();
                return;
            }

            if (attempts >= maxAttempts) {
                console.error('[Tutorial] driver.js 加载失败（超时 10 秒）');
                return;
            }

            setTimeout(checkDriver, 100);
        };

        checkDriver();
    }

    /**
     * 初始化 driver.js 实例
     */
    initDriver() {
        if (this.isInitialized) return;

        try {
            const DriverClass = window.driver;

            if (!DriverClass) {
                console.error('[Tutorial] driver.js 类未找到');
                return;
            }

            // 注意：此处不再立即创建 driver 实例，而是延迟到 startTutorialSteps 中
            // 这样可以确保按钮文本等配置能正确获取到最新的 i18n 翻译
            this.isInitialized = true;
            console.log('[Tutorial] driver.js 环境检测成功');

            // 检查是否需要自动启动引导
            this.checkAndStartTutorial();
        } catch (error) {
            console.error('[Tutorial] driver.js 初始化失败:', error);
        }
    }

    /**
     * 获取 driver.js 的统一配置
     */
    getDriverConfig() {
        return {
            padding: this.tutorialPadding,
            allowClose: true,
            overlayClickNext: false,
            animate: true,
            smoothScroll: true, // 启用平滑滚动
            className: 'neko-tutorial-driver',
            disableActiveInteraction: false,
            // i18n 按钮文本
            nextBtnText: this.t('tutorial.buttons.next', '下一步'),
            prevBtnText: this.t('tutorial.buttons.prev', '上一步'),
            doneBtnText: this.t('tutorial.buttons.done', '完成'),
            onDestroyStarted: () => {
                // 教程结束时，如果需要标记 hint 已显示
                if (this.shouldMarkHintShown) {
                    localStorage.setItem('neko_tutorial_reset_hint_shown', 'true');
                    this.shouldMarkHintShown = false;
                    console.log('[Tutorial] 已标记重置提示为已显示');
                }
            },
            onHighlighted: (element, step, options) => {
                // 去重机制说明：
                // 1. driver.js 内部切换步骤时会触发 onHighlighted。
                // 2. onStepChange 手动触发时也会调用此回调。
                // 3. 使用 _lastOnHighlightedStepIndex 记录最后一次处理的步骤索引，
                //    确保同一步骤的逻辑（特别是交互状态应用）只执行一次，避免竞争。
                // 每次高亮元素时，确保元素在视口中
                console.log('[Tutorial] 高亮元素:', step.element);

                // 调用步骤特定的 onHighlighted 回调（如果存在）
                if (step.onHighlighted && typeof step.onHighlighted === 'function') {
                    const currentStepIndex = (this.driver && typeof this.driver.currentStep === 'number')
                        ? this.driver.currentStep
                        : this.currentStep;
                    if (currentStepIndex === this._lastOnHighlightedStepIndex) {
                        console.log('[Tutorial] 跳过重复的 onHighlighted 回调:', step.element);
                    } else {
                        console.log('[Tutorial] 调用步骤特定的 onHighlighted 回调');
                        try {
                            step.onHighlighted.call(this);
                        } catch (error) {
                            console.error('[Tutorial] 步骤 onHighlighted 执行失败:', step.element, error);
                        }
                        this._lastOnHighlightedStepIndex = currentStepIndex;
                    }
                }

                // 给一点时间让 Driver.js 完成定位
                setTimeout(() => {
                    (async () => {
                        if (!window.isInTutorial) return;
                        if (element && element.element) {
                            const targetElement = element.element;
                            const rect = targetElement.getBoundingClientRect();
                            const isInViewport = (
                                rect.top >= 0 &&
                                rect.left >= 0 &&
                                rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                                rect.right <= (window.innerWidth || document.documentElement.clientWidth)
                            );
                            if (!isInViewport) {
                                console.log('[Tutorial] 元素不在视口中，滚动到元素');
                                targetElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            }
                        }

                        await this.applyTutorialInteractionState(step, 'highlight');

                        // 启用 popover 拖动功能
                        this.enablePopoverDragging();

                        // 确保 popover 完全在视口内（防止用户无法点击按钮）
                        this.clampPopoverToViewport();
                    })().catch(err => {
                        console.error('[Tutorial] onHighlighted 回调执行失败:', err);
                    });
                }, this.LAYOUT_REFRESH_DELAY);
            }
        };
    }

    /**
     * 重新创建 driver 实例以确保按钮文本使用最新的 i18n 翻译
     * 这个方法在启动引导时调用，此时 i18n 应该已经加载完成
     */
    recreateDriverWithI18n() {
        try {
            const DriverClass = window.driver;
            if (!DriverClass) {
                console.error('[Tutorial] driver.js 类未找到');
                return;
            }

            // 销毁现有的 driver 实例
            if (this.driver) {
                try {
                    this.driver.destroy();
                } catch (e) {
                    // 忽略销毁错误
                }
                this.driver = null;
            }

            // 重新创建 driver 实例，使用最新的 i18n 翻译
            this.driver = new DriverClass(this.getDriverConfig());

            console.log('[Tutorial] driver.js 重新创建成功，使用 i18n 按钮文本');
        } catch (error) {
            console.error('[Tutorial] driver.js 重新创建失败:', error);
            this.driver = null;
        }
    }

    /**
     * 获取当前页面的存储键（模型管理页区分 Live2D / VRM / MMD）
     */
    getYuiGuideVersionedPageKey(page = this.currentPage) {
        if (page === 'home' && this.isYuiGuideEnabledForPage(page)) {
            return 'home_yui_v1';
        }

        return null;
    }

    getPreferredStoragePageKey(page = this.currentPage) {
        if (page === 'model_manager') {
            const mode = UniversalTutorialManager.getModelManagerDisplayMode();
            const pageKey = UniversalTutorialManager.modelManagerModeToPageKey(mode);
            console.log('[Tutorial] 模型管理页存储键，展示模式:', mode, '→', pageKey);
            return pageKey;
        }

        return this.getYuiGuideVersionedPageKey(page) || page;
    }

    getStorageKey() {
        const pageKey = this.getPreferredStoragePageKey(this.currentPage);
        return getTutorialStorageKeyForPage(pageKey);
    }

    /**
     * 获取指定页面相关的所有存储键（用于重置/判断）
     */
    getStorageKeysForPage(page) {
        const targetPage = page || this.currentPage;
        if (targetPage === 'model_manager') {
            return ['model_manager', 'model_manager_live2d', 'model_manager_vrm', 'model_manager_mmd', 'model_manager_common']
                .map(getTutorialStorageKeyForPage);
        }

        const preferredPageKey = this.getPreferredStoragePageKey(targetPage);
        const pageKeys = [preferredPageKey];
        if (preferredPageKey !== targetPage) {
            pageKeys.push(targetPage);
        }

        return Array.from(new Set(pageKeys)).map(getTutorialStorageKeyForPage);
    }

    getManualStartIntentKey(page = null) {
        const targetPage = page || this.currentPage;
        return getTutorialManualIntentKeyForPage(targetPage);
    }

    markTutorialManualStartIntent(page = null) {
        const targetPage = page || this.currentPage;
        if (!targetPage || targetPage === 'unknown') {
            return;
        }
        localStorage.setItem(this.getManualStartIntentKey(targetPage), 'true');
    }

    peekTutorialStartSource(page = null) {
        const targetPage = page || this.currentPage;
        if (this.pendingTutorialStartSource) {
            return this.pendingTutorialStartSource;
        }

        const intentKey = this.getManualStartIntentKey(targetPage);
        if (localStorage.getItem(intentKey) === 'true') {
            return 'manual';
        }

        return null;
    }

    consumeTutorialStartSource(page = null) {
        const targetPage = page || this.currentPage;

        if (this.pendingTutorialStartSource) {
            const source = this.pendingTutorialStartSource;
            this.pendingTutorialStartSource = null;
            return source;
        }

        const intentKey = this.getManualStartIntentKey(targetPage);
        if (localStorage.getItem(intentKey) === 'true') {
            localStorage.removeItem(intentKey);
            return 'manual';
        }

        return 'auto';
    }

    waitUntilInitialized(maxWaitTime = 5000) {
        if (this.isInitialized) {
            return Promise.resolve(true);
        }

        this.waitForDriver();

        return new Promise(resolve => {
            const startedAt = Date.now();
            const poll = () => {
                if (this.isInitialized) {
                    resolve(true);
                    return;
                }
                if ((Date.now() - startedAt) >= maxWaitTime) {
                    resolve(false);
                    return;
                }
                setTimeout(poll, 100);
            };
            poll();
        });
    }

    async requestTutorialStart(source = 'manual', delayMs = 0) {
        const requestedSource = source || 'manual';
        this.pendingTutorialStartSource = requestedSource;
        this.logPromptFlow('request-tutorial-start', {
            page: this.currentPage,
            source: requestedSource,
            delayMs: delayMs || 0,
        });

        try {
            const ready = await this.waitUntilInitialized();
            if (!ready) {
                this.pendingTutorialStartSource = null;
                throw new Error('tutorial_not_initialized');
            }

            if (this.isTutorialRunning) {
                this.pendingTutorialStartSource = null;
                return true;
            }

            if (this.currentPage === 'home') {
                await this.waitForFloatingButtons();
                this.startTutorialWhenI18nReady(delayMs);
                return true;
            }

            if (this.currentPage === 'chara_manager') {
                await this.waitForCatgirlCards();
                await this.prepareCharaManagerForTutorial();
                this.startTutorialWhenI18nReady(delayMs);
                return true;
            }

            this.startTutorialWhenI18nReady(delayMs);
            return true;
        } catch (error) {
            this.pendingTutorialStartSource = null;
            throw error;
        }
    }

    clearModelManagerTutorialRecheckTimer() {
        if (this._modelManagerTutorialRecheckTimer) {
            clearTimeout(this._modelManagerTutorialRecheckTimer);
            this._modelManagerTutorialRecheckTimer = null;
        }
        if (this._modelManagerBootstrapFallbackTimer) {
            clearInterval(this._modelManagerBootstrapFallbackTimer);
            this._modelManagerBootstrapFallbackTimer = null;
        }
    }

    /**
     * 模型管理页：首次启动时 switchModelDisplay 可能尚未完成，validSteps 会为空；
     * 记忆浏览重置后若不刷新页面，也不会再次执行 checkAndStartTutorial。延迟补检一次。
     */
    scheduleModelManagerTutorialRecheck(delayMs = 8200) {
        if (this.currentPage !== 'model_manager') return;
        this.clearModelManagerTutorialRecheckTimer();
        this._modelManagerTutorialRecheckTimer = setTimeout(() => {
            this._modelManagerTutorialRecheckTimer = null;
            if (this.isTutorialRunning || window.isInTutorial) return;
            const sk = this.getStorageKey();
            if (localStorage.getItem(sk) === 'true') return;
            if (this._pendingI18nStart) {
                console.log('[Tutorial] 模型管理页补检时 i18n 仍在排队，延后由 i18n 就绪回调启动');
                return;
            }
            console.log('[Tutorial] 模型管理页延迟补检：未标记已看过，尝试再次启动引导');
            this.startTutorialWhenI18nReady(0);
        }, delayMs);
    }

    /**
     * 记忆浏览等处重置引导后，若当前就在模型管理页则重新检查是否应弹出教程
     */
    notifyTutorialResetForCurrentPageIfNeeded(pageKey) {
        if (pageKey !== 'model_manager' && pageKey !== 'all') return;
        if (this.currentPage !== 'model_manager') return;
        this.clearModelManagerTutorialRecheckTimer();
        this._pendingI18nStart = false;
        setTimeout(() => {
            if (this.isTutorialRunning || window.isInTutorial) return;
            this.setupModelManagerModeListener();
            this.maybeStartModelManagerTutorial(300, 'reset', null);
        }, 400);
    }

    /**
     * 检查是否需要自动启动引导
     */
    checkAndStartTutorial() {
        if (this.isTutorialRunning || window.isInTutorial) {
            console.log('[Tutorial] 引导进行中，跳过启动检查');
            return;
        }

        const handoffToken = this.consumePendingYuiGuideHandoffToken();
        if (handoffToken) {
            console.log('[Tutorial] 检测到跨页 handoff，强制恢复当前页面引导:', this.currentPage, handoffToken);
            this.startTutorialWhenI18nReady(500);
            return;
        }

        const storageKey = this.getStorageKey();
        const hasSeen = localStorage.getItem(storageKey);

        console.log('[Tutorial] 检查引导状态:',
            '页面:', this.currentPage,
            '键:', storageKey,
            '已看过:', hasSeen);

        if (!hasSeen) {
            // 对于主页，需要等待浮动按钮创建
            if (this.currentPage === 'home') {
                this.waitForFloatingButtons().then((found) => {
                    if (!found) {
                        console.warn('[Tutorial] 浮动按钮始终未出现，跳过主页引导');
                        return;
                    }
                    // 延迟启动，确保 DOM 完全加载，并等待 i18n 准备完成
                    this.startTutorialWhenI18nReady(1500);
                });
            } else if (this.currentPage === 'chara_manager') {
                // 对于角色管理页面，需要等待猫娘卡片加载
                this.waitForCatgirlCards().then(async () => {
                    // 先展开猫娘卡片和进阶设定，并为元素添加唯一 ID
                    await this.prepareCharaManagerForTutorial();
                    // 延迟启动，确保 DOM 完全加载，并等待 i18n 准备完成
                    this.startTutorialWhenI18nReady(500);
                });
            } else if (this.currentPage === 'model_manager') {
                // 首次加载由 neko-model-manager-mode-set 事件触发；restartCurrentTutorial 等
                // 清完存储键后重新走到这里时事件不会再次派发，需主动尝试启动
                this.maybeStartModelManagerTutorial(400, 'checkAndStart', null);
            } else {
                // 其他页面延迟启动，并等待 i18n 准备完成
                this.startTutorialWhenI18nReady(1500);
            }
        }
    }

    /**
     * 模型管理页：在展示模式就绪后尝试启动引导（eventMode 来自 neko-model-manager-mode-set 时优先）
     */
    maybeStartModelManagerTutorial(delayMs = 400, reason = '', eventMode = null) {
        if (this.currentPage !== 'model_manager') return;
        if (this.isTutorialRunning || window.isInTutorial) {
            console.log('[Tutorial] maybeStart 跳过: 引导正在运行', reason);
            return;
        }

        const resolvePageKey = () => {
            const mode = eventMode || UniversalTutorialManager.getModelManagerDisplayMode();
            return UniversalTutorialManager.modelManagerModeToPageKey(mode);
        };

        const pageKey = resolvePageKey();
        const storageKey = this.STORAGE_KEY_PREFIX + pageKey;
        if (localStorage.getItem(storageKey) === 'true') {
            console.log('[Tutorial] maybeStart 跳过: 已看过', reason, pageKey);
            return;
        }

        if (this._modelManagerTutorialDebounceTimer) {
            clearTimeout(this._modelManagerTutorialDebounceTimer);
        }
        this._modelManagerTutorialDebounceTimer = setTimeout(() => {
            this._modelManagerTutorialDebounceTimer = null;
            if (this.currentPage !== 'model_manager') return;
            if (this.isTutorialRunning || window.isInTutorial) {
                console.log('[Tutorial] maybeStart debounce 跳过: 引导正在运行', reason);
                return;
            }
            const pk = resolvePageKey();
            const sk = this.STORAGE_KEY_PREFIX + pk;
            if (localStorage.getItem(sk) === 'true') {
                console.log('[Tutorial] maybeStart debounce 跳过: 已看过', reason, pk);
                return;
            }
            console.log('[Tutorial] 模型管理页尝试启动引导:', reason, pk);
            this._pendingI18nStart = false;
            this.startTutorialWhenI18nReady(delayMs);
            this.scheduleModelManagerTutorialRecheck(8500);
        }, 320);
    }

    /**
     * 监听 model_manager 展示模式稳定事件（由 switchModelDisplay 派发）
     */
    setupModelManagerModeListener() {
        if (this._modelManagerModeListenerAttached) return;
        this._modelManagerModeListenerAttached = true;
        this._modelManagerModeHandler = (ev) => {
            if (this.currentPage !== 'model_manager') return;
            const mode = ev.detail && ev.detail.mode;
            console.log('[Tutorial] 收到 neko-model-manager-mode-set 事件, mode:', mode);
            if (!mode || !['live2d', 'vrm', 'mmd'].includes(mode)) {
                return;
            }
            this._modelManagerReceivedModeEvent = true;
            setTimeout(() => {
                this.maybeStartModelManagerTutorial(200, 'mode-set', mode);
            }, 80);
        };
        window.addEventListener('neko-model-manager-mode-set', this._modelManagerModeHandler);
        console.log('[Tutorial] neko-model-manager-mode-set 监听器已设置');
    }

    /**
     * 清理模型管理页相关的事件监听和定时器，防止实例替换后幽灵回调
     */
    teardownModelManagerListeners() {
        if (this._modelManagerModeHandler) {
            window.removeEventListener('neko-model-manager-mode-set', this._modelManagerModeHandler);
            this._modelManagerModeHandler = null;
        }
        this._modelManagerModeListenerAttached = false;
        this._modelManagerReceivedModeEvent = false;
        this.clearModelManagerTutorialRecheckTimer();
        if (this._modelManagerTutorialDebounceTimer) {
            clearTimeout(this._modelManagerTutorialDebounceTimer);
            this._modelManagerTutorialDebounceTimer = null;
        }
    }

    /**
     * 模型管理页定时轮询兜底：switchModelDisplay 可能因 VRM 初始化耗时（最长 8s）而延迟派发事件，
     * 单次定时器容易在事件到达前就已过期。改为每 3 秒轮询一次，直到引导启动或确认已看过。
     */
    scheduleModelManagerBootstrapFallback() {
        if (this.currentPage !== 'model_manager') return;
        if (this._modelManagerBootstrapFallbackTimer) {
            clearInterval(this._modelManagerBootstrapFallbackTimer);
        }
        let pollCount = 0;
        const maxPolls = 8;
        this._modelManagerBootstrapFallbackTimer = setInterval(() => {
            pollCount++;
            if (this.currentPage !== 'model_manager' || pollCount > maxPolls) {
                clearInterval(this._modelManagerBootstrapFallbackTimer);
                this._modelManagerBootstrapFallbackTimer = null;
                return;
            }
            if (this.isTutorialRunning || window.isInTutorial) {
                console.log('[Tutorial] 模型管理页兜底轮询: 引导已在运行，停止轮询');
                clearInterval(this._modelManagerBootstrapFallbackTimer);
                this._modelManagerBootstrapFallbackTimer = null;
                return;
            }
            const sk = this.getStorageKey();
            if (localStorage.getItem(sk) === 'true') {
                console.log('[Tutorial] 模型管理页兜底轮询: 已看过', sk);
                clearInterval(this._modelManagerBootstrapFallbackTimer);
                this._modelManagerBootstrapFallbackTimer = null;
                return;
            }
            console.log('[Tutorial] 模型管理页兜底轮询 #' + pollCount + ' 尝试启动引导');
            this.maybeStartModelManagerTutorial(0, 'bootstrap-poll-' + pollCount, null);
        }, 3000);
    }

    /**
     * 获取当前页面的引导步骤配置
     */
    getStepsForPage() {
        console.log('[Tutorial] getStepsForPage 被调用，当前页面:', this.currentPage);

        const configs = {
            home: this.getHomeSteps(),
            model_manager: this.getModelManagerSteps(),
            parameter_editor: this.getParameterEditorSteps(),
            emotion_manager: this.getEmotionManagerSteps(),
            chara_manager: this.getCharaManagerSteps(),
            settings: this.getSettingsSteps(),
            voice_clone: this.getVoiceCloneSteps(),
            steam_workshop: this.getSteamWorkshopSteps(),
            memory_browser: this.getMemoryBrowserSteps(),
        };

        let steps = configs[this.currentPage] || [];

        // 如果是主页且有步骤，且提示还没显示过，添加最后的提示步骤
        const hintShown = localStorage.getItem('neko_tutorial_reset_hint_shown');
        if (steps.length > 0 && this.currentPage === 'home' && !hintShown) {
            steps = [...steps, this.getTutorialResetHintStep()];
            // 标记需要在教程结束时设置 hint 已显示
            this.shouldMarkHintShown = true;
        } else {
            this.shouldMarkHintShown = false;
        }

        console.log('[Tutorial] 返回的步骤数:', steps.length);
        if (steps.length > 0) {
            console.log('[Tutorial] 第一个步骤元素:', steps[0].element);
        }

        return steps;
    }

    /**
     * 获取引导结束提示步骤（告知用户可以在记忆浏览重置引导）
     */
    getTutorialResetHintStep() {
        return {
            element: 'body',
            popover: {
                title: this.t('tutorial.resetHint.title', '✨ 引导完成'),
                description: this.t('tutorial.resetHint.desc', '如果想再次查看引导，可以前往「记忆浏览」页面，在「新手引导」区域重置。'),
            },
            disableActiveInteraction: true
        };
    }

    /**
     * 主页引导步骤
     */
    getHomeSteps() {
        const t = (key, fallback) => this.t(key, fallback);
        // 根据当前模型类型动态选择元素前缀（live2d / vrm / mmd）
        const p = UniversalTutorialManager.detectModelPrefix();

        return [
            {
                element: `#${p}-container`,
                popover: {
                    title: window.t ? window.t('tutorial.step1.title', '👋 欢迎来到 N.E.K.O') : '👋 欢迎来到 N.E.K.O',
                    description: window.t ? window.t('tutorial.step1.desc', '这是你的猫娘！接下来我会带你熟悉各项功能~') : '这是你的猫娘！接下来我会带你熟悉各项功能~',
                },
                disableActiveInteraction: false
            },
            {
                element: `#${p}-container`,
                popover: {
                    title: window.t ? window.t('tutorial.step1b.title', '🖱️ 拖拽与缩放') : '🖱️ 拖拽与缩放',
                    description: window.t ? window.t('tutorial.step1b.desc', '你可以拖拽猫娘移动位置，也可以用<strong>鼠标滚轮</strong>放大缩小，试试看吧~') : '你可以拖拽猫娘移动位置，也可以用<strong>鼠标滚轮</strong>放大缩小，试试看吧~',
                },
                disableActiveInteraction: false,
                enableModelInteraction: true
            },
            {
                element: `#${p}-lock-icon`,
                popover: {
                    title: window.t ? window.t('tutorial.step1c.title', '🔒 锁定猫娘') : '🔒 锁定猫娘',
                    description: window.t ? window.t('tutorial.step1c.desc', '点击这个锁可以锁定猫娘位置，防止误触移动。锁定后周围的浮动工具栏也不会再出现。再次点击可以解锁~') : '点击这个锁可以锁定猫娘位置，防止误触移动。锁定后周围的浮动工具栏也不会再出现。再次点击可以解锁~',
                },
                disableActiveInteraction: true
            },
            {
                element: `#${p}-floating-buttons`,
                popover: {
                    title: window.t ? window.t('tutorial.step5.title', '🎛️ 浮动工具栏') : '🎛️ 浮动工具栏',
                    description: window.t ? window.t('tutorial.step5.desc', '浮动工具栏包含多个实用功能按钮，让我为你逐一介绍~') : '浮动工具栏包含多个实用功能按钮，让我为你逐一介绍~',
                },
                disableActiveInteraction: true
            },
            {
                element: `#${p}-btn-mic`,
                popover: {
                    title: window.t ? window.t('tutorial.step6.title', '🎤 语音控制') : '🎤 语音控制',
                    description: window.t ? window.t('tutorial.step6.desc', '启用语音控制，猫娘通过语音识别理解你的话语~') : '启用语音控制，猫娘通过语音识别理解你的话语~',
                },
                disableActiveInteraction: true
            },
            {
                element: `#${p}-btn-screen`,
                popover: {
                    title: window.t ? window.t('tutorial.step7.title', '🖥️ 屏幕分享') : '🖥️ 屏幕分享',
                    description: window.t ? window.t('tutorial.step7.desc', '开启后会持续地将屏幕分享给猫娘，只能在语音对话期间使用~') : '开启后会持续地将屏幕分享给猫娘，只能在语音对话期间使用~',
                },
                disableActiveInteraction: true
            },
            {
                element: `#${p}-btn-agent`,
                popover: {
                    title: window.t ? window.t('tutorial.step8.title', '🔨 OpenClaw') : '🔨 OpenClaw',
                    description: window.t ? window.t('tutorial.step8.desc', '打开猫爪面板，使用 computer use、browser use 和用户插件等功能。让猫娘使用你的电脑、帮你工作、陪你游戏~') : '打开猫爪面板，使用 computer use、browser use 和用户插件等功能。让猫娘使用你的电脑、帮你工作、陪你游戏~',
                },
                yuiGuideSceneId: 'intro_cat_paw',
                disableActiveInteraction: true
            },
            {
                element: `#${p}-btn-goodbye`,
                popover: {
                    title: window.t ? window.t('tutorial.step9.title', '💤 请她离开') : '💤 请她离开',
                    description: window.t ? window.t('tutorial.step9.desc', '让猫娘暂时离开并隐藏界面，需要时可点击\"请她回来\"恢复~ <strong>当她出现问题时，让她离开休息一会儿，往往能解决问题。</strong>') : '让猫娘暂时离开并隐藏界面，需要时可点击\"请她回来\"恢复~ <strong>当她出现问题时，让她离开休息一会儿，往往能解决问题。</strong>',
                },
                disableActiveInteraction: true
            },
            {
                element: `#${p}-btn-settings`,
                popover: {
                    title: window.t ? window.t('tutorial.step10.title', '⚙️ 设置') : '⚙️ 设置',
                    description: window.t ? window.t('tutorial.step10.desc', '打开设置面板，下面会依次介绍设置里的各个项目~') : '打开设置面板，下面会依次介绍设置里的各个项目~',
                },
                action: 'click',
                yuiGuideSceneId: 'takeover_settings_peek',
                disableActiveInteraction: true
            },
            {
                element: `#${p}-toggle-proactive-chat`,
                popover: {
                    title: window.t ? window.t('tutorial.step13.title', '💬 主动搭话') : '💬 主动搭话',
                    description: window.t ? window.t('tutorial.step13.desc', '开启后猫娘会主动发起对话，频率可在此调整~') : '开启后猫娘会主动发起对话，频率可在此调整~',
                },
                yuiGuideSceneId: 'intro_proactive',
                disableActiveInteraction: true
            },
            {
                element: `#${p}-toggle-proactive-vision`,
                popover: {
                    title: window.t ? window.t('tutorial.step14.title', '👀 自主视觉') : '👀 自主视觉',
                    description: window.t ? window.t('tutorial.step14.desc', '与语音会话中实时传输的屏幕分享不同，开启自主视觉后猫娘会时不时自己看一眼你的屏幕。间隔可在此调整~') : '与语音会话中实时传输的屏幕分享不同，开启自主视觉后猫娘会时不时自己看一眼你的屏幕。间隔可在此调整~',
                },
                disableActiveInteraction: true
            },
            {
                element: `#${p}-menu-character`,
                popover: {
                    title: window.t ? window.t('tutorial.step15.title', '👤 角色管理') : '👤 角色管理',
                    description: window.t ? window.t('tutorial.step15.desc', '调整猫娘的性格、形象、声音等~') : '调整猫娘的性格、形象、声音等~',
                },
                disableActiveInteraction: true
            },
            {
                element: `#${p}-menu-api-keys`,
                popover: {
                    title: window.t ? window.t('tutorial.step16.title', '🔑 API 密钥') : '🔑 API 密钥',
                    description: window.t ? window.t('tutorial.step16.desc', '配置 AI 服务的 API 密钥，这是和猫娘互动的必要配置~') : '配置 AI 服务的 API 密钥，这是和猫娘互动的必要配置~',
                },
                yuiGuideSceneId: 'handoff_api_key',
                disableActiveInteraction: true
            },
            {
                element: `#${p}-menu-memory`,
                popover: {
                    title: window.t ? window.t('tutorial.step17.title', '🧠 记忆浏览') : '🧠 记忆浏览',
                    description: window.t ? window.t('tutorial.step17.desc', '查看与管理猫娘的记忆内容~') : '查看与管理猫娘的记忆内容~',
                },
                yuiGuideSceneId: 'handoff_memory_browser',
                disableActiveInteraction: true
            },
            {
                element: `#${p}-menu-steam-workshop`,
                popover: {
                    title: window.t ? window.t('tutorial.step18.title', '🛠️ 创意工坊') : '🛠️ 创意工坊',
                    description: window.t ? window.t('tutorial.step18.desc', '进入 Steam 创意工坊页面，管理订阅内容~') : '进入 Steam 创意工坊页面，管理订阅内容~',
                },
                yuiGuideSceneId: 'handoff_steam_workshop',
                disableActiveInteraction: true
            },
            {
                element: 'body',
                popover: {
                    title: t('tutorial.systray.location.title', '🖥️ 托盘图标位置'),
                    description: `
                        <div class="neko-systray-location">
                            <img
                                src="/static/icons/stray_intro.png"
                                alt="${this.safeEscapeHtml(t('tutorial.systray.location.alt', '系统托盘位置示例'))}"
                                class="neko-systray-location__image"
                            />
                            <div class="neko-systray-location__caption">
                                ${this.safeEscapeHtml(t('tutorial.systray.location.desc', 'N.E.K.O 图标会出现在屏幕右下角的系统托盘中，点击它即可找到 N.E.K.O。'))}
                            </div>
                            <div class="neko-systray-location__note">
                                ${this.safeEscapeHtml(t('tutorial.systray.location.note', '如果看不到，可点击托盘展开箭头查看隐藏的图标。'))}
                            </div>
                        </div>
                    `
                },
                disableActiveInteraction: true
            },
            {
                element: 'body',
                popover: {
                    title: t('tutorial.systray.menu.title', '📋 托盘菜单'),
                    description: `
                        <div class="neko-systray-menu">
                            <div class="neko-systray-menu__hint">
                                <strong>${this.safeEscapeHtml(t('tutorial.systray.important', '重要：'))}</strong>
                                ${this.safeEscapeHtml(t('tutorial.systray.menu.desc', '右键点击系统托盘（见上一步提示）中的 N.E.K.O 图标即可打开菜单。以下是一些常用功能：'))}
                            </div>
                            <div class="neko-systray-menu__panel">
                                <div class="neko-systray-menu__item">
                                    <div class="neko-systray-menu__item-label">
                                        ${this.safeEscapeHtml(t('tutorial.systray.resetPosition', '重置角色位置'))}
                                    </div>
                                    <div class="neko-systray-menu__item-desc">
                                        ${this.safeEscapeHtml(t('tutorial.systray.resetPositionDesc', '猫娘跑到屏幕外时，点此恢复默认位置~'))}
                                    </div>
                                </div>
                                <div class="neko-systray-menu__separator"></div>
                                <div class="neko-systray-menu__item">
                                    <div class="neko-systray-menu__item-label">
                                        ${this.safeEscapeHtml(t('tutorial.systray.openChat', '打开对话框'))}
                                    </div>
                                    <div class="neko-systray-menu__item-desc">
                                        ${this.safeEscapeHtml(t('tutorial.systray.openChatDesc', '打开独立的对话框进行文字对话~'))}
                                    </div>
                                </div>
                                <div class="neko-systray-menu__separator"></div>
                                <div class="neko-systray-menu__item">
                                    <div class="neko-systray-menu__item-label">
                                        ${this.safeEscapeHtml(t('tutorial.systray.hotkey', '快捷键设置'))}
                                    </div>
                                    <div class="neko-systray-menu__item-desc">
                                        ${this.safeEscapeHtml(t('tutorial.systray.hotkeyDesc', '设置全局快捷键，更高效地控制 N.E.K.O~'))}
                                    </div>
                                </div>
                                <div class="neko-systray-menu__separator"></div>
                                <div class="neko-systray-menu__item neko-systray-menu__item--danger">
                                    <div class="neko-systray-menu__item-label">
                                        ${this.safeEscapeHtml(t('tutorial.systray.exit', '退出'))}
                                    </div>
                                    <div class="neko-systray-menu__item-desc">
                                        ${this.safeEscapeHtml(t('tutorial.systray.exitDesc', '关闭 N.E.K.O。托盘菜单是退出应用的主要方式~'))}
                                    </div>
                                </div>
                            </div>
                        </div>
                    `
                },
                disableActiveInteraction: true
            }
        ];
    }

    /**
     * 模型管理页面引导步骤
     */
    getModelManagerSteps() {
        const mode = UniversalTutorialManager.getModelManagerDisplayMode();
        console.log('[Tutorial] 模型管理页面 - 展示模式:', mode);

        // Live2D 特定步骤
        const live2dSteps = [
            {
                element: '#persistent-expression-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.live2d.step4.title', '🧷 常驻表情'),
                    description: this.t('tutorial.model_manager.live2d.step4.desc', '选择一个常驻表情，让模型持续保持该表情，直到你再次更改。'),
                }
            },
            {
                element: '#emotion-config-btn',
                popover: {
                    title: this.t('tutorial.model_manager.live2d.step5.title', '😄 情感配置'),
                    description: this.t('tutorial.model_manager.live2d.step5.desc', '进入前请先选择一个模型。点击这里配置 Live2D 模型的情感表现，可为不同的情感设置对应的表情和动作组合。'),
                }
            },
            {
                element: '#parameter-editor-btn',
                popover: {
                    title: this.t('tutorial.model_manager.live2d.step6.title', '✨ 捏脸系统'),
                    description: this.t('tutorial.model_manager.live2d.step6.desc', '点击这里进入捏脸系统，可以精细调整 Live2D 模型的面部参数，打造独特的猫娘形象。'),
                }
            }
        ];

        // VRM 特定步骤
        const vrmSteps = [
            {
                element: '#ambient-light-control',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step6.title', '🌟 环境光'),
                    description: this.t('tutorial.model_manager.vrm.step6.desc', '调整环境光强度。环境光影响整体亮度，数值越高模型越亮。'),
                }
            },
            {
                element: '#main-light-control',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step7.title', '☀️ 主光源'),
                    description: this.t('tutorial.model_manager.vrm.step7.desc', '调整主光源强度。主光源是主要的照明来源，影响模型的明暗对比。'),
                }
            },
            {
                element: '#exposure-control',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step8.title', '🌞 曝光'),
                    description: this.t('tutorial.model_manager.vrm.step8.desc', '调整整体曝光强度。数值越高整体越亮，越低则更暗更有对比。'),
                }
            },
            {
                element: '#tonemapping-control',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step9.title', '🎞️ 色调映射'),
                    description: this.t('tutorial.model_manager.vrm.step9.desc', '选择不同的色调映射算法，决定画面亮部和暗部的呈现风格。'),
                }
            }
        ];

        // MMD（Live3D 子类型）：常驻表情/捏脸/Live2D 情感按钮在此模式下隐藏，勿用 Live2D 步骤
        const mmdSteps = [
            {
                element: '#vrm-model-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.mmd.step1.title', '🎭 选择 MMD 模型'),
                    description: this.t('tutorial.model_manager.mmd.step1.desc', '在 Live3D（MMD）模式下从这里选择要使用的模型。MMD 与 VRM 共用同一模型列表。'),
                }
            },
            {
                element: '#mmd-animation-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.mmd.step2.title', '💃 VMD 动画'),
                    description: this.t('tutorial.model_manager.mmd.step2.desc', '为 MMD 模型选择 VMD 动作。也可使用「导入 VMD 动画」添加自定义动作文件。'),
                }
            },
            {
                element: '#mmd-ambient-intensity-slider',
                popover: {
                    title: this.t('tutorial.model_manager.mmd.step3.title', '🌟 MMD 光照'),
                    description: this.t('tutorial.model_manager.mmd.step3.desc', '在「MMD 模型设置」中调节环境光、主光源、曝光与色调映射等，控制 3D 画面效果。'),
                }
            },
            {
                element: '#live3d-emotion-config-btn',
                popover: {
                    title: this.t('tutorial.model_manager.mmd.step4.title', '😄 情感配置'),
                    description: this.t('tutorial.model_manager.mmd.step4.desc', '先选好模型后，可由此进入情感配置，为不同情感设置表现（Live3D 下 MMD 与 VRM 共用此入口）。'),
                }
            }
        ];

        if (mode === 'mmd') return mmdSteps;
        if (mode === 'vrm') return vrmSteps;
        return live2dSteps;
    }

    /**
     * Live2D 捏脸系统页面引导步骤
     */
    getParameterEditorSteps() {
        return [
            {
                element: '#model-select-btn',
                popover: {
                    title: this.t('tutorial.parameter_editor.step1.title', '🎭 选择模型'),
                    description: this.t('tutorial.parameter_editor.step1.desc', '首先选择要编辑的 Live2D 模型。只有选择了模型后，才能调整参数。'),
                }
            },
            {
                element: '#parameters-list',
                popover: {
                    title: this.t('tutorial.parameter_editor.step2.title', '🎨 参数列表'),
                    description: this.t('tutorial.parameter_editor.step2.desc', '这里显示了模型的所有可调参数。每个参数控制模型的不同部分，如眼睛大小、嘴巴形状、头部角度等。'),
                }
            }
        ];
    }

    /**
     * Live2D 情感管理页面引导步骤
     */
    getEmotionManagerSteps() {
        return [
            {
                element: '#model-singleselect',
                popover: {
                    title: this.t('tutorial.emotion_manager.step1.title', '🎭 选择模型'),
                    description: this.t('tutorial.emotion_manager.step1.desc', '首先选择要配置情感的 Live2D 模型。每个模型可以有独立的情感配置。选好模型后才能进入下一步。'),
                }
            },
            {
                element: '#emotion-config',
                popover: {
                    title: this.t('tutorial.emotion_manager.step2.title', '😊 情感配置区域'),
                    description: this.t('tutorial.emotion_manager.step2.desc', '这里可以为不同的情感（如开心、悲伤、生气等）配置对应的表情和动作组合。猫娘会根据对话内容自动切换情感表现。'),
                },
                // 避免在引导开始时强制显示（应在选择模型后显示）
                skipAutoShow: true
            },
            {
                element: '#reset-btn',
                popover: {
                    title: this.t('tutorial.emotion_manager.step3.title', '🔄 重置配置'),
                    description: this.t('tutorial.emotion_manager.step3.desc', '点击这个按钮可以将情感配置重置为默认值。'),
                },
                skipAutoShow: true
            }
        ];
    }

    /**
     * 角色管理页面引导步骤）
     */
    getCharaManagerSteps() {
        return [
            {
                element: '#master-section',
                popover: {
                    title: this.t('tutorial.chara_manager.step1.title', '👤 主人档案'),
                    description: this.t('tutorial.chara_manager.step1.desc', '这是您的主人档案。填写您的信息后，猫娘会根据这些信息来称呼您。'),
                }
            },
            {
                element: '#catgirl-section',
                popover: {
                    title: this.t('tutorial.chara_manager.step6.title', '🐱 猫娘档案'),
                    description: this.t('tutorial.chara_manager.step6.desc', '这里可以创建和管理多个猫娘角色。每个角色都有独特的性格设定。'),
                }
            },
            {
                element: '.catgirl-block:first-child button[id^="switch-btn-"]',
                popover: {
                    title: this.t('tutorial.chara_manager.step11.title', '🔄 切换猫娘'),
                    description: this.t('tutorial.chara_manager.step11.desc', '点击此按钮可以将这个猫娘设为当前活跃角色。切换后，主页会使用该角色的形象和性格。'),
                }
            }
        ];
    }

    /**
     * 设置页面引导步骤
     */
    getSettingsSteps() {
        // 原生 #coreApiSelect 增强后为 1×1 隐藏；Driver 按 getBoundingClientRect() 画框，必须高亮可见按钮。
        // 使用 api_key_settings.js 为 trigger 分配的固定 id（不依赖 :has()，避免旧版 Chromium/CEF 与 querySelector 多分支顺序问题）。
        return [
            {
                element: '#coreApiSelect-dropdown-trigger',
                popover: {
                    title: this.t('tutorial.settings.step2.title', '🔑 核心 API 服务商'),
                    description: this.t('tutorial.settings.step2.desc', '这是最重要的设置。核心 API 负责对话功能。\n\n• 免费版：完全免费，无需 API Key，适合新手体验\n• 阿里：有免费额度，功能全面\n• 智谱：有免费额度，支持联网搜索\n• OpenAI：智能水平最高，但需要翻墙且价格昂贵'),
                },
                yuiGuideSceneId: 'api_key_intro'
            },
            {
                element: '#apiKeyInput',
                popover: {
                    title: this.t('tutorial.settings.step3.title', '📝 核心 API Key'),
                    description: this.t('tutorial.settings.step3.desc', '将您选择的 API 服务商的 API Key 粘贴到这里。如果选择了免费版，这个字段可以留空。'),
                }
            }
        ];
    }

    /**
     * 语音克隆页面引导步骤
     */
    getVoiceCloneSteps() {
        return [
            {
                element: '.alibaba-api-notice',
                popover: {
                    title: this.t('tutorial.voice_clone.step1.title', '⚠️ 重要提示'),
                    description: this.t('tutorial.voice_clone.step1.desc', '语音克隆功能需要使用阿里云 API。请确保您已经在 API 设置中配置了阿里云的 API Key。'),
                }
            },
            {
                element: '#refLanguage',
                popover: {
                    title: this.t('tutorial.voice_clone.step2.title', '🌍 选择参考音频语言'),
                    description: this.t('tutorial.voice_clone.step2.desc', '选择您上传的音频文件的语言。这帮助系统更准确地识别和克隆声音特征。'),
                }
            },
            {
                element: '#prefix',
                popover: {
                    title: this.t('tutorial.voice_clone.step3.title', '🏷️ 自定义前缀'),
                    description: this.t('tutorial.voice_clone.step3.desc', '输入一个 10 字符以内的前缀（只能用数字和英文字母）。这个前缀会作为克隆音色的标识。'),
                }
            },
            {
                element: '.register-voice-btn',
                popover: {
                    title: this.t('tutorial.voice_clone.step4.title', '✨ 注册音色'),
                    description: this.t('tutorial.voice_clone.step4.desc', '点击这个按钮开始克隆您的音色。系统会处理音频并生成一个独特的音色 ID。'),
                }
            },
            {
                element: '.voice-list-section',
                popover: {
                    title: this.t('tutorial.voice_clone.step5.title', '📋 已注册音色列表'),
                    description: this.t('tutorial.voice_clone.step5.desc', '这里显示所有已成功克隆的音色。您可以在角色管理中选择这些音色来为猫娘配音。'),
                }
            }
        ];
    }

    /**
     * Steam Workshop 页面引导步骤
     */
    getSteamWorkshopSteps() {
        return [
            {
                element: '#workshop-tabs',
                popover: {
                    title: this.t('tutorial.steam_workshop.step1.title', '🧭 创意工坊分区'),
                    description: this.t('tutorial.steam_workshop.step1.desc', '这里可以在订阅内容和角色卡之间切换，后续管理 Workshop 内容都会从这里展开。'),
                },
                yuiGuideSceneId: 'steam_workshop_intro'
            },
            {
                element: '#subscriptions-list',
                popover: {
                    title: this.t('tutorial.steam_workshop.step2.title', '📦 订阅内容列表'),
                    description: this.t('tutorial.steam_workshop.step2.desc', '这里会展示当前已订阅的内容，您可以刷新、筛选并继续管理创意工坊资源。'),
                }
            }
        ];
    }

    /**
     * 内存浏览器页面引导步骤
     */
    getMemoryBrowserSteps() {
        return [
            {
                element: '#memory-file-list',
                popover: {
                    title: this.t('tutorial.memory_browser.step2.title', '🐱 猫娘记忆库'),
                    description: this.t('tutorial.memory_browser.step2.desc', '这里列出了所有猫娘的记忆库。点击一个猫娘的名称可以查看和编辑她的对话历史。'),
                },
                yuiGuideSceneId: 'memory_browser_intro'
            },
            {
                element: '#memory-chat-edit',
                popover: {
                    title: this.t('tutorial.memory_browser.step4.title', '📝 聊天记录编辑'),
                    description: this.t('tutorial.memory_browser.step4.desc', '这里显示选中猫娘的所有对话记录。您可以在这里查看、编辑或删除特定的对话内容。'),
                }
            }
        ];
    }

    /**
     * 检查元素是否可见
     */
    isElementVisible(element) {
        if (!element) return false;

        // 检查 display 属性
        const style = window.getComputedStyle(element);
        if (style.display === 'none') {
            return false;
        }

        // 检查 visibility 属性
        if (style.visibility === 'hidden') {
            return false;
        }

        // 检查 opacity 属性
        if (style.opacity === '0') {
            return false;
        }

        // 检查元素是否在视口内或至少有尺寸
        const rect = element.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) {
            return false;
        }

        return true;
    }

    /**
     * 是否已加载 Live2D 模型（用于情感配置等前置判断）
     */
    hasLive2DModelLoaded() {
        const live2dManager = window.live2dManager;
        if (live2dManager && typeof live2dManager.getCurrentModel === 'function') {
            return !!live2dManager.getCurrentModel();
        }
        return false;
    }

    /**
     * 情感配置页面是否已选择模型
     */
    hasEmotionManagerModelSelected() {
        const select = document.querySelector('#model-select');
        return !!(select && select.value);
    }

    /**
     * 情感配置页面是否已有可选模型项（非占位空值）
     */
    hasEmotionManagerSelectableModels() {
        const select = document.querySelector('#model-select');
        if (!select) return false;
        return Array.from(select.options || []).some(option => option && option.value);
    }

    /**
     * 设置“下一步”按钮状态
     */
    setNextButtonState(enabled, disabledTitle = '') {
        const nextBtn = document.querySelector('.driver-next');
        if (!nextBtn) return;

        nextBtn.disabled = !enabled;
        nextBtn.style.pointerEvents = enabled ? 'auto' : 'none';
        nextBtn.style.opacity = enabled ? '1' : '0.5';
        nextBtn.title = enabled ? '' : disabledTitle;
    }

    /**
     * 清理“下一步”按钮的前置校验
     */
    clearNextButtonGuard() {
        if (this.nextButtonGuardTimer) {
            clearInterval(this.nextButtonGuardTimer);
            this.nextButtonGuardTimer = null;
        }

        if (this.nextButtonGuardActive) {
            this.setNextButtonState(true);
            this.nextButtonGuardActive = false;
        }
    }

    /**
     * 显示隐藏的元素（用于引导）
     */
    showElementForTutorial(element, selector) {
        if (!element) return;

        const style = window.getComputedStyle(element);

        // 保存元素的原始内联样式和类名（如果还未保存）
        if (!this.modifiedElementsMap.has(element)) {
            this.modifiedElementsMap.set(element, {
                originalInlineStyle: element.getAttribute('style') || '',
                originalClassName: element.className,
                modifiedProperties: []
            });
            console.log(`[Tutorial] 已保存元素原始样式: ${selector}`);
        }

        const elementRecord = this.modifiedElementsMap.get(element);

        // 显示元素（使用 !important 确保样式被应用）
        if (style.display === 'none') {
            element.style.setProperty('display', 'flex', 'important');
            elementRecord.modifiedProperties.push('display');
            console.log(`[Tutorial] 显示隐藏元素: ${selector}`);
        }

        if (style.visibility === 'hidden') {
            element.style.setProperty('visibility', 'visible', 'important');
            elementRecord.modifiedProperties.push('visibility');
            console.log(`[Tutorial] 恢复隐藏元素可见性: ${selector}`);
        }

        if (style.opacity === '0') {
            element.style.setProperty('opacity', '1', 'important');
            elementRecord.modifiedProperties.push('opacity');
            console.log(`[Tutorial] 恢复隐藏元素透明度: ${selector}`);
        }

        // 特殊处理浮动工具栏：确保它在引导中保持可见
        if (selector.endsWith('-floating-buttons')) {
            // 标记浮动工具栏在引导中，防止自动隐藏
            element.dataset.inTutorial = 'true';
            console.log('[Tutorial] 浮动工具栏已标记为引导中');
        }

        return { originalDisplay: element.style.display, originalVisibility: element.style.visibility, originalOpacity: element.style.opacity };
    }

    getTutorialInteractiveSelectors() {
        return [
            '#live2d-canvas', '#vrm-canvas', '#mmd-canvas',
            '#live2d-container', '#vrm-container', '#mmd-container',
            '#chat-container',
            '#live2d-floating-buttons', '#vrm-floating-buttons', '#mmd-floating-buttons',
            '#live2d-return-button-container', '#vrm-return-button-container', '#mmd-return-button-container',
            '#live2d-btn-return', '#vrm-btn-return', '#mmd-btn-return',
            '#resetSessionButton',
            '#returnSessionButton',
            '#live2d-lock-icon', '#vrm-lock-icon', '#mmd-lock-icon',
            '#toggle-chat-btn',
            '.live2d-floating-btn', '.vrm-floating-btn', '.mmd-floating-btn',
            '.live2d-trigger-btn', '.vrm-trigger-btn', '.mmd-trigger-btn',
            // 宽泛匹配：所有以模型前缀开头 ID 的元素都将被教程系统自动识别并控制交互状态
            '[id^="live2d-"]', '[id^="vrm-"]', '[id^="mmd-"]'
        ];
    }

    isTutorialControlledElement(element) {
        if (!element) return false;

        // 复用选择器列表进行匹配检查
        const selectors = this.getTutorialInteractiveSelectors();
        const isMatched = selectors.some(selector => {
            try {
                return element.matches(selector) || (element.closest && element.closest(selector));
            } catch (e) {
                console.warn(`[Tutorial] 选择器匹配失败: ${selector}`, e);
                return false;
            }
        });

        return isMatched;
    }

    collectTutorialControlledElements(steps = []) {
        const elements = new Set();
        const selectors = this.getTutorialInteractiveSelectors();
        selectors.forEach(selector => {
            document.querySelectorAll(selector).forEach(element => { elements.add(element); });
        });
        steps.forEach(step => {
            const element = document.querySelector(step.element);
            if (element && this.isTutorialControlledElement(element)) {
                elements.add(element);
            }
        });
        this.tutorialControlledElements = elements;
        console.log(`[Tutorial] 已收集交互元素: ${elements.size}`);
    }

    setTutorialMarkersVisible(visible, options = {}) {
        const overlay = document.querySelector('.driver-overlay');
        const highlight = document.querySelector('.driver-highlight');
        const popover = document.querySelector('.driver-popover');
        const elements = [overlay, highlight, popover].filter(Boolean);

        if (!this.tutorialMarkerDisplayCache) {
            this.tutorialMarkerDisplayCache = new Map();
        }

        if (!visible) {
            const keepPopover = options.keepPopover === true;
            elements.forEach(element => {
                // 如果指定保留弹窗且当前元素是弹窗，则跳过隐藏
                if (keepPopover && element === popover) return;

                if (!this.tutorialMarkerDisplayCache.has(element)) {
                    this.tutorialMarkerDisplayCache.set(element, element.style.visibility);
                }
                // 使用 visibility: hidden 代替 display: none，保持布局占位，过渡更平滑
                element.style.visibility = 'hidden';
            });
            return;
        }

        elements.forEach(element => {
            const cached = this.tutorialMarkerDisplayCache.get(element);
            if (cached !== undefined) {
                element.style.visibility = cached;
            } else {
                element.style.visibility = 'visible';
            }
        });
    }

    setElementInteractive(element, enabled) {
        if (!element) return;
        if (!this.tutorialInteractionStates.has(element)) {
            this.tutorialInteractionStates.set(element, {
                pointerEvents: element.style.pointerEvents,
                cursor: element.style.cursor,
                userSelect: element.style.userSelect
            });
        }
        if (enabled) {
            const state = this.tutorialInteractionStates.get(element);
            element.style.pointerEvents = state?.pointerEvents || '';
            element.style.cursor = state?.cursor || '';
            element.style.userSelect = state?.userSelect || '';
            if (element.dataset.tutorialDisabled) {
                delete element.dataset.tutorialDisabled;
            }
            return;
        }
        element.style.pointerEvents = 'none';
        element.style.cursor = 'default';
        element.style.userSelect = 'none';
        element.dataset.tutorialDisabled = 'true';
    }

    disableAllTutorialInteractions() {
        this.tutorialControlledElements.forEach(element => {
            this.setElementInteractive(element, false);
        });
        console.log('[Tutorial] 已禁用所有交互元素');
    }

    enableCurrentStepInteractions(currentElement) {
        if (!currentElement) return;
        this.tutorialControlledElements.forEach(element => {
            // 启用当前元素、其父级容器以及其内部的受控子元素
            if (element === currentElement || element.contains(currentElement) || currentElement.contains(element)) {
                this.setElementInteractive(element, true);
            }
        });
        console.log('[Tutorial] 已启用当前步骤交互元素');
    }

    validateTutorialLayout(currentElement, context) {
        if (!currentElement) return true;
        const highlight = document.querySelector('.driver-highlight');
        if (!highlight) {
            console.log('[Tutorial] 未检测到高亮框，跳过布局验证');
            return true;
        }
        const rect = currentElement.getBoundingClientRect();
        const highlightRect = highlight.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) {
            console.log('[Tutorial] 当前步骤元素尺寸异常，跳过布局验证');
            return true;
        }
        const padding = this.tutorialPadding || 0;
        const diffLeft = Math.abs(highlightRect.left - (rect.left - padding));
        const diffTop = Math.abs(highlightRect.top - (rect.top - padding));
        const diffWidth = Math.abs(highlightRect.width - (rect.width + padding * 2));
        const diffHeight = Math.abs(highlightRect.height - (rect.height + padding * 2));
        // 模型管理页在 MMD/VRM 加载、画布出现时侧栏会长时间连续重排，阈值过小会反复触发回滚（遮罩/高亮被隐藏）
        const threshold = this.currentPage === 'model_manager' ? 120 : 12;
        const hasOffset = diffLeft > threshold || diffTop > threshold || diffWidth > threshold || diffHeight > threshold;
        if (hasOffset) {
            console.error('[Tutorial] 检测到高亮框偏移，执行回滚', {
                context,
                diffLeft,
                diffTop,
                diffWidth,
                diffHeight,
                threshold
            });
            return false;
        }
        console.log('[Tutorial] 布局验证通过', {
            context,
            diffLeft,
            diffTop,
            diffWidth,
            diffHeight
        });
        return true;
    }

    async refreshAndValidateTutorialLayout(currentElement, context) {
        const isModelManager = this.currentPage === 'model_manager';
        // 模型管理页：多轮等待 WebGL/MMD 布局稳定；仍失败时也不回滚（回滚会隐藏遮罩/高亮，而此处多为误判）
        const extraRetries = isModelManager ? 7 : 0;
        const attempts = 1 + extraRetries;

        for (let attempt = 0; attempt < attempts; attempt++) {
            if (this.driver && typeof this.driver.refresh === 'function') {
                this.driver.refresh();
            }
            await new Promise(r => setTimeout(r, this.LAYOUT_REFRESH_DELAY));
            void document.body.offsetHeight;

            const ok = this.validateTutorialLayout(currentElement, context);
            if (ok) {
                return true;
            }

            if (attempt < attempts - 1) {
                const waitMs = isModelManager ? (200 + attempt * 160) : (280 + attempt * 220);
                await new Promise(r => setTimeout(r, waitMs));
            }
        }

        if (isModelManager) {
            console.warn('[Tutorial] 模型管理页布局校验在多次重试后仍未对齐，跳过回滚并最后刷新一次高亮（避免 MMD 重排误清遮罩）');
            if (this.driver && typeof this.driver.refresh === 'function') {
                this.driver.refresh();
            }
            return true;
        }

        this.rollbackTutorialInteractionState();
        return false;
    }

    rollbackTutorialInteractionState() {
        this.tutorialRollbackActive = true;
        this.disableAllTutorialInteractions();
        // 仅隐藏遮罩和高亮，保留引导弹窗以避免用户卡死，并允许其通过弹窗按钮退出
        this.setTutorialMarkersVisible(false, { keepPopover: true });
        console.error('[Tutorial] 检测到布局异常，已回滚交互并保留引导弹窗');
    }

    lockBodyScroll() {
        if (this._isBodyLocked) return;
        this._originalBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        this._isBodyLocked = true;
        console.log('[Tutorial] 禁用页面滚动');
    }

    unlockBodyScroll() {
        if (!this._isBodyLocked) return;
        document.body.style.overflow = this._originalBodyOverflow ?? '';
        this._originalBodyOverflow = undefined;
        this._isBodyLocked = false;
        console.log('[Tutorial] 恢复页面滚动');
    }

    restoreTutorialInteractionState() {
        this.tutorialControlledElements.forEach(element => {
            const state = this.tutorialInteractionStates.get(element);
            element.style.pointerEvents = state?.pointerEvents || '';
            element.style.cursor = state?.cursor || '';
            element.style.userSelect = state?.userSelect || '';
            if (element.dataset.tutorialDisabled) {
                delete element.dataset.tutorialDisabled;
            }
        });
        this.tutorialInteractionStates.clear();
        this.tutorialControlledElements = new Set();
        this.tutorialMarkerDisplayCache = null;
        this.tutorialRollbackActive = false;
        this._lastAppliedStateKey = null;
        console.log('[Tutorial] 已恢复交互元素默认状态');
    }

    async applyTutorialInteractionState(currentStepConfig, context) {
        if (!window.isInTutorial || !currentStepConfig) return;

        // 生成当前状态的唯一标识
        const currentStepIndex = (this.driver && typeof this.driver.currentStep === 'number')
            ? this.driver.currentStep
            : this.currentStep;
        const stateKey = `${currentStepIndex}|${currentStepConfig.element}|${!!currentStepConfig.disableActiveInteraction}|${!!currentStepConfig.enableModelInteraction}`;

        if (this._applyingInteractionState) {
            console.log('[Tutorial] 交互状态正在应用中，跳过重复调用');
            return;
        }

        // 如果状态已应用且不是特殊上下文（如 start 或 rollback），则跳过以减少重复验证周期
        if (this._lastAppliedStateKey === stateKey && context !== 'start' && context !== 'rollback') {
            console.log(`[Tutorial] 交互状态已应用，跳过重复操作 (Context: ${context})`);
            return;
        }

        try {
            this._applyingInteractionState = true;
            this.tutorialRollbackActive = false;
            if (!this.tutorialControlledElements || this.tutorialControlledElements.size === 0) {
                this.collectTutorialControlledElements(this.cachedValidSteps || []);
            }

            // 仅在初次启动或特定上下文时才隐藏标记，减少闪烁
            const shouldHideMarkers = context === 'start' || context === 'rollback';
            if (shouldHideMarkers) {
                this.setTutorialMarkersVisible(false);
            }

            this.disableAllTutorialInteractions();
            const currentElement = document.querySelector(currentStepConfig.element);
            if (currentElement && !currentStepConfig.disableActiveInteraction) {
                this.enableCurrentStepInteractions(currentElement);
            }
            if (currentStepConfig.enableModelInteraction) {
                const canvasPrefix = this._tutorialModelPrefix || UniversalTutorialManager.detectModelPrefix();
                const modelCanvas = document.getElementById(`${canvasPrefix}-canvas`);
                if (modelCanvas) {
                    this.setElementInteractive(modelCanvas, true);
                }
            }

            if (shouldHideMarkers) {
                this.setTutorialMarkersVisible(true);
            }

            await this.refreshAndValidateTutorialLayout(currentElement, context);
            if (!this.tutorialRollbackActive) {
                this._lastAppliedStateKey = stateKey;
            }
        } finally {
            this._applyingInteractionState = false;
        }
    }

    /**
     * 启动引导
     */
    startTutorial() {
        if (!this.isInitialized) {
            console.warn('[Tutorial] driver.js 未初始化');
            return;
        }

        // 防止重复启动
        if (this.isTutorialRunning) {
            console.warn('[Tutorial] 引导已在运行中，跳过重复启动');
            return;
        }

        try {
            // 在 early-return 之前先消耗 pendingTutorialStartSource 和 manual_intent
            // localStorage 标记，避免页面无步骤/无有效步骤时把用户的"启动"意图遗留到下次。
            this.currentTutorialStartSource = this.consumeTutorialStartSource();

            const steps = this.getStepsForPage();

            if (steps.length === 0) {
                console.warn('[Tutorial] 当前页面没有引导步骤');
                return;
            }

            // 过滤掉不存在的元素，并显示隐藏的元素
            const validSteps = steps.filter(step => {
                // 如果步骤标记为跳过初始检查，则直接通过
                if (step.skipInitialCheck) {
                    console.log(`[Tutorial] 跳过初始检查: ${step.element}`);
                    return true;
                }

                const element = document.querySelector(step.element);
                if (!element) {
                    console.warn(`[Tutorial] 元素不存在: ${step.element}`);
                    return false;
                }

                // 检查元素是否可见，如果隐藏则显示它
                if (!this.isElementVisible(element) && !step.skipAutoShow) {
                    console.warn(`[Tutorial] 元素隐藏，正在显示: ${step.element}`);
                    this.showElementForTutorial(element, step.element);
                }

                return true;
            });

            const resumedSteps = this.applyYuiGuideResumeScene(validSteps);

            if (resumedSteps.length === 0) {
                console.warn('[Tutorial] 没有有效的引导步骤');
                return;
            }

            // 标记引导正在运行
            this.isTutorialRunning = true;

            // 立即禁用页面滚动，防止等待异步加载期间用户滚动导致高亮框位置偏移
            this.lockBodyScroll();

            // 检查当前页面是否需要全屏提示
            const pagesNeedingFullscreen = [
                // 已禁用全屏提示
            ];

            if (pagesNeedingFullscreen.includes(this.currentPage)) {
                // 显示全屏提示
                this.showFullscreenPrompt(resumedSteps);
            } else {
                // 直接启动引导，不显示全屏提示
                this.startTutorialSteps(resumedSteps);
            }
        } catch (error) {
            console.error('[Tutorial] 启动引导失败:', error);
            this.resetTutorialStartState();
        }
    }

    resetTutorialStartState() {
        this._teardownTutorialUI();
        this.setTutorialMarkersVisible(true);
    }

    emitTutorialStarted(page = this.currentPage, source = this.currentTutorialStartSource) {
        window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
            detail: {
                page: page,
                source: source
            }
        }));
        this.logPromptFlow('tutorial-started', {
            page: page,
            source: source,
        });
        console.log('[Tutorial] 引导启动来源:', source);
    }

    /**
     * 显示全屏提示
     */
    showFullscreenPrompt(validSteps) {
        // 创建提示遮罩
        const overlay = document.createElement('div');
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.background = 'rgba(0, 0, 0, 0.8)';
        overlay.style.zIndex = '99999';
        overlay.style.display = 'flex';
        overlay.style.alignItems = 'center';
        overlay.style.justifyContent = 'center';

        // 创建提示框
        const prompt = document.createElement('div');
        prompt.style.background = 'rgba(30, 30, 40, 0.95)';
        prompt.style.border = '2px solid #44b7fe';
        prompt.style.borderRadius = '16px';
        prompt.style.padding = '40px';
        prompt.style.maxWidth = '500px';
        prompt.style.textAlign = 'center';
        prompt.style.backdropFilter = 'blur(10px)';
        prompt.style.boxShadow = '0 8px 32px rgba(0, 0, 0, 0.4)';

        // 标题
        const title = document.createElement('h2');
        title.textContent = this.t('tutorial.fullscreenPrompt.title', '🎓 开始新手引导');
        title.style.color = '#44b7fe';
        title.style.marginBottom = '20px';
        title.style.fontSize = '24px';

        // 描述
        const description = document.createElement('p');
        description.textContent = this.t('tutorial.fullscreenPrompt.desc', '为了获得最佳的引导体验，建议进入全屏模式。\n全屏模式下，引导内容会更清晰，不会被其他元素遮挡。');
        description.style.color = 'rgba(255, 255, 255, 0.85)';
        description.style.marginBottom = '30px';
        description.style.lineHeight = '1.6';
        description.style.whiteSpace = 'pre-line';

        // 按钮容器
        const buttonContainer = document.createElement('div');
        buttonContainer.style.display = 'flex';
        buttonContainer.style.gap = '15px';
        buttonContainer.style.justifyContent = 'center';

        // 全屏按钮
        const fullscreenBtn = document.createElement('button');
        fullscreenBtn.textContent = this.t('tutorial.fullscreenPrompt.enterFullscreen', '进入全屏引导');
        fullscreenBtn.style.padding = '12px 30px';
        fullscreenBtn.style.background = 'linear-gradient(135deg, #44b7fe 0%, #40C5F1 100%)';
        fullscreenBtn.style.color = '#fff';
        fullscreenBtn.style.border = 'none';
        fullscreenBtn.style.borderRadius = '8px';
        fullscreenBtn.style.fontSize = '16px';
        fullscreenBtn.style.fontWeight = '600';
        fullscreenBtn.style.cursor = 'pointer';
        fullscreenBtn.style.transition = 'all 0.2s ease';

        fullscreenBtn.onmouseover = () => {
            fullscreenBtn.style.transform = 'translateY(-2px)';
            fullscreenBtn.style.boxShadow = '0 4px 12px rgba(68, 183, 254, 0.4)';
        };
        fullscreenBtn.onmouseout = () => {
            fullscreenBtn.style.transform = 'translateY(0)';
            fullscreenBtn.style.boxShadow = 'none';
        };

        fullscreenBtn.onclick = () => {
            document.body.removeChild(overlay);

            // 进入全屏
            this.enterFullscreenMode();

            // 监听全屏变化事件，等待全屏完成后再启动引导
            const onFullscreenChange = () => {
                if (document.fullscreenElement || document.webkitFullscreenElement ||
                    document.mozFullScreenElement || document.msFullscreenElement) {
                    // 已进入全屏，延迟一点确保布局稳定
                    setTimeout(() => {
                        console.log('[Tutorial] 全屏布局已稳定');

                        // 对于角色管理页面，需要等待猫娘卡片加载
                        if (this.currentPage === 'chara_manager') {
                            console.log('[Tutorial] 等待猫娘卡片加载...');
                            this.waitForCatgirlCards().then(async () => {
                                console.log('[Tutorial] 猫娘卡片已加载');
                                await this.prepareCharaManagerForTutorial();
                                console.log('[Tutorial] 启动引导');
                                this.startTutorialSteps(validSteps);
                            });
                        } else {
                            console.log('[Tutorial] 启动引导');
                            this.startTutorialSteps(validSteps);
                        }
                    }, 300);

                    // 移除监听器
                    document.removeEventListener('fullscreenchange', onFullscreenChange);
                    document.removeEventListener('webkitfullscreenchange', onFullscreenChange);
                    document.removeEventListener('mozfullscreenchange', onFullscreenChange);
                    document.removeEventListener('MSFullscreenChange', onFullscreenChange);
                }
            };

            // 添加全屏变化监听器
            document.addEventListener('fullscreenchange', onFullscreenChange);
            document.addEventListener('webkitfullscreenchange', onFullscreenChange);
            document.addEventListener('mozfullscreenchange', onFullscreenChange);
            document.addEventListener('MSFullscreenChange', onFullscreenChange);

            // 超时保护：如果2秒内没有进入全屏，直接启动引导
            setTimeout(() => {
                if (!document.fullscreenElement && !document.webkitFullscreenElement &&
                    !document.mozFullScreenElement && !document.msFullscreenElement) {
                    console.warn('[Tutorial] 全屏超时');

                    // 对于角色管理页面，需要等待猫娘卡片加载
                    if (this.currentPage === 'chara_manager') {
                        console.log('[Tutorial] 等待猫娘卡片加载...');
                        this.waitForCatgirlCards().then(() => {
                            console.log('[Tutorial] 猫娘卡片已加载，启动引导');
                            this.startTutorialSteps(validSteps);
                        });
                    } else {
                        console.log('[Tutorial] 直接启动引导');
                        this.startTutorialSteps(validSteps);
                    }

                    // 移除监听器
                    document.removeEventListener('fullscreenchange', onFullscreenChange);
                    document.removeEventListener('webkitfullscreenchange', onFullscreenChange);
                    document.removeEventListener('mozfullscreenchange', onFullscreenChange);
                    document.removeEventListener('MSFullscreenChange', onFullscreenChange);
                }
            }, 2000);
        };

        // 组装（只有全屏按钮，没有跳过按钮）
        buttonContainer.appendChild(fullscreenBtn);
        prompt.appendChild(title);
        prompt.appendChild(description);
        prompt.appendChild(buttonContainer);
        overlay.appendChild(prompt);
        document.body.appendChild(overlay);
    }

    /**
     * 启动引导步骤（内部方法）
     */
    startTutorialSteps(validSteps) {
        // 预加载所有步骤中的图片，确保走到含图片的步骤时图片已在浏览器缓存中
        this._preloadStepImages(validSteps);

        // 重置步骤 onHighlighted 触发标记（避免重复/跨次引导）
        this._lastOnHighlightedStepIndex = null;
        this._tutorialEndHandled = false;
        this._tutorialEndReason = null;
        this._tutorialEndRawReason = null;

        // 缓存已验证的步骤，供 onStepChange 使用
        this.cachedValidSteps = validSteps;

        const useYuiOnlyHomeFlow = (
            this.currentPage === 'home'
            && this.isYuiGuideEnabledForPage(this.currentPage)
        );

        if (useYuiOnlyHomeFlow) {
            window.isInTutorial = true;
            this.currentStep = 0;
            this.driver = null;
            console.log('[Tutorial] 首页启用 Yui Guide，跳过旧版 driver 教程启动流程');
            this.emitTutorialStarted();
            this.notifyYuiGuidePreludeStart(validSteps);
            this.showSkipButton();
            return;
        }

        // 重新创建 driver 实例以确保按钮文本使用最新的 i18n 翻译
        this.recreateDriverWithI18n();

        if (!this.driver) {
            console.error('[Tutorial] driver 实例创建失败，无法启动引导');
            this.resetTutorialStartState();
            return;
        }

        // 定义步骤
        this.driver.setSteps(validSteps);

        // 设置全局标记，表示正在进行引导
        window.isInTutorial = true;
        console.log('[Tutorial] 设置全局引导标记');
        this.collectTutorialControlledElements(validSteps);
        this.disableAllTutorialInteractions();
        this.setTutorialMarkersVisible(false);

        // 对于角色管理页面，临时移除容器的上边距以修复高亮框偏移问题
        if (this.currentPage === 'chara_manager') {
            const container = document.querySelector('.container');
            if (container) {
                this.originalContainerMargin = container.style.marginTop;
                container.style.marginTop = '0';
                console.log('[Tutorial] 临时移除容器上边距以修复高亮框位置');
            }
        }

        // 将模型容器移到屏幕右边（在引导中）
        const modelPrefix = UniversalTutorialManager.detectModelPrefix();
        const modelContainer = document.getElementById(`${modelPrefix}-container`);
        if (modelContainer) {
            this.originalLive2dStyle = {
                left: modelContainer.style.left,
                right: modelContainer.style.right,
                transform: modelContainer.style.transform
            };
            modelContainer.style.left = 'auto';
            modelContainer.style.right = '0';
            console.log(`[Tutorial] 将模型容器移到屏幕右边 (${modelPrefix})`);
        }

        // 立即强制显示浮动工具栏（引导开始时）
        const floatingButtons = document.getElementById(`${modelPrefix}-floating-buttons`);
        if (floatingButtons) {
            // 保存原始的内联样式值
            this._floatingButtonsOriginalStyles = {
                display: floatingButtons.style.display,
                visibility: floatingButtons.style.visibility,
                opacity: floatingButtons.style.opacity
            };
            console.log('[Tutorial] 已保存浮动工具栏原始样式:', this._floatingButtonsOriginalStyles);

            floatingButtons.style.setProperty('display', 'flex', 'important');
            floatingButtons.style.setProperty('visibility', 'visible', 'important');
            floatingButtons.style.setProperty('opacity', '1', 'important');
            console.log('[Tutorial] 强制显示浮动工具栏');
        }

        // 立即强制显示锁图标（如果当前页面的引导包含锁图标步骤）
        const lockIconId = `${modelPrefix}-lock-icon`;
        const hasLockIconStep = validSteps.some(step => step.element === `#${lockIconId}`);
        if (hasLockIconStep) {
            const lockIcon = document.getElementById(lockIconId);
            if (lockIcon) {
                // 保存原始的内联样式值
                this._lockIconOriginalStyles = {
                    display: lockIcon.style.display,
                    visibility: lockIcon.style.visibility,
                    opacity: lockIcon.style.opacity
                };
                console.log('[Tutorial] 已保存锁图标原始样式:', this._lockIconOriginalStyles);

                lockIcon.style.setProperty('display', 'block', 'important');
                lockIcon.style.setProperty('visibility', 'visible', 'important');
                lockIcon.style.setProperty('opacity', '1', 'important');
                console.log('[Tutorial] 强制显示锁图标');
            }
        }

        // 启动浮动工具栏保护定时器（每 500ms 检查一次）
        this._tutorialModelPrefix = modelPrefix; // 缓存，给定时器用
        this.floatingButtonsProtectionTimer = setInterval(() => {
            const pfx = this._tutorialModelPrefix || 'live2d';
            const floatingButtons = document.getElementById(`${pfx}-floating-buttons`);
            if (floatingButtons && window.isInTutorial) {
                // 强制设置所有可能隐藏浮动按钮的样式
                floatingButtons.style.setProperty('display', 'flex', 'important');
                floatingButtons.style.setProperty('visibility', 'visible', 'important');
                floatingButtons.style.setProperty('opacity', '1', 'important');
            }

            // 同样保护锁图标（如果当前引导包含锁图标步骤）
            if (this._lockIconOriginalStyles !== undefined && window.isInTutorial) {
                const lockIcon = document.getElementById(`${pfx}-lock-icon`);
                if (lockIcon) {
                    lockIcon.style.setProperty('display', 'block', 'important');
                    lockIcon.style.setProperty('visibility', 'visible', 'important');
                    lockIcon.style.setProperty('opacity', '1', 'important');
                }
            }
        }, 500);

        // 监听事件
        this.driver.on('destroy', () => this.onTutorialEnd());
        this.driver.on('next', () => this.onStepChange().catch(err => {
            console.error('[Tutorial] 步骤切换失败:', err);
        }));
        this.driver.on('prev', () => this.onStepChange().catch(err => {
            console.error('[Tutorial] 步骤切换失败:', err);
        }));

        this.notifyYuiGuidePreludeStart(validSteps);
        const tutorialStartPage = this.currentPage;
        const tutorialStartSource = this.currentTutorialStartSource;
        let startResult;

        // 启动引导
        try {
            startResult = this.driver.start();
        } catch (error) {
            console.error('[Tutorial] 启动引导步骤失败:', error);
            this.resetTutorialStartState();
            return;
        }

        if (validSteps.length > 0) {
            this.notifyYuiGuideStepEnter(validSteps[0], 0, 'tutorial-start');
        }

        // 监听窗口大小变化，刷新 SVG 遮罩和高亮框位置（注册前先清理旧的，防止重复注册）
        if (this._resizeHandler) {
            window.removeEventListener('resize', this._resizeHandler);
            if (this._resizeRafId) { cancelAnimationFrame(this._resizeRafId); this._resizeRafId = null; }
            if (this._resizeTimeoutId) { clearTimeout(this._resizeTimeoutId); this._resizeTimeoutId = null; }
        }
        this._resizeHandler = () => {
            if (this._resizeRafId) cancelAnimationFrame(this._resizeRafId);
            if (this._resizeTimeoutId) clearTimeout(this._resizeTimeoutId);
            this._resizeRafId = requestAnimationFrame(() => {
                this._resizeTimeoutId = setTimeout(() => {
                    this._resizeRafId = null;
                    this._resizeTimeoutId = null;
                    if (this.driver && window.isInTutorial) this.driver.refresh();
                }, 50);
            });
        };
        window.addEventListener('resize', this._resizeHandler);
        setTimeout(() => {
            const steps = this.cachedValidSteps || [];
            if (steps.length > 0) {
                this.applyTutorialInteractionState(steps[0], 'start').catch(err => {
                    console.error('[Tutorial] 初始交互状态应用失败:', err);
                });
            }
        }, 0);

        // 显示跳过按钮
        this.showSkipButton();

        Promise.resolve(startResult).then(() => {
            if (!this.isTutorialRunning || !window.isInTutorial) {
                return;
            }
            this.emitTutorialStarted(tutorialStartPage, tutorialStartSource);
        }).catch(error => {
            console.error('[Tutorial] 启动引导步骤失败:', error);
            this.resetTutorialStartState();
        });

        console.log('[Tutorial] 引导已启动，页面:', this.currentPage);
    }

    /**
     * 在右上角显示「跳过」按钮，点击后结束引导
     */
    showSkipButton() {
        // 避免重复创建
        this.hideSkipButton();

        const btn = document.createElement('button');
        btn.id = 'neko-tutorial-skip-btn';
        btn.textContent = this.t('tutorial.buttons.skip', '跳过');

        // 明确设置点击区域，防止 CEF 继承父元素 pointer-events 导致无法点击
        btn.style.pointerEvents = 'auto';
        btn.style.position = 'fixed';
        btn.style.zIndex = '100007';
        btn.style.touchAction = 'manipulation'; // 消除 CEF 的 300ms 点击延迟

        let skipHandled = false;
        const handleSkipFailure = (error) => {
            console.warn('[Tutorial] Yui Guide skip 失败，回退到 requestTutorialDestroy:', error);
            skipHandled = false;
            this.requestTutorialDestroy('skip');
        };
        const handleSkipRequest = (e) => {
            if (skipHandled) {
                return;
            }
            skipHandled = true;
            if (e && typeof e.preventDefault === 'function') {
                e.preventDefault();
            }
            if (e && typeof e.stopImmediatePropagation === 'function') {
                e.stopImmediatePropagation();
            }
            if (e && typeof e.stopPropagation === 'function') {
                e.stopPropagation();
            }
            const director = this.isYuiGuideEnabledForPage(this.currentPage)
                ? this.ensureYuiGuideDirector()
                : null;
            if (director && typeof director.skip === 'function') {
                Promise.resolve().then(async () => {
                    try {
                        await Promise.race([
                            Promise.resolve(director.skip('skip', 'skip')),
                            new Promise((_, reject) => {
                                window.setTimeout(() => reject(new Error('skip_timeout')), 1800);
                            })
                        ]);
                    } catch (error) {
                        handleSkipFailure(error);
                    }
                });
                return;
            }

            this.requestTutorialDestroy('skip');
        };

        btn.addEventListener('pointerdown', handleSkipRequest);
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            handleSkipRequest(e);
        });
        document.body.appendChild(btn);
        console.log('[Tutorial] 跳过按钮已显示');
    }

    /**
     * 移除「跳过」按钮
     */
    hideSkipButton() {
        const existing = document.getElementById('neko-tutorial-skip-btn');
        if (existing) {
            existing.remove();
            console.log('[Tutorial] 跳过按钮已移除');
        }
    }

    /**
     * 检查并等待浮动按钮创建（用于主页引导）
     * 优先监听 live2d-floating-buttons-ready 事件（Live2D / VRM / MMD 均会派发），
     * 辅以轮询兜底，解决模型加载慢导致教程跳过按钮步骤的问题。
     */
    waitForFloatingButtons(maxWaitTime = 60000) {
        return new Promise((resolve) => {
            // 检查任意模型类型的浮动按钮容器是否已存在
            const findExisting = () =>
                document.getElementById('live2d-floating-buttons') ||
                document.getElementById('vrm-floating-buttons') ||
                document.getElementById('mmd-floating-buttons');

            if (findExisting()) {
                console.log('[Tutorial] 浮动按钮已存在');
                resolve(true);
                return;
            }

            let resolved = false;
            const done = (result) => {
                if (resolved) return;
                resolved = true;
                clearTimeout(timer);
                clearInterval(poller);
                window.removeEventListener('live2d-floating-buttons-ready', onReady);
                resolve(result);
            };

            // 1. 事件监听（所有模型类型都派发 live2d-floating-buttons-ready）
            const onReady = () => {
                console.log('[Tutorial] 收到浮动按钮就绪事件');
                done(true);
            };
            window.addEventListener('live2d-floating-buttons-ready', onReady);

            // 2. 轮询兜底（防止事件在监听注册前已派发）
            const poller = setInterval(() => {
                if (findExisting()) {
                    console.log('[Tutorial] 轮询发现浮动按钮已创建');
                    done(true);
                }
            }, 500);

            // 3. 超时兜底
            const timer = setTimeout(() => {
                console.warn(`[Tutorial] 等待浮动按钮超时（${maxWaitTime / 1000}秒）`);
                done(false);
            }, maxWaitTime);
        });
    }

    /**
     * 检查并等待猫娘卡片创建（用于角色管理页面引导）
     */
    waitForCatgirlCards(maxWaitTime = 5000) {
        return new Promise((resolve) => {
            const startTime = Date.now();

            const checkCatgirlCards = () => {
                const catgirlList = document.getElementById('catgirl-list');
                const firstCatgirl = document.querySelector('.catgirl-block:first-child');

                if (catgirlList && firstCatgirl) {
                    console.log('[Tutorial] 猫娘卡片已创建');
                    resolve(true);
                    return;
                }

                const elapsedTime = Date.now() - startTime;
                if (elapsedTime > maxWaitTime) {
                    console.warn('[Tutorial] 等待猫娘卡片超时（5秒）');
                    resolve(false);
                    return;
                }

                setTimeout(checkCatgirlCards, 100);
            };

            checkCatgirlCards();
        });
    }

    /**
     * 获取用于教程展示的目标猫娘卡片
     * 优先选择第一个，如果不存在则返回 null
     */
    getTargetCatgirlBlock() {
        const catgirlBlocks = document.querySelectorAll('.catgirl-block');
        if (catgirlBlocks.length === 0) {
            console.warn('[Tutorial] 没有找到任何猫娘卡片');
            return null;
        }

        // 返回第一个猫娘卡片
        return catgirlBlocks[0];
    }

    /**
     * 确保猫娘卡片已展开（用于教程）
     * @param {Element} catgirlBlock - 猫娘卡片元素
     */
    async ensureCatgirlExpanded(catgirlBlock) {
        if (!catgirlBlock) return false;

        const expandBtn = catgirlBlock.querySelector('.catgirl-expand');
        const detailsDiv = catgirlBlock.querySelector('.catgirl-details');

        if (!expandBtn || !detailsDiv) {
            console.warn('[Tutorial] 猫娘卡片结构不完整');
            return false;
        }

        // 检查是否已展开 - 通过检查 detailsDiv 的 display 样式
        const isExpanded = detailsDiv.style.display === 'block';
        console.log(`[Tutorial] 猫娘卡片展开状态: ${isExpanded}`);

        if (!isExpanded) {
            console.log('[Tutorial] 展开猫娘卡片');
            expandBtn.click();
            // 等待展开动画完成
            await new Promise(resolve => setTimeout(resolve, 500));
        }

        return true;
    }

    /**
     * 确保进阶设定已展开（用于教程）
     * @param {Element} catgirlBlock - 猫娘卡片元素
     */
    async ensureAdvancedSettingsExpanded(catgirlBlock) {
        if (!catgirlBlock) return false;

        const foldToggle = catgirlBlock.querySelector('.fold-toggle');
        const foldContainer = catgirlBlock.querySelector('.fold');

        if (!foldToggle || !foldContainer) {
            console.warn('[Tutorial] 进阶设定结构不完整');
            return false;
        }

        // 检查是否已展开 - 通过检查 .fold 元素是否有 .open 类
        const isExpanded = foldContainer.classList.contains('open');
        console.log(`[Tutorial] 进阶设定展开状态: ${isExpanded}`);

        if (!isExpanded) {
            console.log('[Tutorial] 展开进阶设定');
            foldToggle.click();
            // 等待展开动画完成
            await new Promise(resolve => setTimeout(resolve, 500));
        }

        return true;
    }

    /**
     * 滚动元素到可视区域
     * @param {Element} element - 要滚动到的元素
     */
    scrollIntoViewSmooth(element) {
        if (!element) return;

        element.scrollIntoView({
            behavior: 'smooth',
            block: 'center',
            inline: 'nearest'
        });
    }

    /**
     * 为角色管理页面准备引导
     * 关闭所有已展开的卡片，确保初始状态一致
     */
    async prepareCharaManagerForTutorial() {
        console.log('[Tutorial] 准备角色管理页面引导...');

        // 1. 先关闭所有内部的"进阶设定" (.fold-toggle)
        // 防止外部卡片关闭了，里面还撑着
        const allFoldToggles = document.querySelectorAll('.fold-toggle');
        allFoldToggles.forEach(toggle => {
            let foldContent = toggle.parentElement.querySelector('.fold');
            // 检查是否处于展开状态 (通常有 'open' 类或者 style display 不为 none)
            const isExpanded = foldContent && (
                foldContent.classList.contains('open') ||
                foldContent.style.display === 'block' ||
                window.getComputedStyle(foldContent).display === 'block'
            );

            if (isExpanded) {
                console.log('[Tutorial] 检测到进阶设定已展开，正在关闭...');
                toggle.click(); // 触发点击来关闭它，保证状态同步
            }
        });

        // 2. 再关闭所有"猫娘卡片" (.catgirl-block)
        const allCatgirlBlocks = document.querySelectorAll('.catgirl-block');
        allCatgirlBlocks.forEach(block => {
            const details = block.querySelector('.catgirl-details');
            const expandBtn = block.querySelector('.catgirl-expand');

            // 检查内容区域是否可见
            if (details && expandBtn) {
                const style = window.getComputedStyle(details);
                if (style.display !== 'none') {
                    console.log('[Tutorial] 检测到猫娘卡片已展开，正在关闭...');
                    expandBtn.click(); // 点击折叠按钮关闭它
                }
            }
        });

        // 3. 等待关闭动画完成
        await new Promise(resolve => setTimeout(resolve, 500));

        console.log('[Tutorial] 角色管理页面引导准备完成');
    }

    /**
     * 清理角色管理页面引导（保留用于兼容性）
     */
    cleanupCharaManagerTutorialIds() {
        // 不再需要清理 ID，因为我们使用 CSS 选择器
        console.log('[Tutorial] 角色管理页面引导清理完成');
    }

    /**
     * 检查元素是否需要点击（用于折叠/展开组件）
     */
    shouldClickElement(element, selector) {
        // 检查是否是折叠/展开类型的元素（支持类名和 ID）
        const isToggleElement = selector.includes('.fold-toggle') ||
            selector.includes('.catgirl-header') ||
            selector === '#tutorial-target-fold-toggle' ||
            selector === '#tutorial-target-catgirl-header';

        if (isToggleElement) {
            // 查找相关的内容容器
            let contentContainer = element.nextElementSibling;

            // 如果直接的下一个兄弟元素不是内容，向上查找到父元素再查找
            if (!contentContainer) {
                // 针对进阶设定按钮的特殊处理（它可能被包在 div 或 span 里）
                const foldParent = element.closest('.fold, .fold-toggle-wrapper') || element.parentElement;
                if (foldParent) {
                    // 尝试找兄弟节点中的内容
                    contentContainer = foldParent.nextElementSibling || foldParent.querySelector('.fold-content');
                }

                // 如果还是没找到，尝试通用的查找方式
                if (!contentContainer) {
                    const parent = element.closest('[class*="catgirl"]');
                    if (parent) {
                        contentContainer = parent.querySelector('[class*="details"], [class*="content"], .fold-content, .fold');
                        // 注意：对于进阶设定，内容通常是 .fold 元素本身或其子元素，视具体 DOM 结构而定
                        // 如果 element 是 toggle，那么内容通常是它控制的那个区域
                    }
                }
            }


            // 检查内容是否可见
            if (contentContainer) {
                const style = window.getComputedStyle(contentContainer);
                const isVisible = style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';

                console.log(`[Tutorial] 折叠组件状态检查 - 选择器: ${selector}, 已展开: ${isVisible}`);

                // 如果已经展开，就不需要再点击
                return !isVisible;
            }

            // 检查元素本身是否有 aria-expanded 属性
            const ariaExpanded = element.getAttribute('aria-expanded');
            if (ariaExpanded !== null) {
                const isExpanded = ariaExpanded === 'true';
                console.log(`[Tutorial] 折叠组件 aria-expanded 检查 - 已展开: ${isExpanded}`);
                return !isExpanded;
            }

            // 检查是否有 active/open 类
            if (element.classList.contains('active') || element.classList.contains('open') || element.classList.contains('expanded')) {
                console.log(`[Tutorial] 折叠组件已处于展开状态（通过class检查）`);
                return false;
            }
        }

        // 其他类型的元素总是需要点击
        return true;
    }

    /**
     * 检查元素是否在可见视口内
     */
    isElementInViewport(element) {
        if (!element) return false;

        const rect = element.getBoundingClientRect();
        return (
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
            rect.right <= (window.innerWidth || document.documentElement.clientWidth)
        );
    }

    /**
     * 自动滚动到目标元素
     */
    scrollToElement(element) {
        return new Promise((resolve) => {
            if (!element) {
                resolve();
                return;
            }

            // 检查元素是否已经在视口内
            if (this.isElementInViewport(element)) {
                console.log('[Tutorial] 元素已在视口内，无需滚动');
                resolve();
                return;
            }

            console.log('[Tutorial] 元素不在视口内，正在滚动...');

            // 尝试找到可滚动的父容器
            let scrollableParent = element.parentElement;
            while (scrollableParent) {
                const style = window.getComputedStyle(scrollableParent);
                const hasScroll = style.overflowY === 'auto' ||
                    style.overflowY === 'scroll' ||
                    style.overflow === 'auto' ||
                    style.overflow === 'scroll';

                if (hasScroll) {
                    console.log('[Tutorial] 找到可滚动容器，正在滚动到元素...');
                    // 计算元素相对于可滚动容器的位置
                    const elementTop = element.offsetTop;
                    const containerHeight = scrollableParent.clientHeight;
                    const elementHeight = element.clientHeight;

                    // 计算需要滚动的距离，使元素居中显示
                    const targetScroll = elementTop - (containerHeight - elementHeight) / 2;

                    scrollableParent.scrollTo({
                        top: Math.max(0, targetScroll),
                        behavior: 'smooth'
                    });

                    // 等待滚动完成（平滑滚动大约需要 300-500ms）
                    setTimeout(() => {
                        console.log('[Tutorial] 滚动完成');
                        resolve();
                    }, 600);
                    return;
                }

                scrollableParent = scrollableParent.parentElement;
            }

            // 如果没有找到可滚动的父容器，尝试滚动 window
            console.log('[Tutorial] 未找到可滚动容器，尝试滚动 window');
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });

            // 等待滚动完成
            setTimeout(() => {
                console.log('[Tutorial] 滚动完成');
                resolve();
            }, 600);
        });
    }

    /**
     * 预加载所有教程步骤中的图片。
     * 解析每个步骤的 popover.description HTML，提取 <img src="..."> 中的 URL，
     * 通过 new Image() 提前下载到浏览器缓存。这样走到含图片的步骤时，
     * createPopover 插入 DOM 后图片能立即渲染，offsetHeight 计算准确，
     * 避免图片异步加载导致弹窗定位偏移、按钮被截断。
     */
    _preloadStepImages(steps) {
        if (!steps || steps.length === 0) return;
        const srcSet = new Set();
        const imgTagRegex = /<img[^>]+src\s*=\s*["']([^"']+)["'][^>]*>/gi;
        for (const step of steps) {
            const desc = step.popover && step.popover.description;
            if (!desc || typeof desc !== 'string') continue;
            let match;
            while ((match = imgTagRegex.exec(desc)) !== null) {
                srcSet.add(match[1]);
            }
            imgTagRegex.lastIndex = 0;
        }
        if (srcSet.size === 0) return;
        console.log(`[Tutorial] 预加载 ${srcSet.size} 张教程图片:`, [...srcSet]);
        for (const src of srcSet) {
            const img = new Image();
            img.src = src;
        }
    }

    /**
     * 将 popover 钳位到视口内，确保用户始终能看到并操作它
     */
    clampPopoverToViewport() {
        const popover = document.querySelector('.driver-popover');
        if (!popover) return;

        const rect = popover.getBoundingClientRect();
        const vw = window.innerWidth || document.documentElement.clientWidth;
        const vh = window.innerHeight || document.documentElement.clientHeight;

        // 如果已经完全在视口内，不做任何操作
        if (rect.left >= 0 && rect.top >= 0 && rect.right <= vw && rect.bottom <= vh) {
            return;
        }

        console.log('[Tutorial] Popover 超出视口，钳位到可见区域', {
            rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom },
            viewport: { vw, vh }
        });

        // 切换到 fixed 定位以便精确控制位置
        popover.style.position = 'fixed';
        popover.style.margin = '0';
        popover.style.transform = 'none';

        let newLeft = rect.left;
        let newTop = rect.top;

        // 水平钳位
        if (rect.right > vw) newLeft = vw - rect.width - 8;
        if (newLeft < 8) newLeft = 8;
        // 如果 popover 比视口还宽，至少让左边对齐
        if (rect.width > vw - 16) newLeft = 8;

        // 垂直钳位
        if (rect.bottom > vh) newTop = vh - rect.height - 8;
        if (newTop < 8) newTop = 8;
        // 如果 popover 比视口还高，至少让顶部对齐，用户可以通过拖拽来看底部
        if (rect.height > vh - 16) newTop = 8;

        popover.style.left = newLeft + 'px';
        popover.style.top = newTop + 'px';
        popover.style.zIndex = '10000';
    }

    /**
     * 设置 popover 拖动视觉提示
     * 注意：实际拖动事件由 driver.min.js 的 bindDragEvents() 处理，
     * 此方法仅添加视觉提示（cursor、title），避免重复绑定导致冲突。
     */
    enablePopoverDragging() {
        const popover = document.querySelector('.driver-popover');
        if (!popover) return;

        const popoverTitle = popover.querySelector('.driver-popover-title');
        if (popoverTitle) {
            popoverTitle.style.cursor = 'move';
            popoverTitle.style.userSelect = 'none';
            popoverTitle.title = this.t('tutorial.drag_hint', '按住拖动以移动提示框');
        }
    }

    /**
     * 步骤改变时的回调
     */
    async onStepChange() {
        if (this._stepChanging) {
            console.log('[Tutorial] 步骤正在切换中，标记待处理请求');
            this._pendingStepChange = true;
            return;
        }
        
        this._stepChanging = true;
        this._pendingStepChange = false;
        let succeeded = false;

        try {
            if (!this.driver) {
                console.warn('[Tutorial] driver 已销毁，跳过步骤切换');
                this.currentStep = 0;
                return;
            }
            const steps = this.cachedValidSteps || this.getStepsForPage();
            const previousStepIndex = this.currentStep;
            const previousStepConfig = (previousStepIndex >= 0 && previousStepIndex < steps.length)
                ? steps[previousStepIndex]
                : null;

            this.currentStep = this.driver.currentStep || 0;
            console.log(`[Tutorial] 当前步骤: ${this.currentStep + 1}`);

            const previousSceneId = this.getYuiGuideSceneIdForStep(previousStepConfig);
            if (this.currentStep < steps.length) {
                const currentStepConfig = steps[this.currentStep];
                const currentSceneId = this.getYuiGuideSceneIdForStep(currentStepConfig);

                if (previousSceneId && previousSceneId !== currentSceneId) {
                    this.notifyYuiGuideStepLeave(previousStepConfig, previousStepIndex, 'step-change');
                }

                if (currentSceneId && currentSceneId !== previousSceneId) {
                    this.notifyYuiGuideStepEnter(currentStepConfig, this.currentStep, 'step-change');
                }

                // 进入新步骤前，先清理上一阶段的"下一步"前置校验
                this.clearNextButtonGuard();

                // 清除旧的刷新定时器
                if (this._refreshTimers) {
                    this._refreshTimers.forEach(t => clearTimeout(t));
                    this._refreshTimers = [];
                }

                // 触发步骤特定的 onHighlighted（driver.min.js 不支持该回调）
                if (currentStepConfig.onHighlighted && typeof currentStepConfig.onHighlighted === 'function') {
                    if (this._lastOnHighlightedStepIndex !== this.currentStep) {
                        try {
                            console.log('[Tutorial] 手动触发步骤 onHighlighted');
                            currentStepConfig.onHighlighted.call(this);
                            this._lastOnHighlightedStepIndex = this.currentStep;
                        } catch (error) {
                            console.error('[Tutorial] 步骤 onHighlighted 执行失败:', error);
                        }
                    }
                }

                // 角色管理页面：进入进阶设定相关步骤前，确保猫娘卡片和进阶设定都已展开
                if (this.currentPage === 'chara_manager') {
                    const needsAdvancedSettings = [
                        '.catgirl-block:first-child .fold-toggle',
                        '.catgirl-block:first-child .live2d-link',
                        '.catgirl-block:first-child select[name="voice_id"]'
                    ].includes(currentStepConfig.element);

                    if (needsAdvancedSettings) {
                        console.log('[Tutorial] 进入进阶设定相关步骤，确保展开状态');
                        await this._ensureCharaManagerExpanded();
                    }
                }

                await this.applyTutorialInteractionState(currentStepConfig, 'step-change');


                // 情感配置页面：未选择模型时禁止进入下一步
                if (this.currentPage === 'emotion_manager' &&
                    currentStepConfig.element === '#model-singleselect') {
                    const updateNextState = () => {
                        const hasModel = this.hasEmotionManagerModelSelected();
                        const hasSelectableModels = this.hasEmotionManagerSelectableModels();
                        const canProceed = !hasSelectableModels || hasModel;
                        this.setNextButtonState(
                            canProceed,
                            this.t('emotionManager.pleaseSelectModelFirst', '请先选择模型')
                        );
                        if (canProceed && this.nextButtonGuardTimer) {
                            clearInterval(this.nextButtonGuardTimer);
                            this.nextButtonGuardTimer = null;
                        }
                    };

                    this.nextButtonGuardActive = true;
                    updateNextState();
                    this.nextButtonGuardTimer = setInterval(updateNextState, 300);
                }

                // 情感配置前必须先选择/加载 Live2D 模型，避免进入后出错
                if (this.currentPage === 'model_manager' &&
                    currentStepConfig.element === '#emotion-config-btn' &&
                    !this.hasLive2DModelLoaded()) {
                    console.warn('[Tutorial] 未检测到已加载的 Live2D 模型，跳转回选择模型步骤');
                    const targetIndex = steps.findIndex(step => step.element === '#live2d-model-select-btn');
                    if (this.driver && typeof this.driver.showStep === 'function' && targetIndex >= 0) {
                        this.driver.showStep(targetIndex);
                        return;
                    }
                }

                // 情感配置页面中，未选模型时不进入配置区域
                if (this.currentPage === 'emotion_manager' &&
                    currentStepConfig.element === '#emotion-config' &&
                    !this.hasEmotionManagerModelSelected()) {
                    console.warn('[Tutorial] 情感配置页面未选择模型，跳转回选择模型步骤');
                    const targetIndex = steps.findIndex(step => step.element === '#model-singleselect');
                    if (this.driver && typeof this.driver.showStep === 'function' && targetIndex >= 0) {
                        this.driver.showStep(targetIndex);
                        return;
                    }
                }

                const element = document.querySelector(currentStepConfig.element);

                if (element) {
                    // 检查元素是否隐藏，如果隐藏则显示
                    if (!this.isElementVisible(element) && !currentStepConfig.skipAutoShow) {
                        console.warn(`[Tutorial] 当前步骤的元素隐藏，正在显示: ${currentStepConfig.element}`);
                        this.showElementForTutorial(element, currentStepConfig.element);
                    }

                    // 执行步骤中定义的操作
                    if (currentStepConfig.action) {
                        if (currentStepConfig.action === 'click') {
                            const timer = setTimeout(() => {
                                console.log(`[Tutorial] 执行自动点击: ${currentStepConfig.element}`);

                                // 1. 找到要点击的元素
                                const innerTrigger = element.querySelector('.catgirl-expand, .fold-toggle');
                                const clickTarget = innerTrigger || element;

                                // 2. 检查是否是折叠类元素，如果已展开则不点击
                                let shouldClick = true;
                                if (clickTarget.classList.contains('fold-toggle')) {
                                    // 检查进阶设定是否已展开
                                    const foldContainer = clickTarget.closest('.catgirl-block')?.querySelector('.fold');
                                    if (foldContainer) {
                                        const isExpanded = foldContainer.classList.contains('open') ||
                                            window.getComputedStyle(foldContainer).display !== 'none';
                                        if (isExpanded) {
                                            console.log('[Tutorial] 进阶设定已展开，跳过点击');
                                            shouldClick = false;
                                        }
                                    }
                                } else if (clickTarget.classList.contains('catgirl-expand')) {
                                    // 检查猫娘卡片是否已展开
                                    const details = clickTarget.closest('.catgirl-block')?.querySelector('.catgirl-details');
                                    if (details) {
                                        const isExpanded = window.getComputedStyle(details).display !== 'none';
                                        if (isExpanded) {
                                            console.log('[Tutorial] 猫娘卡片已展开，跳过点击');
                                            shouldClick = false;
                                        }
                                    }
                                }

                                // 3. 执行点击
                                if (shouldClick) {
                                    clickTarget.click();
                                }

                                // 4. 刷新高亮框
                                const refreshTimer = setTimeout(() => {
                                    if (this.driver) this.driver.refresh();
                                }, 500);
                                if (this._refreshTimers) this._refreshTimers.push(refreshTimer);

                            }, 300);
                            if (this._refreshTimers) this._refreshTimers.push(timer);
                        }
                    } else {
                        // 即使没有点击操作，也在步骤切换后刷新位置
                        // 对于需要等待动态元素的步骤，多次刷新以确保位置正确
                        if (currentStepConfig.skipInitialCheck) {
                            console.log(`[Tutorial] 动态元素步骤，将多次刷新位置`);
                            this.DYNAMIC_REFRESH_DELAYS.forEach((delay, i) => {
                                const timer = setTimeout(() => {
                                    if (this.driver && typeof this.driver.refresh === 'function') {
                                        this.driver.refresh();
                                        console.log(`[Tutorial] 步骤切换后刷新高亮框位置 (第${i + 1}次)`);
                                    }
                                }, delay);
                                if (this._refreshTimers) this._refreshTimers.push(timer);
                            });
                        } else {
                            const timer = setTimeout(() => {
                                if (this.driver && typeof this.driver.refresh === 'function') {
                                    this.driver.refresh();
                                    console.log(`[Tutorial] 步骤切换后刷新高亮框位置`);
                                }
                            }, 200);
                            if (this._refreshTimers) this._refreshTimers.push(timer);
                        }
                    }

                    if (this.currentPage === 'model_manager') {
                        [900, 1800].forEach(delay => {
                            const t = setTimeout(() => {
                                if (window.isInTutorial && this.driver && typeof this.driver.refresh === 'function') {
                                    this.driver.refresh();
                                    console.log(`[Tutorial] 模型管理页延迟刷新高亮 (${delay}ms)`);
                                }
                            }, delay);
                            if (this._refreshTimers) this._refreshTimers.push(t);
                        });
                    }
                }
            }

            // 在步骤切换后，延迟启用 popover 拖动功能
            // 因为 driver.js 可能会重新渲染 popover
            setTimeout(() => {
                this.enablePopoverDragging();
            }, 200);

            succeeded = true;
        } catch (error) {
            console.error('[Tutorial] 步骤切换回调执行出错:', error);
            // 发生错误时确保清除待处理标记，避免进入死循环
            this._pendingStepChange = false;
            throw error;
        } finally {
            this._stepChanging = false;
            // 如果在执行期间有新的步骤切换请求，且当前步骤处理成功，则再次触发
            if (succeeded && this._pendingStepChange) {
                console.log('[Tutorial] 处理待处理的步骤切换请求');
                this.onStepChange().catch(err => {
                    console.error('[Tutorial] 待处理步骤切换失败:', err);
                });
            }
        }
    }

    /**
     * 引导结束时的回调
     */
    onTutorialEnd() {
        if (this._tutorialEndHandled) {
            return;
        }

        this._tutorialEndHandled = true;
        const finalSteps = this.cachedValidSteps || [];
        const finalStepIndex = this.currentStep;
        const finalStepConfig = (finalStepIndex >= 0 && finalStepIndex < finalSteps.length)
            ? finalSteps[finalStepIndex]
            : null;
        const endMeta = this.resolveTutorialEndMeta(finalSteps);

        this.notifyYuiGuideStepLeave(finalStepConfig, finalStepIndex, 'tutorial-end');
        this.notifyYuiGuideTutorialEnd(endMeta.rawReason);
        const completedSource = this.currentTutorialStartSource;

        this._teardownTutorialUI();

        // 标记用户已看过该页面的引导
        const storageKey = this.getStorageKey();
        localStorage.setItem(storageKey, 'true');
        if (this.currentPage === 'model_manager') {
            const commonStorageKey = getTutorialStorageKeyForPage('model_manager_common');
            localStorage.setItem(commonStorageKey, 'true');
            console.log('[Tutorial] 已标记模型管理通用步骤为已看过');
        }

        window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
            detail: {
                page: this.currentPage,
                source: completedSource
            }
        }));
        this.logPromptFlow('tutorial-completed', {
            page: this.currentPage,
            source: completedSource,
            reason: endMeta.reason,
            rawReason: endMeta.rawReason
        });
        console.log('[Tutorial] 引导已完成，页面:', this.currentPage);
    }

    /**
     * 拆除引导期间安装的 UI 状态（定时器、临时样式、监听器等）。
     * 不写入"已看过"存储，也不派发 tutorial-completed 事件，
     * 因此既能给正常结束（onTutorialEnd）复用，也能给启动失败的回退路径复用。
     */
    _teardownTutorialUI() {
        // 重置运行标志
        this.isTutorialRunning = false;
        this.clearNextButtonGuard();
        this._lastAppliedStateKey = null;
        this._stepChanging = false;
        this._pendingStepChange = false;
        this._applyingInteractionState = false;
        this.cachedValidSteps = null;
        this._tutorialEndReason = null;
        this._tutorialEndRawReason = null;
        this.currentTutorialStartSource = 'auto';

        // 移除跳过按钮
        this.hideSkipButton();

        // 清除刷新定时器
        if (this._refreshTimers) {
            this._refreshTimers.forEach(t => clearTimeout(t));
            this._refreshTimers = [];
        }

        // 清理 resize 监听
        if (this._resizeHandler) {
            window.removeEventListener('resize', this._resizeHandler);
            this._resizeHandler = null;
        }
        if (this._resizeRafId) { cancelAnimationFrame(this._resizeRafId); this._resizeRafId = null; }
        if (this._resizeTimeoutId) { clearTimeout(this._resizeTimeoutId); this._resizeTimeoutId = null; }

        // 只有进入了全屏的页面才需要退出全屏
        const pagesNeedingFullscreen = []; // 已禁用全屏提示
        if (pagesNeedingFullscreen.includes(this.currentPage)) {
            this.exitFullscreenMode();
        }

        // 对于角色管理页面，恢复容器的上边距
        if (this.currentPage === 'chara_manager') {
            const container = document.querySelector('.container');
            if (container && this.originalContainerMargin !== undefined) {
                container.style.marginTop = this.originalContainerMargin;
                console.log('[Tutorial] 恢复容器上边距');
            }
            // 清理引导添加的 ID
            this.cleanupCharaManagerTutorialIds();
        }

        if (this.currentPage === 'model_manager') {
            this.clearModelManagerTutorialRecheckTimer();
        }

        // 清除全局引导标记
        window.isInTutorial = false;

        // 恢复页面滚动
        this.unlockBodyScroll();

        const endPrefix = this._tutorialModelPrefix || UniversalTutorialManager.detectModelPrefix();
        const modelContainer = document.getElementById(`${endPrefix}-container`);
        if (modelContainer && this.originalLive2dStyle) {
            modelContainer.style.left = this.originalLive2dStyle.left;
            modelContainer.style.right = this.originalLive2dStyle.right;
            modelContainer.style.transform = this.originalLive2dStyle.transform;
            console.log(`[Tutorial] 恢复 ${endPrefix} 模型原始位置`);
        }

        // 清除浮动工具栏保护定时器
        if (this.floatingButtonsProtectionTimer) {
            clearInterval(this.floatingButtonsProtectionTimer);
            this.floatingButtonsProtectionTimer = null;
            console.log('[Tutorial] 浮动工具栏保护定时器已清除');
        }

        // 恢复浮动工具栏的原始样式
        if (this._floatingButtonsOriginalStyles !== undefined) {
            const floatingButtons = document.getElementById(`${endPrefix}-floating-buttons`);
            if (floatingButtons) {
                floatingButtons.style.removeProperty('display');
                floatingButtons.style.removeProperty('visibility');
                floatingButtons.style.removeProperty('opacity');
                if (this._floatingButtonsOriginalStyles.display) {
                    floatingButtons.style.display = this._floatingButtonsOriginalStyles.display;
                }
                if (this._floatingButtonsOriginalStyles.visibility) {
                    floatingButtons.style.visibility = this._floatingButtonsOriginalStyles.visibility;
                }
                if (this._floatingButtonsOriginalStyles.opacity) {
                    floatingButtons.style.opacity = this._floatingButtonsOriginalStyles.opacity;
                }
                console.log('[Tutorial] 已恢复浮动工具栏原始样式');
            }
            this._floatingButtonsOriginalStyles = undefined;
        }

        // 恢复锁图标的原始样式
        if (this._lockIconOriginalStyles !== undefined) {
            const lockIcon = document.getElementById(`${endPrefix}-lock-icon`);
            if (lockIcon) {
                // 先移除 !important 样式
                lockIcon.style.removeProperty('display');
                lockIcon.style.removeProperty('visibility');
                lockIcon.style.removeProperty('opacity');

                // 恢复原始样式（如果原始样式为空字符串则不设置，让 CSS 规则生效）
                if (this._lockIconOriginalStyles.display) {
                    lockIcon.style.display = this._lockIconOriginalStyles.display;
                }
                if (this._lockIconOriginalStyles.visibility) {
                    lockIcon.style.visibility = this._lockIconOriginalStyles.visibility;
                }
                if (this._lockIconOriginalStyles.opacity) {
                    lockIcon.style.opacity = this._lockIconOriginalStyles.opacity;
                }
                console.log('[Tutorial] 已恢复锁图标原始样式');
            }
            this._lockIconOriginalStyles = undefined;
        }

        // 清理 popover 拖动监听器（从 manager 对象获取引用）
        if (this._popoverDragListeners) {
            const { onMouseDown, onMouseMove, onMouseUp, dragElement } = this._popoverDragListeners;
            if (dragElement) {
                dragElement.removeEventListener('mousedown', onMouseDown);
            }
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            this._popoverDragListeners = undefined;
            console.log('[Tutorial] Popover 拖动监听器已清除');
        }
        const popover = document.querySelector('.driver-popover');
        if (popover && popover.dataset.draggableEnabled) {
            delete popover.dataset.draggableEnabled;
        }

        // 恢复所有在引导中修改过的元素的原始样式
        this.restoreAllModifiedElements();
        this.restoreTutorialInteractionState();
    }

    /**
     * 恢复所有在引导中修改过的元素
     */
    restoreAllModifiedElements() {
        if (this.modifiedElementsMap.size === 0) {
            console.log('[Tutorial] 没有需要恢复的元素');
            return;
        }

        console.log(`[Tutorial] 开始恢复 ${this.modifiedElementsMap.size} 个元素的原始样式`);

        this.modifiedElementsMap.forEach((elementRecord, element) => {
            try {
                // 恢复原始的内联样式
                if (elementRecord.originalInlineStyle) {
                    element.setAttribute('style', elementRecord.originalInlineStyle);
                } else {
                    element.removeAttribute('style');
                }

                // 恢复原始的类名
                element.className = elementRecord.originalClassName;

                // 移除任何添加的数据属性
                if (element.dataset.inTutorial) {
                    delete element.dataset.inTutorial;
                }

                console.log(`[Tutorial] 已恢复元素: ${element.tagName}${element.id ? '#' + element.id : ''}${element.className ? '.' + element.className : ''}`);
            } catch (error) {
                console.error('[Tutorial] 恢复元素样式失败:', error);
            }
        });

        // 清空 Map
        this.modifiedElementsMap.clear();
        console.log('[Tutorial] 所有元素样式已恢复，Map 已清空');
    }

    /**
     * 重新启动引导（用户手动触发）
     */
    restartTutorial() {
        const storageKeys = this.getStorageKeysForPage(this.currentPage);
        storageKeys.forEach(key => localStorage.removeItem(key));
        this.pendingTutorialStartSource = 'manual';

        if (this.driver) {
            this.driver.destroy();
        }

        this.startTutorial();
    }

    /**
     * 获取引导状态
     */
    hasSeenTutorial(page = null) {
        if (!page) {
            return localStorage.getItem(this.getStorageKey()) === 'true';
        }

        const storageKeys = this.getStorageKeysForPage(page);
        return storageKeys.some(key => localStorage.getItem(key) === 'true');
    }

    /**
     * 进入全屏模式
     */
    enterFullscreenMode() {
        console.log('[Tutorial] 请求进入全屏模式');

        const elem = document.documentElement;

        // 使用 Fullscreen API 进入全屏
        if (elem.requestFullscreen) {
            elem.requestFullscreen().catch(err => {
                console.error('[Tutorial] 进入全屏失败:', err);
            });
        } else if (elem.webkitRequestFullscreen) { // Safari
            elem.webkitRequestFullscreen();
        } else if (elem.msRequestFullscreen) { // IE11
            elem.msRequestFullscreen();
        } else if (elem.mozRequestFullScreen) { // Firefox
            elem.mozRequestFullScreen();
        }

        console.log('[Tutorial] 全屏模式已请求');
    }

    /**
     * 等待指定时间
     * @param {number} ms - 毫秒数
     */
    sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    /**
     * 退出全屏模式
     */
    exitFullscreenMode() {
        console.log('[Tutorial] 退出全屏模式');

        // 使用 Fullscreen API 退出全屏
        if (document.exitFullscreen) {
            document.exitFullscreen().catch(err => {
                console.error('[Tutorial] 退出全屏失败:', err);
            });
        } else if (document.webkitExitFullscreen) { // Safari
            document.webkitExitFullscreen();
        } else if (document.msExitFullscreen) { // IE11
            document.msExitFullscreen();
        } else if (document.mozCancelFullScreen) { // Firefox
            document.mozCancelFullScreen();
        }

        console.log('[Tutorial] 全屏模式已退出');
    }
    /**
     * 确保角色管理页面的猫娘卡片和进阶设定都已展开
     * 用于进入进阶设定相关步骤前的预处理
     * 使用 async/await + 重试机制确保 DOM 状态稳定
     */
    async _ensureCharaManagerExpanded() {
        let attempts = 0;
        const maxAttempts = 10;

        while (attempts < maxAttempts) {
            attempts++;
            console.log(`[Tutorial] _ensureCharaManagerExpanded: 尝试 ${attempts}/${maxAttempts}`);

            // 1. 找到第一个猫娘卡片
            const targetBlock = document.querySelector('.catgirl-block:first-child');
            if (!targetBlock) {
                console.warn('[Tutorial] _ensureCharaManagerExpanded: 未找到目标猫娘卡片，重试中...');
                await this.sleep(300);
                continue;
            }

            // 2. 确保猫娘卡片详情区域已展开
            const details = targetBlock.querySelector('.catgirl-details');
            const expandBtn = targetBlock.querySelector('.catgirl-expand');
            if (details && expandBtn) {
                const detailsStyle = window.getComputedStyle(details);
                if (detailsStyle.display === 'none') {
                    console.log('[Tutorial] 猫娘卡片详情未展开，正在点击展开按钮...');
                    expandBtn.click();
                    // 等待卡片展开动画完成
                    await this.sleep(600);
                    continue; // 重新进入循环以验证展开结果
                }
            } else {
                console.warn('[Tutorial] _ensureCharaManagerExpanded: 猫娘卡片结构异常，缺少详情或展开按钮');
                return false;
            }

            // 3. 确保“进阶设定”折叠区域已展开
            const foldContainer = targetBlock.querySelector('.fold');
            const foldToggle = targetBlock.querySelector('.fold-toggle');

            if (!foldContainer || !foldToggle) {
                console.warn('[Tutorial] _ensureCharaManagerExpanded: 未找到进阶设定折叠区域或开关');
                return false;
            }

            const isExpanded = foldContainer.classList.contains('open') ||
                window.getComputedStyle(foldContainer).display !== 'none';

            if (!isExpanded) {
                console.log('[Tutorial] 进阶设定未展开，正在点击切换按钮...');
                foldToggle.click();
                // 等待折叠展开动画并刷新 driver 位置
                await this.sleep(500);
                if (this.driver && typeof this.driver.refresh === 'function') {
                    this.driver.refresh();
                }

                // 再次验证是否成功展开
                const finalCheck = foldContainer.classList.contains('open') ||
                    window.getComputedStyle(foldContainer).display !== 'none';

                if (finalCheck) {
                    console.log('[Tutorial] _ensureCharaManagerExpanded: 进阶设定已成功展开');
                    return true;
                } else {
                    console.warn('[Tutorial] _ensureCharaManagerExpanded: 进阶设定展开状态确认失败，继续重试...');
                    continue;
                }
            }

            // 如果已经走到这里，说明所有部分都已经展开了
            console.log('[Tutorial] _ensureCharaManagerExpanded: 确认所有区域已展开');
            return true;
        }

        console.warn('[Tutorial] _ensureCharaManagerExpanded: 达到最大重试次数，可能未能完全展开');
        return false;
    }

    /**
     * 创建帮助按钮 - 已禁用，改用设置页面的下拉菜单
     */
    createHelpButton() {
        // 不再创建右下角帮助按钮
        return;
    }

    /** 
     * 重置所有页面的引导状态 
     */ 
    resetAllTutorials() {
        TUTORIAL_PAGES.forEach(page => {
            this.getStorageKeysForPage(page).forEach(key => localStorage.removeItem(key));
        });
        this.markTutorialManualStartIntent('home');
        console.log('[Tutorial] 已重置所有页面引导');
        this.notifyTutorialResetForCurrentPageIfNeeded('all');
    } 

    /**
     * 重置指定页面的引导状态
     */
    resetPageTutorial(pageKey) {
        if (pageKey === 'all') {
            this.resetAllTutorials();
            return;
        }

        this.getStorageKeysForPage(pageKey).forEach((storageKey) => {
            const oldVal = localStorage.getItem(storageKey);
            localStorage.removeItem(storageKey);
            if (oldVal) console.log('[Tutorial] 重置: 移除', storageKey, '(旧值:', oldVal, ')');
        });

        if (pageKey === 'home') {
            this.markTutorialManualStartIntent('home');
        }

        console.log('[Tutorial] 已重置页面引导:', pageKey);
        this.notifyTutorialResetForCurrentPageIfNeeded(pageKey);
    }

    /**
     * 重新启动当前页面的引导
     */
    restartCurrentTutorial() {
        // 清除浮动按钮保护定时器，防止在重启时留下陈旧的计时器
        if (this.floatingButtonsProtectionTimer) {
            clearInterval(this.floatingButtonsProtectionTimer);
            this.floatingButtonsProtectionTimer = null;
        }

        // 先销毁现有的 driver 以避免残留的监听器和遮罩
        if (this.isTutorialRunning) {
            this.onTutorialEnd();
        }
        if (this.driver) {
            this.driver.destroy();
            this.driver = null;
        }

        const storageKeys = this.getStorageKeysForPage(this.currentPage);
        storageKeys.forEach(storageKey => localStorage.removeItem(storageKey));
        console.log('[Tutorial] 已清除当前页面引导记录:', this.currentPage, storageKeys);
        this.pendingTutorialStartSource = 'manual';

        // 重新初始化并启动引导
        this.isInitialized = false;
        this.isTutorialRunning = false;
        this.waitForDriver();
    }
}

// 创建全局实例
window.universalTutorialManager = null;

/**
 * 初始化通用教程管理器
 * 应在 DOM 加载完成后调用
 */
function initUniversalTutorialManager() {
    // 检测当前页面类型
    const currentPageType = UniversalTutorialManager.detectPage();

    // 如果全局实例存在，检查页面是否改变
    if (window.universalTutorialManager) {
        if (window.universalTutorialManager.currentPage !== currentPageType) {
            console.log('[Tutorial] 页面已改变，销毁旧实例并创建新实例');
            // 销毁旧的 driver 实例和清理状态
            if (window.universalTutorialManager.isTutorialRunning) {
                window.universalTutorialManager.onTutorialEnd();
            }
            if (window.universalTutorialManager.driver) {
                window.universalTutorialManager.driver.destroy();
            }
            window.universalTutorialManager.teardownModelManagerListeners();
            // 创建新实例
            window.universalTutorialManager = new UniversalTutorialManager();
            console.log('[Tutorial] 通用教程管理器已重新初始化，页面:', currentPageType);
        } else {
            console.log('[Tutorial] 页面未改变，使用现有实例');
        }
    } else {
        // 创建新实例
        window.universalTutorialManager = new UniversalTutorialManager();
        console.log('[Tutorial] 通用教程管理器已初始化，页面:', currentPageType);
    }
}

/**
 * 全局函数：重置所有引导
 * 供 HTML 按钮调用
 */
function resetAllTutorials() {
    if (window.universalTutorialManager) {
        window.universalTutorialManager.resetAllTutorials();
    } else {
        // 如果管理器未初始化，直接清除 localStorage
        TUTORIAL_PAGES.forEach(page => { localStorage.removeItem(getTutorialStorageKeyForPage(page)); });
        localStorage.setItem(getTutorialManualIntentKeyForPage('home'), 'true');
    }
    alert(window.t ? window.t('memory.tutorialResetSuccess', '已重置所有引导，下次进入各页面时将重新显示引导。') : '已重置所有引导，下次进入各页面时将重新显示引导。');
}

/**
 * 全局函数：重置指定页面的引导
 * 供下拉菜单调用
 */
function resetTutorialForPage(pageKey) {
    if (!pageKey) return;
    console.log('%c[Tutorial] resetTutorialForPage 被调用, pageKey:', 'color: red; font-weight: bold', pageKey);

    if (pageKey === 'all') {
        resetAllTutorials();
        return;
    }

    if (window.universalTutorialManager) {
        window.universalTutorialManager.resetPageTutorial(pageKey);
    } else {
        if (pageKey === 'model_manager') {
            localStorage.removeItem(getTutorialStorageKeyForPage('model_manager'));
            localStorage.removeItem(getTutorialStorageKeyForPage('model_manager_live2d'));
            localStorage.removeItem(getTutorialStorageKeyForPage('model_manager_vrm'));
            localStorage.removeItem(getTutorialStorageKeyForPage('model_manager_mmd'));
            localStorage.removeItem(getTutorialStorageKeyForPage('model_manager_common'));
        } else {
            localStorage.removeItem(getTutorialStorageKeyForPage(pageKey));
        }
        if (pageKey === 'home') {
            localStorage.setItem(getTutorialManualIntentKeyForPage('home'), 'true');
        }
    }

    // 验证重置结果
    if (pageKey === 'model_manager') {
        const mmdVal = localStorage.getItem('neko_tutorial_model_manager_mmd');
        const vrmVal = localStorage.getItem('neko_tutorial_model_manager_vrm');
        const l2dVal = localStorage.getItem('neko_tutorial_model_manager_live2d');
        console.log('%c[Tutorial] 重置后验证 → mmd:', 'color: red; font-weight: bold', mmdVal, 'vrm:', vrmVal, 'live2d:', l2dVal);
    }

    const pageNames = {
        'home': window.t ? window.t('memory.tutorialPageHome', '主页') : '主页',
        'model_manager': window.t ? window.t('memory.tutorialPageModelManager', '模型设置') : '模型设置',
        'parameter_editor': window.t ? window.t('memory.tutorialPageParameterEditor', '捏脸系统') : '捏脸系统',
        'emotion_manager': window.t ? window.t('memory.tutorialPageEmotionManager', '情感管理') : '情感管理',
        'chara_manager': window.t ? window.t('memory.tutorialPageCharaManager', '角色管理') : '角色管理',
        'settings': window.t ? window.t('memory.tutorialPageSettings', 'API设置') : 'API设置',
        'voice_clone': window.t ? window.t('memory.tutorialPageVoiceClone', '语音克隆') : '语音克隆',
        'memory_browser': window.t ? window.t('memory.tutorialPageMemoryBrowser', '记忆浏览') : '记忆浏览'
    };
    const pageName = pageNames[pageKey] || pageKey;
    // 使用带参数的 i18n 键，格式：已重置「{{pageName}}」的引导
    const message = window.t
        ? window.t('memory.tutorialPageResetSuccessWithName', { pageName: pageName, defaultValue: `已重置「${pageName}」的引导，下次进入该页面时将重新显示引导。` })
        : `已重置「${pageName}」的引导，下次进入该页面时将重新显示引导。`;
    alert(message);
}

/**
 * 全局函数：重新启动当前页面引导
 * 供帮助按钮调用
 */
function restartCurrentTutorial() {
    if (window.universalTutorialManager) {
        window.universalTutorialManager.restartCurrentTutorial();
    }
}

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { UniversalTutorialManager, initUniversalTutorialManager };
}
