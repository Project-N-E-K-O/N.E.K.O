(function () {
    'use strict';

    const STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    const RESET_EVENT = 'neko:avatar-floating-guide-reset';
    const RESET_BROADCAST_KEY = 'neko_avatar_floating_guide_reset_event';
    const HOME_TUTORIAL_KEYS = ['neko_tutorial_home_yui_v1', 'neko_tutorial_home'];
    const HOME_MANUAL_INTENT_KEY = 'neko_tutorial_home_manual_intent';
    const ROUND_COUNT = 7;
    const RESET_HISTORY_LIMIT = 20;
    const DAY_TUTORIALS = {
        2: {
            title: '第 2 天：屏幕分享、声音与小窗约定',
            steps: [
                {
                    id: 'day2_intro_context',
                    selector: '#home-avatar-floating-guide-player',
                    text: '第二天会先承接昨天是否用过声音聊天，再把你带到屏幕分享入口旁边。',
                    voiceKey: 'avatar_floating_day2_intro',
                    cursorAction: 'wobble',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day2_screen_entry',
                    selector: '#${prefix}-btn-screen',
                    text: '在跟悠怡通语音电话的时候，再点亮这个小按钮，就能把屏幕分享给她。',
                    voiceKey: 'avatar_floating_day2_screen_entry',
                    cursorAction: 'show',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day2_screen_source_popup',
                    selector: '.${prefix}-trigger-icon-screen',
                    text: '小三角会打开来源列表，可以选整个屏幕，也可以只选某个窗口。',
                    voiceKey: 'avatar_floating_day2_screen_source_popup',
                    cursorAction: 'click',
                    operation: 'safe-click',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day2_mic_recap',
                    selector: '#${prefix}-btn-mic',
                    text: '声音入口和屏幕分享会一起出现，方便用户边说边看。',
                    voiceKey: 'avatar_floating_day2_mic_recap',
                    cursorAction: 'show',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day2_mic_popup',
                    selector: '.${prefix}-trigger-icon-mic',
                    text: '麦克风弹窗会展示音量、空间音频、降噪、增益、实时输入状态和设备列表。',
                    voiceKey: 'avatar_floating_day2_mic_popup',
                    cursorAction: 'click',
                    operation: 'safe-click',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day2_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '这一轮结束后会关掉临时弹窗，把界面还给用户；屏幕分享按钮本身不会被强行启动。',
                    voiceKey: 'avatar_floating_day2_wrap',
                    cursorAction: 'wobble',
                    operation: 'cleanup',
                    performanceCue: null,
                },
            ],
        },
        3: {
            title: '第 3 天：互动、娱乐与摸得到的陪伴',
            steps: [
                {
                    id: 'day3_chat_tools',
                    selector: '#react-chat-window-root .composer-bottom-tools',
                    text: '第三天会强接管聊天窗工具区，介绍 Avatar 互动工具、Galgame 模式和小游戏邀请入口。',
                    voiceKey: 'avatar_floating_day3_intro',
                    cursorAction: 'show',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day3_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '这一轮会收起临时工具菜单，把界面还给用户，后续玩法邀请只从聊天窗支线发起。',
                    voiceKey: 'avatar_floating_day3_wrap',
                    cursorAction: 'wobble',
                    operation: 'cleanup',
                    performanceCue: null,
                },
            ],
        },
        4: {
            title: '第 4 天：相处距离、主动陪伴与模型行为',
            steps: [
                {
                    id: 'day4_settings_entry',
                    selector: '#${prefix}-btn-settings',
                    text: '第四天讲相处方式：对话节奏、动画表现、锁定、离开/回来和主动陪伴距离。',
                    voiceKey: 'avatar_floating_day4_settings_entry',
                    cursorAction: 'click',
                    operation: 'safe-click',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day4_lock_interaction',
                    selector: '#${prefix}-lock-icon',
                    text: '小锁会控制模型交互。需要避免误触或想固定她的位置时，可以用它。',
                    voiceKey: 'avatar_floating_day4_lock_interaction',
                    cursorAction: 'show',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day4_goodbye_return',
                    selector: '#${prefix}-btn-goodbye',
                    text: '想安静一会儿时，可以请她先回小猫窝；需要她时，再点返回按钮。',
                    voiceKey: 'avatar_floating_day4_goodbye_return',
                    cursorAction: 'show',
                    operation: 'none',
                    performanceCue: null,
                },
                {
                    id: 'day4_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '四天的教程到这里收尾。之后这些按钮都在模型旁边，想用的时候再叫她就好。',
                    voiceKey: 'avatar_floating_day4_wrap',
                    cursorAction: 'wobble',
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
                    text: '第五天会强接管设置入口，展示角色设置、模型管理、声音克隆与 API 等长期入口。',
                    voiceKey: 'avatar_floating_day5_character_settings',
                    cursorAction: 'click',
                    operation: 'safe-click',
                    keepPanelsOpen: true,
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
                    text: '这一轮会清理设置弹窗并播放每日花瓣收尾。',
                    voiceKey: 'avatar_floating_day5_wrap',
                    cursorAction: 'wobble',
                    operation: 'cleanup',
                    performanceCue: null,
                },
            ],
        },
        6: {
            title: '第 6 天：Agent、任务 HUD 与能力节奏',
            steps: [
                {
                    id: 'day6_agent_entry',
                    selector: '#${prefix}-btn-agent',
                    text: '第六天会强接管猫爪入口，展示状态、权限、用户插件和任务 HUD。',
                    voiceKey: 'avatar_floating_day6_intro',
                    cursorAction: 'click',
                    operation: 'safe-click',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day6_task_hud',
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
                    cursorAction: 'wobble',
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
                    selector: '#${prefix}-menu-memory',
                    text: '第七天会强接管记忆与存储入口，回顾七日教程并强调用户可编辑、可清理。',
                    voiceKey: 'avatar_floating_day7_memory_review',
                    cursorAction: 'show',
                    operation: 'none',
                    keepPanelsOpen: true,
                    performanceCue: null,
                },
                {
                    id: 'day7_graduation_wrap',
                    selector: '#home-avatar-floating-guide-player',
                    text: '毕业收尾会恢复用户原模型与交互权限，保存第七天完成态。',
                    voiceKey: 'avatar_floating_day7_wrap',
                    cursorAction: 'wobble',
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
            updatedAt: parsed.updatedAt || null,
            resetHistory: Array.isArray(parsed.resetHistory) ? parsed.resetHistory.slice(-RESET_HISTORY_LIMIT) : [],
        };
    }

    function saveGuideState(state) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
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

    function markGuideRoundOutcome(day, outcome) {
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
            state.completedRounds = omitRound(state.completedRounds, round);
        }
        state.updatedAt = new Date().toISOString();
        saveGuideState(state);
        window.dispatchEvent(new CustomEvent(`neko:avatar-floating-guide-${outcome}`, {
            detail: { day: round, state },
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

        state.completedRounds = omitRound(state.completedRounds, round);
        state.skippedRounds = omitRound(state.skippedRounds, round);
        if (state.currentRound === round) {
            state.currentRound = null;
        }
        if (state.lastAutoShownRound === round) {
            state.lastAutoShownRound = null;
            state.lastAutoShownDate = '';
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
        window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
            detail: { pageKey: 'home', reason: 'manual_home_tutorial_reset' },
        }));
    }

    async function resetHomeTutorialDay(day, options = {}) {
        const round = normalizeRound(day);
        let state = null;
        const manager = window.universalTutorialManager || null;
        if (manager && typeof manager.resetAvatarFloatingGuideRoundState === 'function') {
            state = manager.resetAvatarFloatingGuideRoundState(round, options);
        } else {
            state = resetGuideRoundState(round, options);
        }

        if (round === 1) {
            if (window.universalTutorialManager &&
                typeof window.universalTutorialManager.resetPageTutorial === 'function') {
                await window.universalTutorialManager.resetPageTutorial('home');
            } else {
                await resetHomeTutorialFallback();
            }
        } else {
            await startAvatarFloatingGuideDay(round, { source: options.source || 'home_reset_button' });
        }

        showResetToast(round);
        return state;
    }

    function detectModelPrefix() {
        if (document.getElementById('vrm-floating-buttons')) return 'vrm';
        if (document.getElementById('mmd-floating-buttons')) return 'mmd';
        if (document.getElementById('live2d-floating-buttons')) return 'live2d';
        const cfg = window.lanlan_config && window.lanlan_config.model_type;
        if (cfg === 'vrm') return 'vrm';
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

    function safeClickTarget(target) {
        if (!target || typeof target.click !== 'function') return false;
        target.click();
        return true;
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
        const overlay = document.getElementById('react-chat-window-overlay');
        return !!(overlay && overlay.style.display === 'none');
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
            const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
            if (channel && typeof channel.postMessage === 'function') {
                try {
                    channel.postMessage({
                        action: 'yui_guide_append_chat_message',
                        message,
                        timestamp: message.createdAt,
                    });
                    return message;
                } catch (error) {
                    console.warn('[AvatarFloatingGuideReset] 转发教程文本到外置聊天窗失败:', error);
                }
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
        const state = loadGuideState();
        state.currentRound = normalizeRound(day);
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
        let cursorEl = null;
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
            cursorEl = document.createElement('div');
            cursorEl.className = 'home-avatar-floating-guide-cursor';
            cursorEl.setAttribute('aria-hidden', 'true');
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
            document.body.appendChild(cursorEl);
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
                    suppressCursorReaction: false,
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
            if (highlightController) {
                highlightController.applyGuideHighlights({
                    key: step && step.id ? step.id : 'avatar-floating-guide-step',
                    persistent: shell,
                    primary: target || null,
                });
                if (typeof highlightController.setPreciseHighlightTargets === 'function') {
                    highlightController.setPreciseHighlightTargets(target ? [target] : []);
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
        }

        async function moveGhostCursorTo(target, action, token) {
            if (!cursorEl || !target) return;
            const center = getElementCenter(target);
            await moveGhostCursorToPoint(center.x, center.y, action, token);
        }

        async function moveGhostCursorToPoint(x, y, action = 'show', token = stepRunToken) {
            if (!cursorEl) return;
            cursorEl.classList.add('is-visible');
            cursorEl.classList.remove('is-clicking', 'is-wobbling');
            cursorEl.style.left = `${Math.round(x)}px`;
            cursorEl.style.top = `${Math.round(y)}px`;
            await sleep(520);
            if (destroyed || token !== stepRunToken) return;
            if (action === 'click') {
                cursorEl.classList.add('is-clicking');
                await sleep(180);
                cursorEl.classList.remove('is-clicking');
            } else if (action === 'wobble') {
                cursorEl.classList.add('is-wobbling');
                await sleep(360);
                cursorEl.classList.remove('is-wobbling');
            }
        }

        function hideGhostCursor() {
            if (!cursorEl) return;
            cursorEl.classList.remove('is-visible', 'is-clicking', 'is-wobbling');
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
            if (cursorEl && cursorEl.parentNode) {
                cursorEl.parentNode.removeChild(cursorEl);
            }
            shell = null;
            cursorEl = null;
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
            if (outcome === 'skip' || outcome === 'complete') {
                markGuideRoundOutcome(day, outcome);
            }
            recordAvatarFloatingGuideEndState(day, outcome, rawReason, options.source || 'home_reset_button');
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
