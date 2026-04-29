(function () {
    // Shared contract file owned by the main integrator.
    // Dev B edits performance blocks. Dev C edits anchor/navigation blocks.
    // If you need to change field shape or scene IDs, update the freeze doc first.

    const CONTRACT_VERSION = 2;
    const PAGE_KEYS = Object.freeze([
        'home',
        'api_key',
        'memory_browser',
        'plugin_dashboard'
    ]);

    const HOME_SCENE_ORDER = Object.freeze([
        'intro_basic',
        'intro_proactive',
        'intro_cat_paw',
        'takeover_capture_cursor',
        'takeover_plugin_preview',
        'takeover_settings_peek',
        'takeover_return_control',
        'interrupt_resist_light',
        'interrupt_angry_exit',
        'handoff_api_key',
        'handoff_memory_browser',
        'handoff_plugin_dashboard'
    ]);

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

    function createBaseStep(id, page, anchor) {
        return {
            id: id,
            page: page,
            anchor: anchor,
            tutorial: {
                title: '',
                description: '',
                autoAdvance: false,
                allowUserInteraction: false
            },
            performance: {
                bubbleText: '',
                bubbleTextKey: '',
                voiceKey: '',
                emotion: 'neutral',
                cursorAction: 'none',
                cursorTarget: '',
                settingsMenuId: '',
                cursorSpeedMultiplier: 1,
                delayMs: 0,
                interruptible: false,
                resistanceVoices: [],
                resistanceVoiceKeys: []
            },
            navigation: {
                openUrl: '',
                windowName: '',
                resumeScene: null
            },
            interrupts: {
                mode: 'ignore',
                threshold: 3,
                throttleMs: 500,
                resetOnStepAdvance: true
            }
        };
    }

    const steps = {};

    steps.intro_basic = createBaseStep('intro_basic', 'home', '#text-input-area');
    steps.intro_basic.tutorial.title = '语音控制入口';
    steps.intro_basic.tutorial.description = '点击输入框解锁首句旁白后，介绍对话窗里的语音控制按钮。';
    steps.intro_basic.performance.bubbleText = '这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新鲜事吗？或者只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！喵！';
    steps.intro_basic.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.introBasic';
    steps.intro_basic.performance.voiceKey = 'intro_basic';
    steps.intro_basic.performance.emotion = 'happy';
    steps.intro_basic.performance.cursorTarget = '#${p}-btn-mic';
    steps.intro_basic.performance.interruptible = true;
    steps.intro_basic.interrupts.mode = 'theatrical_abort';
    steps.intro_basic.interrupts.resetOnStepAdvance = false;

    steps.intro_proactive = createBaseStep('intro_proactive', 'home', '#${p}-toggle-proactive-chat');
    steps.intro_proactive.tutorial.title = '主动搭话';
    steps.intro_proactive.tutorial.description = '介绍主动搭话开关。';
    steps.intro_proactive.performance.bubbleText = '可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）';
    steps.intro_proactive.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.introProactive';
    steps.intro_proactive.performance.voiceKey = 'intro_proactive';
    steps.intro_proactive.performance.emotion = 'surprised';
    steps.intro_proactive.performance.cursorAction = 'wobble';
    steps.intro_proactive.performance.cursorTarget = '#${p}-toggle-proactive-chat';
    steps.intro_proactive.performance.interruptible = true;
    steps.intro_proactive.interrupts.mode = 'theatrical_abort';
    steps.intro_proactive.interrupts.resetOnStepAdvance = false;

    steps.intro_cat_paw = createBaseStep('intro_cat_paw', 'home', '#${p}-btn-agent');
    steps.intro_cat_paw.tutorial.title = '猫爪入口';
    steps.intro_cat_paw.tutorial.description = '介绍 OpenClaw/猫爪面板入口。';
    steps.intro_cat_paw.performance.bubbleText = '好啦不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！';
    steps.intro_cat_paw.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.introCatPaw';
    steps.intro_cat_paw.performance.voiceKey = 'intro_cat_paw';
    steps.intro_cat_paw.performance.emotion = 'happy';
    steps.intro_cat_paw.performance.cursorAction = 'wobble';
    steps.intro_cat_paw.performance.cursorTarget = '#${p}-btn-agent';
    steps.intro_cat_paw.performance.interruptible = true;
    steps.intro_cat_paw.interrupts.mode = 'theatrical_abort';
    steps.intro_cat_paw.interrupts.resetOnStepAdvance = false;

    steps.takeover_capture_cursor = createBaseStep('takeover_capture_cursor', 'home', '#${p}-btn-agent');
    steps.takeover_capture_cursor.tutorial.title = '键鼠控制介绍';
    steps.takeover_capture_cursor.tutorial.description = '介绍猫爪总开关与键鼠控制开关，并完成首页第一段自动化演示。';
    steps.takeover_capture_cursor.performance.bubbleText = '超级魔法按钮出现！只要点一下这里，我就可以把小爪子伸到你的键盘和鼠标上啦！我会帮你打字，帮你点开网页……不过，要是那个鼠标指针动来动去的话，我可能也会忍不住扑上去抓它哦！准备好迎接我的捣乱……啊不，是帮忙了吗？喵！';
    steps.takeover_capture_cursor.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.takeoverCaptureCursor';
    steps.takeover_capture_cursor.performance.voiceKey = 'takeover_capture_cursor';
    steps.takeover_capture_cursor.performance.emotion = 'happy';
    steps.takeover_capture_cursor.performance.cursorAction = 'click';
    steps.takeover_capture_cursor.performance.cursorTarget = '#${p}-btn-agent';
    steps.takeover_capture_cursor.performance.interruptible = true;
    steps.takeover_capture_cursor.interrupts.mode = 'theatrical_abort';
    steps.takeover_capture_cursor.interrupts.resetOnStepAdvance = false;

    steps.takeover_plugin_preview = createBaseStep('takeover_plugin_preview', 'home', '#${p}-btn-agent');
    steps.takeover_plugin_preview.tutorial.title = '系统设置自动化演示';
    steps.takeover_plugin_preview.tutorial.description = '从已打开的猫爪面板继续演示用户插件与管理面板入口。';
    steps.takeover_plugin_preview.performance.bubbleText = '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！';
    steps.takeover_plugin_preview.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.takeoverPluginPreviewHome';
    steps.takeover_plugin_preview.performance.voiceKey = 'takeover_plugin_preview_home';
    steps.takeover_plugin_preview.performance.emotion = 'happy';
    steps.takeover_plugin_preview.performance.cursorAction = 'click';
    steps.takeover_plugin_preview.performance.cursorTarget = '#${p}-btn-agent';
    steps.takeover_plugin_preview.performance.interruptible = true;
    steps.takeover_plugin_preview.interrupts.mode = 'theatrical_abort';
    steps.takeover_plugin_preview.interrupts.resetOnStepAdvance = false;

    steps.takeover_settings_peek = createBaseStep('takeover_settings_peek', 'home', '#${p}-btn-settings');
    steps.takeover_settings_peek.tutorial.title = '设置一瞥';
    steps.takeover_settings_peek.tutorial.description = '只浏览首页真实设置弹层及真实菜单项。';
    steps.takeover_settings_peek.performance.bubbleText = '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。';
    steps.takeover_settings_peek.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.takeoverSettingsPeekIntro';
    steps.takeover_settings_peek.performance.voiceKey = 'takeover_settings_peek_intro';
    steps.takeover_settings_peek.performance.emotion = 'surprised';
    steps.takeover_settings_peek.performance.cursorAction = 'click';
    steps.takeover_settings_peek.performance.cursorTarget = '#${p}-btn-settings';
    steps.takeover_settings_peek.performance.settingsMenuId = 'character';
    steps.takeover_settings_peek.performance.interruptible = true;
    steps.takeover_settings_peek.interrupts.mode = 'theatrical_abort';
    steps.takeover_settings_peek.interrupts.resetOnStepAdvance = false;

    steps.takeover_return_control = createBaseStep('takeover_return_control', 'home', '#${p}-container');
    steps.takeover_return_control.tutorial.title = '归还控制权';
    steps.takeover_return_control.tutorial.description = '收掉临时演出层并把控制权完整交还给用户。';
    steps.takeover_return_control.performance.bubbleText = '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～';
    steps.takeover_return_control.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.takeoverReturnControl';
    steps.takeover_return_control.performance.voiceKey = 'takeover_return_control';
    steps.takeover_return_control.performance.emotion = 'happy';
    steps.takeover_return_control.performance.cursorAction = 'wobble';
    steps.takeover_return_control.performance.cursorTarget = '#${p}-container';
    steps.takeover_return_control.performance.interruptible = true;
    steps.takeover_return_control.interrupts.mode = 'theatrical_abort';
    steps.takeover_return_control.interrupts.resetOnStepAdvance = false;

    steps.interrupt_resist_light = createBaseStep('interrupt_resist_light', 'home', '#${p}-container');
    steps.interrupt_resist_light.tutorial.title = '轻微抵抗';
    steps.interrupt_resist_light.tutorial.description = '用户轻微试探时的较劲反馈。';
    steps.interrupt_resist_light.performance.bubbleText = '喂！不要拽我啦，还没轮到你的回合呢！';
    steps.interrupt_resist_light.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.interruptResistLight1';
    steps.interrupt_resist_light.performance.voiceKey = 'interrupt_resist_light';
    steps.interrupt_resist_light.performance.emotion = 'surprised';
    steps.interrupt_resist_light.performance.cursorAction = 'wobble';
    steps.interrupt_resist_light.performance.cursorTarget = '#${p}-container';
    steps.interrupt_resist_light.performance.interruptible = true;
    steps.interrupt_resist_light.performance.resistanceVoices = [
        '喂！不要拽我啦，还没轮到你的回合呢！',
        '等一下啦！还没结束呢，不要随便打断我啦！'
    ];
    steps.interrupt_resist_light.performance.resistanceVoiceKeys = [
        'tutorial.yuiGuide.lines.interruptResistLight1',
        'tutorial.yuiGuide.lines.interruptResistLight3'
    ];
    steps.interrupt_resist_light.interrupts.mode = 'theatrical_abort';

    steps.interrupt_angry_exit = createBaseStep('interrupt_angry_exit', 'home', '#${p}-container');
    steps.interrupt_angry_exit.tutorial.title = '生气退出';
    steps.interrupt_angry_exit.tutorial.description = '连续有效打断达到阈值后，进入带演出的 angry exit。';
    steps.interrupt_angry_exit.performance.bubbleText = '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！';
    steps.interrupt_angry_exit.performance.bubbleTextKey = 'tutorial.yuiGuide.lines.interruptAngryExit';
    steps.interrupt_angry_exit.performance.voiceKey = 'interrupt_angry_exit';
    steps.interrupt_angry_exit.performance.emotion = 'angry';
    steps.interrupt_angry_exit.performance.cursorAction = 'none';
    steps.interrupt_angry_exit.performance.cursorTarget = '#${p}-container';
    steps.interrupt_angry_exit.performance.interruptible = true;
    steps.interrupt_angry_exit.interrupts.mode = 'theatrical_abort';

    steps.handoff_api_key = createBaseStep('handoff_api_key', 'home', '#${p}-menu-api-keys');
    steps.handoff_api_key.tutorial.title = '接力到 API 密钥';
    steps.handoff_api_key.tutorial.description = '从首页接力到 API 密钥页。';
    steps.handoff_api_key.tutorial.autoAdvance = true;
    steps.handoff_api_key.performance.voiceKey = 'handoff_api_key';
    steps.handoff_api_key.performance.cursorAction = 'click';
    steps.handoff_api_key.performance.cursorTarget = '#${p}-menu-api-keys';
    steps.handoff_api_key.navigation.openUrl = '/api_key';
    steps.handoff_api_key.navigation.windowName = 'api_key';
    steps.handoff_api_key.navigation.resumeScene = 'api_key_intro';

    steps.handoff_memory_browser = createBaseStep('handoff_memory_browser', 'home', '#${p}-menu-memory');
    steps.handoff_memory_browser.tutorial.title = '接力到记忆浏览';
    steps.handoff_memory_browser.tutorial.description = '从首页接力到记忆浏览页。';
    steps.handoff_memory_browser.tutorial.autoAdvance = true;
    steps.handoff_memory_browser.performance.voiceKey = 'handoff_memory_browser';
    steps.handoff_memory_browser.performance.cursorAction = 'click';
    steps.handoff_memory_browser.performance.cursorTarget = '#${p}-menu-memory';
    steps.handoff_memory_browser.navigation.openUrl = '/memory_browser';
    steps.handoff_memory_browser.navigation.windowName = 'memory_browser';
    steps.handoff_memory_browser.navigation.resumeScene = 'memory_browser_intro';

    steps.handoff_plugin_dashboard = createBaseStep('handoff_plugin_dashboard', 'home', '#${p}-btn-agent');
    steps.handoff_plugin_dashboard.tutorial.title = '接力到插件面板';
    steps.handoff_plugin_dashboard.tutorial.description = '从首页接力到插件 dashboard，再转入 /ui。';
    steps.handoff_plugin_dashboard.tutorial.autoAdvance = true;
    steps.handoff_plugin_dashboard.performance.voiceKey = 'handoff_plugin_dashboard';
    steps.handoff_plugin_dashboard.performance.cursorAction = 'click';
    steps.handoff_plugin_dashboard.performance.cursorTarget = '#${p}-btn-agent';
    steps.handoff_plugin_dashboard.navigation.openUrl = '/ui/';
    steps.handoff_plugin_dashboard.navigation.windowName = 'plugin_dashboard';
    steps.handoff_plugin_dashboard.navigation.resumeScene = 'plugin_dashboard_landing';

    steps.plugin_dashboard_landing = createBaseStep('plugin_dashboard_landing', 'plugin_dashboard', '#plugin-list');
    steps.plugin_dashboard_landing.tutorial.title = '插件面板落点';
    steps.plugin_dashboard_landing.tutorial.description = '从首页接力后，在插件面板落到插件列表区域。';
    steps.plugin_dashboard_landing.performance.bubbleText = '这里就是插件管理面板，我先带你看看插件列表和右侧的能力区。';
    steps.plugin_dashboard_landing.performance.voiceKey = 'plugin_dashboard_landing';
    steps.plugin_dashboard_landing.performance.emotion = 'happy';
    steps.plugin_dashboard_landing.performance.cursorAction = 'wobble';
    steps.plugin_dashboard_landing.performance.cursorTarget = '#plugin-list';

    steps.api_key_intro = createBaseStep('api_key_intro', 'api_key', '#coreApiSelect-dropdown-trigger');
    steps.api_key_intro.tutorial.title = 'API 密钥入口';
    steps.api_key_intro.tutorial.description = '从首页接力后，先确认核心 API 服务商入口。';
    steps.api_key_intro.performance.bubbleText = '到啦，这里就是 API 密钥设置页。先把核心服务商选好，我就能更稳地陪你聊天啦。';
    steps.api_key_intro.performance.voiceKey = 'api_key_intro';
    steps.api_key_intro.performance.emotion = 'happy';
    steps.api_key_intro.performance.cursorAction = 'wobble';
    steps.api_key_intro.performance.cursorTarget = '#coreApiSelect-dropdown-trigger';

    steps.memory_browser_intro = createBaseStep('memory_browser_intro', 'memory_browser', '#memory-file-list');
    steps.memory_browser_intro.tutorial.title = '记忆浏览入口';
    steps.memory_browser_intro.tutorial.description = '从首页接力后，先查看猫娘记忆库。';
    steps.memory_browser_intro.performance.bubbleText = '这里会整理我们聊过的重要内容喵。先从左边这份记忆库开始看起吧。';
    steps.memory_browser_intro.performance.voiceKey = 'memory_browser_intro';
    steps.memory_browser_intro.performance.emotion = 'happy';
    steps.memory_browser_intro.performance.cursorAction = 'wobble';
    steps.memory_browser_intro.performance.cursorTarget = '#memory-file-list';

    const sceneOrder = {
        home: HOME_SCENE_ORDER.slice(),
        api_key: ['api_key_intro'],
        memory_browser: ['memory_browser_intro'],
        plugin_dashboard: ['plugin_dashboard_landing']
    };

    const DEV_MODE = !!(
        typeof window !== 'undefined'
        && window.location
        && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    );

    if (DEV_MODE) {
        Object.keys(sceneOrder).forEach(function (page) {
            (sceneOrder[page] || []).forEach(function (id) {
                if (!steps[id]) {
                    console.warn('[YuiGuideSteps] sceneOrder 引用了未定义步骤:', page, id);
                }
            });
        });
    }

    const registry = {
        contractVersion: CONTRACT_VERSION,
        pageKeys: PAGE_KEYS.slice(),
        sceneOrder: sceneOrder,
        steps: steps,
        getPageSteps: function (page) {
            const order = sceneOrder[page] || [];
            return order.map(function (id) {
                return steps[id];
            }).filter(Boolean);
        },
        getStep: function (id) {
            return steps[id] || null;
        },
        hasStep: function (id) {
            return Object.prototype.hasOwnProperty.call(steps, id);
        }
    };

    deepFreeze(sceneOrder);
    deepFreeze(steps);
    deepFreeze(registry);

    window.YuiGuideStepsRegistry = registry;
    window.getYuiGuideStepsRegistry = function () {
        return registry;
    };
})();
