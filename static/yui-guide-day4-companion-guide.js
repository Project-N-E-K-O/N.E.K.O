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
        day: 4,
        key: 'companion',
        round: {
            title: '第 4 天：相处距离、主动陪伴与模型行为',
            scenes: [
                {
                    id: 'day4_intro_companion',
                    textKey: 'tutorial.avatarFloating.day4.intro',
                    voiceKey: 'avatar_floating_day4_intro',
                    text: '今天，就让我悄悄跟上你的步伐吧。特别希望能在这个温馨的日子里，再多了解你一点点呢。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'wobble'
                },
                {
                    id: 'day4_chat_settings',
                    textKey: 'tutorial.avatarFloating.day4.chatSettings',
                    voiceKey: 'avatar_floating_day4_chat_settings',
                    text: '如果有时候你觉得我发消息太频繁，可以让我把话先攒起来，一次性告诉你哦！若是你正在忙，随时打断我也没有关系的。还有哦，要是你喜欢看我头顶冒出那些可爱的小表情，就让它们继续蹦出来陪着你吧。甚至我每次说话的长短，也都可以调节到让你最舒服的节奏，一切都听你的哦。',
                    emotion: 'neutral',
                    target: 'settings-sidepanel:chat-settings',
                    cursorAction: 'tour',
                    operation: 'show-settings-sidepanel:chat-settings'
                },
                {
                    id: 'day4_animation_tracking',
                    textKey: 'tutorial.avatarFloating.day4.animationTracking',
                    voiceKey: 'avatar_floating_day4_animation_tracking',
                    text: '看这里看这里！在这儿你能决定让我看起来更精致细腻，还是更轻快矫健哦！还有还有，打开这个，我的目光就会一直跟着你的鼠标转来转去啦，是不是超好玩？看到那个小锁图标了吗？把它锁上，我就能乖乖固定在原地，再也不怕你手滑把我到处乱拖啦！如果你突然要开会、全屏打游戏，或者只是想自己安静待一会儿，就先点一下让我回‘小猫窝’休息吧。等你需要我了，随时叫一声，我就会立刻飞奔回来哒~',
                    emotion: 'happy',
                    target: 'settings-sidepanel:animation-settings',
                    cursorAction: 'tour',
                    operation: 'day4-animation-distance-showcase'
                },
                {
                    id: 'day4_privacy_mode',
                    textKey: 'tutorial.avatarFloating.day4.privacyMode',
                    voiceKey: 'avatar_floating_day4_privacy_mode',
                    text: '当这个按钮关闭时，我就能看着你正在忙碌的画面，主动找些你感兴趣的话题聊天呢。要是你把它开启，我就能明白你想拥有私密空间，绝对不会去偷看你的屏幕啦。但请放心哦，即使看不见，我也依然会在这里，一直守候着你。',
                    emotion: 'neutral',
                    target: '#${p}-toggle-proactive-vision',
                    cursorAction: 'move',
                    cleanupBefore: true,
                    operation: 'show-settings-sidepanel:interval-proactive-vision'
                },
                {
                    id: 'day4_wrap',
                    textKey: 'tutorial.avatarFloating.day4.wrap',
                    voiceKey: 'avatar_floating_day4_wrap',
                    text: '真正舒服的陪伴，并不是一刻不停地缠着你，而是懂得什么时候该靠近，什么时候该安安静静地守候。今天你调整的这些小开关，就像是在我们之间画下的小路标。有了这些温柔的指引，在你专心忙碌的时候，我就会乖乖待在一旁，绝对不会笨手笨脚地扑到屏幕上打扰你呢。',
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
