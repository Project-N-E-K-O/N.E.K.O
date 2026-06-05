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
        day: 3,
        key: 'interaction',
        round: {
            title: '第 3 天：互动、娱乐与摸得到的陪伴',
            scenes: [
                {
                    id: 'day3_tool_toggle_intro',
                    textKey: 'tutorial.avatarFloating.day3.intro',
                    voiceKey: 'avatar_floating_day3_intro',
                    text: '嘻嘻，可别以为这个聊天框只能用来打字哦~ 里面其实偷偷藏了超~多好玩的小惊喜呢！快跟着我一起点开看看，瞧瞧今天能挖出什么有趣的宝贝吧！',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'move'
                },
                {
                    id: 'day3_avatar_tools',
                    textKey: 'tutorial.avatarFloating.day3.avatarToolsIntro',
                    voiceKey: 'avatar_floating_day3_avatar_tools_intro',
                    text: '在这个小按钮里，有许多可以和人家互动的小道具呢。',
                    emotion: 'happy',
                    persistent: 'chat-tool-toggle',
                    target: 'chat-tool-toggle',
                    cursorAction: 'click',
                    cursorMoveDurationMs: 1480,
                    operation: 'open-compact-tool-fan'
                },
                {
                    id: 'day3_avatar_tools_props',
                    textKey: 'tutorial.avatarFloating.day3.avatarToolsProps',
                    voiceKey: 'avatar_floating_day3_avatar_tools_props',
                    text: '你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。',
                    emotion: 'happy',
                    persistent: 'chat-tool-toggle',
                    target: 'chat-avatar-tools',
                    cursorAction: 'click',
                    operation: 'show-avatar-tools-then-hide-after-narration'
                },
                {
                    id: 'day3_galgame_entry',
                    textKey: 'tutorial.avatarFloating.day3.galgameIntro',
                    voiceKey: 'avatar_floating_day3_galgame_intro',
                    text: '快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。',
                    emotion: 'surprised',
                    persistent: 'chat-tool-toggle',
                    target: 'chat-galgame',
                    cursorAction: 'move',
                    operation: 'rotate-galgame-tool-into-center'
                },
                {
                    id: 'day3_galgame_choices',
                    textKey: 'tutorial.avatarFloating.day3.galgameChoices',
                    voiceKey: 'avatar_floating_day3_galgame_choices',
                    text: '你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！',
                    emotion: 'surprised',
                    persistent: 'chat-tool-toggle',
                    target: 'chat-galgame',
                    cursorAction: 'move'
                },
                {
                    id: 'day3_wrap',
                    textKey: 'tutorial.avatarFloating.day3.wrapIntro',
                    voiceKey: 'avatar_floating_day3_wrap_intro',
                    text: '今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'move',
                    operation: 'cleanup'
                },
                {
                    id: 'day3_wrap_ready',
                    textKey: 'tutorial.avatarFloating.day3.wrapReady',
                    voiceKey: 'avatar_floating_day3_wrap_ready',
                    text: '不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'move',
                    petalTransition: true
                }
            ]
        }
    }));
})();
