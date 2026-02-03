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
        this.STORAGE_KEY_PREFIX = 'neko_tutorial_';
        this.driver = null;
        this.isInitialized = false;
        this.isTutorialRunning = false; // 防止重复启动
        this.currentPage = this.detectPage();
        this.currentStep = 0;
        this.nextButtonGuardTimer = null;
        this.nextButtonGuardActive = false;

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
                this.waitForCatgirlCards().then(() => {
                    // 延迟启动，确保 DOM 完全加载
                    setTimeout(() => {
                        this.startTutorial();
                    }, 1500);
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

        const steps = configs[this.currentPage] || [];
        console.log('[Tutorial] 返回的步骤数:', steps.length);
        if (steps.length > 0) {
            console.log('[Tutorial] 第一个步骤元素:', steps[0].element);
        }

        return steps;
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
                    description: window.t ? window.t('tutorial.step1.desc', '这是您的虚拟伙伴，她会陪伴您进行各种交互。点击她可以触发不同的表情和动作哦~') : '这是您的虚拟伙伴，她会陪伴您进行各种交互。点击她可以触发不同的表情和动作哦~',
                }
            },
            {
                element: '#chat-container',
                popover: {
                    title: window.t ? window.t('tutorial.step2.title', '💬 对话区域') : '💬 对话区域',
                    description: window.t ? window.t('tutorial.step2.desc', '在这里可以和伙伴进行文字对话。输入您的想法，她会给您有趣的回应呢~') : '在这里可以和伙伴进行文字对话。输入您的想法，她会给您有趣的回应呢~',
                }
            },
            {
                element: '#textInputBox',
                popover: {
                    title: window.t ? window.t('tutorial.step3.title', '✍️ 输入框') : '✍️ 输入框',
                    description: window.t ? window.t('tutorial.step3.desc', '在这里输入您想说的话。按 Enter 发送，Shift+Enter 换行。也可以点击右边的按钮发送截图哦~') : '在这里输入您想说的话。按 Enter 发送，Shift+Enter 换行。也可以点击右边的按钮发送截图哦~',
                }
            },
            {
                element: '#button-group',
                popover: {
                    title: window.t ? window.t('tutorial.step4.title', '🎮 快速操作') : '🎮 快速操作',
                    description: window.t ? window.t('tutorial.step4.desc', '上边是发送按钮，下边是截图按钮。您可以分享屏幕截图给伙伴，她会帮您分析哦~') : '左边是发送按钮，右边是截图按钮。您可以分享屏幕截图给伙伴，她会帮您分析哦~',
                }
            },
            {
                element: '#live2d-floating-buttons',
                popover: {
                    title: '🎛️ 浮动工具栏',
                    description: '这是浮动工具栏，包含多个实用功能按钮。让我为您逐一介绍每个按钮的功能吧~',
                }
            },
            {
                element: '#live2d-btn-mic',
                popover: {
                    title: '🎤 语音控制',
                    description: '点击这个按钮可以启用语音控制功能。启用后，虚拟伙伴会通过语音识别来理解您的话语，让交互更加自然和便捷。',
                }
            },
            {
                element: '#live2d-btn-screen',
                popover: {
                    title: '🖥️ 屏幕分享',
                    description: '点击这里可以分享屏幕/窗口/标签页，让伙伴看到你的画面，适合语音通话或需要她帮忙看内容时使用。',
                }
            },
            {
                element: '#live2d-btn-agent',
                popover: {
                    title: '🔨 Agent工具',
                    description: '打开 Agent 工具面板，使用各类辅助功能或工具集。',
                }
            },
            {
                element: '#live2d-btn-goodbye',
                popover: {
                    title: '💤 请她离开',
                    description: '让伙伴暂时离开并隐藏界面，需要时可点击“请她回来”恢复。',
                }
            },
            {
                element: '#live2d-btn-settings',
                popover: {
                    title: '⚙️ 设置',
                    description: '点击这个按钮可以打开设置面板。下面会依次介绍设置里的 8 个项目。',
                },
                action: 'click'
            },
            {
                element: '#live2d-toggle-merge-messages',
                popover: {
                    title: '🧩 合并消息',
                    description: '将多条短消息合并为一次发送，减少打断感。',
                }
            },
            {
                element: '#live2d-toggle-focus-mode',
                popover: {
                    title: '⛔ 允许打断',
                    description: '控制是否允许打断当前回复，适合不同的对话节奏。',
                }
            },
            {
                element: '#live2d-toggle-proactive-chat',
                popover: {
                    title: '💬 主动搭话',
                    description: '开启后伙伴会定时主动发起对话，间隔可在此调整。',
                }
            },
            {
                element: '#live2d-toggle-proactive-vision',
                popover: {
                    title: '👀 自主视觉',
                    description: '开启后伙伴会主动读取画面信息（如屏幕内容），间隔可在此调整。',
                }
            },
            {
                element: '#live2d-menu-character',
                popover: {
                    title: '👤 角色管理',
                    description: '管理虚拟伙伴的角色设置、模型和声音。',
                }
            },
            {
                element: '#live2d-menu-api-keys',
                popover: {
                    title: '🔑 API 密钥',
                    description: '配置 AI 服务的 API 密钥。这是使用虚拟伙伴的必要配置。',
                }
            },
            {
                element: '#live2d-menu-memory',
                popover: {
                    title: '🧠 记忆浏览',
                    description: '查看与管理伙伴的记忆内容。',
                }
            },
            {
                element: '#live2d-menu-steam-workshop',
                popover: {
                    title: '🛠️ 创意工坊',
                    description: '进入 Steam 创意工坊页面，管理订阅内容。',
                }
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

        // 检查通用步骤是否已看过
        const commonStorageKey = this.STORAGE_KEY_PREFIX + 'model_manager_common';
        const hasSeenCommon = localStorage.getItem(commonStorageKey);

        console.log('[Tutorial] 通用步骤状态:', hasSeenCommon ? '已看过' : '未看过');

        // 通用步骤（所有模型类型都有）
        const commonSteps = [
            {
                element: '#model-type-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.common.step1.title', '🎨 选择模型类型'),
                    description: this.t('tutorial.model_manager.common.step1.desc', '首先选择您要使用的模型类型：Live2D（2D 动画）或 VRM（3D 模型）。'),
                }
            },
            {
                element: '#upload-btn',
                popover: {
                    title: this.t('tutorial.model_manager.common.step2.title', '📤 上传模型'),
                    description: this.t('tutorial.model_manager.common.step2.desc', '点击这里上传您的模型文件。支持 Live2D 和 VRM 格式。'),
                }
            }
        ];

        // Live2D 特定步骤
        const live2dSteps = [
            {
                element: '#live2d-model-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.live2d.step1.title', '🎭 选择 Live2D 模型'),
                    description: this.t('tutorial.model_manager.live2d.step1.desc', '从已上传的 Live2D 模型中选择要使用的模型。'),
                }
            },
            {
                element: '#motion-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.live2d.step2.title', '💃 选择动作'),
                    description: this.t('tutorial.model_manager.live2d.step2.desc', '为 Live2D 模型选择动作。点击"播放动作"按钮可以预览效果。'),
                }
            },
            {
                element: '#expression-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.live2d.step3.title', '😊 选择表情'),
                    description: this.t('tutorial.model_manager.live2d.step3.desc', '为 Live2D 模型选择表情。可以设置常驻表情让模型保持该表情。'),
                }
            },
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
                    description: this.t('tutorial.model_manager.live2d.step6.desc', '点击这里进入捏脸系统，可以精细调整 Live2D 模型的面部参数，打造独特的虚拟伙伴形象。'),
                }
            }
        ];

        // VRM 特定步骤
        const vrmSteps = [
            {
                element: '#vrm-model-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step1.title', '🎭 选择 VRM 模型'),
                    description: this.t('tutorial.model_manager.vrm.step1.desc', '从已上传的 VRM 模型中选择要使用的 3D 模型。'),
                }
            },
            {
                element: '#vrm-animation-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step2.title', '💃 选择动画'),
                    description: this.t('tutorial.model_manager.vrm.step2.desc', '为 VRM 模型选择动画。VRM 支持更丰富的 3D 动画效果。'),
                }
            },
            {
                element: '#play-vrm-animation-btn',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step3.title', '▶️ 播放动画'),
                    description: this.t('tutorial.model_manager.vrm.step3.desc', '点击这个按钮可以预览选中的 VRM 动画效果。'),
                }
            },
            {
                element: '#vrm-expression-select-btn',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step4.title', '😊 选择表情'),
                    description: this.t('tutorial.model_manager.vrm.step4.desc', '为 VRM 模型选择表情。VRM 模型支持多种面部表情。'),
                }
            },
            {
                element: '#vrm-lighting-wrapper',
                popover: {
                    title: this.t('tutorial.model_manager.vrm.step5.title', '💡 光照系统'),
                    description: this.t('tutorial.model_manager.vrm.step5.desc', 'VRM 模型支持专业的 3D 光照系统。您可以调整环境光、主光源、补光和轮廓光，打造完美的视觉效果。'),
                }
            },
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

        // 根据当前模型类型和通用步骤状态返回对应的步骤
        let steps = [];

        // 如果通用步骤没看过，添加通用步骤
        if (!hasSeenCommon) {
            steps = [...commonSteps];
        }

        // 添加特定步骤
        if (isVRM) {
            steps = [...steps, ...vrmSteps];
        } else {
            steps = [...steps, ...live2dSteps];
        }

        return steps;
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
            },
            {
                element: '#live2d-container',
                popover: {
                    title: this.t('tutorial.parameter_editor.step3.title', '👁️ 实时预览'),
                    description: this.t('tutorial.parameter_editor.step3.desc', '左侧是实时预览区域。调整参数时，可以立即看到模型的变化效果。'),
                }
            },
            {
                element: '#reset-all-btn',
                popover: {
                    title: this.t('tutorial.parameter_editor.step4.title', '🔄 重置所有参数'),
                    description: this.t('tutorial.parameter_editor.step4.desc', '点击这个按钮可以将所有参数重置为默认值。如果调整效果不满意，可以用这个功能重新开始。'),
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
                    description: this.t('tutorial.emotion_manager.step2.desc', '这里可以为不同的情感（如开心、悲伤、生气等）配置对应的表情和动作组合。虚拟伙伴会根据对话内容自动切换情感表现。'),
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
     * 角色管理页面引导步骤
     */
    getCharaManagerSteps() {
        return [
            {
                element: '#master-section',
                popover: {
                    title: this.t('tutorial.chara_manager.step1.title', '👤 主人档案'),
                    description: this.t('tutorial.chara_manager.step1.desc', '这是您的主人档案。档案名是必填项，其他信息（性别、昵称等）都是可选的。这些信息会影响虚拟伙伴对您的称呼和态度。'),
                }
            },
            {
                element: 'input[name="档案名"]',
                popover: {
                    title: this.t('tutorial.chara_manager.step2.title', '📝 设置档案名'),
                    description: this.t('tutorial.chara_manager.step2.desc', '输入您的名字或昵称。虚拟伙伴会用这个名字来称呼您。最多 20 个字符。'),
                }
            },
            {
                element: 'textarea[name="性别"]',
                popover: {
                    title: this.t('tutorial.chara_manager.step3.title', '👥 性别设定'),
                    description: this.t('tutorial.chara_manager.step3.desc', '这是可选项。您可以输入您的性别或其他相关信息。这会影响虚拟伙伴对您的称呼方式。'),
                }
            },
            {
                element: 'textarea[name="昵称"]',
                popover: {
                    title: this.t('tutorial.chara_manager.step4.title', '💬 昵称设定'),
                    description: this.t('tutorial.chara_manager.step4.desc', '这是可选项。您可以为自己设置一个昵称。虚拟伙伴可能会用这个昵称来称呼您。'),
                }
            },
            {
                element: '#api-key-settings-btn',
                popover: {
                    title: this.t('tutorial.chara_manager.step5.title', '🔑 API Key 设置'),
                    description: this.t('tutorial.chara_manager.step5.desc', '点击这里配置 AI 服务的 API Key。这是虚拟伙伴能够进行对话的必要配置。'),
                }
            },
            {
                element: '#catgirl-section',
                popover: {
                    title: this.t('tutorial.chara_manager.step6.title', '🐱 猫娘档案'),
                    description: this.t('tutorial.chara_manager.step6.desc', '这里可以创建和管理多个虚拟伙伴角色。每个角色都有独特的性格、Live2D 形象和语音设定。您可以在不同的角色之间切换。'),
                }
            },
            {
                element: '#add-catgirl-btn',
                popover: {
                    title: this.t('tutorial.chara_manager.step7.title', '➕ 新增猫娘'),
                    description: this.t('tutorial.chara_manager.step7.desc', '点击这个按钮创建一个新的虚拟伙伴角色。您可以为她设置名字、性格、形象和语音。每个角色都是独立的，有自己的记忆和性格。'),
                }
            },
            {
                element: '.catgirl-block:first-child .catgirl-header',
                popover: {
                    title: this.t('tutorial.chara_manager.step8.title', '📋 猫娘卡片'),
                    description: this.t('tutorial.chara_manager.step8.desc', '点击猫娘名称可以展开或折叠详细信息。每个猫娘都有独立的设定，包括基础信息和进阶配置。'),
                },
                action: 'click' // 使用 action 自动点击展开，系统会自动刷新位置
            },
            {
                element: '.catgirl-block:first-child input[name="档案名"]',
                popover: {
                    title: this.t('tutorial.chara_manager.step9.title', '📝 猫娘档案名'),
                    description: this.t('tutorial.chara_manager.step9.desc', '这是猫娘的名字，也是她的唯一标识。创建后可以通过"修改名称"按钮来更改。'),
                },
                skipInitialCheck: true, // 跳过初始化时的元素检查
                onHighlightStarted: async () => {
                    // 等待表单元素渲染完成
                    const maxWait = 3000; // 最多等待3秒
                    const startTime = Date.now();

                    while (Date.now() - startTime < maxWait) {
                        const element = document.querySelector('.catgirl-block:first-child input[name="档案名"]');
                        if (element) {
                            console.log('[Tutorial] 档案名输入框已找到');
                            break;
                        }
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
                }
            },
            {
                element: '.catgirl-block:first-child .custom-row:first-child',
                popover: {
                    title: this.t('tutorial.chara_manager.step10.title', '✨ 自定义属性'),
                    description: this.t('tutorial.chara_manager.step10.desc', '这些是猫娘的性格设定字段，如性格、背景、爱好、口头禅等。您可以自由添加和编辑这些属性，让每个猫娘都有独特的个性。'),
                },
                skipInitialCheck: true, // 跳过初始化时的元素检查
                onHighlightStarted: async () => {
                    // 等待自定义字段渲染完成
                    const maxWait = 3000;
                    const startTime = Date.now();

                    while (Date.now() - startTime < maxWait) {
                        const element = document.querySelector('.catgirl-block:first-child .custom-row:first-child');
                        if (element) {
                            console.log('[Tutorial] 自定义属性字段已找到');
                            break;
                        }
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
                }
            },
            {
                element: '.catgirl-block:first-child button[id^="switch-btn-"]',
                popover: {
                    title: this.t('tutorial.chara_manager.step11.title', '🔄 切换猫娘'),
                    description: this.t('tutorial.chara_manager.step11.desc', '点击此按钮可以将这个猫娘设为当前活跃角色。切换后，主页和对话界面会使用该角色的形象和性格。'),
                }
            },
            {
                element: '.catgirl-block:first-child .fold-toggle',
                popover: {
                    title: this.t('tutorial.chara_manager.step12.title', '⚙️ 进阶设定'),
                    description: this.t('tutorial.chara_manager.step12.desc', '点击展开进阶设定，可以配置 Live2D 模型、语音 ID、以及添加自定义性格属性（如性格、爱好、口头禅等）。'),
                },
                skipInitialCheck: true, // 跳过初始化时的元素检查
                action: 'click' // 使用 action 自动点击展开，系统会自动刷新位置
            },
            {
                element: '.catgirl-block:first-child .live2d-link',
                popover: {
                    title: this.t('tutorial.chara_manager.step13.title', '🎨 模型设定'),
                    description: this.t('tutorial.chara_manager.step13.desc', '点击此链接可以选择或更换猫娘的 Live2D 形象或 VRM 模型。不同的模型会带来不同的视觉体验。'),
                },
                skipInitialCheck: true, // 跳过初始化时的元素检查
                onHighlightStarted: async () => {
                    // 等待模型设定链接渲染完成
                    const maxWait = 3000;
                    const startTime = Date.now();

                    while (Date.now() - startTime < maxWait) {
                        const element = document.querySelector('.catgirl-block:first-child .live2d-link');
                        if (element) {
                            console.log('[Tutorial] 模型设定链接已找到');
                            break;
                        }
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
                }
            },
            {
                element: '.catgirl-block:first-child select[name="voice_id"]',
                popover: {
                    title: this.t('tutorial.chara_manager.step14.title', '🎤 语音设定'),
                    description: this.t('tutorial.chara_manager.step14.desc', '选择猫娘的语音角色。不同的 voice_id 对应不同的声音特征，让您的虚拟伙伴拥有独特的声音。'),
                },
                skipInitialCheck: true, // 跳过初始化时的元素检查
                onHighlightStarted: async () => {
                    // 等待语音选择框渲染完成
                    const maxWait = 3000;
                    const startTime = Date.now();

                    while (Date.now() - startTime < maxWait) {
                        const element = document.querySelector('.catgirl-block:first-child select[name="voice_id"]');
                        if (element) {
                            console.log('[Tutorial] 语音选择框已找到');
                            break;
                        }
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
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
                element: '.newbie-recommend',
                popover: {
                    title: this.t('tutorial.settings.step1.title', '🎯 新手推荐'),
                    description: this.t('tutorial.settings.step1.desc', '如果您还没有 API Key，可以直接选择"免费版"开始使用，无需注册任何账号！'),
                }
            },
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
            },
            {
                element: '#advanced-toggle-btn',
                popover: {
                    title: this.t('tutorial.settings.step4.title', '⚙️ 高级选项'),
                    description: this.t('tutorial.settings.step4.desc', '点击这里展开高级选项。高级选项包括辅助 API 配置和自定义 API 配置。'),
                },
                action: 'click'
            },
            {
                element: '#assistApiSelect',
                popover: {
                    title: this.t('tutorial.settings.step5.title', '🔧 辅助 API 服务商'),
                    description: this.t('tutorial.settings.step5.desc', '辅助 API 负责记忆管理和自定义语音功能。\n\n• 免费版：完全免费，但不支持自定义语音\n• 阿里：推荐选择，支持自定义语音\n• 智谱：支持 Agent 模式\n• OpenAI：记忆管理能力强\n\n注意：只有阿里支持自定义语音功能。'),
                }
            },
            {
                element: '#assistApiKeyInputQwen',
                popover: {
                    title: this.t('tutorial.settings.step6.title', '🔑 辅助 API Key'),
                    description: this.t('tutorial.settings.step6.desc', '如果您选择了阿里作为辅助 API，需要在这里填写阿里的 API Key。如果不填写，系统会使用核心 API 的 Key。'),
                }
            },
            {
                element: '#custom-api-toggle-btn',
                popover: {
                    title: this.t('tutorial.settings.step7.title', '🔧 自定义 API 配置'),
                    description: this.t('tutorial.settings.step7.desc', '点击这里可以展开自定义 API 配置选项。如果您想使用自己的 API 服务器或其他兼容的 API 服务，可以在这里配置。'),
                },
                action: 'click'
            },
            {
                element: '#enableCustomApi',
                popover: {
                    title: this.t('tutorial.settings.step8.title', '✅ 启用自定义 API'),
                    description: this.t('tutorial.settings.step8.desc', '勾选这个选项可以启用自定义 API 配置。启用后，您可以为不同的功能模块（摘要、纠错、情感分析等）配置独立的 API。'),
                },
                action: 'click'
            },
            {
                element: '.model-config-container:nth-of-type(1)',
                popover: {
                    title: this.t('tutorial.settings.step9.title', '📝 摘要模型配置'),
                    description: this.t('tutorial.settings.step9.desc', '摘要模型用于生成对话摘要和记忆管理。您可以配置独立的 API 服务来处理摘要生成任务。'),
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
                    description: this.t('tutorial.voice_clone.step5.desc', '这里显示所有已成功克隆的音色。您可以在角色管理中选择这些音色来为虚拟伙伴配音。'),
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
                element: '#subscriptions-list',
                popover: {
                    title: this.t('tutorial.steam_workshop.step1.title', '📦 订阅内容列表'),
                    description: this.t('tutorial.steam_workshop.step1.desc', '这里显示所有您已订阅的 Steam Workshop 内容。点击卡片可以查看详情或进行操作。'),
                }
            },
            {
                element: '.workshop-integration-info',
                popover: {
                    title: this.t('tutorial.steam_workshop.step2.title', '💡 使用提示'),
                    description: this.t('tutorial.steam_workshop.step2.desc', '如果您想使用 Steam Workshop 中的语音音色，需要前往 Live2D 设置页面手动注册。'),
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
                element: '.tips-container',
                popover: {
                    title: this.t('tutorial.memory_browser.step1.title', '💡 使用提示'),
                    description: this.t('tutorial.memory_browser.step1.desc', '刚刚结束的对话内容需要稍等片刻才会载入。如果没有看到最新的对话，可以点击猫娘名称来刷新。'),
                }
            },
            {
                element: '#memory-file-list',
                popover: {
                    title: this.t('tutorial.memory_browser.step2.title', '🐱 猫娘记忆库'),
                    description: this.t('tutorial.memory_browser.step2.desc', '这里列出了所有虚拟伙伴的记忆库。点击一个猫娘的名称可以查看和编辑她的对话历史。'),
                }
            },
            {
                element: '.review-toggle',
                popover: {
                    title: this.t('tutorial.memory_browser.step3.title', '🤖 自动记忆整理'),
                    description: this.t('tutorial.memory_browser.step3.desc', '开启这个功能后，系统会自动整理和优化记忆内容，提高对话质量。建议保持开启状态。'),
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
                'chara_manager',  // 角色管理页面需要全屏引导以避免布局问题
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
        title.textContent = '🎓 开始新手引导';
        title.style.color = '#44b7fe';
        title.style.marginBottom = '20px';
        title.style.fontSize = '24px';

        // 描述
        const description = document.createElement('p');
        description.textContent = '为了获得最佳的引导体验，建议进入全屏模式。\n全屏模式下，引导内容会更清晰，不会被其他元素遮挡。';
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
        fullscreenBtn.textContent = '进入全屏引导';
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
                            this.waitForCatgirlCards().then(() => {
                                console.log('[Tutorial] 猫娘卡片已加载，启动引导');
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

        // 跳过按钮
        const skipBtn = document.createElement('button');
        skipBtn.textContent = '跳过全屏';
        skipBtn.style.padding = '12px 30px';
        skipBtn.style.background = 'rgba(68, 183, 254, 0.15)';
        skipBtn.style.color = '#44b7fe';
        skipBtn.style.border = '1px solid rgba(68, 183, 254, 0.3)';
        skipBtn.style.borderRadius = '8px';
        skipBtn.style.fontSize = '16px';
        skipBtn.style.fontWeight = '600';
        skipBtn.style.cursor = 'pointer';
        skipBtn.style.transition = 'all 0.2s ease';

        skipBtn.onmouseover = () => {
            skipBtn.style.background = 'rgba(68, 183, 254, 0.25)';
            skipBtn.style.transform = 'translateY(-1px)';
        };
        skipBtn.onmouseout = () => {
            skipBtn.style.background = 'rgba(68, 183, 254, 0.15)';
            skipBtn.style.transform = 'translateY(0)';
        };

        skipBtn.onclick = () => {
            document.body.removeChild(overlay);
            // 不进入全屏，直接启动引导，使用已验证的 validSteps
            this.startTutorialSteps(validSteps);
        };

        // 组装
        buttonContainer.appendChild(fullscreenBtn);
        buttonContainer.appendChild(skipBtn);
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

        // 定义步骤
        this.driver.setSteps(validSteps);

        // 设置全局标记，表示正在进行引导
        window.isInTutorial = true;
        console.log('[Tutorial] 设置全局引导标记');

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

        // 启动浮动工具栏保护定时器（每 200ms 检查一次，更频繁）
        this.floatingButtonsProtectionTimer = setInterval(() => {
            const floatingButtons = document.getElementById('live2d-floating-buttons');
            if (floatingButtons && window.isInTutorial) {
                // 强制设置所有可能隐藏浮动按钮的样式
                floatingButtons.style.setProperty('display', 'flex', 'important');
                floatingButtons.style.setProperty('visibility', 'visible', 'important');
                floatingButtons.style.setProperty('opacity', '1', 'important');
            }
        }, 200);

        // 监听事件
        this.driver.on('destroy', () => this.onTutorialEnd());
        this.driver.on('next', () => this.onStepChange());

        // 启动引导
        this.driver.start();
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
     * 检查元素是否需要点击（用于折叠/展开组件）
     */
    shouldClickElement(element, selector) {
        // 对于折叠/展开类型的元素，检查是否已经处于展开状态
        if (selector.includes('.fold-toggle') || selector.includes('.catgirl-header')) {
            // 查找相关的内容容器
            let contentContainer = element.nextElementSibling;

            // 如果直接的下一个兄弟元素不是内容，向上查找到父元素再查找
            if (!contentContainer) {
                const parent = element.closest('[class*="catgirl"]');
                if (parent) {
                    contentContainer = parent.querySelector('[class*="details"], [class*="content"], .fold-content');
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

            // 进入新步骤前，先清理上一阶段的“下一步”前置校验
            this.clearNextButtonGuard();

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
                        // 检查是否真正需要点击（对于折叠/展开的元素）
                        const needsClick = this.shouldClickElement(element, currentStepConfig.element);

                        if (!needsClick) {
                            console.log(`[Tutorial] 元素已处于目标状态，跳过点击: ${currentStepConfig.element}`);
                            // 直接刷新位置
                            setTimeout(() => {
                                if (this.driver && typeof this.driver.refresh === 'function') {
                                    this.driver.refresh();
                                }
                            }, 200);
                        } else {
                            // 延迟一点点时间，确保元素已经完全显示
                            setTimeout(() => {
                                console.log(`[Tutorial] 自动点击元素: ${currentStepConfig.element}`);

                                // 创建 MutationObserver 来监听 DOM 变化
                                const observer = new MutationObserver(() => {
                                    if (this.driver && typeof this.driver.refresh === 'function') {
                                        this.driver.refresh();
                                        console.log(`[Tutorial] DOM 变化后刷新高亮框位置`);
                                    }
                                });

                                // 监听整个 body 的子树变化
                                observer.observe(document.body, {
                                    childList: true,
                                    subtree: true,
                                    attributes: true,
                                    attributeFilter: ['style', 'class']
                                });

                                // 点击元素
                                element.click();

                                // 点击后等待布局稳定，然后停止监听并最后刷新一次
                                // 对于角色管理页面的展开操作，需要更长的等待时间以确保表单渲染完成
                                const waitTime = (this.currentPage === 'chara_manager' &&
                                                (currentStepConfig.element.includes('.catgirl-header') ||
                                                 currentStepConfig.element.includes('.fold-toggle'))) ? 1500 : 800;

                                setTimeout(() => {
                                    observer.disconnect();

                                    if (this.driver && typeof this.driver.refresh === 'function') {
                                        this.driver.refresh();
                                        console.log(`[Tutorial] 最终刷新高亮框位置 (等待${waitTime}ms)`);
                                    }
                                }, waitTime);
                            }, 300);
                        }
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
    }

    /**
     * 引导结束时的回调
     */
    onTutorialEnd() {
        // 重置运行标志
        this.isTutorialRunning = false;
        this.clearNextButtonGuard();

        // 只有进入了全屏的页面才需要退出全屏
        const pagesNeedingFullscreen = ['chara_manager'];
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

        // 恢复所有在引导中修改过的元素的原始样式
        this.restoreAllModifiedElements();

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

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { UniversalTutorialManager, initUniversalTutorialManager };
}
