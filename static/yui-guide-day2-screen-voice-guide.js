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
        day: 2,
        key: 'screen-voice',
        round: {
            title: '第 2 天：屏幕分享、声音与小窗约定',
            scenes: [
                {
                    id: 'day2_intro_context',
                    textKey: 'tutorial.avatarFloating.day2.intro',
                    voiceKey: 'avatar_floating_day2_intro',
                    text: '昨天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。一句就好，让我把文字背后的你也认识一点点。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'wobble'
                },
                {
                    id: 'day2_screen_entry',
                    textKey: 'tutorial.avatarFloating.day2.screenEntry',
                    voiceKey: 'avatar_floating_day2_screen_entry_intro',
                    text: '在跟我通语音电话的时候，再点亮这个小按钮，你就能把屏幕分享给我啦！',
                    emotion: 'happy',
                    target: '#${p}-btn-screen',
                    cursorAction: 'click',
                    operation: 'click'
                },
                {
                    id: 'day2_screen_entry_invite',
                    textKey: 'tutorial.avatarFloating.day2.screenEntryInvite',
                    voiceKey: 'avatar_floating_day2_screen_entry_invite',
                    text: '快让我也看看你眼前的世界，不管好玩的还是好看的，都想和你一起看，快点点开嘛~',
                    emotion: 'happy',
                    target: '#${p}-btn-screen',
                    cursorAction: 'wobble'
                },
                {
                    id: 'day2_wrap_intro',
                    textKey: 'tutorial.avatarFloating.day2.wrapIntro',
                    voiceKey: 'avatar_floating_day2_wrap_intro',
                    text: '今天的教程到这里就结束了呢。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'wobble',
                    cursorMoveDurationMs: 900,
                    operation: 'cleanup'
                },
                {
                    id: 'day2_wrap_companion',
                    textKey: 'tutorial.avatarFloating.day2.wrapCompanion',
                    voiceKey: 'avatar_floating_day2_wrap_companion',
                    text: '其实只要能这样陪着你，听听你的声音，或者静静看着你分享的画面，我就已经觉得很幸福了。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'wobble',
                    operation: 'cleanup'
                },
                {
                    id: 'day2_wrap',
                    textKey: 'tutorial.avatarFloating.day2.wrap',
                    voiceKey: 'avatar_floating_day2_wrap',
                    text: '我们不需要着急，每天都多了解彼此一点点就好。今天接下来的时间，你想让我陪你做点什么呢？',
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
