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
            // 对于主页，需要等待浮动按钮创建
            if (this.currentPage === 'home') {
                this.waitForFloatingButtons().then(() => {
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
                    description: '这是浮动工具栏，包含多个实用功能按钮。让我为你逐一介绍每个按钮的功能吧~',
                }
            },
            {
                element: '#live2d-btn-mic',
                popover: {
                    title: '🎤 语音控制',
                    description: '点击这个按钮可以启用语音控制功能。启用后，虚拟伙伴会通过语音识别来理解你的话语，让交互更加自然和便捷。你可以直接说话，而不需要打字。',
                }
            },
            {
                element: '#live2d-btn-screen',
                popover: {
                    title: '🖥️ 屏幕分享',
                    description: '点击这个按钮可以启用屏幕分享功能。启用后，虚拟伙伴可以看到你的屏幕内容，这样她可以更好地理解你的需求并提供帮助。',
                }
            },
            {
                element: '#live2d-btn-agent',
                popover: {
                    title: '🔨 Agent 工具',
                    description: '点击这个按钮可以打开 Agent 工具面板。在这里你可以配置和管理各种自动化工具，让虚拟伙伴能够执行更多复杂的任务。',
                }
            },
            {
                element: '#live2d-btn-settings',
                popover: {
                    title: '⚙️ 设置',
                    description: '点击这个按钮可以打开设置面板。在这里你可以调整虚拟伙伴的行为参数，管理角色、API 密钥、记忆等。让我为你逐一介绍设置面板中的各个功能。',
                },
                action: 'click'
            },
            {
                element: '#live2d-toggle-merge-messages',
                popover: {
                    title: '💬 合并消息',
                    description: '启用此选项后，虚拟伙伴会将多条消息合并为一条发送，使对话更加流畅。',
                }
            },
            {
                element: '#live2d-toggle-focus-mode',
                popover: {
                    title: '🎤 允许打断',
                    description: '启用此选项后，你可以在虚拟伙伴说话时打断她，让对话更加自然和互动。',
                }
            },
            {
                element: '#live2d-toggle-proactive-chat',
                popover: {
                    title: '💭 主动搭话',
                    description: '启用此选项后，虚拟伙伴会主动发起对话，不需要你每次都主动说话。你可以调整搭话的时间间隔。',
                }
            },
            {
                element: '#live2d-toggle-proactive-vision',
                popover: {
                    title: '👀 自主视觉',
                    description: '启用此选项后，虚拟伙伴会主动观察你的屏幕内容，并根据看到的内容主动评论或提问。',
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
                    description: '配置 AI 服务的 API 密钥。',
                }
            },
            {
                element: '#live2d-menu-memory',
                popover: {
                    title: '📚 记忆浏览',
                    description: '查看和管理虚拟伙伴的对话记忆。',
                }
            },
            {
                element: '#live2d-menu-steam-workshop',
                popover: {
                    title: '🎮 创意工坊',
                    description: '订阅和管理 Steam Workshop 中的模型和角色卡。',
                }
            },
            {
                element: '#live2d-btn-goodbye',
                popover: {
                    title: '💤 请她离开',
                    description: '点击这个按钮可以让虚拟伙伴暂时离开。她会播放一个告别动画，然后屏幕会恢复到空白状态。你可以随时点击屏幕让她回来。',
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
                element: 'textarea[name="性别"]',
                popover: {
                    title: '👥 性别设定',
                    description: '这是可选项。你可以输入你的性别或其他相关信息。这会影响虚拟伙伴对你的称呼方式。',
                }
            },
            {
                element: 'textarea[name="昵称"]',
                popover: {
                    title: '💬 昵称设定',
                    description: '这是可选项。你可以为自己设置一个昵称。虚拟伙伴可能会用这个昵称来称呼你。',
                }
            },
            {
                element: '#api-key-settings-btn',
                popover: {
                    title: '🔑 API Key 设置',
                    description: '点击这里配置 AI 服务的 API Key。这是虚拟伙伴能够进行对话的必要配置。',
                }
            },
            {
                element: '#catgirl-section',
                popover: {
                    title: '🐱 猫娘档案',
                    description: '这里可以创建和管理多个虚拟伙伴角色。每个角色都有独特的性格、Live2D 形象和语音设定。你可以在不同的角色之间切换。',
                }
            },
            {
                element: '#add-catgirl-btn',
                popover: {
                    title: '➕ 新增猫娘',
                    description: '点击这个按钮创建一个新的虚拟伙伴角色。你可以为她设置名字、性格、形象和语音。每个角色都是独立的，有自己的记忆和性格。',
                }
            },
            {
                element: '.catgirl-block:first-child .catgirl-header',
                popover: {
                    title: '📋 猫娘卡片',
                    description: '点击猫娘名称可以展开或折叠详细信息。每个猫娘都有独立的设定，包括基础信息和进阶配置。',
                },
                action: 'click' // 自动点击展开卡片
            },
            {
                element: '.catgirl-block:first-child button[id^="switch-btn-"]',
                popover: {
                    title: '🔄 切换猫娘',
                    description: '点击此按钮可以将这个猫娘设为当前活跃角色。切换后，主页和对话界面会使用该角色的形象和性格。',
                }
            },
            {
                element: '.catgirl-block:first-child button.delete',
                popover: {
                    title: '🗑️ 删除猫娘',
                    description: '点击此按钮可以删除该猫娘角色。注意：删除后无法恢复，请谨慎操作。',
                }
            },
            {
                element: '.catgirl-block:first-child .fold-toggle',
                popover: {
                    title: '⚙️ 进阶设定',
                    description: '点击展开进阶设定，可以配置 Live2D 模型、语音 ID、以及添加自定义性格属性（如性格、爱好、口头禅等）。',
                },
                action: 'click' // 自动点击展开
            },
            {
                element: '.catgirl-block:first-child .live2d-link',
                popover: {
                    title: '🎨 模型设定',
                    description: '点击此链接可以选择或更换猫娘的 Live2D 形象或 VRM 模型。不同的模型会带来不同的视觉体验。',
                }
            },
            {
                element: '.catgirl-block:first-child select[name="voice_id"]',
                popover: {
                    title: '🎤 语音设定',
                    description: '选择猫娘的语音角色。不同的 voice_id 对应不同的声音特征，让你的虚拟伙伴拥有独特的声音。',
                }
            },
            {
                element: '#catgirl-section',
                popover: {
                    title: '✅ 引导完成',
                    description: '恭喜！你已经了解了角色管理的所有功能。现在可以开始创建和管理你的虚拟伙伴了。随时可以回到这里修改设定。',
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
                    title: '🔑 核心 API 服务商',
                    description: '这是最重要的设置。核心 API 负责对话功能。\n\n• 免费版：完全免费，无需 API Key，适合新手体验\n• 阿里：有免费额度，功能全面\n• 智谱：有免费额度，支持联网搜索\n• OpenAI：智能水平最高，但需要翻墙且价格昂贵',
                }
            },
            {
                element: '#apiKeyInput',
                popover: {
                    title: '📝 核心 API Key',
                    description: '将你选择的 API 服务商的 API Key 粘贴到这里。如果选择了免费版，这个字段可以留空。',
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
                    description: '点击这里展开高级选项。高级选项包括辅助 API 配置，用于记忆管理、自定义语音等高级功能。',
                },
                action: 'click'
            },
            {
                element: '#assistApiSelect',
                popover: {
                    title: '🔧 辅助 API 服务商',
                    description: '辅助 API 负责记忆管理和自定义语音功能。\n\n• 免费版：完全免费，但不支持自定义语音\n• 阿里：推荐选择，支持自定义语音\n• 智谱：支持 Agent 模式\n• OpenAI：记忆管理能力强\n\n注意：只有阿里支持自定义语音功能。',
                }
            },
            {
                element: '#assistApiKeyInputQwen',
                popover: {
                    title: '🔑 辅助 API Key - 阿里',
                    description: '如果你选择了阿里作为辅助 API，需要在这里填写阿里的 API Key。如果不填写，系统会使用核心 API 的 Key。',
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
     * 显示隐藏的元素（用于引导）
     */
    showElementForTutorial(element, selector) {
        if (!element) return;

        const style = window.getComputedStyle(element);

        // 保存原始样式，以便后续恢复
        const originalDisplay = element.style.display;
        const originalVisibility = element.style.visibility;
        const originalOpacity = element.style.opacity;

        // 显示元素（使用 !important 确保样式被应用）
        if (style.display === 'none') {
            element.style.setProperty('display', 'flex', 'important');
            console.log(`[Tutorial] 显示隐藏元素: ${selector}`);
        }

        if (style.visibility === 'hidden') {
            element.style.setProperty('visibility', 'visible', 'important');
            console.log(`[Tutorial] 恢复隐藏元素可见性: ${selector}`);
        }

        if (style.opacity === '0') {
            element.style.setProperty('opacity', '1', 'important');
            console.log(`[Tutorial] 恢复隐藏元素透明度: ${selector}`);
        }

        // 特殊处理浮动工具栏：确保它在引导中保持可见
        if (selector === '#live2d-floating-buttons') {
            // 标记浮动工具栏在引导中，防止自动隐藏
            element.dataset.inTutorial = 'true';
            console.log('[Tutorial] 浮动工具栏已标记为引导中');
        }

        return { originalDisplay, originalVisibility, originalOpacity };
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
                const element = document.querySelector(step.element);
                if (!element) {
                    console.warn(`[Tutorial] 元素不存在: ${step.element}`);
                    return false;
                }

                // 检查元素是否可见，如果隐藏则显示它
                if (!this.isElementVisible(element)) {
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

            // 先显示全屏提示，等待用户点击
            this.showFullscreenPrompt(validSteps);
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
                        console.log('[Tutorial] 全屏布局已稳定，启动引导');
                        this.startTutorialSteps(validSteps);
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
                    console.warn('[Tutorial] 全屏超时，直接启动引导');
                    this.startTutorialSteps(validSteps);

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
            // 不进入全屏，直接启动引导
            this.startTutorialSteps(this.driver.steps);
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
        // 定义步骤
        this.driver.setSteps(validSteps);

        // 设置全局标记，表示正在进行引导
        window.isInTutorial = true;
        console.log('[Tutorial] 设置全局引导标记');

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

        // 获取当前步骤的元素
        const steps = this.getStepsForPage();
        if (this.currentStep < steps.length) {
            const currentStepConfig = steps[this.currentStep];
            const element = document.querySelector(currentStepConfig.element);

            if (element) {
                // 检查元素是否隐藏，如果隐藏则显示
                if (!this.isElementVisible(element)) {
                    console.warn(`[Tutorial] 当前步骤的元素隐藏，正在显示: ${currentStepConfig.element}`);
                    this.showElementForTutorial(element, currentStepConfig.element);
                }

                // 执行步骤中定义的操作
                if (currentStepConfig.action) {
                    if (currentStepConfig.action === 'click') {
                        // 延迟一点点时间，确保元素已经完全显示
                        setTimeout(() => {
                            console.log(`[Tutorial] 自动点击元素: ${currentStepConfig.element}`);
                            element.click();
                        }, 300);
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

        // 退出全屏模式
        this.exitFullscreenMode();

        // 标记用户已看过该页面的引导
        const storageKey = this.STORAGE_KEY_PREFIX + this.currentPage;
        localStorage.setItem(storageKey, 'true');

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

        // 清除浮动工具栏的引导标记
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        if (floatingButtons) {
            floatingButtons.dataset.inTutorial = 'false';
            console.log('[Tutorial] 浮动工具栏引导标记已清除');
        }

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
    if (!window.universalTutorialManager) {
        window.universalTutorialManager = new UniversalTutorialManager();
        console.log('[Tutorial] 通用教程管理器已初始化');
    }
}

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { UniversalTutorialManager, initUniversalTutorialManager };
}
