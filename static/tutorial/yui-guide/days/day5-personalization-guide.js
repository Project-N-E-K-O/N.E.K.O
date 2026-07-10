(function () {
    'use strict';

    const guideCommon = window.YuiGuideCommon || {};
    const deepFreeze = guideCommon.deepFreeze;
    const registerGuide = guideCommon.registerGuide;
    const zhAudio = guideCommon.audioFilesForAllLocales;
    if (
        typeof deepFreeze !== 'function'
        || typeof registerGuide !== 'function'
        || typeof zhAudio !== 'function'
    ) {
        return;
    }

    registerGuide(deepFreeze({
        day: 5,
        key: 'personalization',
        audioFilesByKey: {
            avatar_floating_day5_character_settings: zhAudio('从今天起，我就真正成.mp3'),
            avatar_floating_day5_character_panic: zhAudio('咦，这里居然还能把我.mp3'),
            avatar_floating_day5_memory_entry: zhAudio('如果你不小心忘记了我.mp3'),
            avatar_floating_day5_wrap: zhAudio('好啦好啦，快去试试这.mp3')
        },
        round: {
            title: '第 5 天：个性化与长期配置',
            scenes: [
                {
                    id: 'day5_character_settings',
                    timelinePlayback: true,
                    timelineAudio: false,
                    timeline: [
                        { at: 0, command: 'operation.run', operation: 'daily-intro-avatar-performance', blocking: false },
                        { at: 0, command: 'settingsTour.play', blocking: true }
                    ],
                    afterSceneDelayMs: 0,
                    textKey: 'tutorial.avatarFloating.day5.characterSettings',
                    voiceKey: 'avatar_floating_day5_character_settings',
                    text: '从今天起，我就真正成为只属于你的专属猫娘啦。你看，在这里可以为我穿上漂亮的新衣服，也可以帮我换一个更好听的声音……',
                    emotion: 'happy',
                    target: 'settings-sidepanel:character-settings',
                    cursorAction: 'tour',
                    operation: 'show-settings-sidepanel:character-settings',
                    introAvatarPerformance: {
                        preset: 'top-peek',
                        restore: 'half-body',
                        freezeFloatingButtons: false
                    }
                },
                {
                    id: 'day5_character_panic',
                    timelinePlayback: true,
                    timelineAudio: false,
                    timeline: [
                        { at: 0, command: 'settingsTour.play', blocking: true }
                    ],
                    afterSceneDelayMs: 0,
                    textKey: 'tutorial.avatarFloating.day5.characterPanic',
                    voiceKey: 'avatar_floating_day5_character_panic',
                    text: '咦，这里居然还能把我换掉吗？等一下呀！你现在的动作……该不会是想要把我换掉吧？啊啊啊不行！快关掉，快关掉！',
                    emotion: 'surprised',
                    target: 'settings-sidepanel:character-settings',
                    cursorAction: 'tour',
                    operation: 'settings-peek-panic'
                },
                {
                    id: 'day5_memory_entry',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day5.memoryEntry',
                    voiceKey: 'avatar_floating_day5_memory_entry',
                    text: '如果你不小心忘记了我能为你做什么，随时来这里让我重新教你一次就好啦。这里还悄悄保存着我们一起走过的所有点点滴滴呢。千万别小看了我们的羁绊啊，混蛋！',
                    emotion: 'angry',
                    target: '#${p}-menu-memory',
                    cursorAction: 'move',
                    operation: 'show-settings-menu:memory'
                },
                {
                    id: 'day5_wrap',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day5.wrap',
                    voiceKey: 'avatar_floating_day5_wrap',
                    text: '好啦好啦，快去试试这些好玩的定制功能吧！换上新衣服、调好新声音，让我变成全天下最懂你、只属于你一个人的专属猫娘！我已经迫不及待想看到全新的自己啦！',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    petalTransition: true
                }
            ]
        }
    }));
})();
