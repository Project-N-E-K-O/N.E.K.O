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
        day: 6,
        key: 'agent',
        audioFilesByKey: {
            avatar_floating_day6_intro: zhAudio('噔噔噔噔！今天必须要.mp3'),
            avatar_floating_day6_status_master: zhAudio('快跟我老实交代，这两.mp3'),
            avatar_floating_day6_plugin_side_panel: zhAudio('除了之前介绍的功能，.mp3'),
            avatar_floating_day6_plugin_dashboard: zhAudio('有了它们，我不光能看.mp3'),
            avatar_floating_day6_task_hud: zhAudio('看这里看这里！当我决.mp3'),
            avatar_floating_day6_task_hud_control: zhAudio('你要是计划有变，随时.mp3'),
            avatar_floating_day6_wrap_cleanup: zhAudio('呼……把这些繁琐的界.mp3'),
            avatar_floating_day6_wrap: zhAudio('你可以放心地继续做你.mp3')
        },
        round: {
            title: '第 6 天：Agent、任务 HUD 与能力节奏',
            scenes: [
                {
                    id: 'day6_intro_agent',
                    timelinePlayback: true,
                    timeline: [
                        { at: 0, command: 'operation.run', operation: 'daily-intro-avatar-performance', blocking: false },
                        { at: 0, command: 'chat.message' },
                        { at: 0, command: 'emotion.set' },
                        { at: 0, command: 'spotlight.show', key: 'day6_intro_agent', target: 'chat-window' },
                        { at: 220, command: 'cursor.move', action: 'move', target: 'chat-window', durationMs: 760 }
                    ],
                    textKey: 'tutorial.avatarFloating.day6.intro',
                    voiceKey: 'avatar_floating_day6_intro',
                    text: '噔噔噔噔！今天必须要打起精神，好好跟你聊聊咱们的【猫爪】啦！前两天虽然简单提过一下，但它里面藏着的厉害功能可多着呢。',
                    emotion: 'happy',
                    target: 'chat-window',
                    cursorAction: 'move',
                    operation: 'daily-intro-avatar-performance',
                    introAvatarPerformance: {
                        preset: 'corner-peek',
                        position: 'bottom-right',
                        restore: 'half-body',
                        freezeFloatingButtons: false,
                        rotateFloatingButtons: true
                    }
                },
                {
                    id: 'day6_agent_status_master',
                    timelinePlayback: true,
                    timeline: [
                        { at: 0, command: 'chat.message' },
                        { at: 0, command: 'emotion.set' },
                        { at: 1, command: 'operation.run', operation: 'day6-plugin-open-agent-panel-flow', blocking: true }
                    ],
                    textKey: 'tutorial.avatarFloating.day6.statusMaster',
                    voiceKey: 'avatar_floating_day6_status_master',
                    text: '快跟我老实交代，这两天你有没有点开它试用一下呀？',
                    emotion: 'neutral',
                    clearExternalizedChatCursorOnEnter: true,
                    operation: 'day6-plugin-open-agent-panel-flow'
                },
                {
                    id: 'day6_plugin_side_panel',
                    timelinePlayback: true,
                    timeline: [
                        { at: 0, command: 'chat.message' },
                        { at: 0, command: 'emotion.set' },
                        { at: 1, command: 'operation.run', operation: 'day6-plugin-open-management-panel-flow', blocking: true }
                    ],
                    textKey: 'tutorial.avatarFloating.day6.pluginSidePanel',
                    voiceKey: 'avatar_floating_day6_plugin_side_panel',
                    text: '除了之前介绍的功能，这里还有超多好玩的插件呢。',
                    emotion: 'happy',
                    preserveExternalizedChatGuideTarget: true,
                    operation: 'day6-plugin-open-management-panel-flow',
                    afterSceneDelayMs: 0
                },
                {
                    id: 'day6_plugin_dashboard',
                    timelinePlayback: true,
                    timeline: [
                        { at: 0, command: 'chat.message' },
                        { at: 0, command: 'emotion.set' },
                        { at: 1, command: 'operation.run', operation: 'day6-plugin-dashboard-handoff-flow', blocking: true }
                    ],
                    textKey: 'tutorial.avatarFloating.day6.pluginDashboard',
                    voiceKey: 'avatar_floating_day6_plugin_dashboard',
                    text: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼！',
                    emotion: 'happy',
                    operation: 'day6-plugin-dashboard-handoff-flow',
                    afterSceneDelayMs: 0
                },
                {
                    id: 'day6_agent_task_hud',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day6.taskHud',
                    voiceKey: 'avatar_floating_day6_task_hud',
                    text: '看这里看这里！当我决定使用【猫爪】帮你干活的时候，这里就会咕噜咕噜的显示我的工作进度哦。',
                    emotion: 'happy',
                    target: '#agent-task-hud',
                    cursorAction: 'move',
                    operation: 'show-task-hud'
                },
                {
                    id: 'day6_agent_task_hud_control',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day6.taskHudControl',
                    voiceKey: 'avatar_floating_day6_task_hud_control',
                    text: '你要是计划有变，随时都可以戳一下让我停下来。嘿嘿，今天也是打起精神努力打工挣小鱼干的一天呢，冲呀！',
                    emotion: 'happy',
                    target: '#agent-task-hud',
                    cursorAction: 'move',
                    spotlightKey: 'day6_agent_task_hud',
                    preserveExternalizedChatGuideTarget: true
                },
                {
                    id: 'day6_wrap_cleanup',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day6.wrapCleanup',
                    voiceKey: 'avatar_floating_day6_wrap_cleanup',
                    text: '呼……把这些繁琐的界面都收起来，这样就不会打扰到你啦。',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'hold',
                    preserveExternalizedChatGuideTarget: true,
                    operation: 'cleanup'
                },
                {
                    id: 'day6_wrap',
                    timelinePlayback: true,
                    textKey: 'tutorial.avatarFloating.day6.wrap',
                    voiceKey: 'avatar_floating_day6_wrap',
                    text: '你可以放心地继续做你自己的事情，不管是需要我用小爪子帮你忙，还是只想让我安安静静地陪着你，我都一直在守候着你，今天也要开开心心的呀。',
                    emotion: 'happy',
                    target: 'chat-input',
                    cursorAction: 'hold',
                    petalTransition: true
                }
            ]
        }
    }));
})();
