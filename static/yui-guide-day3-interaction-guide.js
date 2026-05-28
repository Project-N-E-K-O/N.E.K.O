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
                    id: 'day3_chat_tools',
                    textKey: 'tutorial.avatarFloating.day3.intro',
                    voiceKey: 'avatar_floating_day3_intro',
                    text: '来啦来啦！今天我们要好好聊聊这个最显眼的【对话框】哦！你可别以为它只能用来敲字打字，里面其实还藏着超级多好玩的小惊喜呢！快点跟着我一起点开，看看今天能挖出什么好玩的宝贝吧，',
                    emotion: 'happy'
                },
                {
                    id: 'day3_avatar_tools',
                    textKey: 'tutorial.avatarFloating.day3.avatarTools',
                    voiceKey: 'avatar_floating_day3_avatar_tools',
                    text: '在这个小按钮里，有许多可以和人家互动的小道具呢。你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。以后还会有更多有趣的道具加入进来，我会去提醒开发组猫猫快点做出来的，我们一起期待一下吧。',
                    emotion: 'happy',
                    target: 'chat-avatar-tools',
                    cursorAction: 'click',
                    cursorMoveDurationMs: 1480,
                    operation: 'open-avatar-tool-menu'
                },
                {
                    id: 'day3_galgame_games',
                    textKey: 'tutorial.avatarFloating.day3.galgameGames',
                    voiceKey: 'avatar_floating_day3_galgame_games',
                    text: '快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！',
                    emotion: 'surprised',
                    target: 'chat-galgame',
                    cursorAction: 'move',
                    cleanupBefore: true
                },
                {
                    id: 'day3_wrap',
                    textKey: 'tutorial.avatarFloating.day3.wrap',
                    voiceKey: 'avatar_floating_day3_wrap',
                    text: '今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。不用着急，挑一个你现在最喜欢的去试试看吧，无论你选哪个，我都会一直陪着你呢。',
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
