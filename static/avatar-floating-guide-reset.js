(function () {
    'use strict';

    const STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    const ICEBREAKER_STORAGE_KEY = 'neko.new_user_icebreaker.v1';
    const ICEBREAKER_RESET_EVENT = 'neko:new-user-icebreaker-reset';
    const RESET_EVENT = 'neko:avatar-floating-guide-reset';
    const RESET_BROADCAST_KEY = 'neko_avatar_floating_guide_reset_event';
    const HOME_TUTORIAL_KEYS = ['neko_tutorial_home_yui_v1', 'neko_tutorial_home'];
    const HOME_MANUAL_INTENT_KEY = 'neko_tutorial_home_manual_intent';
    const PC_OVERLAY_RUN_ID_KEY = 'yuiGuidePcOverlayRunId';
    const ROUND_COUNT = 7;
    const RESET_HISTORY_LIMIT = 20;
    const DAY_TUTORIALS = {
        1: {
            title: '第 1 天：初次唤醒、聊天与基础入口',
            steps: [
                {
                    id: 'day1_intro_activation',
                    selector: '#react-chat-window-root .composer-input-shell',
                    text: '第一天先高亮输入区或 PC 胶囊输入框，等待用户点击完成音频激活。',
                    voiceKey: '',
                    cursorAction: 'input-origin',
                    operation: 'day1-intro-activation',
                    performanceCue: null,
                },
                {
                    id: 'day1_intro_greeting',
                    selector: '#react-chat-window-root .composer-input-shell',
                    text: '首句问候期间继续高亮输入区或 PC 胶囊输入框；第一句话播放完后清理该高光。',
                    voiceKey: 'intro_greeting_reply',
                    cursorAction: 'move',
                    operation: 'day1-intro-greeting',
                    performanceCue: null,
                },
                {
                    id: 'day1_capsule_drag_hint',
                    selector: '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                    text: '持续圆角矩形高亮胶囊输入框；Ghost Cursor 在胶囊上左右晃动约 2 秒。',
                    voiceKey: 'day1_capsule_drag_hint',
                    cursorAction: 'wobble',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day1_history_handle',
                    selector: '#react-chat-window-root .compact-history-visibility-handle',
                    text: '不显示聊天窗/胶囊输入框圆角矩形高亮；Ghost Cursor 点击顶部历史小条，播放完后收起历史对话。',
                    voiceKey: 'day1_history_handle',
                    cursorAction: 'click',
                    operation: 'open-compact-history-during-narration',
                    performanceCue: null,
                },
                {
                    id: 'day1_intro_basic_voice',
                    selector: '#${prefix}-btn-mic',
                    text: '随后高亮语音按钮，Ghost Cursor 从输入区锚点平滑移动到按钮，只指认不点击。',
                    voiceKey: 'intro_basic',
                    cursorAction: 'move',
                    operation: 'day1-intro-basic-voice',
                    interruptible: false,
                    performanceCue: null,
                },
                {
                    id: 'day1_screen_entry',
                    selector: '#${prefix}-btn-screen',
                    text: '复用原 Day 2 屏幕分享入口流程，只指认入口，不点击按钮。',
                    voiceKey: 'day1_screen_entry',
                    cursorAction: 'move',
                    operation: 'none',
                    interruptible: false,
                    performanceCue: null,
                },
                {
                    id: 'day1_screen_entry_invite',
                    selector: '#${prefix}-btn-screen',
                    text: '继续高亮屏幕分享按钮，不打开来源列表。',
                    voiceKey: 'day1_screen_entry_invite',
                    cursorAction: 'move',
                    operation: 'none',
                    interruptible: false,
                    performanceCue: null,
                },
                {
                    id: 'day1_takeover_capture_cursor',
                    selector: '#${prefix}-btn-agent',
                    text: '猫爪按钮、Agent 总开关和键鼠控制开关按第一天旧流程演示。',
                    voiceKey: 'takeover_capture_cursor',
                    cursorAction: 'move',
                    operation: 'day1-managed-scene:takeover_capture_cursor',
                    interruptible: false,
                    performanceCue: null,
                },
                {
                    id: 'day1_takeover_return_control',
                    selector: '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"], #react-chat-window-root [data-compact-geometry-part="inputBody"], #react-chat-window-root .compact-chat-surface-frame[data-compact-chat-state="input"], #react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"], #react-chat-window-root .composer-input-shell, #react-chat-window-root .composer-panel',
                    text: '收尾重新高亮聊天窗，约 70% cue 清理高光和 Ghost Cursor 并播放花瓣。',
                    voiceKey: 'takeover_return_control',
                    cursorAction: 'move',
                    cursorMoveDurationMs: 900,
                    operation: 'cleanup',
                    performanceCue: 'returnControl',
                },
            ],
        },
        2: {
            title: '第 2 天：个性化、声音与主动搭话',
            steps: [
                {
                    id: 'day2_intro_context',
                    selector: '#home-avatar-floating-guide-player',
                    text: '第二天先根据昨天是否用过声音聊天切换开场台词，只高亮聊天窗并播放承接语音，不显示“现在说一句 / 继续打字”两个选择。',
                    voiceKey: 'avatar_floating_day2_intro',
                    cursorAction: 'input-origin',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day2_personalization_space',
                    selector: '#${prefix}-btn-settings',
                    text: '打开设置入口，开始个性化空间介绍。',
                    voiceKey: 'takeover_settings_peek_intro',
                    cursorAction: 'click',
                    operation: 'day2-open-settings-personalization',
                    performanceCue: null,
                },
                {
                    id: 'day2_personalization_detail',
                    selector: '#${prefix}-menu-character',
                    text: '高亮角色设置按钮，点击后展开角色设置侧边栏，再高亮侧边栏并移动 Ghost Cursor。',
                    voiceKey: 'takeover_settings_peek_detail',
                    cursorAction: 'click',
                    operation: 'day2-settings-detail',
                    performanceCue: null,
                },
                {
                    id: 'day2_proactive_chat',
                    selector: '#${prefix}-toggle-proactive-chat',
                    text: '高亮主动搭话入口，停留但不点击。',
                    voiceKey: 'takeover_settings_peek_detail_part_2',
                    cursorAction: 'move',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day2_wrap_intro',
                    selector: '#home-avatar-floating-guide-player',
                    text: '今天的教程到这里就结束了呢。',
                    voiceKey: 'avatar_floating_day2_wrap_intro',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: null,
                },
                {
                    id: 'day2_wrap_companion',
                    selector: '#home-avatar-floating-guide-player',
                    text: '其实只要能这样陪着你，听听你的声音，或者静静看着你分享的画面，我就已经觉得很幸福了。',
                    voiceKey: 'avatar_floating_day2_wrap_companion',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: null,
                },
                {
                    id: 'day2_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '最终收尾约 70% 处触发每日花瓣转场。',
                    voiceKey: 'avatar_floating_day2_wrap',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: 'returnControl',
                },
            ],
        },
        3: {
            title: '第 3 天：互动、娱乐与摸得到的陪伴',
            steps: [
                {
                    id: 'day3_tool_toggle_intro',
                    selector: '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"], #react-chat-window-root .compact-chat-surface-frame, #react-chat-window-root .composer-input-shell',
                    text: '圆角矩形高亮胶囊输入框，Ghost Cursor 停留在胶囊聊天框中间。',
                    voiceKey: 'avatar_floating_day3_intro',
                    cursorAction: 'move',
                    operation: null,
                    performanceCue: null,
                },
                {
                    id: 'day3_avatar_tools',
                    selector: '#react-chat-window-root button.send-button-circle.compact-input-tool-toggle',
                    text: '慢慢移动到胶囊工具总按钮并打开弧形工具菜单。',
                    voiceKey: 'avatar_floating_day3_avatar_tools_intro',
                    cursorAction: 'click',
                    cursorMoveDurationMs: 1480,
                    operation: 'open-compact-tool-fan',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day3_avatar_tools_props',
                    selector: '#react-chat-window-root .compact-input-tool-item-avatar',
                    text: '平滑移动到 Avatar 互动工具并点击显示三个小道具，播放完后再次触发该按钮点击事件隐藏三个小道具。',
                    voiceKey: 'avatar_floating_day3_avatar_tools_props',
                    cursorAction: 'click',
                    operation: 'show-avatar-tools-then-hide-after-narration',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day3_galgame_entry',
                    selector: '#react-chat-window-root .compact-input-tool-item-galgame',
                    text: '先移动到初始 Galgame 按钮，再点击态向下拖动约 100px，把 Galgame 从 slot 2 转到 slot 1 后移动到新的 Galgame 中心。',
                    voiceKey: 'avatar_floating_day3_galgame_intro',
                    cursorAction: 'move',
                    operation: 'rotate-galgame-tool-into-center',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day3_galgame_choices',
                    selector: '#react-chat-window-root .compact-input-tool-item-galgame',
                    text: '继续介绍 Galgame 选择，不伪造选项局。',
                    voiceKey: 'avatar_floating_day3_galgame_choices',
                    cursorAction: 'move',
                    operation: 'none',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day3_wrap',
                    selector: '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"], #react-chat-window-root .compact-chat-surface-frame, #react-chat-window-root .composer-input-shell',
                    text: '这一轮会收起临时工具菜单，把界面还给用户，后续玩法邀请只从聊天窗支线发起。',
                    voiceKey: 'avatar_floating_day3_wrap_intro',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: null,
                },
                {
                    id: 'day3_wrap_ready',
                    selector: '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"], #react-chat-window-root .compact-chat-surface-frame, #react-chat-window-root .composer-input-shell',
                    text: '最终收尾约 70% 处触发每日花瓣转场。',
                    voiceKey: 'avatar_floating_day3_wrap_ready',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: 'returnControl',
                },
            ],
        },
        4: {
            title: '第 4 天：相处距离、主动陪伴与模型行为',
            steps: [
                {
                    id: 'day4_intro_companion',
                    selector: '#home-avatar-floating-guide-player',
                    text: '第四天先建立“相处距离”的主题，再进入设置类入口。',
                    voiceKey: 'avatar_floating_day4_intro',
                    cursorAction: 'move',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day4_chat_settings',
                    selector: '#${prefix}-btn-settings',
                    text: '设置弹窗会在主线 Director 的准备阶段打开；兜底路径先点设置按钮，再尝试高亮对话节奏相关入口。',
                    voiceKey: 'avatar_floating_day4_chat_settings',
                    cursorAction: 'click',
                    operation: 'safe-click',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day4_animation_tracking',
                    selector: '#${prefix}-popup-settings',
                    text: '动画设置会说明画质、帧率、鼠标跟踪和悬停淡化等表现选项，不保存临时改动。',
                    voiceKey: 'avatar_floating_day4_animation_tracking',
                    cursorAction: 'show',
                    operation: 'none',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day4_lock_interaction',
                    selector: '#${prefix}-lock-icon',
                    text: '',
                    voiceKey: '',
                    cursorAction: 'show',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day4_goodbye_return',
                    selector: '#${prefix}-btn-goodbye',
                    text: '',
                    voiceKey: '',
                    cursorAction: 'show',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day4_privacy_mode',
                    selector: '#${prefix}-toggle-proactive-vision',
                    text: '隐私模式开启表示关闭主动视觉感知，关闭隐私模式才允许按间隔主动看屏幕。',
                    voiceKey: 'avatar_floating_day4_privacy_mode',
                    cursorAction: 'show',
                    operation: 'none',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day4_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '第四天收尾前会恢复临时设置状态，Ghost Cursor 回到胶囊输入框后播放花瓣转场，再把界面还给用户。',
                    voiceKey: 'avatar_floating_day4_wrap',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: null,
                },
            ],
        },
        5: {
            title: '第 5 天：个性化与长期配置',
            steps: [
                {
                    id: 'day5_character_settings',
                    selector: '#${prefix}-btn-settings',
                    text: '第五天的主线 Director 会预打开角色设置；兜底路径先点设置按钮，再展示模型、声音与 API 等长期入口。',
                    voiceKey: 'avatar_floating_day5_character_settings',
                    cursorAction: 'click',
                    operation: 'safe-click',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day5_character_panic',
                    selector: '[data-neko-sidepanel-type="character-settings"]',
                    text: '角色替换反应继续高亮角色设置侧边栏，播放完后清除高光并收起侧边栏，不阻止用户之后真实进入模型或角色管理。',
                    voiceKey: 'avatar_floating_day5_character_panic',
                    cursorAction: 'move',
                    operation: 'none',
                    keepPanelsOpen: false,
                    performanceCue: null,
                },
                {
                    id: 'day5_memory_entry',
                    selector: '#${prefix}-menu-memory',
                    text: '随后会高亮记忆浏览入口，只认门，不展开具体记忆内容。',
                    voiceKey: 'avatar_floating_day5_memory_entry',
                    cursorAction: 'show',
                    operation: 'none',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day5_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '这一轮会清理设置弹窗，Ghost Cursor 回到胶囊输入框后播放每日花瓣收尾。',
                    voiceKey: 'avatar_floating_day5_wrap',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: null,
                },
            ],
        },
        6: {
            title: '第 6 天：Agent、任务 HUD 与能力节奏',
            steps: [
                {
                    id: 'day6_intro_agent',
                    selector: '#${prefix}-btn-agent',
                    text: '第六天会强接管猫爪入口，展示状态、权限、用户插件和任务 HUD。',
                    voiceKey: 'avatar_floating_day6_intro',
                    cursorAction: 'click',
                    operation: 'safe-click',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day6_agent_status_master',
                    selector: '#${prefix}-toggle-agent-master',
                    text: '',
                    voiceKey: '',
                    cursorAction: 'show',
                    operation: 'none',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day6_plugin_side_panel',
                    selector: '#${prefix}-toggle-agent-user-plugin',
                    text: '用户插件入口可以打开管理面板，但不自动启用具体插件。',
                    voiceKey: 'avatar_floating_day6_plugin_side_panel',
                    cursorAction: 'show',
                    operation: 'none',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day6_agent_task_hud',
                    selector: '#agent-task-hud',
                    text: '任务 HUD 会展示运行、排队、折叠和终止入口，不创建假的后台任务。',
                    voiceKey: 'avatar_floating_day6_task_hud',
                    cursorAction: 'show',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day6_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '这一轮会收起猫爪、HUD 和侧边面板，并播放每日花瓣收尾。',
                    voiceKey: 'avatar_floating_day6_wrap',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: null,
                },
            ],
        },
        7: {
            title: '第 7 天：毕业、进阶入口与共生约定',
            steps: [
                {
                    id: 'day7_memory_review',
                    text: '第七天先回顾相处痕迹，不展示敏感记忆内容。',
                    voiceKey: 'avatar_floating_day7_memory_review',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day7_memory_control',
                    selector: '#${prefix}-menu-memory',
                    text: '记忆整理、保存、强力记忆和清理只在台词层说明，不自动点击高风险操作。',
                    voiceKey: 'avatar_floating_day7_memory_control',
                    cursorAction: 'move',
                    operation: 'none',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day7_graduation_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '毕业收尾会恢复用户原模型与交互权限，保存第七天完成态。',
                    voiceKey: 'avatar_floating_day7_wrap',
                    cursorAction: 'move',
                    operation: 'cleanup',
                    performanceCue: null,
                },
            ],
        },
    };
    let activeRoundPlayer = null;

    function getTodayLocalDate() {
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function normalizeRound(day) {
        const round = Number(day);
        if (!Number.isInteger(round) || round < 1 || round > ROUND_COUNT) {
            throw new Error(`Invalid tutorial day: ${day}`);
        }
        return round;
    }

    function normalizeRoundList(value) {
        if (!Array.isArray(value)) return [];

        return Array.from(new Set(
            value
                .map(item => Number(item))
                .filter(item => Number.isInteger(item) && item >= 1 && item <= ROUND_COUNT)
        )).sort((left, right) => left - right);
    }

    function normalizeOptionalRound(value) {
        const round = Number(value);
        return Number.isInteger(round) && round >= 1 && round <= ROUND_COUNT ? round : null;
    }

    function omitRound(value, round) {
        return normalizeRoundList(value).filter(item => item !== round);
    }

    function loadGuideState() {
        let parsed = {};
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            parsed = raw ? JSON.parse(raw) : {};
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 状态读取失败，使用空状态:', error);
            parsed = {};
        }

        return {
            version: 1,
            firstSeenDate: parsed.firstSeenDate || getTodayLocalDate(),
            completedRounds: normalizeRoundList(parsed.completedRounds),
            skippedRounds: normalizeRoundList(parsed.skippedRounds),
            currentRound: normalizeOptionalRound(parsed.currentRound),
            pendingRound: normalizeOptionalRound(parsed.pendingRound),
            manualResetRound: normalizeOptionalRound(parsed.manualResetRound),
            lastAutoShownRound: normalizeOptionalRound(parsed.lastAutoShownRound),
            lastAutoShownDate: parsed.lastAutoShownDate || '',
            lastEndState: parsed.lastEndState && typeof parsed.lastEndState === 'object' ? parsed.lastEndState : null,
            updatedAt: parsed.updatedAt || null,
            resetHistory: Array.isArray(parsed.resetHistory) ? parsed.resetHistory.slice(-RESET_HISTORY_LIMIT) : [],
        };
    }

    function saveGuideState(state) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    }

    function resetIcebreakerDay(day) {
        const round = normalizeRound(day);
        const key = String(round);
        let store = { version: 1, days: {} };
        try {
            const raw = localStorage.getItem(ICEBREAKER_STORAGE_KEY);
            store = raw ? JSON.parse(raw) : store;
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 破冰状态读取失败，使用空状态:', error);
        }

        if (!store || typeof store !== 'object') {
            store = { version: 1, days: {} };
        }
        if (!store.days || typeof store.days !== 'object') {
            store.days = {};
        }
        delete store.days[key];

        try {
            localStorage.setItem(ICEBREAKER_STORAGE_KEY, JSON.stringify(store));
            window.dispatchEvent(new CustomEvent(ICEBREAKER_RESET_EVENT, {
                detail: { day: round },
            }));
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 破冰状态重置失败:', error);
        }
    }

    function recordAvatarFloatingGuideEndState(day, outcome, rawReason, source) {
        const normalizedDay = normalizeOptionalRound(day);
        const normalizedOutcome = outcome === 'complete'
            ? 'complete'
            : (outcome === 'skip' ? 'skip' : 'destroy');
        const normalizedRawReason = typeof rawReason === 'string' && rawReason.trim()
            ? rawReason.trim().toLowerCase()
            : normalizedOutcome;
        const endState = {
            day: normalizedDay,
            ended: true,
            outcome: normalizedOutcome,
            rawReason: normalizedRawReason,
            isAngryExit: normalizedRawReason === 'angry_exit',
            completed: normalizedOutcome === 'complete',
            skipped: normalizedOutcome === 'skip',
            source: typeof source === 'string' ? source : '',
            endedAt: Date.now(),
        };
        window.avatarFloatingGuideEndState = endState;
        console.log('[AvatarFloatingGuideEndState]', endState);
        return endState;
    }

    function markGuideRoundOutcome(day, outcome, endState) {
        const round = normalizeRound(day);
        const state = loadGuideState();
        state.currentRound = null;
        if (state.pendingRound === round) state.pendingRound = null;
        if (state.manualResetRound === round) state.manualResetRound = null;
        if (outcome === 'complete') {
            state.completedRounds = normalizeRoundList(state.completedRounds.concat(round));
            state.skippedRounds = omitRound(state.skippedRounds, round);
        } else if (outcome === 'skip') {
            state.skippedRounds = normalizeRoundList(state.skippedRounds.concat(round));
            state.completedRounds = round === 1
                ? normalizeRoundList(state.completedRounds.concat(round))
                : omitRound(state.completedRounds, round);
        }
        const fullEndState = endState && typeof endState === 'object'
            ? Object.assign({}, endState, { day: round })
            : recordAvatarFloatingGuideEndState(round, outcome, outcome, 'avatar_floating_guide_state');
        window.avatarFloatingGuideEndState = fullEndState;
        state.lastEndState = fullEndState;
        state.updatedAt = new Date(fullEndState.endedAt || Date.now()).toISOString();
        saveGuideState(state);
        window.dispatchEvent(new CustomEvent(`neko:avatar-floating-guide-${outcome}`, {
            detail: { day: round, state, endState: state.lastEndState },
        }));
        return state;
    }

    function dispatchGuideResetEvent(detail) {
        window.dispatchEvent(new CustomEvent(RESET_EVENT, { detail }));

        try {
            localStorage.setItem(RESET_BROADCAST_KEY, JSON.stringify({
                day: detail.day,
                source: detail.source,
                resetAt: detail.resetAt,
            }));
        } catch (error) {
            console.warn('[AvatarFloatingGuideReset] 跨窗口重置广播失败:', error);
        }
    }

    function resetGuideRoundState(day, options = {}) {
        const round = normalizeRound(day);
        const resetAt = new Date().toISOString();
        const source = options.source || 'home_reset_button';
        const state = loadGuideState();

        resetIcebreakerDay(round);
        state.completedRounds = omitRound(state.completedRounds, round);
        state.skippedRounds = omitRound(state.skippedRounds, round);
        if (state.currentRound === round) {
            state.currentRound = null;
        }
        if (state.lastAutoShownRound === round) {
            state.lastAutoShownRound = null;
            state.lastAutoShownDate = '';
        }
        if (state.lastEndState && state.lastEndState.day === round) {
            state.lastEndState = null;
        }
        state.pendingRound = round;
        state.manualResetRound = round;
        state.updatedAt = resetAt;
        state.resetHistory = state.resetHistory.concat([{ day: round, source, resetAt }]).slice(-RESET_HISTORY_LIMIT);

        saveGuideState(state);
        dispatchGuideResetEvent({ day: round, source, resetAt, state });
        return state;
    }

    async function resetHomeTutorialFallback() {
        HOME_TUTORIAL_KEYS.forEach(key => localStorage.removeItem(key));
        localStorage.setItem(HOME_MANUAL_INTENT_KEY, 'true');
        const detail = { pageKey: 'home', reason: 'manual_home_tutorial_reset' };
        window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', { detail }));
    }

    async function resetHomeTutorialDay(day, options = {}) {
        const round = normalizeRound(day);
        const source = options.source || 'home_reset_button';
        let state = null;
        const manager = window.universalTutorialManager || null;
        if (manager && typeof manager.resetAvatarFloatingGuideRoundState === 'function') {
            state = manager.resetAvatarFloatingGuideRoundState(round, options);
            resetIcebreakerDay(round);
            dispatchGuideResetEvent({
                day: round,
                source,
                resetAt: state && state.updatedAt ? state.updatedAt : new Date().toISOString(),
                state,
            });
        } else {
            state = resetGuideRoundState(round, options);
        }

        await resetHomeTutorialFallback();
        await startAvatarFloatingGuideDay(round, { source });

        showResetToast(round);
        return state;
    }

    function detectModelPrefix() {
        if (document.getElementById('vrm-floating-buttons')) return 'vrm';
        if (document.getElementById('mmd-floating-buttons')) return 'mmd';
        if (document.getElementById('live2d-floating-buttons')) return 'live2d';
        const cfg = window.lanlan_config && window.lanlan_config.model_type;
        if (cfg === 'live3d' || cfg === 'vrm') return 'vrm';
        if (cfg === 'mmd') return 'mmd';
        return 'live2d';
    }

    function resolveSelector(selector, prefix) {
        return String(selector || '').replaceAll('${prefix}', prefix);
    }

    function getTutorialAvatarManager() {
        const manager = window.universalTutorialManager || null;
        if (!manager || typeof manager.beginTutorialAvatarOverride !== 'function') {
            return null;
        }
        return manager;
    }

    function waitForTutorialAvatarManager(timeoutMs = 4000) {
        const existing = getTutorialAvatarManager();
        if (existing) return Promise.resolve(existing);

        if (typeof window.initUniversalTutorialManager === 'function' &&
            !window.__universalTutorialManagerInitialized) {
            window.initUniversalTutorialManager().then(initialized => {
                if (initialized !== false) {
                    window.__universalTutorialManagerInitialized = true;
                }
            }).catch(error => {
                console.warn('[AvatarFloatingGuideReset] 初始化教程管理器失败:', error);
            });
        }

        const startedAt = Date.now();
        return new Promise(resolve => {
            const timer = setInterval(() => {
                const manager = getTutorialAvatarManager();
                if (manager) {
                    clearInterval(timer);
                    resolve(manager);
                    return;
                }
                if (Date.now() - startedAt >= timeoutMs) {
                    clearInterval(timer);
                    resolve(null);
                }
            }, 100);
        });
    }

    function waitForTutorialAvatarEnvironment(timeoutMs = 6000) {
        const startedAt = Date.now();
        return new Promise(resolve => {
            const checkReady = () => {
                const hasModelLoader = !!(
                    typeof window.handleModelReload === 'function' ||
                    typeof window.Live2DManager === 'function' ||
                    window.live2dManager
                );
                const hasLive2dHost = !!(
                    document.getElementById('live2d-container') &&
                    document.getElementById('live2d-canvas')
                );
                if (hasModelLoader && hasLive2dHost) {
                    resolve(true);
                    return;
                }
                if (Date.now() - startedAt >= timeoutMs) {
                    resolve(false);
                    return;
                }
                setTimeout(checkReady, 120);
            };
            checkReady();
        });
    }

    function clearActiveHighlight() {
        document.querySelectorAll('[data-home-avatar-floating-guide-highlight="true"]').forEach(element => {
            element.removeAttribute('data-home-avatar-floating-guide-highlight');
            element.removeAttribute('data-home-avatar-floating-guide-role');
        });
    }

    function closeFloatingPanels() {
        ['live2d', 'vrm', 'mmd'].forEach(prefix => {
            document.querySelectorAll(`[id^="${prefix}-popup-"]`).forEach(popup => {
                popup.style.opacity = '0';
                popup.style.display = 'none';
                popup.classList.remove('is-positioning');
            });
            const manager = prefix === 'live2d'
                ? window.live2dManager
                : (prefix === 'vrm' ? window.vrmManager : window.mmdManager);
            if (manager && typeof manager.updateSeparatePopupTriggerIcon === 'function') {
                ['mic', 'screen'].forEach(buttonId => manager.updateSeparatePopupTriggerIcon(buttonId, false));
            }
        });

        document.querySelectorAll('[data-neko-sidepanel]').forEach(panel => {
            if (typeof window.clearAvatarSidePanelHoverState === 'function') {
                window.clearAvatarSidePanelHoverState(panel);
            }
            panel.remove();
        });
    }

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function getElementCenter(element) {
        const rect = element.getBoundingClientRect();
        return {
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2,
        };
    }

    function getChatInputCenter() {
        const selectors = [
            '#react-chat-window-root .composer-input',
            '#react-chat-window-root .composer-input-shell',
            '#react-chat-window-root .composer-panel',
            '#text-input-area',
        ];
        for (const selector of selectors) {
            const element = document.querySelector(selector);
            if (!element || typeof element.getBoundingClientRect !== 'function') continue;
            const rect = element.getBoundingClientRect();
            if (rect && rect.width > 0 && rect.height > 0) {
                return {
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                };
            }
        }
        return null;
    }

    function safeClickTarget(target) {
        if (!target || typeof target.click !== 'function') return false;
        target.click();
        return true;
    }

    function isVisibleResetElement(element) {
        if (!element || typeof element.getBoundingClientRect !== 'function') return false;
        const rect = element.getBoundingClientRect();
        return !!(rect && rect.width > 0 && rect.height > 0);
    }

    async function waitForResetElement(factory, timeoutMs = 1200) {
        const startedAt = Date.now();
        while (Date.now() - startedAt < timeoutMs) {
            const element = typeof factory === 'function' ? factory() : null;
            if (element && isVisibleResetElement(element)) return element;
            await sleep(80);
        }
        const fallback = typeof factory === 'function' ? factory() : null;
        return fallback && isVisibleResetElement(fallback) ? fallback : null;
    }

    async function ensureResetSettingsPanelVisible(prefix) {
        const settingsPopupSelector = resolveSelector('#${prefix}-popup-settings', prefix);
        const existingPopup = document.querySelector(settingsPopupSelector);
        if (isVisibleResetElement(existingPopup)) return existingPopup;

        const settingsButton = document.querySelector(resolveSelector('#${prefix}-btn-settings', prefix));
        safeClickTarget(settingsButton);
        return waitForResetElement(() => document.querySelector(settingsPopupSelector), 1400);
    }

    async function ensureResetCharacterSettingsSidePanelVisible(prefix) {
        await ensureResetSettingsPanelVisible(prefix);
        const panelSelector = '[data-neko-sidepanel-type="character-settings"]';
        const existingPanel = document.querySelector(panelSelector);
        if (existingPanel && typeof existingPanel._expand === 'function') {
            existingPanel._expand();
        }

        const anchor = document.querySelector(resolveSelector('#${prefix}-sidepanel-character', prefix));
        if (anchor) {
            try {
                anchor.dispatchEvent(new MouseEvent('mouseenter', {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                }));
            } catch (_) {}
        }

        return waitForResetElement(() => document.querySelector(panelSelector), 1400);
    }

    function getResetCharacterSettingsButton(prefix) {
        return document.querySelector(resolveSelector('#${prefix}-menu-character', prefix))
            || document.querySelector(resolveSelector('#${prefix}-sidepanel-character', prefix));
    }

    function collapseResetCharacterSettingsSidePanel() {
        const sidePanel = document.querySelector('[data-neko-sidepanel-type="character-settings"]');
        if (!sidePanel) return;
        if (sidePanel._hoverCollapseTimer) {
            window.clearTimeout(sidePanel._hoverCollapseTimer);
            sidePanel._hoverCollapseTimer = null;
        }
        if (typeof sidePanel._collapse === 'function') {
            sidePanel._collapse();
            return;
        }
        if (sidePanel._collapseTimeout) {
            window.clearTimeout(sidePanel._collapseTimeout);
            sidePanel._collapseTimeout = null;
        }
        sidePanel.style.transition = 'none';
        sidePanel.style.opacity = '0';
        sidePanel.style.display = 'none';
        sidePanel.style.pointerEvents = 'none';
        sidePanel.style.transition = '';
    }

    async function prepareResetStepOperation(step, prefix) {
        const operation = step && typeof step.operation === 'string' ? step.operation : '';
        if (operation === 'day2-settings-detail' || (step && step.id === 'day2_proactive_chat')) {
            await ensureResetSettingsPanelVisible(prefix);
        }
    }

    function getGuideAssistantName() {
        return window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__ ||
            (window.lanlan_config && window.lanlan_config.lanlan_name) ||
            window._currentCatgirl ||
            window.currentCatgirl ||
            'YUI';
    }

    function getGuideAssistantAvatarUrl() {
        if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
            const avatarUrl = window.appChatAvatar.getCurrentAvatarDataUrl();
            if (typeof avatarUrl === 'string' && avatarUrl.trim()) {
                return avatarUrl.trim();
            }
        }
        return '';
    }

    function isHomeChatExternalized() {
        if (window.__NEKO_MULTI_WINDOW__ === true) {
            return true;
        }
        const overlay = document.getElementById('react-chat-window-overlay');
        return !!(overlay && overlay.style.display === 'none');
    }

    function isPcTutorialOverlayAvailable() {
        return !!(
            window.nekoTutorialOverlay
            && typeof window.nekoTutorialOverlay.begin === 'function'
            && typeof window.nekoTutorialOverlay.update === 'function'
            && typeof window.nekoTutorialOverlay.getWindowMetricsSync === 'function'
            && isHomeChatExternalized()
        );
    }

    function getPcTutorialOverlayRunId() {
        try {
            let runId = window.localStorage.getItem(PC_OVERLAY_RUN_ID_KEY) || '';
            if (!runId) {
                runId = 'avatar-floating-reset-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
                window.localStorage.setItem(PC_OVERLAY_RUN_ID_KEY, runId);
            }
            return runId;
        } catch (_) {
            return 'avatar-floating-reset-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
        }
    }

    function getPcTutorialOverlayMetrics() {
        try {
            const metrics = window.nekoTutorialOverlay.getWindowMetricsSync();
            if (metrics && (metrics.bounds || metrics.contentBounds)) {
                return metrics;
            }
        } catch (_) {}
        return {
            bounds: {
                x: Number.isFinite(window.screenX) ? window.screenX : 0,
                y: Number.isFinite(window.screenY) ? window.screenY : 0,
            },
            contentBounds: {
                x: Number.isFinite(window.screenX) ? window.screenX : 0,
                y: Number.isFinite(window.screenY) ? window.screenY : 0,
            },
        };
    }

    function localPointToPcTutorialScreenPoint(x, y) {
        const metrics = getPcTutorialOverlayMetrics();
        const bounds = metrics.bounds || metrics.contentBounds || { x: 0, y: 0 };
        const viewport = window.visualViewport || null;
        const offsetLeft = viewport && Number.isFinite(Number(viewport.offsetLeft)) ? Number(viewport.offsetLeft) : 0;
        const offsetTop = viewport && Number.isFinite(Number(viewport.offsetTop)) ? Number(viewport.offsetTop) : 0;
        return {
            x: Number(bounds.x || 0) + Number(x || 0) + offsetLeft,
            y: Number(bounds.y || 0) + Number(y || 0) + offsetTop,
        };
    }

    let pcTutorialOverlaySequence = 0;

    function sendPcTutorialOverlayCursor(cursorPatch) {
        if (!isPcTutorialOverlayAvailable()) {
            return false;
        }
        const tutorialRunId = getPcTutorialOverlayRunId();
        pcTutorialOverlaySequence = Math.max(pcTutorialOverlaySequence + 1, Date.now() * 1000);
        try {
            Promise.resolve(window.nekoTutorialOverlay.begin({ tutorialRunId })).catch(() => {});
            Promise.resolve(window.nekoTutorialOverlay.update({
                tutorialRunId,
                sceneId: 'avatar-floating-guide-reset',
                sequence: pcTutorialOverlaySequence,
                payload: {
                    cursor: cursorPatch || null,
                },
            })).catch(() => {});
            return true;
        } catch (_) {
            return false;
        }
    }

    function movePcTutorialOverlayCursor(x, y, durationMs, effect = '', effectDurationMs = 0) {
        const point = localPointToPcTutorialScreenPoint(x, y);
        return sendPcTutorialOverlayCursor({
            visible: true,
            x: point.x,
            y: point.y,
            durationMs: Math.max(0, Math.round(Number(durationMs) || 0)),
            effect: effect || '',
            effectDurationMs: Math.max(0, Math.round(Number(effectDurationMs) || 0)),
        });
    }

    function clickPcTutorialOverlayCursor(x, y) {
        return movePcTutorialOverlayCursor(x, y, 420, 'click', 420);
    }

    function wobblePcTutorialOverlayCursor(x, y) {
        return movePcTutorialOverlayCursor(x, y, 0, 'wobble', 2000);
    }

    function hidePcTutorialOverlayCursor() {
        return sendPcTutorialOverlayCursor({
            visible: false,
        });
    }

    function buildGuideChatMessage(step, day) {
        const createdAt = Date.now();
        let time = '';
        try {
            time = new Date(createdAt).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
            });
        } catch (_) {}
        return {
            id: 'avatar-floating-guide-' + day + '-' + createdAt + '-' + Math.random().toString(36).slice(2, 8),
            role: 'assistant',
            author: getGuideAssistantName(),
            time,
            createdAt,
            avatarUrl: getGuideAssistantAvatarUrl(),
            blocks: [{
                type: 'text',
                text: step.text || '',
            }],
            status: 'sent',
        };
    }

    function appendTutorialTextToChat(step, day) {
        if (!step || !step.text) return null;
        const message = buildGuideChatMessage(step, day);

        if (isHomeChatExternalized()) {
            let posted = false;
            const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
            if (channel && typeof channel.postMessage === 'function') {
                try {
                    channel.postMessage({
                        action: 'yui_guide_append_chat_message',
                        message,
                        timestamp: message.createdAt,
                    });
                    posted = true;
                } catch (error) {
                    console.warn('[AvatarFloatingGuideReset] 转发教程文本到外置聊天窗失败:', error);
                }
            }
            if (window.nekoTutorialOverlay && typeof window.nekoTutorialOverlay.relayToChat === 'function') {
                try {
                    let tutorialRunId = '';
                    try {
                        tutorialRunId = window.localStorage.getItem('yuiGuidePcOverlayRunId') || '';
                    } catch (_) {}
                    window.nekoTutorialOverlay.relayToChat({
                        action: 'yui_guide_append_chat_message',
                        message,
                        tutorialRunId,
                        timestamp: message.createdAt,
                    });
                    posted = true;
                } catch (error) {
                    console.warn('[AvatarFloatingGuideReset] 原生转发教程文本到外置聊天窗失败:', error);
                }
            }
            if (posted) {
                return message;
            }
        }

        const host = window.reactChatWindowHost;
        if (host && typeof host.appendMessage === 'function') {
            const appended = host.appendMessage(message);
            if (typeof host.openWindow === 'function') {
                try {
                    host.openWindow();
                } catch (error) {
                    console.warn('[AvatarFloatingGuideReset] 打开聊天窗失败:', error);
                }
            }
            return appended || message;
        }

        if (typeof window.appendMessage === 'function') {
            window.appendMessage(step.text, 'gemini', true);
            return message;
        }

        console.log('[AvatarFloatingGuideReset] 教程文本:', step.text);
        return message;
    }

    function isTutorialAvatarVisible() {
        const container = document.getElementById('live2d-container');
        if (!container) return false;
        const style = window.getComputedStyle ? window.getComputedStyle(container) : null;
        const display = style ? style.display : container.style.display;
        const visibility = style ? style.visibility : container.style.visibility;
        return display !== 'none' && visibility !== 'hidden' && !container.classList.contains('hidden');
    }

    async function forceShowTutorialAvatar(manager) {
        const payload = {
            model_type: 'live2d',
            live2d: 'yui-origin',
            live2d_idle_animation: '',
        };

        if (manager && typeof manager.loadTemporaryTutorialLive2dModel === 'function') {
            await manager.loadTemporaryTutorialLive2dModel(payload);
            return true;
        }
        return false;
    }

    function setRoundCurrent(day) {
        const round = normalizeRound(day);
        const state = loadGuideState();
        state.currentRound = round;
        if (state.lastEndState && state.lastEndState.day === round) {
            state.lastEndState = null;
        }
        state.updatedAt = new Date().toISOString();
        saveGuideState(state);
    }

    async function startAvatarFloatingGuideDay(day, options = {}) {
        const round = normalizeRound(day);
        const config = DAY_TUTORIALS[round];
        if (!config) return null;

        const manager = await waitForTutorialAvatarManager();
        if (manager && typeof manager.startAvatarFloatingGuideRound === 'function') {
            return manager.startAvatarFloatingGuideRound(round, {
                source: options.source || 'home_reset_button',
            });
        }

        if (activeRoundPlayer && typeof activeRoundPlayer.destroy === 'function') {
            await activeRoundPlayer.destroy('restart');
        }

        setRoundCurrent(round);
        activeRoundPlayer = createRoundPlayer(round, config, options);
        await activeRoundPlayer.start();
        return activeRoundPlayer;
    }

    function createRoundPlayer(day, config, options = {}) {
        let stepIndex = 0;
        let shell = null;
        let titleEl = null;
        let textEl = null;
        let metaEl = null;
        let prevBtn = null;
        let nextBtn = null;
        let finishBtn = null;
        let stepRunToken = 0;
        let destroyed = false;
        let stopping = false;
        let avatarOverrideStarted = false;
        let fallbackAvatarLoaded = false;
        let overlayAdapter = null;
        let highlightController = null;
        let interactionTakeover = null;
        let skipController = null;
        let interruptController = null;
        let pointerInterruptHandler = null;
        let lastPointerPoint = null;
        let interruptCount = 0;
        let angryExitTriggered = false;
        const prefix = detectModelPrefix();
        let manager = null;

        async function start() {
            manager = await waitForTutorialAvatarManager();
            await waitForTutorialAvatarEnvironment();
            if (manager && typeof manager.beginTutorialAvatarOverride === 'function') {
                try {
                    await manager.beginTutorialAvatarOverride();
                    avatarOverrideStarted = true;
                } catch (error) {
                    console.warn('[AvatarFloatingGuideReset] 第 ' + day + ' 天临时切换 yui-origin 失败，继续显示教程:', error);
                }
            }
            if (!isTutorialAvatarVisible()) {
                try {
                    fallbackAvatarLoaded = await forceShowTutorialAvatar(manager);
                } catch (error) {
                    console.warn('[AvatarFloatingGuideReset] fallback 加载 yui-origin 失败:', error);
                }
            }
            buildShell();
            setupLifecycleControllers();
            renderStep();
            window.dispatchEvent(new CustomEvent('neko:avatar-floating-guide-started', {
                detail: {
                    day,
                    source: options.source || 'home_reset_button',
                    usesTutorialAvatarOverride: avatarOverrideStarted || fallbackAvatarLoaded,
                },
            }));
        }

        function buildShell() {
            shell = document.createElement('section');
            shell.id = 'home-avatar-floating-guide-player';
            shell.className = 'home-avatar-floating-guide-player';
            shell.setAttribute('role', 'dialog');
            shell.setAttribute('aria-live', 'polite');
            shell.innerHTML = [
                '<div class="home-avatar-floating-guide-kicker">临时新手教程</div>',
                '<h2 class="home-avatar-floating-guide-title"></h2>',
                '<p class="home-avatar-floating-guide-text"></p>',
                '<div class="home-avatar-floating-guide-meta"></div>',
                '<div class="home-avatar-floating-guide-actions">',
                '  <button type="button" data-guide-action="prev">上一步</button>',
                '  <button type="button" data-guide-action="next">下一步</button>',
                '  <button type="button" data-guide-action="finish">完成</button>',
                '</div>',
            ].join('');
            titleEl = shell.querySelector('.home-avatar-floating-guide-title');
            textEl = shell.querySelector('.home-avatar-floating-guide-text');
            metaEl = shell.querySelector('.home-avatar-floating-guide-meta');
            prevBtn = shell.querySelector('[data-guide-action="prev"]');
            nextBtn = shell.querySelector('[data-guide-action="next"]');
            finishBtn = shell.querySelector('[data-guide-action="finish"]');
            prevBtn.addEventListener('click', () => {
                stepIndex = Math.max(0, stepIndex - 1);
                renderStep();
            });
            nextBtn.addEventListener('click', () => {
                stepIndex = Math.min(config.steps.length - 1, stepIndex + 1);
                renderStep();
            });
            finishBtn.addEventListener('click', () => {
                destroy('complete').catch(error => console.warn('[AvatarFloatingGuideReset] 完成清理失败:', error));
            });
            document.body.appendChild(shell);
        }

        function setupLifecycleControllers() {
            overlayAdapter = createGuideOverlayAdapter();

            if (window.TutorialHighlightController &&
                typeof window.TutorialHighlightController.createController === 'function') {
                highlightController = window.TutorialHighlightController.createController({
                    document,
                    window,
                    overlay: overlayAdapter,
                });
            }

            if (window.TutorialInteractionTakeover &&
                typeof window.TutorialInteractionTakeover.createController === 'function') {
                interactionTakeover = window.TutorialInteractionTakeover.createController({
                    document,
                    window,
                    page: 'home',
                    overlay: overlayAdapter,
                    allowTarget: isAllowedTutorialTarget,
                    isSystemDialogTarget,
                    allowTouchPassthrough: () => false,
                    isDestroyed: () => destroyed,
                    externalizedChatDetector: isHomeChatExternalized,
                    externalChatChannelProvider: () => (
                        window.appInterpage && window.appInterpage.nekoBroadcastChannel
                    ) || null,
                });
                interactionTakeover.setActive(true);
                interactionTakeover.enableFaceForwardLock();
                interactionTakeover.setExternalizedChatButtonsDisabled(true);
                interactionTakeover.setExternalizedChatSpotlight('window');
            }

            if (window.TutorialSkipController &&
                typeof window.TutorialSkipController.createController === 'function') {
                skipController = window.TutorialSkipController.createController({
                    document,
                    buttonId: 'neko-tutorial-skip-btn',
                });
                skipController.show({
                    label: '跳过',
                    onSkip: () => destroy('skip'),
                });
            }

            if (window.TutorialInterruptController &&
                typeof window.TutorialInterruptController.createController === 'function') {
                interruptController = window.TutorialInterruptController.createController({
                    overlay: overlayAdapter,
                    cursor: {
                        resistTo: (x, y) => moveGhostCursorToPoint(x, y, 'wobble'),
                    },
                    callbacks: createInterruptCallbacks(),
                });
                startPointerInterruptWatch();
            }
        }

        function createGuideOverlayAdapter() {
            const highlighted = new Set();
            let persistentTarget = null;
            let actionTarget = null;
            let secondaryTarget = null;
            let extraTargets = [];

            function clearElement(element) {
                if (!element || typeof element.removeAttribute !== 'function') return;
                element.removeAttribute('data-home-avatar-floating-guide-highlight');
                element.removeAttribute('data-home-avatar-floating-guide-role');
                highlighted.delete(element);
            }

            function markElement(element, role) {
                if (!element || typeof element.setAttribute !== 'function') return;
                element.setAttribute('data-home-avatar-floating-guide-highlight', 'true');
                element.setAttribute('data-home-avatar-floating-guide-role', role);
                highlighted.add(element);
            }

            function refresh() {
                Array.from(highlighted).forEach(clearElement);
                markElement(persistentTarget, 'persistent');
                markElement(actionTarget, 'action');
                markElement(secondaryTarget, 'secondary');
                extraTargets.forEach(element => markElement(element, 'extra'));
            }

            return {
                setPersistentSpotlight(element) {
                    persistentTarget = element || null;
                    refresh();
                },
                clearPersistentSpotlight() {
                    persistentTarget = null;
                    refresh();
                },
                activateSpotlight(element) {
                    actionTarget = element || null;
                    refresh();
                },
                clearActionSpotlight() {
                    actionTarget = null;
                    secondaryTarget = null;
                    refresh();
                },
                activateSecondarySpotlight(element) {
                    secondaryTarget = element || null;
                    refresh();
                },
                setExtraSpotlights(elements) {
                    extraTargets = Array.isArray(elements) ? elements.filter(Boolean) : [];
                    refresh();
                },
                clearExtraSpotlights() {
                    extraTargets = [];
                    refresh();
                },
                setTakingOver(active) {
                    document.body.classList.toggle('home-avatar-floating-guide-taking-over', active === true);
                },
                setInteractionShieldSuppressed() {},
                hideBubble() {},
                hidePluginPreview() {},
                setAngry(active) {
                    document.body.classList.toggle('home-avatar-floating-guide-angry', active === true);
                },
                destroy() {
                    persistentTarget = null;
                    actionTarget = null;
                    secondaryTarget = null;
                    extraTargets = [];
                    refresh();
                    document.body.classList.remove(
                        'home-avatar-floating-guide-taking-over',
                        'home-avatar-floating-guide-angry'
                    );
                },
            };
        }

        function createInterruptCallbacks() {
            return {
                isDestroyed: () => destroyed,
                isStopping: () => stopping,
                getInterruptCount: () => interruptCount,
                isAngryExitTriggered: () => angryExitTriggered,
                setAngryExitTriggered: (value) => {
                    angryExitTriggered = value === true;
                },
                getCurrentSceneId: () => {
                    const step = config.steps[stepIndex] || null;
                    return step ? step.id : null;
                },
                getLastPointerPoint: () => lastPointerPoint,
                getStep: (id) => {
                    if (id === 'interrupt_resist_light') {
                        return {
                            id,
                            performance: {
                                bubbleText: '先跟着教程走完这一小步，稍后就把操作还给你。',
                                emotion: 'surprised',
                            },
                        };
                    }
                    if (id === 'interrupt_angry_exit') {
                        return {
                            id,
                            performance: {
                                bubbleText: '这轮教程先停在这里，你可以稍后从重置按钮重新开始。',
                                voiceKey: 'avatar_floating_interrupt_exit',
                                emotion: 'angry',
                            },
                        };
                    }
                    return null;
                },
                resolveBubbleText: (performance) => performance && performance.bubbleText,
                appendGuideChatMessage: (message, meta) => appendTutorialTextToChat({
                    id: 'avatar_floating_interrupt_' + Date.now(),
                    text: message,
                    voiceKey: (meta && meta.voiceKey) || '',
                }, day),
                applyGuideEmotion: () => {},
                pauseCurrentSceneForResistance: () => {},
                resumeCurrentSceneAfterResistance: () => {},
                interruptNarrationForResistance: () => {},
                prepareResistanceCursorReveal: () => {},
                runInterruptResistPerformance: () => null,
                speakResistanceLine: () => null,
                speakGuideLine: () => null,
                capturePresentationSnapshot: () => null,
                restoreGuidePresentationSnapshot: () => false,
                restoreCurrentScenePresentation: () => {},
                getActiveNarration: () => null,
                scheduleNarrationResume: () => {},
                recordExperienceMetric: () => {},
                clearSceneTimers: () => {},
                disableInterrupts: stopPointerInterruptWatch,
                cancelActiveNarration: () => {},
                beginGuideInterruptPresentation: () => {
                    hideGhostCursor();
                    closeFloatingPanels();
                },
                notifyPluginDashboardNarrationFinished: () => {},
                setTutorialTakingOver: (active) => {
                    if (interactionTakeover) interactionTakeover.setActive(active === true);
                },
                requestTermination: (reason, tutorialReason) => destroy(tutorialReason || reason || 'skip'),
            };
        }

        function isAllowedTutorialTarget(target) {
            if (!target || typeof target.closest !== 'function') return false;
            return !!target.closest([
                '#home-avatar-floating-guide-player',
                '#neko-tutorial-skip-btn',
                '#home-tutorial-reset-controls',
            ].join(','));
        }

        function isSystemDialogTarget(target) {
            if (!target || typeof target.closest !== 'function') return false;
            return !!target.closest([
                '[role="dialog"]',
                '.modal',
                '.swal2-container',
                '.toast',
                '#status-toast',
            ].join(','));
        }

        function startPointerInterruptWatch() {
            stopPointerInterruptWatch();
            pointerInterruptHandler = (event) => {
                if (destroyed || stopping || !interruptController || !event || event.isTrusted === false) return;
                if (isAllowedTutorialTarget(event.target)) return;

                const point = { x: event.clientX, y: event.clientY, time: Date.now() };
                const previousPoint = lastPointerPoint;
                lastPointerPoint = point;
                if (!previousPoint) return;

                const distance = Math.hypot(point.x - previousPoint.x, point.y - previousPoint.y);
                const elapsedMs = Math.max(16, point.time - previousPoint.time);
                const speed = distance / elapsedMs * 1000;
                if (distance < 80 && speed < 1200) return;

                interruptCount += 1;
                if (interruptCount >= 3) {
                    interruptController.abortAsAngryExit('avatar_floating_pointer_interrupt');
                    return;
                }
                interruptController.playLightResistance(point.x, point.y, {
                    suppressCursorReveal: true,
                });
            };
            document.addEventListener('pointermove', pointerInterruptHandler, true);
        }

        function stopPointerInterruptWatch() {
            if (!pointerInterruptHandler) return;
            document.removeEventListener('pointermove', pointerInterruptHandler, true);
            pointerInterruptHandler = null;
        }

        function renderStep() {
            const token = ++stepRunToken;
            const step = config.steps[stepIndex] || config.steps[0];
            clearActiveHighlight();
            closeFloatingPanels();
            applyStepHighlights(step, null);
            titleEl.textContent = config.title;
            textEl.textContent = '教程文本已输出到对话框。';
            metaEl.textContent = `文本输出 → 准备高亮 · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
            prevBtn.disabled = stepIndex <= 0;
            nextBtn.hidden = stepIndex >= config.steps.length - 1;
            finishBtn.hidden = stepIndex < config.steps.length - 1;
            appendTutorialTextToChat(step, day);

            runStepShowcase(step, token).catch(error => {
                if (!destroyed && token === stepRunToken) {
                    console.warn('[AvatarFloatingGuideReset] 步骤演示失败:', error);
                    metaEl.textContent = `文本输出 → 目标缺失，已降级为说明 · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
                }
            });
        }

        async function runStepShowcase(step, token) {
            await sleep(260);
            if (destroyed || token !== stepRunToken) return;

            await prepareResetStepOperation(step, prefix);
            if (destroyed || token !== stepRunToken) return;

            const selector = resolveSelector(step.selector, prefix);
            const target = selector ? document.querySelector(selector) : null;
            if (target) {
                applyStepHighlights(step, target);
                if (typeof target.scrollIntoView === 'function') {
                    target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                }
                metaEl.textContent = `文本输出 → action 高亮 → ghost cursor 移动 · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
                await moveGhostCursorTo(target, step.cursorAction || 'show', token);
                if (destroyed || token !== stepRunToken) return;
                if (step.operation === 'safe-click') {
                    metaEl.textContent = `文本输出 → 高亮 → cursor click → 打开真实 UI · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
                    safeClickTarget(target);
                    await sleep(320);
                } else if (step.operation === 'day2-open-settings-personalization') {
                    metaEl.textContent = `文本输出 → 高亮 → cursor click → 打开设置面板 · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
                    safeClickTarget(target);
                    await ensureResetSettingsPanelVisible(prefix);
                } else if (step.operation === 'day2-settings-detail') {
                    metaEl.textContent = `文本输出 → 高亮角色设置按钮 → cursor click → 展开角色设置侧边栏 · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
                    safeClickTarget(target);
                    const sidePanel = await ensureResetCharacterSettingsSidePanelVisible(prefix);
                    if (destroyed || token !== stepRunToken) return;
                    if (sidePanel) {
                        applyStepHighlights(step, sidePanel);
                        await moveGhostCursorTo(sidePanel, 'move', token);
                        await runGhostCursorEllipseOnTarget(sidePanel, token);
                        collapseResetCharacterSettingsSidePanel();
                    }
                } else if (step.operation === 'cleanup') {
                    closeFloatingPanels();
                    metaEl.textContent = `文本输出 → 清理临时弹窗和高亮 · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
                } else {
                    metaEl.textContent = `文本输出 → 高亮 → cursor 停留，不修改用户状态 · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
                }
            } else {
                metaEl.textContent = `文本输出 → 目标缺失，已降级为说明 · 步骤 ${stepIndex + 1}/${config.steps.length} · voiceKey: ${step.voiceKey}`;
                applyStepHighlights(step, null);
                hideGhostCursor();
            }
            metaEl.dataset.targetFound = target ? 'true' : 'false';
            metaEl.dataset.selector = selector;
        }

        function applyStepHighlights(step, target) {
            const characterSettingsButton = (
                step
                && (step.id === 'day2_personalization_detail' || step.id === 'day2_proactive_chat')
            )
                ? getResetCharacterSettingsButton(prefix)
                : null;
            const secondaryTarget = characterSettingsButton && characterSettingsButton !== target
                ? characterSettingsButton
                : null;
            if (highlightController) {
                highlightController.applyGuideHighlights({
                    key: step && step.id ? step.id : 'avatar-floating-guide-step',
                    persistent: shell,
                    primary: target || null,
                    secondary: secondaryTarget,
                });
                if (typeof highlightController.setPreciseHighlightTargets === 'function') {
                    highlightController.setPreciseHighlightTargets([target, secondaryTarget].filter(Boolean));
                }
                return;
            }

            clearActiveHighlight();
            if (shell) {
                shell.setAttribute('data-home-avatar-floating-guide-highlight', 'true');
                shell.setAttribute('data-home-avatar-floating-guide-role', 'persistent');
            }
            if (target) {
                target.setAttribute('data-home-avatar-floating-guide-highlight', 'true');
                target.setAttribute('data-home-avatar-floating-guide-role', 'action');
            }
            if (secondaryTarget) {
                secondaryTarget.setAttribute('data-home-avatar-floating-guide-highlight', 'true');
                secondaryTarget.setAttribute('data-home-avatar-floating-guide-role', 'secondary');
            }
        }

        async function moveGhostCursorTo(target, action, token) {
            if (!target) return;
            if (action === 'none') {
                hideGhostCursor();
                return;
            }
            if (action === 'input-origin') {
                const origin = getChatInputCenter();
                if (origin) {
                    if (isPcTutorialOverlayAvailable()) {
                        movePcTutorialOverlayCursor(origin.x, origin.y, 0);
                    }
                } else {
                    hideGhostCursor();
                }
                return;
            }
            if (isPcTutorialOverlayAvailable()) {
                const center = getElementCenter(target);
                await moveGhostCursorToPoint(center.x, center.y, action, token);
            }
        }

        async function moveGhostCursorToPoint(x, y, action = 'show', token = stepRunToken) {
            if (isPcTutorialOverlayAvailable()) {
                movePcTutorialOverlayCursor(x, y, 520);
                await sleep(520);
                if (destroyed || token !== stepRunToken) return;
                if (action === 'click') {
                    clickPcTutorialOverlayCursor(x, y);
                    await sleep(180);
                } else if (action === 'wobble') {
                    wobblePcTutorialOverlayCursor(x, y);
                    await sleep(360);
                }
            }
        }

        async function runGhostCursorEllipseOnTarget(target, token = stepRunToken) {
            if (!isPcTutorialOverlayAvailable() || !target || typeof target.getBoundingClientRect !== 'function') return;
            const rect = target.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) return;
            const centerX = rect.left + rect.width / 2;
            const centerY = rect.top + rect.height / 2;
            const radiusX = Math.max(36, rect.width * 0.32);
            const radiusY = Math.max(60, rect.height * 0.36);
            const startedAt = Date.now();
            const durationMs = 2200;
            while (!destroyed && token === stepRunToken && Date.now() - startedAt < durationMs) {
                const progress = ((Date.now() - startedAt) % 1400) / 1400;
                const angle = progress * Math.PI * 2;
                movePcTutorialOverlayCursor(
                    centerX + Math.cos(angle) * radiusX,
                    centerY + Math.sin(angle) * radiusY,
                    160
                );
                await sleep(160);
            }
        }

        function hideGhostCursor() {
            hidePcTutorialOverlayCursor();
        }

        async function destroy(reason = 'complete') {
            if (destroyed) return;
            const rawReason = typeof reason === 'string' && reason.trim()
                ? reason.trim().toLowerCase()
                : 'complete';
            const outcome = rawReason === 'complete'
                ? 'complete'
                : (rawReason === 'skip' || rawReason === 'escape' || rawReason === 'angry_exit' ? 'skip' : 'destroy');
            destroyed = true;
            stopping = true;
            stepRunToken += 1;
            stopPointerInterruptWatch();
            if (interruptController && typeof interruptController.destroy === 'function') {
                interruptController.destroy();
            }
            interruptController = null;
            if (skipController && typeof skipController.destroy === 'function') {
                skipController.destroy();
            }
            skipController = null;
            if (interactionTakeover && typeof interactionTakeover.destroy === 'function') {
                interactionTakeover.destroy();
            }
            interactionTakeover = null;
            if (highlightController && typeof highlightController.destroy === 'function') {
                highlightController.destroy();
            }
            highlightController = null;
            if (overlayAdapter && typeof overlayAdapter.destroy === 'function') {
                overlayAdapter.destroy();
            }
            overlayAdapter = null;
            clearActiveHighlight();
            closeFloatingPanels();
            if (shell && shell.parentNode) {
                shell.parentNode.removeChild(shell);
            }
            shell = null;
            if (avatarOverrideStarted && manager && typeof manager.restoreTutorialAvatarOverride === 'function') {
                try {
                    await manager.restoreTutorialAvatarOverride();
                } catch (error) {
                    console.warn('[AvatarFloatingGuideReset] 恢复用户模型失败:', error);
                }
            }
            if (!avatarOverrideStarted && fallbackAvatarLoaded && typeof window.showCurrentModel === 'function') {
                try {
                    await window.showCurrentModel();
                } catch (error) {
                    console.warn('[AvatarFloatingGuideReset] fallback 模型恢复失败:', error);
                }
            }
            const endState = recordAvatarFloatingGuideEndState(day, outcome, rawReason, options.source || 'home_reset_button');
            if (outcome === 'skip' || outcome === 'complete') {
                markGuideRoundOutcome(day, outcome, endState);
            }
            if (activeRoundPlayer && activeRoundPlayer.destroy === destroy) {
                activeRoundPlayer = null;
            }
        }

        return { start, destroy };
    }

    function showResetToast(day) {
        const message = `已重置第 ${day} 天新手教程。`;
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, 2500, { priority: 1 });
            return;
        }
        console.log('[AvatarFloatingGuideReset]', message);
    }

    function bindResetButtons(root = document) {
        const buttons = Array.from(root.querySelectorAll('[data-home-tutorial-reset-day]'));
        buttons.forEach(button => {
            if (button.dataset.tutorialResetBound === 'true') return;
            button.dataset.tutorialResetBound = 'true';
            button.addEventListener('click', async () => {
                const day = Number(button.dataset.homeTutorialResetDay);
                button.disabled = true;
                try {
                    await resetHomeTutorialDay(day, { source: 'home_reset_button' });
                } catch (error) {
                    console.error('[AvatarFloatingGuideReset] 重置失败:', error);
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast('新手教程重置失败，请稍后再试。', 3000, { priority: 2 });
                    }
                } finally {
                    button.disabled = false;
                }
            });
        });
    }

    function bootstrap() {
        bindResetButtons();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrap, { once: true });
    } else {
        bootstrap();
    }

    window.AvatarFloatingGuideReset = {
        STORAGE_KEY,
        RESET_EVENT,
        loadGuideState,
        resetGuideRoundState,
        startAvatarFloatingGuideDay,
        resetHomeTutorialDay,
        bindResetButtons,
    };
    window.resetHomeTutorialDay = resetHomeTutorialDay;
    window.resetAvatarFloatingGuideDay = resetHomeTutorialDay;
    window.startAvatarFloatingGuideDay = startAvatarFloatingGuideDay;
})();
