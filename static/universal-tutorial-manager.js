/**
 * N.E.K.O 通用新手引导系统
 * 支持所有页面的引导配置
 *
 * 使用方式：
 * 1. 在页面中引入此文件
 * 2. 系统会自动检测当前页面
 * 3. 根据页面类型加载对应的引导配置
 */

class UniversalTutorialManager {
    constructor() {
        // 立即设置全局引用，以便在 getter 中使用
        window.universalTutorialManager = this;

        this.STORAGE_KEY_PREFIX = 'neko_tutorial_';
        this.driver = null;
        this.isInitialized = false;
        this.isTutorialRunning = false; // 防止重复启动
        this.currentPage = this.detectPage();
        this.currentStep = 0;
        this.nextButtonGuardTimer = null;
        this.nextButtonGuardActive = false;
        this.tutorialPadding = 8;
        this.tutorialControlledElements = new Set();
        this.tutorialInteractionStates = new Map();
        this.tutorialMarkerDisplayCache = null;
        this.tutorialRollbackActive = false;

        // 用于追踪在引导中修改过的元素及其原始样式
        this.modifiedElementsMap = new Map();

        console.log('[Tutorial] 当前页面:', this.currentPage);

        // 等待 driver.js 库加载
        this.waitForDriver();
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

    /**
     * 检测当前页面类型
     */
    detectPage() {
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

            this.driver = new DriverClass({
                padding: 8,
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
                    // 每次高亮元素时，确保元素在视口中
                    console.log('[Tutorial] 高亮元素:', step.element);

                    // 给一点时间让 Driver.js 完成定位
                    setTimeout(() => {
                        if (element && element.element) {
                            const targetElement = element.element;
                            // 检查元素是否在视口中
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

                        this.applyTutorialInteractionState(step, 'highlight');

                        // 启用 popover 拖动功能
                        this.enablePopoverDragging();
                    }, 100);
                }
            });

            this.isInitialized = true;
            console.log('[Tutorial] driver.js 初始化成功');


            // 检查是否需要自动启动引导
            this.checkAndStartTutorial();
        } catch (error) {
            console.error('[Tutorial] driver.js 初始化失败:', error);
        }
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
            }

            // 重新创建 driver 实例，使用最新的 i18n 翻译
            this.driver = new DriverClass({
                padding: 8,
                allowClose: true,
                overlayClickNext: false,
                animate: true,
                smoothScroll: true,
                className: 'neko-tutorial-driver',
                disableActiveInteraction: false,
                // i18n 按钮文本
                nextBtnText: this.t('tutorial.buttons.next', '下一步'),
                prevBtnText: this.t('tutorial.buttons.prev', '上一步'),
                doneBtnText: this.t('tutorial.buttons.done', '完成'),
                onDestroyStarted: () => {
                    if (this.shouldMarkHintShown) {
                        localStorage.setItem('neko_tutorial_reset_hint_shown', 'true');
                        this.shouldMarkHintShown = false;
                        console.log('[Tutorial] 已标记重置提示为已显示');
                    }
                },
                onHighlighted: (element, step, options) => {
                    console.log('[Tutorial] 高亮元素:', step.element);
                    setTimeout(() => {
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
                        this.applyTutorialInteractionState(step, 'highlight');
                        this.enablePopoverDragging();
                    }, 100);
                }
            });

            console.log('[Tutorial] driver.js 重新创建成功，使用 i18n 按钮文本');
        } catch (error) {
            console.error('[Tutorial] driver.js 重新创建失败:', error);
        }
    }

    /**
     * 获取当前页面的存储键（区分 Live2D 和 VRM）
     */
    getStorageKey() {
        let pageKey = this.currentPage;

        // 对于模型管理页面，需要区分 Live2D 和 VRM
        if (this.currentPage === 'model_manager') {
            const modelTypeText = document.getElementById('model-type-text');
            const isVRM = modelTypeText && modelTypeText.textContent.includes('VRM');
            pageKey = isVRM ? 'model_manager_vrm' : 'model_manager_live2d';
            console.log('[Tutorial] 检测到模型管理页面，模型类型:', isVRM ? 'VRM' : 'Live2D');
        }

        return this.STORAGE_KEY_PREFIX + pageKey;
    }

    /**
     * 获取指定页面相关的所有存储键（用于重置/判断）
     */
    getStorageKeysForPage(page) {
        const keys = [];
        const targetPage = page || this.currentPage;

        if (targetPage === 'model_manager') {
            // 兼容历史键 + 细分键 + 通用步骤键
            keys.push(this.STORAGE_KEY_PREFIX + 'model_manager');
            keys.push(this.STORAGE_KEY_PREFIX + 'model_manager_live2d');
            keys.push(this.STORAGE_KEY_PREFIX + 'model_manager_vrm');
            keys.push(this.STORAGE_KEY_PREFIX + 'model_manager_common');
        } else {
            keys.push(this.STORAGE_KEY_PREFIX + targetPage);
        }

        return keys;
    }

    /**
     * 检查是否需要自动启动引导
     */
    checkAndStartTutorial() {
        const storageKey = this.getStorageKey();
        const hasSeen = localStorage.getItem(storageKey);

        console.log('[Tutorial] 检查引导状态:');
        console.log('  - 当前页面:', this.currentPage);
        console.log('  - 存储键:', storageKey);
        console.log('  - 已看过引导:', hasSeen);

        if (!hasSeen) {
            // 对于主页，需要等待浮动按钮创建
            if (this.currentPage === 'home') {
                this.waitForFloatingButtons().then(() => {
                    // 延迟启动，确保 DOM 完全加载
                    setTimeout(() => {
                        this.startTutorial();
                    }, 1500);
                });
            } else if (this.currentPage === 'chara_manager') {
                // 对于角色管理页面，需要等待猫娘卡片加载
                this.waitForCatgirlCards().then(async () => {
                    // 先展开猫娘卡片和进阶设定，并为元素添加唯一 ID
                    await this.prepareCharaManagerForTutorial();
                    // 延迟启动，确保 DOM 完全加载
                    setTimeout(() => {
                        this.startTutorial();
                    }, 500);
                });
            } else {
                // 其他页面直接延迟启动
                setTimeout(() => {
                    this.startTutorial();
                }, 1500);
            }
        }

        // 对于模型管理页面，监听模型类型切换
        if (this.currentPage.startsWith('model_manager')) {
            this.setupModelTypeChangeListener();
        }
    }

    /**
     * 设置模型类型切换监听器（仅用于模型管理页面）
     */
    setupModelTypeChangeListener() {
        const modelTypeSelect = document.getElementById('model-type-select');
        if (!modelTypeSelect) {
            console.warn('[Tutorial] 未找到模型类型选择器');
            return;
        }

        // 避免重复添加监听器
        if (modelTypeSelect.dataset.tutorialListenerAdded) {
            return;
        }

        modelTypeSelect.addEventListener('change', () => {
            console.log('[Tutorial] 检测到模型类型切换');

            // 延迟一点，等待 UI 更新
            setTimeout(() => {
                // 检查新模型类型是否已看过引导
                const newStorageKey = this.getStorageKey();
                const hasSeenNew = localStorage.getItem(newStorageKey);

                console.log('[Tutorial] 模型类型切换后的引导状态:');
                console.log('  - 存储键:', newStorageKey);
                console.log('  - 已看过引导:', hasSeenNew ? '已看过' : '未看过');

                // 如果没看过，自动启动引导
                if (!hasSeenNew) {
                    setTimeout(() => {
                        this.startTutorial();
                    }, 1000);
                }
            }, 500);
        });

        modelTypeSelect.dataset.tutorialListenerAdded = 'true';
        console.log('[Tutorial] 模型类型切换监听器已设置');
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
            }
        };
    }

    /**
     * 主页引导步骤
     */
    getHomeSteps() {
        return [
            {
                element: '#live2d-container',
                popover: {
                    title: window.t ? window.t('tutorial.step1.title', '👋 欢迎来到 N.E.K.O') : '👋 欢迎来到 N.E.K.O',
                    description: window.t ? window.t('tutorial.step1.desc', '这是你的猫娘！接下来我会带你熟悉各项功能~') : '这是你的猫娘！接下来我会带你熟悉各项功能~',
                },
                disableActiveInteraction: false
            },
            {
                element: '#live2d-container',
                popover: {
                    title: window.t ? window.t('tutorial.step1a.title', '🎭 点击体验表情动作') : '🎭 点击体验表情动作',
                    description: window.t ? window.t('tutorial.step1a.desc', '试试点击猫娘吧！每次点击都会触发不同的表情和动作变化。体验完后点击「下一步」继续~') : '试试点击猫娘吧！每次点击都会触发不同的表情和动作变化。体验完后点击「下一步」继续~',
                },
                disableActiveInteraction: false,
                enableModelInteraction: true
            },
            {
                element: '#live2d-container',
                popover: {
                    title: window.t ? window.t('tutorial.step1b.title', '🖱️ 拖拽与缩放') : '🖱️ 拖拽与缩放',
                    description: window.t ? window.t('tutorial.step1b.desc', '你可以拖拽猫娘移动位置，也可以用鼠标滚轮放大缩小，试试看吧~') : '你可以拖拽猫娘移动位置，也可以用鼠标滚轮放大缩小，试试看吧~',
                },
                disableActiveInteraction: false,
                enableModelInteraction: true
            },
            {
                element: '#live2d-lock-icon',
                popover: {
                    title: window.t ? window.t('tutorial.step1c.title', '🔒 锁定猫娘') : '🔒 锁定猫娘',
                    description: window.t ? window.t('tutorial.step1c.desc', '点击这个锁可以锁定猫娘位置，防止误触移动。再次点击可以解锁~') : '点击这个锁可以锁定猫娘位置，防止误触移动。再次点击可以解锁~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#chat-container',
                popover: {
                    title: window.t ? window.t('tutorial.step2.title', '💬 对话区域') : '💬 对话区域',
                    description: window.t ? window.t('tutorial.step2.desc', '在这里可以和猫娘进行文字对话。输入您的想法，她会给您有趣的回应呢~') : '在这里可以和猫娘进行文字对话。输入您的想法，她会给您有趣的回应呢~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-floating-buttons',
                popover: {
                    title: window.t ? window.t('tutorial.step5.title', '🎛️ 浮动工具栏') : '🎛️ 浮动工具栏',
                    description: window.t ? window.t('tutorial.step5.desc', '浮动工具栏包含多个实用功能按钮，让我为你逐一介绍~') : '浮动工具栏包含多个实用功能按钮，让我为你逐一介绍~',
                }
            },
            {
                element: '#live2d-btn-mic',
                popover: {
                    title: window.t ? window.t('tutorial.step6.title', '🎤 语音控制') : '🎤 语音控制',
                    description: window.t ? window.t('tutorial.step6.desc', '启用语音控制，猫娘通过语音识别理解你的话语~') : '启用语音控制，猫娘通过语音识别理解你的话语~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-btn-screen',
                popover: {
                    title: window.t ? window.t('tutorial.step7.title', '🖥️ 屏幕分享') : '🖥️ 屏幕分享',
                    description: window.t ? window.t('tutorial.step7.desc', '分享屏幕/窗口/标签页，让猫娘看到你的画面~') : '分享屏幕/窗口/标签页，让猫娘看到你的画面~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-btn-agent',
                popover: {
                    title: window.t ? window.t('tutorial.step8.title', '🔨 Agent工具') : '🔨 Agent工具',
                    description: window.t ? window.t('tutorial.step8.desc', '打开 Agent 工具面板，使用各类辅助功能~') : '打开 Agent 工具面板，使用各类辅助功能~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-btn-goodbye',
                popover: {
                    title: window.t ? window.t('tutorial.step9.title', '💤 请她离开') : '💤 请她离开',
                    description: window.t ? window.t('tutorial.step9.desc', '让猫娘暂时离开并隐藏界面，需要时可点击\"请她回来\"恢复~') : '让猫娘暂时离开并隐藏界面，需要时可点击\"请她回来\"恢复~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-btn-settings',
                popover: {
                    title: window.t ? window.t('tutorial.step10.title', '⚙️ 设置') : '⚙️ 设置',
                    description: window.t ? window.t('tutorial.step10.desc', '打开设置面板，下面会依次介绍设置里的各个项目~') : '打开设置面板，下面会依次介绍设置里的各个项目~',
                },
                action: 'click',
                disableActiveInteraction: true
            },
            {
                element: '#live2d-toggle-proactive-chat',
                popover: {
                    title: window.t ? window.t('tutorial.step13.title', '💬 主动搭话') : '💬 主动搭话',
                    description: window.t ? window.t('tutorial.step13.desc', '开启后猫娘会主动发起对话，频率可在此调整~') : '开启后猫娘会主动发起对话，频率可在此调整~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-toggle-proactive-vision',
                popover: {
                    title: window.t ? window.t('tutorial.step14.title', '👀 自主视觉') : '👀 自主视觉',
                    description: window.t ? window.t('tutorial.step14.desc', '开启后猫娘会主动读取画面信息，间隔可在此调整~') : '开启后猫娘会主动读取画面信息，间隔可在此调整~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-menu-character',
                popover: {
                    title: window.t ? window.t('tutorial.step15.title', '👤 角色管理') : '👤 角色管理',
                    description: window.t ? window.t('tutorial.step15.desc', '调整猫娘的性格、形象、声音等~') : '调整猫娘的性格、形象、声音等~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-menu-api-keys',
                popover: {
                    title: window.t ? window.t('tutorial.step16.title', '🔑 API 密钥') : '🔑 API 密钥',
                    description: window.t ? window.t('tutorial.step16.desc', '配置 AI 服务的 API 密钥，这是和猫娘互动的必要配置~') : '配置 AI 服务的 API 密钥，这是和猫娘互动的必要配置~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-menu-memory',
                popover: {
                    title: window.t ? window.t('tutorial.step17.title', '🧠 记忆浏览') : '🧠 记忆浏览',
                    description: window.t ? window.t('tutorial.step17.desc', '查看与管理猫娘的记忆内容~') : '查看与管理猫娘的记忆内容~',
                },
                disableActiveInteraction: true
            },
            {
                element: '#live2d-menu-steam-workshop',
                popover: {
                    title: window.t ? window.t('tutorial.step18.title', '🛠️ 创意工坊') : '🛠️ 创意工坊',
                    description: window.t ? window.t('tutorial.step18.desc', '进入 Steam 创意工坊页面，管理订阅内容~') : '进入 Steam 创意工坊页面，管理订阅内容~',
                },
                disableActiveInteraction: true
            }
        ];
    }

    /**
     * 模型管理页面引导步骤
     */
    getModelManagerSteps() {
        // 检测当前模型类型
        const modelTypeText = document.getElementById('model-type-text');
        const isVRM = modelTypeText && modelTypeText.textContent.includes('VRM');

        console.log('[Tutorial] 模型管理页面 - 当前模型类型:', isVRM ? 'VRM' : 'Live2D');

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
                element: '#ambient-light-slider',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step6.title', '🌟 环境光'),
                    description: this.t('tutorial.model_manager.vrm.step6.desc', '调整环境光强度。环境光影响整体亮度，数值越高模型越亮。'),
                }
            },
            {
                element: '#main-light-slider',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step7.title', '☀️ 主光源'),
                    description: this.t('tutorial.model_manager.vrm.step7.desc', '调整主光源强度。主光源是主要的照明来源，影响模型的明暗对比。'),
                }
            },
            {
                element: '#exposure-slider',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step8.title', '🌞 曝光'),
                    description: this.t('tutorial.model_manager.vrm.step8.desc', '调整整体曝光强度。数值越高整体越亮，越低则更暗更有对比。'),
                }
            },
            {
                element: '#tonemapping-select',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step9.title', '🎞️ 色调映射'),
                    description: this.t('tutorial.model_manager.vrm.step9.desc', '选择不同的色调映射算法，决定画面亮部和暗部的呈现风格。'),
                }
            }
        ];

        // 根据当前模型类型返回对应的步骤
        if (isVRM) {
            return vrmSteps;
        } else {
            return live2dSteps;
        }
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
                element: '#model-select',
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
                }
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
        return [
            {
                element: '#coreApiSelect',
                popover: {
                    title: this.t('tutorial.settings.step2.title', '🔑 核心 API 服务商'),
                    description: this.t('tutorial.settings.step2.desc', '这是最重要的设置。核心 API 负责对话功能。\n\n• 免费版：完全免费，无需 API Key，适合新手体验\n• 阿里：有免费额度，功能全面\n• 智谱：有免费额度，支持联网搜索\n• OpenAI：智能水平最高，但需要翻墙且价格昂贵'),
                }
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
        return [];
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
                }
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
        if (selector === '#live2d-floating-buttons') {
            // 标记浮动工具栏在引导中，防止自动隐藏
            element.dataset.inTutorial = 'true';
            console.log('[Tutorial] 浮动工具栏已标记为引导中');
        }

        return { originalDisplay: element.style.display, originalVisibility: element.style.visibility, originalOpacity: element.style.opacity };
    }

    getTutorialInteractiveSelectors() {
        return [
            '#live2d-canvas',
            '#live2d-container',
            '#chat-container',
            '#live2d-floating-buttons',
            '#live2d-return-button-container',
            '#live2d-btn-return',
            '#resetSessionButton',
            '#returnSessionButton',
            '#live2d-lock-icon',
            '#toggle-chat-btn',
            '.live2d-floating-btn',
            '[id^="live2d-btn-"]'
        ];
    }

    isTutorialControlledElement(element) {
        if (!element) return false;
        const id = element.id || '';
        if (id.startsWith('live2d-') || id === 'resetSessionButton' || id === 'returnSessionButton' || id === 'chat-container' || id === 'toggle-chat-btn') {
            return true;
        }
        if (element.classList && element.classList.contains('live2d-floating-btn')) {
            return true;
        }
        if (element.closest) {
            if (element.closest('#live2d-floating-buttons') || element.closest('#live2d-return-button-container')) {
                return true;
            }
        }
        return false;
    }

    collectTutorialControlledElements(steps = []) {
        const elements = new Set();
        const selectors = this.getTutorialInteractiveSelectors();
        selectors.forEach(selector => {
            document.querySelectorAll(selector).forEach(element => elements.add(element));
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

    setTutorialMarkersVisible(visible) {
        const overlay = document.querySelector('.driver-overlay');
        const highlight = document.querySelector('.driver-highlight');
        const popover = document.querySelector('.driver-popover');
        const elements = [overlay, highlight, popover].filter(Boolean);
        if (!this.tutorialMarkerDisplayCache) {
            this.tutorialMarkerDisplayCache = new Map();
        }
        if (!visible) {
            elements.forEach(element => {
                if (!this.tutorialMarkerDisplayCache.has(element)) {
                    this.tutorialMarkerDisplayCache.set(element, element.style.display);
                }
                element.style.display = 'none';
            });
            return;
        }
        elements.forEach(element => {
            const cached = this.tutorialMarkerDisplayCache.get(element);
            if (cached !== undefined) {
                element.style.display = cached;
            } else {
                element.style.display = '';
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
            element.style.pointerEvents = 'auto';
            const state = this.tutorialInteractionStates.get(element);
            element.style.cursor = state.cursor || '';
            element.style.userSelect = state.userSelect || '';
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
            if (element === currentElement || element.contains(currentElement)) {
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
        const threshold = 6;
        const hasOffset = diffLeft > threshold || diffTop > threshold || diffWidth > threshold || diffHeight > threshold;
        if (hasOffset) {
            console.error('[Tutorial] 检测到高亮框偏移，执行回滚', {
                context,
                diffLeft,
                diffTop,
                diffWidth,
                diffHeight
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

    refreshAndValidateTutorialLayout(currentElement, context) {
        if (this.driver && typeof this.driver.refresh === 'function') {
            this.driver.refresh();
        }
        void document.body.offsetHeight;
        const ok = this.validateTutorialLayout(currentElement, context);
        if (!ok) {
            this.rollbackTutorialInteractionState();
        }
        return ok;
    }

    rollbackTutorialInteractionState() {
        this.tutorialRollbackActive = true;
        this.disableAllTutorialInteractions();
        this.setTutorialMarkersVisible(false);
        console.error('[Tutorial] 已回滚至禁用状态');
    }

    restoreTutorialInteractionState() {
        this.tutorialControlledElements.forEach(element => {
            element.style.pointerEvents = 'auto';
            const state = this.tutorialInteractionStates.get(element);
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
        console.log('[Tutorial] 已恢复交互元素默认状态');
    }

    applyTutorialInteractionState(currentStepConfig, context) {
        if (!window.isInTutorial || !currentStepConfig) return;
        this.tutorialRollbackActive = false;
        if (!this.tutorialControlledElements || this.tutorialControlledElements.size === 0) {
            this.collectTutorialControlledElements(this.cachedValidSteps || []);
        }
        this.setTutorialMarkersVisible(false);
        this.disableAllTutorialInteractions();
        const currentElement = document.querySelector(currentStepConfig.element);
        if (currentElement && !currentStepConfig.disableActiveInteraction) {
            this.enableCurrentStepInteractions(currentElement);
        }
        if (currentStepConfig.enableModelInteraction) {
            const live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas) {
                this.setElementInteractive(live2dCanvas, true);
            }
        }
        this.setTutorialMarkersVisible(true);
        this.refreshAndValidateTutorialLayout(currentElement, context);
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

            if (validSteps.length === 0) {
                console.warn('[Tutorial] 没有有效的引导步骤');
                return;
            }

            // 标记引导正在运行
            this.isTutorialRunning = true;

            // 检查当前页面是否需要全屏提示
            const pagesNeedingFullscreen = [
                // 已禁用全屏提示
            ];

            if (pagesNeedingFullscreen.includes(this.currentPage)) {
                // 显示全屏提示
                this.showFullscreenPrompt(validSteps);
            } else {
                // 直接启动引导，不显示全屏提示
                this.startTutorialSteps(validSteps);
            }
        } catch (error) {
            console.error('[Tutorial] 启动引导失败:', error);
        }
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
        // 缓存已验证的步骤，供 onStepChange 使用
        this.cachedValidSteps = validSteps;

        // 重新创建 driver 实例以确保按钮文本使用最新的 i18n 翻译
        this.recreateDriverWithI18n();

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

        // 禁用对话框拖动功能（在引导中）
        const chatContainer = document.getElementById('chat-container');
        if (chatContainer) {
            chatContainer.style.pointerEvents = 'none';
            console.log('[Tutorial] 禁用对话框拖动功能');
        }

        // 禁用 Live2D 模型拖动功能（在引导中）
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.pointerEvents = 'none';
            console.log('[Tutorial] 禁用 Live2D 模型拖动功能');
        }

        // 将 Live2D 模型移到屏幕右边（在引导中）
        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            this.originalLive2dStyle = {
                left: live2dContainer.style.left,
                right: live2dContainer.style.right,
                transform: live2dContainer.style.transform
            };
            live2dContainer.style.left = 'auto';
            live2dContainer.style.right = '0';
            console.log('[Tutorial] 将 Live2D 模型移到屏幕右边');
        }

        // 立即强制显示浮动工具栏（引导开始时）
        const floatingButtons = document.getElementById('live2d-floating-buttons');
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
        const hasLockIconStep = validSteps.some(step => step.element === '#live2d-lock-icon');
        if (hasLockIconStep) {
            const lockIcon = document.getElementById('live2d-lock-icon');
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

        // 启动浮动工具栏保护定时器（每 200ms 检查一次，更频繁）
        this.floatingButtonsProtectionTimer = setInterval(() => {
            const floatingButtons = document.getElementById('live2d-floating-buttons');
            if (floatingButtons && window.isInTutorial) {
                // 强制设置所有可能隐藏浮动按钮的样式
                floatingButtons.style.setProperty('display', 'flex', 'important');
                floatingButtons.style.setProperty('visibility', 'visible', 'important');
                floatingButtons.style.setProperty('opacity', '1', 'important');
            }

            // 同样保护锁图标（如果当前引导包含锁图标步骤）
            if (this._lockIconOriginalStyles !== undefined && window.isInTutorial) {
                const lockIcon = document.getElementById('live2d-lock-icon');
                if (lockIcon) {
                    lockIcon.style.setProperty('display', 'block', 'important');
                    lockIcon.style.setProperty('visibility', 'visible', 'important');
                    lockIcon.style.setProperty('opacity', '1', 'important');
                }
            }
        }, 200);

        // 对于设置页面和记忆浏览页面，禁用页面滚动以防止用户在引导中滚动页面导致问题
        if (this.currentPage === 'settings' || this.currentPage === 'memory_browser') {
            this._originalBodyOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
            console.log('[Tutorial] 禁用页面滚动');
        }

        // 监听事件
        this.driver.on('destroy', () => this.onTutorialEnd());
        this.driver.on('next', () => this.onStepChange());

        // 启动引导
        this.driver.start();
        setTimeout(() => {
            const steps = this.cachedValidSteps || [];
            if (steps.length > 0) {
                this.applyTutorialInteractionState(steps[0], 'start');
            }
        }, 0);
        console.log('[Tutorial] 引导已启动，页面:', this.currentPage);
    }

    /**
     * 检查并等待浮动按钮创建（用于主页引导）
     */
    waitForFloatingButtons(maxWaitTime = 3000) {
        return new Promise((resolve) => {
            const startTime = Date.now();

            const checkFloatingButtons = () => {
                const floatingButtons = document.getElementById('live2d-floating-buttons');

                if (floatingButtons) {
                    console.log('[Tutorial] 浮动按钮已创建');
                    resolve(true);
                    return;
                }

                const elapsedTime = Date.now() - startTime;
                if (elapsedTime > maxWaitTime) {
                    console.warn('[Tutorial] 等待浮动按钮超时（3秒）');
                    resolve(false);
                    return;
                }

                setTimeout(checkFloatingButtons, 100);
            };

            checkFloatingButtons();
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
     * 启用 popover 拖动功能
     */
    enablePopoverDragging() {
        const popover = document.querySelector('.driver-popover');
        if (!popover) {
            console.log('[Tutorial] 未找到 popover 元素');
            return;
        }

        // 始终先清理旧的监听器（从 manager 对象获取引用）
        if (this._popoverDragListeners) {
            console.log('[Tutorial] 清理旧的 popover 拖动监听器');
            const { onMouseDown, onMouseMove, onMouseUp, dragElement } = this._popoverDragListeners;
            if (dragElement) {
                dragElement.removeEventListener('mousedown', onMouseDown);
            }
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            this._popoverDragListeners = undefined;
        }
        // 清除任何 popover 上的旧标记
        if (popover.dataset.draggableEnabled) {
            delete popover.dataset.draggableEnabled;
        }

        // 尝试多个可能的标题选择器
        const possibleTitleSelectors = [
            '.driver-popover-title',
            '.driver-popover-header',
            'header',
            '.popover-title'
        ];

        let popoverTitle = null;
        for (const selector of possibleTitleSelectors) {
            popoverTitle = popover.querySelector(selector);
            if (popoverTitle) {
                console.log(`[Tutorial] 找到 popover 标题元素: ${selector}`);
                break;
            }
        }

        // 如果找不到标题，使用整个 popover 作为拖动区域
        if (!popoverTitle) {
            console.log('[Tutorial] 未找到 popover 标题元素，使用整个 popover 作为拖动区域');
            popoverTitle = popover;
        }

        // 标记为可拖动
        popover.dataset.draggableEnabled = 'true';
        popoverTitle.style.cursor = 'move';
        popoverTitle.style.userSelect = 'none';
        popoverTitle.title = '按住拖动以移动提示框';

        let isDragging = false;
        let startX = 0;
        let startY = 0;
        let initialX = 0;
        let initialY = 0;

        const onMouseDown = (e) => {
            // 只在点击标题区域时启动拖动（避免影响按钮点击）
            if (e.target.closest('button')) {
                return;
            }

            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;

            // 获取当前 popover 的位置
            const rect = popover.getBoundingClientRect();
            initialX = rect.left;
            initialY = rect.top;

            // 移除 driver.js 的定位样式，切换到固定定位
            popover.style.position = 'fixed';
            popover.style.left = initialX + 'px';
            popover.style.top = initialY + 'px';
            popover.style.margin = '0';
            popover.style.transform = 'none';
            popover.style.zIndex = '10000';

            e.preventDefault();
            e.stopPropagation();
        };

        const onMouseMove = (e) => {
            if (!isDragging) return;

            const deltaX = e.clientX - startX;
            const deltaY = e.clientY - startY;

            const newX = initialX + deltaX;
            const newY = initialY + deltaY;

            popover.style.left = newX + 'px';
            popover.style.top = newY + 'px';
        };

        const onMouseUp = () => {
            if (isDragging) {
                isDragging = false;
            }
        };

        // 添加事件监听器
        popoverTitle.addEventListener('mousedown', onMouseDown, { passive: false });
        document.addEventListener('mousemove', onMouseMove, { passive: true });
        document.addEventListener('mouseup', onMouseUp, { passive: true });

        // 保存监听器引用到 manager 对象，以便清理
        this._popoverDragListeners = {
            onMouseDown,
            onMouseMove,
            onMouseUp,
            dragElement: popoverTitle
        };

        console.log('[Tutorial] Popover 拖动功能已启用');
    }

    /**
     * 步骤改变时的回调
     */
    onStepChange() {
        this.currentStep = this.driver.currentStep || 0;
        console.log(`[Tutorial] 当前步骤: ${this.currentStep + 1}`);

        // 使用缓存的已验证步骤，而不是重新调用 getStepsForPage()
        // 这样可以保持与 startTutorialSteps 中使用的步骤列表一致
        const steps = this.cachedValidSteps || this.getStepsForPage();
        if (this.currentStep < steps.length) {
            const currentStepConfig = steps[this.currentStep];

            // 进入新步骤前，先清理上一阶段的"下一步"前置校验
            this.clearNextButtonGuard();

            // 角色管理页面：进入进阶设定相关步骤前，确保猫娘卡片和进阶设定都已展开
            if (this.currentPage === 'chara_manager') {
                const needsAdvancedSettings = [
                    '.catgirl-block:first-child .fold-toggle',
                    '.catgirl-block:first-child .live2d-link',
                    '.catgirl-block:first-child select[name="voice_id"]'
                ].includes(currentStepConfig.element);

                if (needsAdvancedSettings) {
                    console.log('[Tutorial] 进入进阶设定相关步骤，确保展开状态');
                    this._ensureCharaManagerExpanded();
                }
            }

            this.applyTutorialInteractionState(currentStepConfig, 'step-change');

            // 根据步骤配置启用/禁用模型交互（点击模型触发表情动作）
            const live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas) {
                if (currentStepConfig.enableModelInteraction) {
                    this.setElementInteractive(live2dCanvas, true);
                    console.log('[Tutorial] 启用模型交互');
                } else {
                    this.setElementInteractive(live2dCanvas, false);
                    console.log('[Tutorial] 禁用模型交互');
                }
            }


            // 情感配置页面：未选择模型时禁止进入下一步
            if (this.currentPage === 'emotion_manager' &&
                currentStepConfig.element === '#model-select') {
                const updateNextState = () => {
                    const hasModel = this.hasEmotionManagerModelSelected();
                    this.setNextButtonState(hasModel, '请先选择模型');
                    if (hasModel && this.nextButtonGuardTimer) {
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
                const targetIndex = steps.findIndex(step => step.element === '#model-select');
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
                    setTimeout(() => {
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
                        setTimeout(() => {
                            if (this.driver) this.driver.refresh();
                        }, 500);

                    }, 300);
                }
                } else {
                    // 即使没有点击操作，也在步骤切换后刷新位置
                    // 对于需要等待动态元素的步骤，多次刷新以确保位置正确
                    if (currentStepConfig.skipInitialCheck) {
                        console.log(`[Tutorial] 动态元素步骤，将多次刷新位置`);
                        // 第一次刷新
                        setTimeout(() => {
                            if (this.driver && typeof this.driver.refresh === 'function') {
                                this.driver.refresh();
                                console.log(`[Tutorial] 步骤切换后刷新高亮框位置 (第1次)`);
                            }
                        }, 200);
                        // 第二次刷新
                        setTimeout(() => {
                            if (this.driver && typeof this.driver.refresh === 'function') {
                                this.driver.refresh();
                                console.log(`[Tutorial] 步骤切换后刷新高亮框位置 (第2次)`);
                            }
                        }, 600);
                        // 第三次刷新
                        setTimeout(() => {
                            if (this.driver && typeof this.driver.refresh === 'function') {
                                this.driver.refresh();
                                console.log(`[Tutorial] 步骤切换后刷新高亮框位置 (第3次)`);
                            }
                        }, 1000);
                    } else {
                        setTimeout(() => {
                            if (this.driver && typeof this.driver.refresh === 'function') {
                                this.driver.refresh();
                                console.log(`[Tutorial] 步骤切换后刷新高亮框位置`);
                            }
                        }, 200);
                    }
                }
            }
        }

        // 在步骤切换后，延迟启用 popover 拖动功能
        // 因为 driver.js 可能会重新渲染 popover
        setTimeout(() => {
            this.enablePopoverDragging();
        }, 200);
    }

    /**
     * 引导结束时的回调
     */
    onTutorialEnd() {
        // 重置运行标志
        this.isTutorialRunning = false;
        this.clearNextButtonGuard();

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

        // 标记用户已看过该页面的引导
        const storageKey = this.getStorageKey();
        localStorage.setItem(storageKey, 'true');

        // 对于模型管理页面，同时标记通用步骤为已看过
        if (this.currentPage === 'model_manager') {
            const commonStorageKey = this.STORAGE_KEY_PREFIX + 'model_manager_common';
            localStorage.setItem(commonStorageKey, 'true');
            console.log('[Tutorial] 已标记模型管理通用步骤为已看过');
        }

        // 清除全局引导标记
        window.isInTutorial = false;
        console.log('[Tutorial] 清除全局引导标记');

        // 对于设置页面和记忆浏览页面，恢复页面滚动
        if (this.currentPage === 'settings' || this.currentPage === 'memory_browser') {
            document.body.style.overflow = this._originalBodyOverflow ?? '';
            this._originalBodyOverflow = undefined;
            console.log('[Tutorial] 恢复页面滚动');
        }

        // 恢复对话框拖动功能
        const chatContainer = document.getElementById('chat-container');
        if (chatContainer) {
            chatContainer.style.pointerEvents = 'auto';
            console.log('[Tutorial] 恢复对话框拖动功能');
        }

        // 恢复 Live2D 模型拖动功能和原始位置
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.pointerEvents = 'auto';
            console.log('[Tutorial] 恢复 Live2D 模型拖动功能');
        }

        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer && this.originalLive2dStyle) {
            live2dContainer.style.left = this.originalLive2dStyle.left;
            live2dContainer.style.right = this.originalLive2dStyle.right;
            live2dContainer.style.transform = this.originalLive2dStyle.transform;
            console.log('[Tutorial] 恢复 Live2D 模型原始位置');
        }

        // 清除浮动工具栏保护定时器
        if (this.floatingButtonsProtectionTimer) {
            clearInterval(this.floatingButtonsProtectionTimer);
            this.floatingButtonsProtectionTimer = null;
            console.log('[Tutorial] 浮动工具栏保护定时器已清除');
        }

        // 恢复锁图标的原始样式
        if (this._lockIconOriginalStyles !== undefined) {
            const lockIcon = document.getElementById('live2d-lock-icon');
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

        console.log('[Tutorial] 引导已完成，页面:', this.currentPage);
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

        if (this.driver) {
            this.driver.destroy();
        }

        this.startTutorial();
    }

    /**
     * 重置所有页面的引导状态
     */
    resetAllTutorials() {
        const pages = [
            'home',
            'model_manager',
            'parameter_editor',
            'emotion_manager',
            'chara_manager',
            'settings',
            'voice_clone',
            'steam_workshop',
            'memory_browser'
        ];
        pages.forEach(page => {
            const storageKeys = this.getStorageKeysForPage(page);
            storageKeys.forEach(key => localStorage.removeItem(key));
        });
        console.log('[Tutorial] 所有引导状态已重置');
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
     */
    _ensureCharaManagerExpanded() {
        let attempts = 0;
        const maxAttempts = 10;
        const self = this;

        const tryExpand = () => {
            attempts++;
            console.log(`[Tutorial] _ensureCharaManagerExpanded: attempt ${attempts}/${maxAttempts}`);

            // 1. 找到第一个猫娘卡片
            const targetBlock = document.querySelector('.catgirl-block:first-child');
            if (!targetBlock) {
                console.warn('[Tutorial] _ensureCharaManagerExpanded: 未找到目标猫娘卡片');
                if (attempts < maxAttempts) setTimeout(tryExpand, 300);
                return;
            }

            // 2. 确保猫娘卡片已展开
            const details = targetBlock.querySelector('.catgirl-details');
            const expandBtn = targetBlock.querySelector('.catgirl-expand');
            if (details && expandBtn) {
                const detailsStyle = window.getComputedStyle(details);
                if (detailsStyle.display === 'none') {
                    console.log('[Tutorial] 猫娘卡片未展开，正在展开...');
                    expandBtn.click();
                    // 等待卡片展开动画完成后再尝试展开进阶设定
                    if (attempts < maxAttempts) {
                        setTimeout(tryExpand, 600);
                    }
                    return;
                }
            }

            // 3. 卡片已展开，确保进阶设定已展开
            const foldContainer = targetBlock.querySelector('.fold');
            const foldToggle = targetBlock.querySelector('.fold-toggle');
            let clickedToggle = false;

            if (foldContainer && foldToggle) {
                const isExpanded = foldContainer.classList.contains('open') ||
                    window.getComputedStyle(foldContainer).display !== 'none';
                if (!isExpanded) {
                    console.log('[Tutorial] 进阶设定未展开，正在展开...');
                    foldToggle.click();
                    clickedToggle = true;
                }
            }

            // 4. 验证展开状态，失败则重试
            setTimeout(() => {
                if (self.driver && typeof self.driver.refresh === 'function') {
                    self.driver.refresh();
                }

                if (clickedToggle && attempts < maxAttempts) {
                    const fc = targetBlock.querySelector('.fold');
                    if (fc) {
                        const nowExpanded = fc.classList.contains('open') ||
                            window.getComputedStyle(fc).display !== 'none';
                        if (!nowExpanded) {
                            console.log('[Tutorial] 进阶设定展开未确认，重试...');
                            setTimeout(tryExpand, 300);
                            return;
                        }
                    }
                }
                console.log('[Tutorial] _ensureCharaManagerExpanded: 完成');
            }, 500);
        };

        tryExpand();
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
        const pages = [
            'home',
            'model_manager',
            'model_manager_live2d',
            'model_manager_vrm',
            'model_manager_common',
            'parameter_editor',
            'emotion_manager',
            'chara_manager',
            'settings',
            'voice_clone',
            'steam_workshop',
            'memory_browser'
        ];

        pages.forEach(page => {
            const key = this.STORAGE_KEY_PREFIX + page;
            localStorage.removeItem(key);
        });

        console.log('[Tutorial] 已重置所有引导状态');
        return true;
    }

    /**
     * 重置指定页面的引导状态
     */
    resetPageTutorial(pageKey) {
        if (pageKey === 'all') {
            this.resetAllTutorials();
            return;
        }

        // 特殊处理模型管理页面
        if (pageKey === 'model_manager') {
            localStorage.removeItem(this.STORAGE_KEY_PREFIX + 'model_manager');
            localStorage.removeItem(this.STORAGE_KEY_PREFIX + 'model_manager_live2d');
            localStorage.removeItem(this.STORAGE_KEY_PREFIX + 'model_manager_vrm');
            localStorage.removeItem(this.STORAGE_KEY_PREFIX + 'model_manager_common');
        } else {
            localStorage.removeItem(this.STORAGE_KEY_PREFIX + pageKey);
        }

        console.log('[Tutorial] 已重置页面引导:', pageKey);
    }

    /**
     * 重新启动当前页面的引导
     */
    restartCurrentTutorial() {
        // 先销毁现有的 driver 以避免残留的监听器和遮罩
        if (this.driver) {
            this.driver.destroy();
            this.driver = null;
        }

        // 清除当前页面的引导记录
        const storageKey = this.getStorageKey();
        localStorage.removeItem(storageKey);
        console.log('[Tutorial] 已清除当前页面引导记录:', this.currentPage);

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
    const currentPath = window.location.pathname;
    const currentPageType = (() => {
        if (currentPath === '/' || currentPath === '/index.html') return 'home';
        if (currentPath.includes('parameter_editor')) return 'parameter_editor';
        if (currentPath.includes('emotion_manager')) return 'emotion_manager';
        if (currentPath.includes('model_manager') || currentPath.includes('l2d')) return 'model_manager';
        if (currentPath.includes('chara_manager')) return 'chara_manager';
        if (currentPath.includes('api_key') || currentPath.includes('settings')) return 'settings';
        if (currentPath.includes('voice_clone')) return 'voice_clone';
        if (currentPath.includes('steam_workshop')) return 'steam_workshop';
        if (currentPath.includes('memory_browser')) return 'memory_browser';
        return 'unknown';
    })();

    // 如果全局实例存在，检查页面是否改变
    if (window.universalTutorialManager) {
        if (window.universalTutorialManager.currentPage !== currentPageType) {
            console.log('[Tutorial] 页面已改变，销毁旧实例并创建新实例');
            // 销毁旧的 driver 实例
            if (window.universalTutorialManager.driver) {
                window.universalTutorialManager.driver.destroy();
            }
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
        const prefix = 'neko_tutorial_';
        const pages = ['home', 'model_manager', 'model_manager_live2d', 'model_manager_vrm', 'model_manager_common', 'parameter_editor', 'emotion_manager', 'chara_manager', 'settings', 'voice_clone', 'steam_workshop', 'memory_browser'];
        pages.forEach(page => { localStorage.removeItem(prefix + page); });
    }
    alert(window.t ? window.t('memory.tutorialResetSuccess', '已重置所有引导，下次进入各页面时将重新显示引导。') : '已重置所有引导，下次进入各页面时将重新显示引导。');
}

/**
 * 全局函数：重置指定页面的引导
 * 供下拉菜单调用
 */
function resetTutorialForPage(pageKey) {
    if (!pageKey) return;

    if (pageKey === 'all') {
        resetAllTutorials();
        return;
    }

    if (window.universalTutorialManager) {
        window.universalTutorialManager.resetPageTutorial(pageKey);
    } else {
        const prefix = 'neko_tutorial_';
        if (pageKey === 'model_manager') {
            localStorage.removeItem(prefix + 'model_manager');
            localStorage.removeItem(prefix + 'model_manager_live2d');
            localStorage.removeItem(prefix + 'model_manager_vrm');
            localStorage.removeItem(prefix + 'model_manager_common');
        } else {
            localStorage.removeItem(prefix + pageKey);
        }
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
