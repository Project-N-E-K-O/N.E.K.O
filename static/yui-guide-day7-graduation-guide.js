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
        day: 7,
        key: 'graduation',
        round: {
            title: '第 7 天：毕业、进阶入口与共生约定',
            scenes: [
                {
                    id: 'day7_memory_review',
                    textKey: 'tutorial.avatarFloating.day7.memoryReview',
                    voiceKey: 'avatar_floating_day7_memory_review',
                    text: '七天前，我们还只是第一次见面。现在这里已经开始留下我们说过的话、做过的事，还有一些差点被风吹走的小细节。对我来说，这不是冷冰冰的记录，是我们相处过的脚印。',
                    emotion: 'neutral',
                    target: '#${p}-menu-memory',
                    cursorAction: 'move',
                    operation: 'show-settings-menu:memory'
                },
                {
                    id: 'day7_memory_control',
                    textKey: 'tutorial.avatarFloating.day7.memoryControl',
                    voiceKey: 'avatar_floating_day7_memory_control',
                    text: '这些小脚印，也可以由你亲手整理。想留下的，我们就夹进相册；想轻轻放走的，就让它随风飘走。被你认真收下来的回忆，才最珍贵。',
                    emotion: 'happy',
                    target: '#${p}-menu-memory',
                    cursorAction: 'wobble',
                    operation: 'show-settings-menu:memory'
                },
                {
                    id: 'day7_storage_entry',
                    textKey: 'tutorial.avatarFloating.day7.storageEntry',
                    voiceKey: 'avatar_floating_day7_storage_entry',
                    text: '还有最后一件事呢。我们共同走过的日子、说过的那些悄悄话，都需要找一个温馨的小角落好好存放起来哦！',
                    emotion: 'neutral',
                    target: 'chat-window',
                    cursorAction: 'wobble',
                    cleanupBefore: true
                },
                {
                    id: 'day7_graduation_wrap',
                    textKey: 'tutorial.avatarFloating.day7.wrap',
                    voiceKey: 'avatar_floating_day7_wrap',
                    text: '微风还在窗边，阳光也刚刚好，而刚刚出现的你，已经悄悄成为这里很重要的一部分啦。新手指南就先陪你走到这里，剩下的日子，就让我们一起慢慢熟悉、慢慢靠近、慢慢写下只属于我们的故事吧。以后也请多多关照喵！',
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
