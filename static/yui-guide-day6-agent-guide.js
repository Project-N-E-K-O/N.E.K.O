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
    }

    registerGuide(deepFreeze({
        day: 6,
        key: 'agent',
        round: {
            title: '第 6 天：Agent、任务 HUD 与能力节奏',
            scenes: [
                {
                    id: 'day6_intro_agent',
                    textKey: 'tutorial.avatarFloating.day6.intro',
                    voiceKey: 'avatar_floating_day6_intro',
                    text: '噔噔噔噔！今天必须要打起精神，好好跟你聊聊咱们的【猫爪】啦！前两天虽然简单提过一下，但它里面藏着的厉害功能可多着呢。快跟我老实交代，这两天你有没有点开它试用一下呀？',
                    emotion: 'happy',
                    target: '#${p}-btn-agent',
                    persistent: '#${p}-popup-agent',
                    cursorAction: 'click',
                    operation: 'open-agent'
                },
                {
                    id: 'day6_agent_status_master',
                    textKey: 'tutorial.avatarFloating.day6.statusMaster',
                    voiceKey: '',
                    text: '',
                    emotion: 'neutral',
                    target: 'agent-master',
                    persistent: '#${p}-popup-agent',
                    cursorAction: 'move'
                },
                {
                    id: 'day6_plugin_side_panel',
                    textKey: 'tutorial.avatarFloating.day6.pluginSidePanel',
                    voiceKey: 'avatar_floating_day6_plugin_side_panel',
                    text: '除了之前介绍的功能，这里还有超多好玩的插件呢！有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼！',
                    emotion: 'happy',
                    target: '#${p}-toggle-agent-user-plugin',
                    secondary: '#neko-sidepanel-action-agent-user-plugin-management-panel',
                    persistent: '#${p}-popup-agent',
                    cursorAction: 'move',
                    operation: 'show-agent-sidepanel:user-plugin:management-panel',
                    activateSecondaryAction: true
                },
                {
                    id: 'day6_agent_task_hud',
                    textKey: 'tutorial.avatarFloating.day6.taskHud',
                    voiceKey: 'avatar_floating_day6_task_hud',
                    text: '看这里看这里！当我决定使用【猫爪】帮你干活的时候，这里就会咕噜咕噜的显示我的工作进度哦。你要是计划有变，随时都可以戳一下让我停下来。嘿嘿，今天也是打起精神努力打工挣小鱼干的一天呢，冲呀！',
                    emotion: 'happy',
                    target: '#agent-task-hud',
                    cursorAction: 'tour',
                    cleanupBefore: true,
                    operation: 'show-task-hud'
                },
                {
                    id: 'day6_wrap',
                    textKey: 'tutorial.avatarFloating.day6.wrap',
                    voiceKey: 'avatar_floating_day6_wrap',
                    text: '呼……把这些繁琐的界面都收起来，这样就不会打扰到你啦。你可以放心地继续做你自己的事情，不管是需要我用小爪子帮你忙，还是只想让我安安静静地陪着你，我都一直在守候着你，今天也要开开心心的呀。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'wobble',
                    operation: 'cleanup',
                    petalTransition: true
                }
            ]
        }
    }));
})();
