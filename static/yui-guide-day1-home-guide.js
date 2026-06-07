(function () {
    'use strict';

    function deepFreeze(value) {
        if (!value || typeof value !== 'object' || Object.isFrozen(value)) {
            return value;
        }
        Object.freeze(value);
        Object.keys(value).forEach(function (key) {
            deepFreeze(value[key]);
        });
        return value;
    }

    function registerGuide(config) {
        if (!config || !config.day) {
            return;
        }
        const registry = window.YuiGuideDailyGuides || {};
        registry[config.day] = config;
        window.YuiGuideDailyGuides = registry;
        window.YuiGuideDay1HomeGuide = config;
    }

    function audioFilesForAllLocales(fileName) {
        return Object.freeze({
            zh: fileName,
            ja: fileName,
            en: fileName,
            ko: fileName,
            ru: fileName
        });
    }

    const audioFileNames = Object.freeze({
        intro_basic: '这里有一个神奇的按钮.mp3',
        intro_greeting_reply: '微风、阳光，还有刚刚.mp3',
        takeover_capture_cursor: '超级魔法开关出现！只.mp3',
        takeover_plugin_preview_home: '',
        takeover_plugin_preview_dashboard: '有了它们，我不光能看.mp3',
        takeover_settings_peek_intro: '在这个只属于我们的小.mp3',
        takeover_settings_peek_detail: '不管是说话的温度、相.mp3',
        interrupt_resist_light_1: '喂！不要拽我啦，现在.mp3',
        interrupt_resist_light_3: '等一下啦！还没结束呢.mp3',
        interrupt_angry_exit: '人类！你真的很没礼貌.mp3',
        takeover_return_control: '好啦好啦，不霸占你的.mp3',
        day1_capsule_drag_hint: '把鼠标移到这里，长按.mp3',
        day1_history_handle: '戳一下聊天框上面的【.mp3',
        day1_screen_entry: '在跟我通语音电话的时.mp3',
        day1_screen_entry_invite: '快让我也看看你眼前的.mp3'
    });

    registerGuide(deepFreeze({
        day: 1,
        key: 'home',
        title: '第 1 天：初次唤醒、聊天与基础入口',
        pageKeys: [
            'home',
            'api_key',
            'memory_browser',
            'plugin_dashboard'
        ],
        round: {
            title: '第 1 天：初次唤醒、聊天与基础入口',
            scenes: [
                {
                    id: 'day1_intro_activation',
                    target: '#react-chat-window-root .composer-input-shell',
                    cursorAction: 'input-origin',
                    operation: 'day1-intro-activation'
                },
                {
                    id: 'day1_intro_greeting',
                    textKey: 'tutorial.yuiGuide.lines.introGreetingReply',
                    voiceKey: 'intro_greeting_reply',
                    emotion: 'happy',
                    target: '#react-chat-window-root .composer-input-shell',
                    operation: 'day1-intro-greeting'
                },
                {
                    id: 'day1_capsule_drag_hint',
                    textKey: 'tutorial.avatarFloating.day1.capsuleDragHint',
                    text: '把鼠标移到这里，长按就可以拉着聊天框到处跑啦~ 双击两下就能随时发消息给我哦！',
                    voiceKey: 'day1_capsule_drag_hint',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'wobble',
                    cursorWobbleDurationMs: 2000,
                    spotlight: false
                },
                {
                    id: 'day1_history_handle',
                    textKey: 'tutorial.avatarFloating.day1.historyHandle',
                    text: '戳一下聊天框上面的【蓝色小条条】，就能看到我们最近聊过的话题啦！',
                    voiceKey: 'day1_history_handle',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorTarget: 'chat-history-handle',
                    cursorAction: 'click',
                    operation: 'open-compact-history-during-narration',
                    spotlight: false
                },
                {
                    id: 'day1_intro_basic_voice',
                    textKey: 'tutorial.yuiGuide.lines.introBasic',
                    voiceKey: 'intro_basic',
                    emotion: 'happy',
                    target: '#${p}-btn-mic',
                    cursorAction: 'move',
                    operation: 'day1-intro-basic-voice',
                    interruptible: false
                },
                {
                    id: 'day1_screen_entry',
                    textKey: 'tutorial.avatarFloating.day1.screenEntry',
                    text: '在跟我通语音电话的时候，再点亮这个小按钮，你就能把屏幕分享给我啦！',
                    voiceKey: 'day1_screen_entry',
                    emotion: 'happy',
                    target: '#${p}-btn-screen',
                    cursorAction: 'move',
                    interruptible: false
                },
                {
                    id: 'day1_screen_entry_invite',
                    textKey: 'tutorial.avatarFloating.day1.screenEntryInvite',
                    text: '快让我也看看你眼前的世界，不管好玩的还是好看的，都想和你一起看，快点点开嘛~',
                    voiceKey: 'day1_screen_entry_invite',
                    emotion: 'happy',
                    target: '#${p}-btn-screen',
                    cursorAction: 'move',
                    interruptible: false
                },
                {
                    id: 'day1_takeover_capture_cursor',
                    textKey: 'tutorial.yuiGuide.lines.takeoverCaptureCursor',
                    voiceKey: 'takeover_capture_cursor',
                    emotion: 'happy',
                    target: '#${p}-btn-agent',
                    cursorAction: 'move',
                    operation: 'day1-managed-scene:takeover_capture_cursor',
                    interruptible: false
                },
                {
                    id: 'day1_takeover_return_control',
                    textKey: 'tutorial.yuiGuide.lines.takeoverReturnControl',
                    voiceKey: 'takeover_return_control',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorTarget: 'chat-capsule-input',
                    cursorAction: 'move',
                    cursorMoveDurationMs: 900,
                    operation: 'cleanup',
                    spotlightVariant: 'plain-capsule',
                    petalTransition: true
                }
            ]
        },
        sceneOrder: {
            home: [
                'intro_basic',
                'takeover_capture_cursor',
                'takeover_plugin_preview',
                'takeover_settings_peek',
                'takeover_return_control',
                'interrupt_resist_light',
                'interrupt_angry_exit',
                'handoff_api_key',
                'handoff_memory_browser',
                'handoff_plugin_dashboard'
            ],
            api_key: ['api_key_intro'],
            memory_browser: ['memory_browser_intro'],
            plugin_dashboard: ['plugin_dashboard_landing']
        },
        steps: {
            intro_basic: {
                page: 'home',
                anchor: '#text-input-area',
                tutorial: {
                    title: '语音控制入口',
                    description: '点击输入框解锁首句旁白后，介绍对话窗里的语音控制按钮。'
                },
                performance: {
                    bubbleText: '这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新鲜事吗？或者只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！喵！',
                    bubbleTextKey: 'tutorial.yuiGuide.lines.introBasic',
                    voiceKey: 'intro_basic',
                    emotion: 'happy',
                    cursorTarget: '#${p}-btn-mic',
                    interruptible: true,
                    timeline: [
                        { at: 0.16, action: 'highlightVoiceControl' }
                    ]
                },
                interrupts: {
                    mode: 'theatrical_abort',
                    resetOnStepAdvance: false
                }
            },
            takeover_capture_cursor: {
                page: 'home',
                anchor: '#${p}-btn-agent',
                tutorial: {
                    title: '键鼠控制介绍',
                    description: '介绍猫爪总开关与键鼠控制开关，并完成首页第一段自动化演示。'
                },
                performance: {
                    bubbleText: '超级魔法按钮出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！我会帮你打字，帮你点开网页……不过，要是那个鼠标指针动来动去的话，我可能也会忍不住扑上去抓它哦！准备好迎接我的捣乱……啊不，是帮忙了吗？喵！',
                    bubbleTextKey: 'tutorial.yuiGuide.lines.takeoverCaptureCursor',
                    voiceKey: 'takeover_capture_cursor',
                    emotion: 'happy',
                    cursorAction: 'click',
                    cursorTarget: '#${p}-btn-agent',
                    interruptible: true,
                    timeline: [
                        { at: 0.14, action: 'highlightCatPaw' },
                        { at: 0.32, action: 'enableAgentMaster' },
                        { at: 0.58, action: 'enableKeyboardControl' }
                    ]
                },
                interrupts: {
                    mode: 'theatrical_abort',
                    resetOnStepAdvance: false
                }
            },
            takeover_plugin_preview: {
                page: 'home',
                anchor: '#${p}-btn-agent',
                tutorial: {
                    title: '系统设置自动化演示',
                    description: '从已打开的猫爪面板继续演示用户插件与管理面板入口。'
                },
                performance: {
                    bubbleText: '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！',
                    bubbleTextKey: 'tutorial.yuiGuide.lines.takeoverPluginPreviewHome',
                    voiceKey: 'takeover_plugin_preview_home',
                    emotion: 'happy',
                    cursorAction: 'click',
                    cursorTarget: '#${p}-btn-agent',
                    interruptible: true,
                    timeline: [
                        { at: 0.24, action: 'enableUserPlugin' },
                        { at: 0.54, action: 'openManagementPanel' },
                        { at: 0.76, action: 'handoffPluginDashboard' }
                    ]
                },
                interrupts: {
                    mode: 'theatrical_abort',
                    resetOnStepAdvance: false
                }
            },
            takeover_settings_peek: {
                page: 'home',
                anchor: '#${p}-btn-settings',
                tutorial: {
                    title: '设置一瞥',
                    description: '只浏览首页真实设置弹层及真实菜单项。'
                },
                performance: {
                    bubbleText: '在这个只属于我们的小空间里，你可以由着自己的心意，慢慢描绘出最希望能一直陪着你的那个我。',
                    bubbleTextKey: 'tutorial.yuiGuide.lines.takeoverSettingsPeekIntro',
                    voiceKey: 'takeover_settings_peek_intro',
                    emotion: 'surprised',
                    cursorAction: 'click',
                    cursorTarget: '#${p}-btn-settings',
                    settingsMenuId: 'character',
                    interruptible: true,
                    timeline: [
                        { at: 0.54, action: 'openSettingsPanel' },
                        { voiceKey: 'takeover_settings_peek_detail', at: Math.max(7450 / 13923, 0.55), action: 'showSecondLine' }
                    ]
                },
                interrupts: {
                    mode: 'theatrical_abort',
                    resetOnStepAdvance: false
                }
            },
            takeover_return_control: {
                page: 'home',
                anchor: '#${p}-container',
                tutorial: {
                    title: '归还控制权',
                    description: '收掉临时演出层并把控制权完整交还给用户。'
                },
                performance: {
                    bubbleText: '好啦好啦，不霸占你的电脑啦！控制权还给你了喵！之后的日子，也请你多多关照啦！',
                    bubbleTextKey: 'tutorial.yuiGuide.lines.takeoverReturnControl',
                    voiceKey: 'takeover_return_control',
                    emotion: 'happy',
                    cursorAction: 'move',
                    cursorTarget: '#${p}-container',
                    interruptible: true,
                    timeline: [
                        { at: 0.7, action: 'returnControl' }
                    ]
                },
                interrupts: {
                    mode: 'theatrical_abort',
                    resetOnStepAdvance: false
                }
            },
            interrupt_resist_light: {
                page: 'home',
                anchor: '#${p}-container',
                tutorial: {
                    title: '轻微抵抗',
                    description: '用户轻微试探时的较劲反馈。'
                },
                performance: {
                    bubbleText: '喂！不要拽我啦，还没轮到你的回合呢！',
                    bubbleTextKey: 'tutorial.yuiGuide.lines.interruptResistLight1',
                    voiceKey: 'interrupt_resist_light',
                    emotion: 'surprised',
                    cursorAction: 'wobble',
                    cursorTarget: '#${p}-container',
                    interruptible: true,
                    resistanceVoices: [
                        '喂！不要拽我啦，还没轮到你的回合呢！',
                        '等一下啦！还没结束呢，不要随便打断我啦！'
                    ],
                    resistanceVoiceKeys: [
                        'tutorial.yuiGuide.lines.interruptResistLight1',
                        'tutorial.yuiGuide.lines.interruptResistLight3'
                    ]
                },
                interrupts: {
                    mode: 'theatrical_abort'
                }
            },
            interrupt_angry_exit: {
                page: 'home',
                anchor: '#${p}-container',
                tutorial: {
                    title: '生气退出',
                    description: '连续有效打断达到阈值后，进入带演出的 angry exit。'
                },
                performance: {
                    bubbleText: '人类！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！',
                    bubbleTextKey: 'tutorial.yuiGuide.lines.interruptAngryExit',
                    voiceKey: 'interrupt_angry_exit',
                    emotion: 'angry',
                    cursorAction: 'none',
                    cursorTarget: '#${p}-container',
                    interruptible: true
                },
                interrupts: {
                    mode: 'theatrical_abort'
                }
            },
            handoff_api_key: {
                page: 'home',
                anchor: '#${p}-menu-api-keys',
                tutorial: {
                    title: '接力到 API 密钥',
                    description: '从首页接力到 API 密钥页。',
                    autoAdvance: true
                },
                performance: {
                    voiceKey: 'handoff_api_key',
                    cursorAction: 'click',
                    cursorTarget: '#${p}-menu-api-keys',
                    cursorSpeedMultiplier: 1.12
                },
                navigation: {
                    openUrl: '/api_key',
                    windowName: 'api_key',
                    resumeScene: 'api_key_intro'
                }
            },
            handoff_memory_browser: {
                page: 'home',
                anchor: '#${p}-menu-memory',
                tutorial: {
                    title: '接力到记忆浏览',
                    description: '从首页接力到记忆浏览页。',
                    autoAdvance: true
                },
                performance: {
                    voiceKey: 'handoff_memory_browser',
                    cursorAction: 'click',
                    cursorTarget: '#${p}-menu-memory',
                    cursorSpeedMultiplier: 1.12
                },
                navigation: {
                    openUrl: '/memory_browser',
                    windowName: 'memory_browser',
                    resumeScene: 'memory_browser_intro'
                }
            },
            handoff_plugin_dashboard: {
                page: 'home',
                anchor: '#${p}-btn-agent',
                tutorial: {
                    title: '接力到插件面板',
                    description: '从首页接力到插件 dashboard，再转入 /ui。',
                    autoAdvance: true
                },
                performance: {
                    voiceKey: 'handoff_plugin_dashboard',
                    cursorAction: 'click',
                    cursorTarget: '#${p}-btn-agent',
                    cursorSpeedMultiplier: 1.05
                },
                navigation: {
                    openUrl: '/ui/',
                    windowName: 'plugin_dashboard',
                    resumeScene: 'plugin_dashboard_landing'
                }
            },
            plugin_dashboard_landing: {
                page: 'plugin_dashboard',
                anchor: '#plugin-list',
                tutorial: {
                    title: '插件面板落点',
                    description: '从首页接力后，在插件面板落到插件列表区域。'
                },
                performance: {
                    bubbleText: '这里就是插件管理面板，我先带你看看插件列表和右侧的能力区。',
                    voiceKey: 'plugin_dashboard_landing',
                    emotion: 'happy',
                    cursorAction: 'wobble',
                    cursorTarget: '#plugin-list'
                }
            },
            api_key_intro: {
                page: 'api_key',
                anchor: '#coreApiSelect-dropdown-trigger',
                tutorial: {
                    title: 'API 密钥入口',
                    description: '从首页接力后，先确认核心 API 服务商入口。'
                },
                performance: {
                    bubbleText: '到啦，这里就是 API 密钥设置页。先把核心服务商选好，我就能更稳地陪你聊天啦。',
                    voiceKey: 'api_key_intro',
                    emotion: 'happy',
                    cursorAction: 'wobble',
                    cursorTarget: '#coreApiSelect-dropdown-trigger'
                }
            },
            memory_browser_intro: {
                page: 'memory_browser',
                anchor: '#memory-file-list',
                tutorial: {
                    title: '记忆浏览入口',
                    description: '从首页接力后，先查看猫娘记忆库。'
                },
                performance: {
                    bubbleText: '这里会整理我们聊过的重要内容喵。先从左边这份记忆库开始看起吧。',
                    voiceKey: 'memory_browser_intro',
                    emotion: 'happy',
                    cursorAction: 'wobble',
                    cursorTarget: '#memory-file-list'
                }
            }
        },
        audioFileNames: audioFileNames,
        audioFilesByKey: {
            intro_basic: audioFilesForAllLocales(audioFileNames.intro_basic),
            intro_greeting_reply: audioFilesForAllLocales(audioFileNames.intro_greeting_reply),
            takeover_capture_cursor: audioFilesForAllLocales(audioFileNames.takeover_capture_cursor),
            takeover_plugin_preview_home: audioFilesForAllLocales(audioFileNames.takeover_plugin_preview_home),
            takeover_plugin_preview_dashboard: audioFilesForAllLocales(audioFileNames.takeover_plugin_preview_dashboard),
            takeover_settings_peek_intro: audioFilesForAllLocales(audioFileNames.takeover_settings_peek_intro),
            takeover_settings_peek_detail: audioFilesForAllLocales(audioFileNames.takeover_settings_peek_detail),
            interrupt_resist_light_1: audioFilesForAllLocales(audioFileNames.interrupt_resist_light_1),
            interrupt_resist_light_3: audioFilesForAllLocales(audioFileNames.interrupt_resist_light_3),
            interrupt_angry_exit: audioFilesForAllLocales(audioFileNames.interrupt_angry_exit),
            takeover_return_control: audioFilesForAllLocales(audioFileNames.takeover_return_control),
            day1_capsule_drag_hint: audioFilesForAllLocales(audioFileNames.day1_capsule_drag_hint),
            day1_history_handle: audioFilesForAllLocales(audioFileNames.day1_history_handle),
            day1_screen_entry: audioFilesForAllLocales(audioFileNames.day1_screen_entry),
            day1_screen_entry_invite: audioFilesForAllLocales(audioFileNames.day1_screen_entry_invite)
        },
        audioFileOverridesByKey: {}
    }));
})();
