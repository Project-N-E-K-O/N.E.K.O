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
        this.currentPage = this.detectPage();
        this.currentStep = 0;

        console.log('[Tutorial] 当前页面:', this.currentPage);

        // 等待 driver.js 库加载
        this.waitForDriver();
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

        // 模型管理
        if (path.includes('model_manager') || path.includes('l2d')) {
            return 'model_manager';
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
                className: 'neko-tutorial-driver',
                disableActiveInteraction: false,
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
     * 检查是否需要自动启动引导
     */
    checkAndStartTutorial() {
        const storageKey = this.STORAGE_KEY_PREFIX + this.currentPage;
        const hasSeen = localStorage.getItem(storageKey);

        if (!hasSeen) {
            // 延迟启动，确保 DOM 完全加载
            setTimeout(() => {
                this.startTutorial();
            }, 1500);
        }
    }

    /**
     * 获取当前页面的引导步骤配置
     */
    getStepsForPage() {
        const configs = {
            home: this.getHomeSteps(),
            model_manager: this.getModelManagerSteps(),
            chara_manager: this.getCharaManagerSteps(),
            settings: this.getSettingsSteps(),
            voice_clone: this.getVoiceCloneSteps(),
            steam_workshop: this.getSteamWorkshopSteps(),
            memory_browser: this.getMemoryBrowserSteps(),
        };

        return configs[this.currentPage] || [];
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
                    description: window.t ? window.t('tutorial.step1.desc', '这是你的虚拟伙伴，她会陪伴你进行各种交互。点击她可以触发不同的表情和动作哦~') : '这是你的虚拟伙伴，她会陪伴你进行各种交互。点击她可以触发不同的表情和动作哦~',
                }
            },
            {
                element: '#chat-container',
                popover: {
                    title: window.t ? window.t('tutorial.step2.title', '💬 对话区域') : '💬 对话区域',
                    description: window.t ? window.t('tutorial.step2.desc', '在这里可以和伙伴进行文字对话。输入你的想法，她会给你有趣的回应呢~') : '在这里可以和伙伴进行文字对话。输入你的想法，她会给你有趣的回应呢~',
                }
            },
            {
                element: '#textInputBox',
                popover: {
                    title: window.t ? window.t('tutorial.step3.title', '✍️ 输入框') : '✍️ 输入框',
                    description: window.t ? window.t('tutorial.step3.desc', '在这里输入你想说的话。按 Enter 发送，Shift+Enter 换行。也可以点击右边的按钮发送截图哦~') : '在这里输入你想说的话。按 Enter 发送，Shift+Enter 换行。也可以点击右边的按钮发送截图哦~',
                }
            },
            {
                element: '#button-group',
                popover: {
                    title: window.t ? window.t('tutorial.step4.title', '🎮 快速操作') : '🎮 快速操作',
                    description: window.t ? window.t('tutorial.step4.desc', '左边是发送按钮，右边是截图按钮。你可以分享屏幕截图给伙伴，她会帮你分析哦~') : '左边是发送按钮，右边是截图按钮。你可以分享屏幕截图给伙伴，她会帮你分析哦~',
                }
            },
            {
                element: '#screenshotButton',
                popover: {
                    title: '📸 截图功能',
                    description: '点击这个按钮可以截取屏幕。截图会被添加到待发送列表，你可以在发送前预览或删除。',
                }
            },
            {
                element: '#textSendButton',
                popover: {
                    title: '📤 发送消息',
                    description: '点击这个按钮发送你的消息和截图。虚拟伙伴会立即做出回应。',
                }
            },
            {
                element: '#toggle-chat-btn',
                popover: {
                    title: '🔽 对话框控制',
                    description: '点击这个按钮可以最小化或展开对话框。当你想要更好地看到虚拟伙伴时，可以最小化对话框。',
                }
            },
            {
                element: '#live2d-floating-buttons',
                popover: {
                    title: '🎛️ 浮动工具栏',
                    description: '这是浮动工具栏，包含语音控制和屏幕分享功能。你可以拖动它来改变位置。',
                }
            }
        ];
    }

    /**
     * 模型管理页面引导步骤
     */
    getModelManagerSteps() {
        return [
            {
                element: '#model-type-select-btn',
                popover: {
                    title: '🎨 选择模型类型',
                    description: '首先选择你要使用的模型类型：Live2D（2D 动画）或 VRM（3D 模型）。',
                }
            },
            {
                element: '#upload-btn',
                popover: {
                    title: '📤 上传模型',
                    description: '点击这里上传你的模型文件。支持 Live2D 和 VRM 格式。',
                }
            },
            {
                element: '#live2d-model-select-btn',
                popover: {
                    title: '🎭 选择模型',
                    description: '从已上传的模型中选择要使用的模型。',
                }
            },
            {
                element: '#motion-select-btn',
                popover: {
                    title: '💃 选择动作',
                    description: '为模型选择动作。点击"播放动作"按钮可以预览效果。',
                }
            },
            {
                element: '#expression-select-btn',
                popover: {
                    title: '😊 选择表情',
                    description: '为模型选择表情。可以设置常驻表情让模型保持该表情。',
                }
            },
            {
                element: '#save-position-btn',
                popover: {
                    title: '💾 保存设置',
                    description: '点击这里保存当前的模型、动作和表情设置。',
                }
            },
            {
                element: '#emotion-config-btn',
                popover: {
                    title: '😄 情感配置',
                    description: '点击这里配置模型的情感表现。可以为不同的情感设置对应的表情和动作组合。',
                }
            },
            {
                element: '#parameter-editor-btn',
                popover: {
                    title: '✨ 捏脸系统',
                    description: '点击这里进入捏脸系统，可以精细调整模型的面部参数，打造独特的虚拟伙伴形象。',
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
                    title: '👤 主人档案',
                    description: '这是你的主人档案。档案名是必填项，其他信息（性别、昵称等）都是可选的。这些信息会影响虚拟伙伴对你的称呼和态度。',
                }
            },
            {
                element: 'input[name="档案名"]',
                popover: {
                    title: '📝 设置档案名',
                    description: '输入你的名字或昵称。虚拟伙伴会用这个名字来称呼你。最多 20 个字符。',
                }
            },
            {
                element: '#catgirl-section',
                popover: {
                    title: '🐱 猫娘档案',
                    description: '这里可以创建和管理多个虚拟伙伴角色。每个角色都有独特的性格、Live2D 形象和语音设定。',
                }
            },
            {
                element: '#add-catgirl-btn',
                popover: {
                    title: '➕ 新增猫娘',
                    description: '点击这个按钮创建一个新的虚拟伙伴角色。你可以为她设置名字、性格、形象和语音。',
                }
            },
            {
                element: '#api-key-settings-btn',
                popover: {
                    title: '🔑 API Key 设置',
                    description: '点击这里配置 AI 服务的 API Key。这是虚拟伙伴能够进行对话的必要配置。',
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
                element: '.api-key-info',
                popover: {
                    title: '📖 快速开始',
                    description: '这里提供了详细的 API Key 获取步骤。如果你是新手，建议先阅读这部分内容。',
                }
            },
            {
                element: '.newbie-recommend',
                popover: {
                    title: '🎯 新手推荐',
                    description: '如果你还没有 API Key，可以直接选择"免费版"开始使用，无需注册任何账号！',
                }
            },
            {
                element: '#coreApiSelect',
                popover: {
                    title: '🔑 选择核心 API 服务商',
                    description: '选择你要使用的 AI 服务商。免费版、阿里、智谱、OpenAI 等都支持。不同服务商有不同的功能和价格。',
                }
            },
            {
                element: '#apiKeyInput',
                popover: {
                    title: '📝 输入 API Key',
                    description: '将你的 API Key 粘贴到这里。如果选择了免费版，这个字段可以留空。',
                }
            },
            {
                element: '#save-settings-btn',
                popover: {
                    title: '💾 保存设置',
                    description: '点击这个按钮保存你的 API 配置。保存后需要重启服务才能生效。',
                }
            },
            {
                element: '#advanced-toggle-btn',
                popover: {
                    title: '⚙️ 高级选项',
                    description: '点击这里展开高级选项，可以配置辅助 API（用于记忆管理和自定义语音）和 MCP Router Token。',
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
                    title: '⚠️ 重要提示',
                    description: '语音克隆功能需要使用阿里云 API。请确保你已经在 API 设置中配置了阿里云的 API Key。',
                }
            },
            {
                element: '.file-input-wrapper',
                popover: {
                    title: '🎵 选择音频文件',
                    description: '上传一个 15 秒左右的音频样本（最长 30 秒）。支持 WAV 和 MP3 格式。这个音频会被用来克隆虚拟伙伴的声音。',
                }
            },
            {
                element: '#refLanguage',
                popover: {
                    title: '🌍 选择参考音频语言',
                    description: '选择你上传的音频文件的语言。这帮助系统更准确地识别和克隆声音特征。',
                }
            },
            {
                element: '#prefix',
                popover: {
                    title: '🏷️ 自定义前缀',
                    description: '输入一个 10 字符以内的前缀（只能用数字和英文字母）。这个前缀会作为克隆音色的标识。',
                }
            },
            {
                element: '.register-voice-btn',
                popover: {
                    title: '✨ 注册音色',
                    description: '点击这个按钮开始克隆你的音色。系统会处理音频并生成一个独特的音色 ID。',
                }
            },
            {
                element: '.voice-list-section',
                popover: {
                    title: '📋 已注册音色列表',
                    description: '这里显示所有已成功克隆的音色。你可以在角色管理中选择这些音色来为虚拟伙伴配音。',
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
                    title: '📑 标签切换',
                    description: '在这里可以切换不同的内容类型。"订阅内容"显示你已订阅的模型和角色卡，"角色卡"显示所有可用的角色卡。',
                }
            },
            {
                element: '#search-subscription',
                popover: {
                    title: '🔍 搜索功能',
                    description: '输入关键词来搜索你想要的模型或角色卡。支持按名称搜索。',
                }
            },
            {
                element: '#sort-subscription',
                popover: {
                    title: '📊 排序选项',
                    description: '选择排序方式来组织你的订阅内容。可以按名称、订阅日期、文件大小或更新时间排序。',
                }
            },
            {
                element: '#subscriptions-list',
                popover: {
                    title: '📦 订阅内容列表',
                    description: '这里显示所有你已订阅的 Steam Workshop 内容。点击卡片可以查看详情或进行操作。',
                }
            },
            {
                element: '.workshop-integration-info',
                popover: {
                    title: '💡 使用提示',
                    description: '如果你想使用 Steam Workshop 中的语音音色，需要前往 Live2D 设置页面手动注册。',
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
                    title: '💡 使用提示',
                    description: '刚刚结束的对话内容需要稍等片刻才会载入。如果没有看到最新的对话，可以点击猫娘名称来刷新。',
                }
            },
            {
                element: '#memory-file-list',
                popover: {
                    title: '🐱 猫娘记忆库',
                    description: '这里列出了所有虚拟伙伴的记忆库。点击一个猫娘的名称可以查看和编辑她的对话历史。',
                }
            },
            {
                element: '.review-toggle',
                popover: {
                    title: '🤖 自动记忆整理',
                    description: '开启这个功能后，系统会自动整理和优化记忆内容，提高对话质量。建议保持开启状态。',
                }
            },
            {
                element: '#memory-chat-edit',
                popover: {
                    title: '📝 聊天记录编辑',
                    description: '这里显示选中猫娘的所有对话记录。你可以在这里查看、编辑或删除特定的对话内容。',
                }
            },
            {
                element: '#save-memory-btn',
                popover: {
                    title: '💾 保存修改',
                    description: '编辑完对话记录后，点击这个按钮保存你的修改。',
                }
            },
            {
                element: '#clear-memory-btn',
                popover: {
                    title: '🗑️ 清空记忆',
                    description: '点击这个按钮可以清空选中猫娘的所有对话记录。请谨慎使用，此操作无法撤销。',
                }
            }
        ];
    }

    /**
     * 启动引导
     */
    startTutorial() {
        if (!this.isInitialized) {
            console.warn('[Tutorial] driver.js 未初始化');
            return;
        }

        try {
            const steps = this.getStepsForPage();

            if (steps.length === 0) {
                console.warn('[Tutorial] 当前页面没有引导步骤');
                return;
            }

            // 过滤掉不存在的元素
            const validSteps = steps.filter(step => {
                const element = document.querySelector(step.element);
                if (!element) {
                    console.warn(`[Tutorial] 元素不存在: ${step.element}`);
                    return false;
                }
                return true;
            });

            if (validSteps.length === 0) {
                console.warn('[Tutorial] 没有有效的引导步骤');
                return;
            }

            // 定义步骤
            this.driver.setSteps(validSteps);

            // 监听事件
            this.driver.on('destroy', () => this.onTutorialEnd());
            this.driver.on('next', () => this.onStepChange());

            // 启动引导
            this.driver.start();
            console.log('[Tutorial] 引导已启动，页面:', this.currentPage);
        } catch (error) {
            console.error('[Tutorial] 启动引导失败:', error);
        }
    }

    /**
     * 步骤改变时的回调
     */
    onStepChange() {
        this.currentStep = this.driver.currentStep || 0;
        console.log(`[Tutorial] 当前步骤: ${this.currentStep + 1}`);
    }

    /**
     * 引导结束时的回调
     */
    onTutorialEnd() {
        // 标记用户已看过该页面的引导
        const storageKey = this.STORAGE_KEY_PREFIX + this.currentPage;
        localStorage.setItem(storageKey, 'true');
        console.log('[Tutorial] 引导已完成，页面:', this.currentPage);
    }

    /**
     * 重新启动引导（用户手动触发）
     */
    restartTutorial() {
        const storageKey = this.STORAGE_KEY_PREFIX + this.currentPage;
        localStorage.removeItem(storageKey);

        if (this.driver) {
            this.driver.destroy();
        }

        this.startTutorial();
    }

    /**
     * 重置所有页面的引导状态
     */
    resetAllTutorials() {
        const pages = ['home', 'model_manager', 'chara_manager', 'settings', 'voice_clone', 'steam_workshop', 'memory_browser'];
        pages.forEach(page => {
            localStorage.removeItem(this.STORAGE_KEY_PREFIX + page);
        });
        console.log('[Tutorial] 所有引导状态已重置');
    }

    /**
     * 获取引导状态
     */
    hasSeenTutorial(page = null) {
        const targetPage = page || this.currentPage;
        const storageKey = this.STORAGE_KEY_PREFIX + targetPage;
        return localStorage.getItem(storageKey) === 'true';
    }
}

// 创建全局实例
window.universalTutorialManager = null;

/**
 * 初始化通用教程管理器
 * 应在 DOM 加载完成后调用
 */
function initUniversalTutorialManager() {
    if (!window.universalTutorialManager) {
        window.universalTutorialManager = new UniversalTutorialManager();
        console.log('[Tutorial] 通用教程管理器已初始化');
    }
}

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { UniversalTutorialManager, initUniversalTutorialManager };
}
