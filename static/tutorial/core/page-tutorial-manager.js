/**
 * N.E.K.O non-home page tutorial manager.
 *
 * Home now uses the seven-day Yui guide. This file restores the older
 * Driver.js page tutorials for settings/model/character/memory pages.
 */
(function () {
    'use strict';

    const PAGE_STORAGE_PREFIX = 'neko_tutorial_';
    const YUI_HANDOFF_STORAGE_KEY = 'neko_yui_guide_handoff_token';
    const SUPPORTED_PAGES = Object.freeze([
        'model_manager',
        'parameter_editor',
        'emotion_manager',
        'chara_manager',
        'settings',
        'voice_clone',
        'memory_browser',
        'steam_workshop'
    ]);

    function storageKeyForPage(pageKey) {
        return PAGE_STORAGE_PREFIX + pageKey;
    }

    function manualIntentKeyForPage(pageKey) {
        return storageKeyForPage(pageKey) + '_manual_intent';
    }

    function getAllPageTutorialStorageKeys(pageKey) {
        if (pageKey === 'model_manager') {
            return [
                'model_manager',
                'model_manager_live2d',
                'model_manager_vrm',
                'model_manager_mmd',
                'model_manager_common'
            ].map(storageKeyForPage);
        }
        return [storageKeyForPage(pageKey)];
    }

    function dispatchPageTutorialReset(pageKey) {
        if (typeof window.dispatchEvent !== 'function' || typeof CustomEvent === 'undefined') return;
        window.dispatchEvent(new CustomEvent('neko:page-tutorial-reset', {
            detail: {
                page: pageKey,
                timestamp: Date.now()
            }
        }));
    }

    function parseYuiHandoffToken(rawToken) {
        if (!rawToken) return null;
        if (typeof rawToken === 'object') return rawToken;
        if (typeof rawToken !== 'string') return null;
        try {
            return JSON.parse(rawToken);
        } catch (error) {
            return null;
        }
    }

    function getYuiHandoffTargetPagesForPage(pageKey) {
        if (pageKey === 'settings') {
            return ['settings', 'api_key'];
        }
        return [pageKey];
    }

    function isActiveYuiHandoffTokenForPage(rawToken, pageKey) {
        const token = parseYuiHandoffToken(rawToken);
        if (!token || !token.token) return false;
        if (token.consumed) return false;

        const expiresAt = Number(token.expires_at);
        if (Number.isFinite(expiresAt) && Date.now() > expiresAt) return false;

        const targetPage = typeof token.target_page === 'string' ? token.target_page.trim() : '';
        return !!targetPage && getYuiHandoffTargetPagesForPage(pageKey).includes(targetPage);
    }

    class PageTutorialManager {
        constructor() {
            this.currentPage = PageTutorialManager.detectPage();
            this.driver = null;
            this.cachedValidSteps = [];
            this.isTutorialRunning = false;
            this.currentTutorialStartSource = 'auto';
            this.endReason = '';
            this.skipButton = null;
            this._highlightedStepIndex = -1;
            this._refreshTimers = [];
            this._modelManagerModeListenerAttached = false;
            this._modelManagerBootstrapTimer = null;
            this._skipSafeAreaCleanup = null;
            this._skipSafeAreaController = null;
        }

        static detectPage() {
            const path = String(window.location.pathname || '');

            if (path === '/' || path === '/index.html' || path === '/chat') {
                return 'home';
            }
            if (path.includes('parameter_editor')) {
                return 'parameter_editor';
            }
            if (path.includes('emotion_manager')) {
                return 'emotion_manager';
            }
            if (path.includes('model_manager') || path === '/l2d') {
                return 'model_manager';
            }
            if (path.includes('character_card_manager') || path.includes('chara_manager')) {
                return 'chara_manager';
            }
            if (path.includes('api_key') || path.includes('settings')) {
                return 'settings';
            }
            if (path.includes('voice_clone')) {
                return 'voice_clone';
            }
            if (path.includes('steam_workshop')) {
                return 'steam_workshop';
            }
            if (path.includes('memory_browser')) {
                return 'memory_browser';
            }
            return 'unknown';
        }

        static getModelManagerDisplayMode() {
            const typeSelect = document.getElementById('model-type-select');
            let value = typeSelect && typeSelect.value;

            if (typeSelect && value === 'live2d') {
                try {
                    let saved = (localStorage.getItem('modelType') || 'live2d').toLowerCase();
                    if (saved === 'vrm') saved = 'live3d';
                    if (saved === 'live3d') value = 'live3d';
                } catch (error) {
                    // Ignore storage failures; live2d is the safe fallback.
                }
            }

            if (value === 'live3d') {
                let subType = '';
                try {
                    subType = (localStorage.getItem('live3dSubType') || '').toLowerCase();
                } catch (error) {
                    subType = '';
                }
                if (subType === 'mmd') return 'mmd';

                const mmdSection = document.getElementById('mmd-settings-section');
                if (mmdSection) {
                    try {
                        const styles = window.getComputedStyle(mmdSection);
                        if (styles.display !== 'none' && styles.visibility !== 'hidden' && styles.opacity !== '0') {
                            return 'mmd';
                        }
                    } catch (error) {
                        // Ignore style read failures.
                    }
                }
                return 'vrm';
            }

            return 'live2d';
        }

        static modelManagerModeToPageKey(mode) {
            if (mode === 'mmd') return 'model_manager_mmd';
            if (mode === 'vrm') return 'model_manager_vrm';
            return 'model_manager_live2d';
        }

        init() {
            if (!this.shouldManageCurrentPage()) {
                return false;
            }

            if (this.currentPage === 'model_manager') {
                this.setupModelManagerModeListener();
                this.scheduleModelManagerBootstrapFallback();
            }

            this.waitForDriver().then(() => {
                this.checkAndStartTutorial();
            }).catch((error) => {
                console.warn('[PageTutorial] Driver.js unavailable:', error);
            });

            return true;
        }

        shouldManageCurrentPage() {
            if (!SUPPORTED_PAGES.includes(this.currentPage)) return false;

            // Honor the same mobile bailout as the homepage tutorial, but keep
            // desktop popup pages usable. Voice clone is intentionally opened in
            // a 700px desktop popup, which otherwise looks like mobile width here.
            if (window.innerWidth <= 768 && !this.shouldAllowCompactDesktopTutorial()) return false;
            return true;
        }

        shouldAllowCompactDesktopTutorial() {
            if (this.currentPage !== 'voice_clone') return false;
            const viewportWidth = Number(window.innerWidth || 0);
            const screenWidth = Number(window.screen && window.screen.width || 0);
            return viewportWidth >= 640 && screenWidth > 768;
        }

        waitForDriver() {
            if (typeof window.driver !== 'undefined') {
                return Promise.resolve(true);
            }

            return new Promise((resolve, reject) => {
                let attempts = 0;
                const maxAttempts = 100;
                const check = () => {
                    attempts += 1;
                    if (typeof window.driver !== 'undefined') {
                        resolve(true);
                        return;
                    }
                    if (attempts >= maxAttempts) {
                        reject(new Error('driver_timeout'));
                        return;
                    }
                    window.setTimeout(check, 100);
                };
                check();
            });
        }

        isApiSettingsPage() {
            const path = String(window.location.pathname || '');
            return this.currentPage === 'settings' && path.includes('api_key');
        }

        waitForApiSettingsReady(maxWaitTime = 5000) {
            if (!this.isApiSettingsPage()) {
                return Promise.resolve(true);
            }

            return new Promise((resolve) => {
                const start = Date.now();
                const isLoadingOverlayHidden = () => {
                    const loadingOverlay = document.getElementById('loading-overlay');
                    if (!loadingOverlay) return true;
                    if (loadingOverlay.hidden) return true;
                    const style = window.getComputedStyle
                        ? window.getComputedStyle(loadingOverlay)
                        : loadingOverlay.style;
                    return style.display === 'none' || style.visibility === 'hidden';
                };
                const check = () => {
                    if (isLoadingOverlayHidden() || Date.now() - start >= maxWaitTime) {
                        resolve(true);
                        return;
                    }
                    window.setTimeout(check, 120);
                };
                check();
            });
        }

        t(key, fallback) {
            try {
                if (typeof window.t === 'function') {
                    const translated = window.t(key, fallback);
                    if (translated && translated !== key) return translated;
                }
                if (window.i18next && typeof window.i18next.t === 'function') {
                    const translated = window.i18next.t(key, { defaultValue: fallback });
                    if (translated && translated !== key) return translated;
                }
            } catch (error) {
                // Fall through to fallback.
            }
            return fallback;
        }

        getPreferredStoragePageKey(page = this.currentPage) {
            if (page === 'model_manager') {
                return PageTutorialManager.modelManagerModeToPageKey(PageTutorialManager.getModelManagerDisplayMode());
            }
            return page;
        }

        getStorageKey(page = this.currentPage) {
            return storageKeyForPage(this.getPreferredStoragePageKey(page));
        }

        hasSeenTutorial() {
            return localStorage.getItem(this.getStorageKey()) === 'true';
        }

        markTutorialSeen() {
            localStorage.setItem(this.getStorageKey(), 'true');
            localStorage.removeItem(manualIntentKeyForPage(this.currentPage));
            if (this.currentPage === 'model_manager') {
                localStorage.setItem(storageKeyForPage('model_manager_common'), 'true');
            }
        }

        consumeManualIntent() {
            const keys = [
                manualIntentKeyForPage(this.currentPage),
                manualIntentKeyForPage(this.getPreferredStoragePageKey(this.currentPage))
            ];
            const hasIntent = keys.some((key) => localStorage.getItem(key) === 'true');
            keys.forEach((key) => localStorage.removeItem(key));
            return hasIntent;
        }

        hasActiveYuiHandoff() {
            try {
                if (isActiveYuiHandoffTokenForPage(
                    window.universalTutorialManager && window.universalTutorialManager._yuiGuideHandoffToken,
                    this.currentPage
                )) {
                    return true;
                }
                const token = localStorage.getItem(YUI_HANDOFF_STORAGE_KEY);
                return isActiveYuiHandoffTokenForPage(token, this.currentPage);
            } catch (error) {
                return false;
            }
        }

        checkAndStartTutorial() {
            if (!this.shouldManageCurrentPage()) return;
            if (this.isTutorialRunning || window.isInTutorial) return;
            if (this.hasActiveYuiHandoff()) return;

            const manual = this.consumeManualIntent();
            if (!manual && this.hasSeenTutorial()) return;

            if (this.currentPage === 'model_manager') {
                this.maybeStartModelManagerTutorial(450, 'checkAndStart');
                return;
            }

            if (this.currentPage === 'chara_manager') {
                this.waitForCharacterCards().then(() => {
                    this.startTutorialWhenI18nReady(500, manual ? 'manual' : 'auto');
                });
                return;
            }

            if (this.isApiSettingsPage()) {
                this.waitForApiSettingsReady().then(() => {
                    this.startTutorialWhenI18nReady(300, manual ? 'manual' : 'auto');
                });
                return;
            }

            this.startTutorialWhenI18nReady(1200, manual ? 'manual' : 'auto');
        }

        startTutorialWhenI18nReady(delayMs = 0, source = 'auto') {
            this.currentTutorialStartSource = source;
            const startedAt = Date.now();
            const timeoutMs = 5000;

            const isReady = () => {
                if (window.i18nReady === true) return true;
                if (window.i18next && typeof window.i18next.isInitialized === 'boolean') {
                    return window.i18next.isInitialized === true;
                }
                return typeof window.t === 'function';
            };

            const wait = () => {
                if (isReady() || Date.now() - startedAt >= timeoutMs) {
                    window.setTimeout(() => this.startTutorial(), delayMs);
                    return;
                }
                window.setTimeout(wait, 100);
            };

            wait();
        }

        maybeStartModelManagerTutorial(delayMs = 400, reason = '') {
            if (this.currentPage !== 'model_manager') return;
            if (this.isTutorialRunning || window.isInTutorial) return;
            if (this.hasActiveYuiHandoff()) return;

            const manual = this.consumeManualIntent();
            if (!manual && this.hasSeenTutorial()) return;

            window.setTimeout(() => {
                if (this.isTutorialRunning || window.isInTutorial) return;
                if (this.hasActiveYuiHandoff()) return;
                if (!manual && this.hasSeenTutorial()) return;
                console.log('[PageTutorial] start model manager tutorial:', reason);
                this.startTutorialWhenI18nReady(0, manual ? 'manual' : 'auto');
            }, delayMs);
        }

        setupModelManagerModeListener() {
            if (this._modelManagerModeListenerAttached) return;
            this._modelManagerModeListenerAttached = true;
            window.addEventListener('neko-model-manager-mode-set', () => {
                this.maybeStartModelManagerTutorial(250, 'mode-set');
            });
        }

        scheduleModelManagerBootstrapFallback() {
            if (this._modelManagerBootstrapTimer) {
                window.clearTimeout(this._modelManagerBootstrapTimer);
            }
            this._modelManagerBootstrapTimer = window.setTimeout(() => {
                this.maybeStartModelManagerTutorial(0, 'bootstrap-fallback');
            }, 1800);
        }

        getStepsForPage() {
            const configs = {
                model_manager: this.getModelManagerSteps(),
                parameter_editor: this.getParameterEditorSteps(),
                emotion_manager: this.getEmotionManagerSteps(),
                chara_manager: this.getCharaManagerSteps(),
                settings: this.getSettingsSteps(),
                voice_clone: this.getVoiceCloneSteps(),
                memory_browser: this.getMemoryBrowserSteps(),
                steam_workshop: this.getSteamWorkshopSteps()
            };
            return configs[this.currentPage] || [];
        }

        getModelManagerSteps() {
            const mode = PageTutorialManager.getModelManagerDisplayMode();

            const live2dSteps = [
                {
                    element: '#persistent-expression-select-btn',
                    popover: {
                        title: this.t('tutorial.model_manager.live2d.step4.title', '常驻表情'),
                        description: this.t('tutorial.model_manager.live2d.step4.desc', '选择一个常驻表情，让模型持续保持该表情，直到你再次更改。')
                    }
                },
                {
                    element: '#emotion-config-btn',
                    popover: {
                        title: this.t('tutorial.model_manager.live2d.step5.title', '情感配置'),
                        description: this.t('tutorial.model_manager.live2d.step5.desc', '进入前请先选择一个模型。点击这里配置 Live2D 模型的情感表现，可为不同的情感设置对应的表情和动作组合。')
                    }
                },
                {
                    element: '#parameter-editor-btn',
                    popover: {
                        title: this.t('tutorial.model_manager.live2d.step6.title', '捏脸系统'),
                        description: this.t('tutorial.model_manager.live2d.step6.desc', '点击这里进入捏脸系统，可以精细调整 Live2D 模型的面部参数，打造独特的猫娘形象。')
                    }
                }
            ];

            const vrmSteps = [
                {
                    element: '#ambient-light-control',
                    popover: {
                        title: this.t('tutorial.model_manager.vrm.step6.title', '环境光'),
                        description: this.t('tutorial.model_manager.vrm.step6.desc', '调整环境光强度。环境光影响整体亮度，数值越高模型越亮。')
                    }
                },
                {
                    element: '#main-light-control',
                    popover: {
                        title: this.t('tutorial.model_manager.vrm.step7.title', '主光源'),
                        description: this.t('tutorial.model_manager.vrm.step7.desc', '调整主光源强度。主光源是主要的照明来源，影响模型的明暗对比。')
                    }
                },
                {
                    element: '#exposure-control',
                    popover: {
                        title: this.t('tutorial.model_manager.vrm.step8.title', '曝光'),
                        description: this.t('tutorial.model_manager.vrm.step8.desc', '调整整体曝光强度。数值越高整体越亮，越低则更暗更有对比。')
                    }
                },
                {
                    element: '#tonemapping-control',
                    popover: {
                        title: this.t('tutorial.model_manager.vrm.step9.title', '色调映射'),
                        description: this.t('tutorial.model_manager.vrm.step9.desc', '选择不同的色调映射算法，决定画面亮部和暗部的呈现风格。')
                    }
                }
            ];

            const mmdSteps = [
                {
                    element: '#vrm-model-select-btn',
                    popover: {
                        title: this.t('tutorial.model_manager.mmd.step1.title', '选择 MMD 模型'),
                        description: this.t('tutorial.model_manager.mmd.step1.desc', '在 3D模型（MMD）模式下从这里选择要使用的模型。MMD 与 VRM 共用同一模型列表。')
                    }
                },
                {
                    element: '#mmd-animation-select-btn',
                    popover: {
                        title: this.t('tutorial.model_manager.mmd.step2.title', 'VMD 动画'),
                        description: this.t('tutorial.model_manager.mmd.step2.desc', '为 MMD 模型选择 VMD 动作。也可使用「导入 VMD 动画」添加自定义动作文件。')
                    }
                },
                {
                    element: '#mmd-ambient-intensity-slider',
                    popover: {
                        title: this.t('tutorial.model_manager.mmd.step3.title', 'MMD 光照'),
                        description: this.t('tutorial.model_manager.mmd.step3.desc', '在「MMD 模型设置」中调节环境光、主光源、曝光与色调映射等，控制 3D 画面效果。')
                    }
                },
                {
                    element: '#live3d-emotion-config-btn',
                    popover: {
                        title: this.t('tutorial.model_manager.mmd.step4.title', '情感配置'),
                        description: this.t('tutorial.model_manager.mmd.step4.desc', '先选好模型后，可由此进入情感配置，为不同情感设置表现（3D模型下 MMD 与 VRM 共用此入口）。')
                    }
                }
            ];

            if (mode === 'mmd') return mmdSteps;
            if (mode === 'vrm') return vrmSteps;
            return live2dSteps;
        }

        getParameterEditorSteps() {
            return [
                {
                    element: '#model-select-btn',
                    popover: {
                        title: this.t('tutorial.parameter_editor.step1.title', '选择模型'),
                        description: this.t('tutorial.parameter_editor.step1.desc', '首先选择要编辑的 Live2D 模型。只有选择了模型后，才能调整参数。')
                    }
                },
                {
                    element: '#parameters-list',
                    popover: {
                        title: this.t('tutorial.parameter_editor.step2.title', '参数列表'),
                        description: this.t('tutorial.parameter_editor.step2.desc', '这里显示了模型的所有可调参数。每个参数控制模型的不同部分，如眼睛大小、嘴巴形状、头部角度等。')
                    }
                }
            ];
        }

        getEmotionManagerSteps() {
            return [
                {
                    element: '#model-singleselect',
                    popover: {
                        title: this.t('tutorial.emotion_manager.step1.title', '选择模型'),
                        description: this.t('tutorial.emotion_manager.step1.desc', '首先选择要配置情感的模型。每个模型可以有独立的情感配置。')
                    }
                },
                {
                    element: '#model-singleselect',
                    onHighlighted: () => this.openEmotionModelPicker(),
                    popover: {
                        title: this.t('tutorial.emotion_manager.step_pick.title', '选择一个模型'),
                        description: this.t('tutorial.emotion_manager.step_pick.desc', '从下拉列表中点击选择一个模型。选好模型后才能继续配置。')
                    }
                },
                {
                    element: '#emotion-config',
                    // #emotion-config / #reset-btn start hidden (display:none) until a
                    // model is picked in the step above. Filtering on current visibility
                    // would drop these steps before the user ever selects a model, so the
                    // restored tutorial would end right after the model picker and never
                    // cover the actual config area. Keep them (the elements exist in the
                    // DOM) and let Driver highlight them once selection reveals them.
                    requiresVisible: false,
                    popover: {
                        title: this.t('tutorial.emotion_manager.step2.title', '情感配置区域'),
                        description: this.t('tutorial.emotion_manager.step2.desc', '这里可以为不同的情感配置对应的表情和动作组合。猫娘会根据对话内容自动切换情感表现。')
                    }
                },
                {
                    element: '#reset-btn',
                    requiresVisible: false,
                    popover: {
                        title: this.t('tutorial.emotion_manager.step3.title', '重置配置'),
                        description: this.t('tutorial.emotion_manager.step3.desc', '点击这个按钮可以将情感配置重置为默认值。')
                    }
                }
            ];
        }

        getCharaManagerSteps() {
            return [
                {
                    element: '#master-profile-section',
                    popover: {
                        title: this.t('tutorial.chara_manager.step1.title', '主人档案'),
                        description: this.t('tutorial.chara_manager.step1.desc', '这是您的主人档案。档案名是必填项，其他信息都是可选的，这些信息会影响猫娘对您的称呼和态度。')
                    }
                },
                {
                    element: '#character-cards-content',
                    popover: {
                        title: this.t('tutorial.chara_manager.step6.title', '猫娘档案'),
                        description: this.t('tutorial.chara_manager.step6.desc', '这里可以创建和管理多个猫娘角色。每个角色都有独特的性格、形象和语音设定。')
                    }
                },
                {
                    element: '.chara-add-btn',
                    popover: {
                        title: this.t('tutorial.chara_manager.step7.title', '新增猫娘'),
                        description: this.t('tutorial.chara_manager.step7.desc', '点击这个按钮创建一个新的猫娘角色。每个角色都是独立的，有自己的记忆和性格。')
                    }
                },
                {
                    element: '.chara-card-item:first-child, .chara-list-item:first-child',
                    popover: {
                        title: this.t('tutorial.chara_manager.step8.title', '猫娘卡片'),
                        description: this.t('tutorial.chara_manager.step8.desc', '点击猫娘名称可以展开或折叠详细信息。每个猫娘都有独立的设定。')
                    }
                },
                {
                    element: '.chara-card-item:first-child .card-action-btn.switch-btn, .chara-list-item:first-child .list-action-btn.switch-btn',
                    popover: {
                        title: this.t('tutorial.chara_manager.step11.title', '切换猫娘'),
                        description: this.t('tutorial.chara_manager.step11.desc', '点击此按钮可以将这个猫娘设为当前活跃角色。切换后主页会使用该角色的形象和性格。')
                    }
                },
                {
                    element: '#api-key-settings-btn',
                    popover: {
                        title: this.t('tutorial.chara_manager.step5.title', 'API Key 设置'),
                        description: this.t('tutorial.chara_manager.step5.desc', '点击这里配置 AI 服务的 API Key。这是猫娘能够进行对话的必要配置。')
                    }
                }
            ];
        }

        getSettingsSteps() {
            return [
                {
                    element: '#coreApiSelect-dropdown-trigger',
                    popover: {
                        title: this.t('tutorial.settings.step2.title', '核心 API 服务商'),
                        description: this.t('tutorial.settings.step2.desc', '这是最重要的设置。核心 API 负责对话功能。')
                    }
                },
                {
                    element: '#apiKeyInput',
                    popover: {
                        title: this.t('tutorial.settings.step3.title', '核心 API Key'),
                        description: this.t('tutorial.settings.step3.desc', '将您选择的 API 服务商的 API Key 粘贴到这里。如果选择了免费版，这个字段可以留空。')
                    }
                }
            ];
        }

        getVoiceCloneSteps() {
            return [
                {
                    element: '#provider-notice, .alibaba-api-notice, #voiceProvider-dropdown-trigger, #voiceProvider',
                    popover: {
                        title: this.t('tutorial.voice_clone.step1.title', '重要提示'),
                        description: this.t('tutorial.voice_clone.step1.desc', '语音克隆功能需要对应的 API 或本地语音服务，请先确认 API 设置可用。')
                    }
                },
                {
                    element: '#refLanguage-dropdown-trigger, #refLanguage',
                    popover: {
                        title: this.t('tutorial.voice_clone.step2.title', '选择参考音频语言'),
                        description: this.t('tutorial.voice_clone.step2.desc', '选择您上传的音频文件的语言。这帮助系统更准确地识别和克隆声音特征。')
                    }
                },
                {
                    element: '#prefix',
                    popover: {
                        title: this.t('tutorial.voice_clone.step3.title', '自定义前缀'),
                        description: this.t('tutorial.voice_clone.step3.desc', '输入一个 10 字符以内的前缀。这个前缀会作为克隆音色的标识。')
                    }
                },
                {
                    element: '.register-voice-btn',
                    popover: {
                        title: this.t('tutorial.voice_clone.step4.title', '注册音色'),
                        description: this.t('tutorial.voice_clone.step4.desc', '点击这个按钮开始克隆您的音色。系统会处理音频并生成一个独特的音色 ID。')
                    }
                },
                {
                    element: '.voice-list-section',
                    popover: {
                        title: this.t('tutorial.voice_clone.step5.title', '已注册音色列表'),
                        description: this.t('tutorial.voice_clone.step5.desc', '这里显示所有已成功克隆的音色。您可以在角色管理中选择这些音色来为猫娘配音。')
                    }
                }
            ];
        }

        getSteamWorkshopSteps() {
            return [
                {
                    element: '#workshop-tabs, .tabs',
                    popover: {
                        title: this.t('tutorial.steam_workshop.step1.title', '创意工坊分区'),
                        description: this.t('tutorial.steam_workshop.step1.desc', '这里可以在订阅内容和角色卡之间切换，后续管理 Workshop 内容都会从这里展开。')
                    }
                },
                {
                    element: '#subscriptions-list',
                    popover: {
                        title: this.t('tutorial.steam_workshop.step2.title', '订阅内容列表'),
                        description: this.t('tutorial.steam_workshop.step2.desc', '这里会展示当前已订阅的内容，您可以刷新、筛选并继续管理创意工坊资源。')
                    }
                }
            ];
        }

        getMemoryBrowserSteps() {
            return [
                {
                    element: '#memory-file-list',
                    popover: {
                        title: this.t('tutorial.memory_browser.step2.title', '猫娘记忆库'),
                        description: this.t('tutorial.memory_browser.step2.desc', '这里列出了所有猫娘的记忆库。点击一个猫娘的名称可以查看和编辑她的对话历史。')
                    }
                },
                {
                    element: '#memory-chat-edit',
                    requiresVisible: true,
                    popover: {
                        title: this.t('tutorial.memory_browser.step4.title', '聊天记录编辑'),
                        description: this.t('tutorial.memory_browser.step4.desc', '这里显示选中猫娘的所有对话记录。您可以在这里查看、编辑或删除特定的对话内容。')
                    }
                }
            ];
        }

        isElementVisible(element) {
            if (!element) return false;
            const style = window.getComputedStyle(element);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                return false;
            }
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }

        resolveStepElementSelector(step) {
            const selectors = String(step.element || '')
                .split(',')
                .map((selector) => selector.trim())
                .filter(Boolean);

            for (const selector of selectors) {
                const element = document.querySelector(selector);
                if (!element) continue;
                if (step.requiresVisible === false || this.isElementVisible(element)) {
                    return { element, selector };
                }
            }

            if (step.requiresVisible === false && selectors.length > 0) {
                const element = document.querySelector(selectors[0]);
                if (element) return { element, selector: selectors[0] };
            }

            return { element: null, selector: selectors[0] || '' };
        }

        getValidSteps() {
            return this.getStepsForPage().filter((step) => {
                const resolved = this.resolveStepElementSelector(step);
                if (!resolved.element) {
                    console.warn('[PageTutorial] missing element, skip step:', step.element);
                    return false;
                }
                return true;
            }).map((step) => {
                const resolved = this.resolveStepElementSelector(step);
                return Object.assign({}, step, {
                    element: resolved.selector || step.element
                });
            });
        }

        startTutorial() {
            if (!this.shouldManageCurrentPage()) return false;
            if (this.isTutorialRunning || window.isInTutorial) return false;
            if (this.hasActiveYuiHandoff()) return false;

            const steps = this.getValidSteps();
            if (steps.length === 0) {
                console.warn('[PageTutorial] no valid tutorial steps:', this.currentPage);
                return false;
            }

            const DriverClass = window.driver;
            if (!DriverClass) {
                console.warn('[PageTutorial] driver missing');
                return false;
            }

            this.cachedValidSteps = steps;
            this.endReason = '';
            this._highlightedStepIndex = -1;
            this.driver = new DriverClass({
                padding: 8,
                allowClose: true,
                overlayClickNext: false,
                animate: true,
                smoothScroll: true,
                className: 'neko-tutorial-driver',
                nextBtnText: this.t('tutorial.buttons.next', '下一步'),
                prevBtnText: this.t('tutorial.buttons.prev', '上一步'),
                doneBtnText: this.t('tutorial.buttons.done', '完成')
            });

            this.driver.setSteps(steps);
            this.driver.on('next', () => this.handleStepHighlighted());
            this.driver.on('destroy', () => this.handleTutorialEnd());

            this.isTutorialRunning = true;
            window.isInTutorial = true;
            document.body.classList.add('page-tutorial-running');
            this.showSkipButton();

            try {
                this.driver.start();
            } catch (error) {
                console.error('[PageTutorial] failed to start:', error);
                this.handleTutorialEnd('destroy');
                return false;
            }

            window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                detail: {
                    page: this.currentPage,
                    source: this.currentTutorialStartSource
                }
            }));
            return true;
        }

        handleStepHighlighted() {
            if (!this.driver || !this.cachedValidSteps.length) return;
            const index = typeof this.driver.currentStep === 'number' ? this.driver.currentStep : 0;
            if (index === this._highlightedStepIndex) return;
            this._highlightedStepIndex = index;

            const step = this.cachedValidSteps[index];
            if (step && typeof step.onHighlighted === 'function') {
                try {
                    step.onHighlighted();
                } catch (error) {
                    console.warn('[PageTutorial] step onHighlighted failed:', error);
                }
            }

            this.scheduleRefreshes([80, 200, 450]);
        }

        scheduleRefreshes(delays) {
            delays.forEach((delay) => {
                const timer = window.setTimeout(() => {
                    if (this.driver && typeof this.driver.refresh === 'function') {
                        this.driver.refresh();
                    }
                }, delay);
                this._refreshTimers.push(timer);
            });
        }

        openEmotionModelPicker() {
            const singleSelect = document.querySelector('#model-singleselect');
            if (!singleSelect) return;
            const header = singleSelect.querySelector('.singleselect-header');
            const options = singleSelect.querySelector('.singleselect-options');

            singleSelect.classList.add('active');
            if (header) header.setAttribute('aria-expanded', 'true');
            if (options) {
                options.style.setProperty('display', 'block', 'important');
            }
            this.scheduleRefreshes([80, 220, 500]);
        }

        ensureSkipSafeAreaController() {
            if (!this._skipSafeAreaController
                && window.TutorialSkipController
                && typeof window.TutorialSkipController.createController === 'function') {
                this._skipSafeAreaController = window.TutorialSkipController.createController({
                    document,
                    buttonId: 'neko-page-tutorial-skip-btn'
                });
            }
            return this._skipSafeAreaController;
        }

        applySkipSafeAreaVariables() {
            try {
                const controller = this.ensureSkipSafeAreaController();
                if (controller && typeof controller.applySafeAreaVariables === 'function') {
                    controller.applySafeAreaVariables();
                } else if (window.TutorialSkipController
                    && typeof window.TutorialSkipController.applySafeAreaVariables === 'function') {
                    window.TutorialSkipController.applySafeAreaVariables({ document, buttonId: 'neko-page-tutorial-skip-btn' });
                }
            } catch (error) {
                console.warn('[PageTutorial] skip safe area refresh failed:', error);
            }
        }

        clearSkipSafeAreaRefreshHooks() {
            if (typeof this._skipSafeAreaCleanup === 'function') {
                this._skipSafeAreaCleanup();
            }
            this._skipSafeAreaCleanup = null;
        }

        installSkipSafeAreaRefreshHooks() {
            this.clearSkipSafeAreaRefreshHooks();
            const refresh = () => this.applySkipSafeAreaVariables();
            const timers = [0, 80, 240, 600].map((delay) => window.setTimeout(refresh, delay));
            window.addEventListener('neko:niri-pet-physical-crop-state-applied', refresh);
            window.addEventListener('resize', refresh);
            this._skipSafeAreaCleanup = () => {
                timers.forEach((timer) => window.clearTimeout(timer));
                window.removeEventListener('neko:niri-pet-physical-crop-state-applied', refresh);
                window.removeEventListener('resize', refresh);
            };
        }

        showSkipButton() {
            this.hideSkipButton();
            let skipHandled = false;
            const absorbSkipEvent = (event) => {
                if (event && typeof event.preventDefault === 'function') {
                    event.preventDefault();
                }
                if (event && typeof event.stopImmediatePropagation === 'function') {
                    event.stopImmediatePropagation();
                }
                if (event && typeof event.stopPropagation === 'function') {
                    event.stopPropagation();
                }
            };
            const completeSkipRequest = () => {
                this.endReason = 'skip';
                if (this.driver && typeof this.driver.destroy === 'function') {
                    this.driver.destroy();
                } else {
                    this.handleTutorialEnd('skip');
                }
            };
            const handleSkipPress = (event) => {
                absorbSkipEvent(event);
            };
            const handleSkipRequest = (event, delayMs = 0) => {
                absorbSkipEvent(event);
                if (skipHandled) {
                    return;
                }
                skipHandled = true;
                if (delayMs > 0) {
                    window.setTimeout(completeSkipRequest, delayMs);
                    return;
                }
                completeSkipRequest();
            };
            this.installSkipSafeAreaRefreshHooks();
            const button = document.createElement('button');
            button.type = 'button';
            button.id = 'neko-page-tutorial-skip-btn';
            button.className = 'neko-page-tutorial-skip-btn';
            button.style.setProperty('pointer-events', 'auto', 'important');
            button.style.setProperty('z-index', '2147483647', 'important');
            button.style.touchAction = 'manipulation';
            button.textContent = this.t('tutorial.buttons.skip', '跳过');
            button.addEventListener('pointerdown', handleSkipPress);
            button.addEventListener('mousedown', handleSkipPress);
            button.addEventListener('touchstart', handleSkipPress, { passive: false });
            button.addEventListener('pointerup', (event) => handleSkipRequest(event, 80));
            button.addEventListener('touchend', (event) => handleSkipRequest(event, 80), { passive: false });
            button.addEventListener('click', handleSkipRequest);
            const controller = this.ensureSkipSafeAreaController();
            const host = controller && typeof controller.getButtonHost === 'function'
                ? controller.getButtonHost()
                : document.body;
            if (host && typeof host.appendChild === 'function') {
                host.appendChild(button);
            } else {
                document.body.appendChild(button);
            }
            this.skipButton = button;
            this.applySkipSafeAreaVariables();
        }

        hideSkipButton() {
            this.clearSkipSafeAreaRefreshHooks();
            if (this._skipSafeAreaController && typeof this._skipSafeAreaController.hide === 'function') {
                this._skipSafeAreaController.hide();
            }
            if (this.skipButton && this.skipButton.parentNode) {
                this.skipButton.parentNode.removeChild(this.skipButton);
            }
            if (this._skipSafeAreaController
                && typeof this._skipSafeAreaController.removeEmptyFixedPortal === 'function') {
                this._skipSafeAreaController.removeEmptyFixedPortal();
            }
            this.skipButton = null;
        }

        handleTutorialEnd(forcedReason = '') {
            if (!this.isTutorialRunning && !window.isInTutorial) return;

            const reason = forcedReason || this.endReason || (
                this.driver && this.driver.currentStep >= this.cachedValidSteps.length - 1
                    ? 'complete'
                    : 'skip'
            );

            this._refreshTimers.forEach((timer) => window.clearTimeout(timer));
            this._refreshTimers = [];
            this.hideSkipButton();
            this.restoreEmotionPicker();

            this.isTutorialRunning = false;
            this.driver = null;
            window.isInTutorial = false;
            document.body.classList.remove('page-tutorial-running');

            if (reason === 'complete' || reason === 'skip') {
                this.markTutorialSeen();
                window.dispatchEvent(new CustomEvent(
                    reason === 'skip' ? 'neko:tutorial-skipped' : 'neko:tutorial-completed',
                    {
                        detail: {
                            page: this.currentPage,
                            source: this.currentTutorialStartSource,
                            reason: reason
                        }
                    }
                ));
            }
        }

        restoreEmotionPicker() {
            const singleSelect = document.querySelector('#model-singleselect');
            if (!singleSelect) return;
            singleSelect.classList.remove('active', 'open-up', 'open-down');
            const header = singleSelect.querySelector('.singleselect-header');
            if (header) header.setAttribute('aria-expanded', 'false');
            const options = singleSelect.querySelector('.singleselect-options');
            if (options) options.style.removeProperty('display');
        }

        waitForCharacterCards(maxWaitTime = 5000) {
            return new Promise((resolve) => {
                const start = Date.now();
                const check = () => {
                    const hasCard = !!document.querySelector('.chara-card-item, .chara-list-item');
                    if (hasCard || Date.now() - start >= maxWaitTime) {
                        resolve();
                        return;
                    }
                    window.setTimeout(check, 120);
                };
                check();
            });
        }
    }

    function resetPageTutorialStorage(pageKey) {
        if (!pageKey || pageKey === 'home' || pageKey === 'current_personality') return false;
        if (pageKey === 'all') {
            SUPPORTED_PAGES.forEach((page) => {
                getAllPageTutorialStorageKeys(page).forEach((key) => localStorage.removeItem(key));
                localStorage.setItem(manualIntentKeyForPage(page), 'true');
            });
            dispatchPageTutorialReset('all');
            return true;
        }
        getAllPageTutorialStorageKeys(pageKey).forEach((key) => localStorage.removeItem(key));
        localStorage.setItem(manualIntentKeyForPage(pageKey), 'true');
        dispatchPageTutorialReset(pageKey);
        return true;
    }

    async function initPageTutorialManager() {
        if (window.pageTutorialManager && window.pageTutorialManager.isTutorialRunning) {
            return window.pageTutorialManager;
        }
        const manager = new PageTutorialManager();
        window.pageTutorialManager = manager;
        manager.init();
        return manager;
    }

    window.PageTutorialManager = PageTutorialManager;
    window.initPageTutorialManager = initPageTutorialManager;
    window.resetPageTutorialStorage = resetPageTutorialStorage;
})();
