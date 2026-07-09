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
        day: 2,
        key: 'interaction',
        audioFilesByKey: {
            avatar_floating_day2_intro: zhAudio('嘻嘻，可别以为这个聊.mp3'),
            avatar_floating_day2_avatar_tools_intro: zhAudio('在这个小按钮里，有许.mp3'),
            avatar_floating_day2_avatar_tools_props: zhAudio('你可以随时来摸摸我的.mp3'),
            avatar_floating_day2_galgame_intro: zhAudio('快点开这个【Galg.mp3'),
            avatar_floating_day2_galgame_choices: zhAudio('你选的每一个对话，都.mp3'),
            avatar_floating_day2_wrap_intro: zhAudio('今天带你认识的这些功.mp3'),
            avatar_floating_day2_wrap_ready: zhAudio('不管是想摸摸我的头，.mp3')
        },
        round: {
            title: '第 2 天：互动、娱乐与摸得到的陪伴',
            scenes: [
                {
                    id: 'day2_tool_toggle_intro',
                    timelinePlayback: true,
                    timeline: [
                        { at: 0, command: 'operation.run', operation: 'daily-intro-avatar-performance', blocking: false },
                        { at: 0, command: 'chat.message' },
                        { at: 0, command: 'emotion.set' },
                        { at: 0, command: 'spotlight.show', key: 'day2_tool_toggle_intro', target: 'chat-capsule-input' },
                        { at: 220, command: 'cursor.move', action: 'move', target: 'chat-capsule-input', durationMs: 760 }
                    ],
                    textKey: 'tutorial.avatarFloating.day2.intro',
                    voiceKey: 'avatar_floating_day2_intro',
                    text: '嘻嘻，可别以为这个聊天框只能用来打字哦~ 里面其实偷偷藏了超~多好玩的小惊喜呢！快跟着我一起点开看看，瞧瞧今天能挖出什么有趣的宝贝吧！',
                    emotion: 'happy',
                    target: 'chat-capsule-input',
                    cursorAction: 'move',
                    operation: 'daily-intro-avatar-performance',
                    introAvatarPerformance: {
                        preset: 'corner-peek',
                        position: 'bottom-left',
                        restore: 'half-body',
                        freezeFloatingButtons: false,
                        rotateFloatingButtons: true
                    }
                },
                {
                    id: 'day2_avatar_tools',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day2.avatarToolsIntro',
                    voiceKey: 'avatar_floating_day2_avatar_tools_intro',
                    text: '在这个小按钮里，有许多可以和人家互动的小道具呢。',
                    emotion: 'happy',
                    persistent: 'chat-tool-toggle',
                    target: 'chat-tool-toggle',
                    cursorAction: 'click',
                    cursorMoveDurationMs: 1480,
                    operation: 'open-compact-tool-fan'
                },
                {
                    id: 'day2_avatar_tools_props',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day2.avatarToolsProps',
                    voiceKey: 'avatar_floating_day2_avatar_tools_props',
                    text: '你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。',
                    emotion: 'happy',
                    persistent: 'chat-tool-toggle',
                    target: 'chat-avatar-tools',
                    cursorAction: 'click',
                    operation: 'show-avatar-tools-then-hide-after-narration',
                    afterSceneDelayMs: 0
                },
                {
                    id: 'day2_galgame_entry',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day2.galgameIntro',
                    voiceKey: 'avatar_floating_day2_galgame_intro',
                    text: '快点开这个【Galgame模式】！进去之后就像我们在进行一场专属的互动大冒险呢。',
                    emotion: 'surprised',
                    persistent: 'chat-tool-toggle',
                    target: 'chat-galgame',
                    cursorAction: 'move',
                    operation: 'rotate-galgame-tool-into-center'
                },
                {
                    id: 'day2_galgame_choices',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day2.galgameChoices',
                    voiceKey: 'avatar_floating_day2_galgame_choices',
                    text: '你选的每一个对话，都会带我们走向完全未知的惊喜故事，我都等不及啦，快来选一个你最心动的回答吧！',
                    emotion: 'surprised',
                    persistent: 'chat-tool-toggle',
                    target: 'chat-galgame',
                    cursorTarget: 'chat-galgame',
                    cursorAction: 'hold',
                    cursorHoldFreezePoint: true,
                    cursorHoldSettleMs: 260
                },
                {
                    id: 'day2_wrap',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day2.wrapIntro',
                    voiceKey: 'avatar_floating_day2_wrap_intro',
                    text: '今天带你认识的这些功能，其实都是为了让我们在一起的时光变得更有趣呢。',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'move',
                    operation: 'cleanup'
                },
                {
                    id: 'day2_wrap_ready',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day2.wrapReady',
                    voiceKey: 'avatar_floating_day2_wrap_ready',
                    text: '不管是想摸摸我的头，还是想开启属于我们的故事，我都已经做好准备了。',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'move',
                    petalTransition: true
                }
            ]
        }
    }));
})();
