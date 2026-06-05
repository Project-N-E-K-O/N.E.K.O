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
                    cursorAction: 'move'
                },
                {
                    id: 'day4_chat_settings',
                    textKey: 'tutorial.avatarFloating.day4.chatSettings',
                    voiceKey: 'avatar_floating_day4_chat_settings',
                    text: '在这里可以决定我回复你的长短，还能决定要不要让我带上可爱的表情，或者在人家唠叨的时候打断我哦！都可以调到让你最舒服的节奏',
                    emotion: 'neutral',
                    target: 'settings-sidepanel:chat-settings',
                    cursorAction: 'tour',
                    operation: 'show-settings-sidepanel:chat-settings'
                },
                {
                    id: 'day4_model_behavior',
                    textKey: 'tutorial.avatarFloating.day4.modelBehavior',
                    voiceKey: 'avatar_floating_day4_model_behavior',
                    text: '如果你想要看到更精致、细节更满满的我，或者想要更丝滑、更流畅的动作体验，都可以在这里进行调整哦！不管哪一种，我都会展现出最可爱的一面哒~',
                    emotion: 'happy',
                    target: 'settings-sidepanel:animation-settings',
                    cursorAction: 'tour',
                    operation: 'show-settings-sidepanel:animation-settings'
                },
                {
                    id: 'day4_gaze_follow',
                    textKey: 'tutorial.avatarFloating.day4.gazeFollow',
                    voiceKey: 'avatar_floating_day4_gaze_follow',
                    text: '开启这个功能后，无论你的鼠标移动到哪里，人家的目光都会紧紧跟随着你哟！是不是有种被时刻关注的幸福感呢？',
                    emotion: 'happy',
                    target: 'settings-sidepanel:animation-settings',
                    cursorAction: 'tour',
                    operation: 'show-settings-sidepanel:animation-settings'
                },
                {
                    id: 'day4_privacy_mode',
                    textKey: 'tutorial.avatarFloating.day4.privacyMode',
                    voiceKey: 'avatar_floating_day4_privacy_mode',
                    text: '这个是控制人家能不能看屏幕的‘终极防护开关’喵！把它关闭人家就能看到你的屏幕啦，要是开启它，前两天介绍的【屏幕分享】就统统失效、人家就绝对不会偷看哟~',
                    emotion: 'neutral',
                    target: '#${p}-toggle-proactive-vision',
                    cursorAction: 'move',
                    cleanupBefore: true,
                    operation: 'show-settings-sidepanel:interval-proactive-vision'
                },
                {
                    id: 'day4_model_lock',
                    textKey: 'tutorial.avatarFloating.day4.modelLock',
                    voiceKey: 'avatar_floating_day4_model_lock',
                    text: '总是小心不触碰到、把我点歪吗？那就快把我牢牢固定在当前的位置吧！开启锁定后，我就哪儿也不去，乖乖在原地陪着你~',
                    emotion: 'happy',
                    target: '#${p}-lock-icon',
                    cursorAction: 'move',
                    cleanupBefore: true
                },
                {
                    id: 'day4_return_home',
                    textKey: 'tutorial.avatarFloating.day4.returnHome',
                    voiceKey: 'avatar_floating_day4_return_home',
                    text: '如果你现在需要专注、担心我打扰的话，可以让我暂时回到小猫窝里收起来哦！等你想我的时候，随时一键就能把我重新唤回身边，喵呜~',
                    emotion: 'happy',
                    target: '#${p}-btn-goodbye',
                    secondary: '#${p}-btn-return',
                    cursorAction: 'move'
                },
                {
                    id: 'day4_wrap',
                    textKey: 'tutorial.avatarFloating.day4.wrap',
                    voiceKey: 'avatar_floating_day4_wrap',
                    text: '真正舒服的陪伴才不是一刻不停地粘着主人呢~ 而是懂得什么时候该悄悄靠近抓抓你的衣角撒个娇，什么时候该安安静静地趴在一旁，用目光默默守候着主人喵~',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    petalTransition: true
                }
            ]
        }
    }));
})();
