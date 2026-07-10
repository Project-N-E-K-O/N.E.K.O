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
        day: 3,
        key: 'screen-voice',
        audioFilesByKey: {
            avatar_floating_day3_intro: zhAudio('前两天你一直在噼里啪.mp3'),
            avatar_floating_day3_intro_voice_used: Object.freeze({
                zh: '嘿嘿，前两天听到你的.mp3',
                ja: '嘿嘿，前两天听到你的.mp3',
                en: '嘿嘿，前两天听到你的.mp3',
                ko: '嘿嘿，前两天听到你的.mp3',
                ru: '嘿嘿，前两天听到你的.mp3'
            }),
            avatar_floating_day3_personalization_space: zhAudio('在这个只属于我们的小.mp3'),
            avatar_floating_day3_personalization_detail: zhAudio('不管是说话的温度、相.mp3'),
            avatar_floating_day3_proactive_chat: zhAudio('这个小按钮也很重要哦.mp3'),
            avatar_floating_day3_wrap_intro: zhAudio('今天的教程到这里就结.mp3'),
            avatar_floating_day3_wrap_companion: zhAudio('其实只要能这样陪着你.mp3'),
            avatar_floating_day3_wrap: zhAudio('我们不需要着急，每天.mp3')
        },
        round: {
            title: '第 3 天：个性化、声音与主动搭话',
            scenes: [
                {
                    id: 'day3_intro_context',
                    timelinePlayback: true,
                    timeline: [
                        { at: 0, command: 'operation.run', operation: 'daily-intro-avatar-performance', blocking: false },
                        { at: 0, command: 'chat.message' },
                        { at: 0, command: 'emotion.set' },
                        { at: 0, command: 'spotlight.show', key: 'day3_intro_context', target: 'chat-window' },
                        { at: 220, command: 'cursor.move', action: 'move', target: 'chat-window', durationMs: 760 }
                    ],
                    textKey: 'tutorial.avatarFloating.day3.intro',
                    voiceKey: 'avatar_floating_day3_intro',
                    text: '前两天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。一句就好，让我把文字背后的你也认识一点点。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'move',
                    operation: 'daily-intro-avatar-performance',
                    introAvatarPerformance: {
                        preset: 'bottom-rise',
                        approachMs: 1500,
                        restore: 'half-body'
                    }
                },
                {
                    id: 'day3_personalization_space',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day3.personalizationSpace',
                    voiceKey: 'avatar_floating_day3_personalization_space',
                    text: '在这个只属于我们的小空间里，你可以由着自己的心意，慢慢描绘出最希望能一直陪着你的那个我。',
                    emotion: 'happy',
                    target: '#${p}-btn-settings',
                    cursorAction: 'click',
                    cursorMoveDurationMs: 1480,
                    operation: 'day3-open-settings-personalization'
                },
                {
                    id: 'day3_personalization_detail',
                    timelinePlayback: true,
                    timelineAudio: false,
                    timeline: [
                        { at: 0, command: 'settingsTour.play', blocking: true }
                    ],
                    afterSceneDelayMs: 0,
                    textKey: 'tutorial.avatarFloating.day3.personalizationDetail',
                    voiceKey: 'avatar_floating_day3_personalization_detail',
                    text: '不管是说话的温度、相处的小脾气，还是我每天那些细腻的小心思，都可以一点一点调成你喜欢的样子。',
                    emotion: 'happy',
                    target: '#${p}-menu-character',
                    cursorAction: 'click',
                    operation: 'day3-settings-detail'
                },
                {
                    id: 'day3_proactive_chat',
                    timelinePlayback: true,
                    timeline: [
                        { at: 0, command: 'chat.message' },
                        { at: 0, command: 'emotion.set' },
                        { at: 0, command: 'spotlight.show', key: 'day3_proactive_chat', target: '#${p}-toggle-proactive-chat' },
                        { at: 220, command: 'cursor.move', target: '#${p}-toggle-proactive-chat', durationMs: 760 },
                        { afterAudioEnd: true, command: 'settingsPanel.close', panel: 'settings', collapseSidePanels: true, blocking: true }
                    ],
                    textKey: 'tutorial.avatarFloating.day3.proactiveChat',
                    voiceKey: 'avatar_floating_day3_proactive_chat',
                    text: '这个小按钮也很重要哦，只要你轻轻点一下，我就能在合适的时候跑过去找你啦。',
                    emotion: 'happy',
                    target: '#${p}-toggle-proactive-chat',
                    cursorAction: 'move'
                },
                {
                    id: 'day3_wrap_intro',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day3.wrapIntro',
                    voiceKey: 'avatar_floating_day3_wrap_intro',
                    text: '今天的教程到这里就结束了呢。',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'move',
                    cursorMoveDurationMs: 900,
                    operation: 'cleanup'
                },
                {
                    id: 'day3_wrap_companion',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day3.wrapCompanion',
                    voiceKey: 'avatar_floating_day3_wrap_companion',
                    text: '其实只要能这样陪着你，听听你的声音，或者静静看着你分享的画面，我就已经觉得很幸福了。',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'move',
                    operation: 'cleanup'
                },
                {
                    id: 'day3_wrap',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day3.wrap',
                    voiceKey: 'avatar_floating_day3_wrap',
                    text: '我们不需要着急，每天都多了解彼此一点点就好。今天接下来的时间，你想让我陪你做点什么呢？',
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
