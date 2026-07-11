(function () {
    'use strict';

    const api = {
        stories: '/api/theater/stories',
        start: '/api/theater/session/start',
        input: '/api/theater/session/input',
        state: '/api/theater/session/state',
        active: '/api/theater/session/active',
    };
    const ACTIVE_SESSION_STORAGE_KEY = 'neko.theater.activeSession.v1';
    const state = {
        sessionId: '',
        storyId: '',
        stories: [],
        stateRevision: null,
        busy: false,
        inputClosed: false,
        restoreReason: '',
    };

    // 统一按 ID 读取页面节点，避免业务函数重复查询表达式。
    function $(id) {
        return document.getElementById(id);
    }

    // 优先读取当前语言文案，资源尚未就绪时使用安全回退。
    function t(key, fallback) {
        if (typeof window.t === 'function') {
            const value = window.t(key);
            if (value && value !== key) return value;
        }
        return fallback;
    }

    // 舞台折叠只改变页面布局，不修改剧情状态；展开时原背景介绍和场景内容会直接恢复。
    function initStageToggle() {
        const shell = document.querySelector('[data-theater-app]');
        const button = $('theater-stage-toggle');
        const label = $('theater-stage-toggle-label');
        if (!shell || !button || !label) return;

        function renderToggle(collapsed) {
            shell.dataset.stageCollapsed = collapsed ? 'true' : 'false';
            button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            const key = collapsed ? 'theater.expandStage' : 'theater.collapseStage';
            const text = t(key, collapsed ? '展开舞台' : '折叠舞台');
            label.textContent = text;
            label.setAttribute('data-i18n', key);
            button.title = text;
            button.setAttribute('data-i18n-title', key);
        }

        button.addEventListener('click', function () {
            renderToggle(shell.dataset.stageCollapsed !== 'true');
        });
        // 切换界面语言时刷新当前状态文案，避免折叠按钮停留在旧语言。
        window.addEventListener('localechange', function () {
            renderToggle(shell.dataset.stageCollapsed === 'true');
        });
        renderToggle(false);
    }

    // 写请求复用全站本地 mutation 安全头。
    async function getMutationHeaders() {
        const helper = window.nekoLocalMutationSecurity;
        if (!helper || typeof helper.getMutationHeaders !== 'function') return {};
        try {
            return await helper.getMutationHeaders();
        } catch (_) {
            return {};
        }
    }

    // 发送 JSON 请求，并对瞬时网络错误和 CSRF 刷新各做有限重试。
    async function requestJson(url, options) {
        const requestOptions = options || {};
        const method = requestOptions.method || 'GET';
        const body = requestOptions.body || null;
        // 只有 GET 或携带稳定幂等 ID 的写请求允许在结果未知时自动重发。
        const canRetryUnknownResult = method === 'GET' || Boolean(body && (body.client_turn_id || body.client_start_id));
        // 首次发送前冻结请求体，网络重试必须复用同一个幂等 ID。
        const serializedBody = requestOptions.body ? JSON.stringify(requestOptions.body) : undefined;

        async function send() {
            const headers = { 'Content-Type': 'application/json' };
            if (method !== 'GET') Object.assign(headers, await getMutationHeaders());
            return fetch(url, { method: method, headers: headers, body: serializedBody });
        }

        let response;
        try {
            response = await send();
        } catch (_) {
            if (!canRetryUnknownResult) throw _;
            response = await send();
        }
        if (canRetryUnknownResult && [502, 503, 504].includes(response.status)) response = await send();
        if (response.status === 403 && method !== 'GET' && window.nekoLocalMutationSecurity &&
            typeof window.nekoLocalMutationSecurity.refreshToken === 'function') {
            await window.nekoLocalMutationSecurity.refreshToken();
            response = await send();
        }
        return response.json();
    }

    // 本地只保存不可读 Session ID，剧情正文仍以服务端快照为准。
    function rememberSession(sessionId) {
        try {
            if (sessionId) window.localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, sessionId);
        } catch (_) {
            // 服务端 active session 仍可在浏览器禁用本地存储时恢复。
        }
    }

    // 读取刷新恢复使用的本地 Session 指针。
    function rememberedSession() {
        try {
            return String(window.localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY) || '').trim();
        } catch (_) {
            return '';
        }
    }

    // Session 落幕、离场或失效后清理本地指针。
    function forgetSession() {
        try {
            window.localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
        } catch (_) {
            // 清理本地指针失败不会绕过服务端 ended/stale 判断。
        }
    }

    // 更新舞台右上角的用户可见状态。
    function setStatus(text) {
        $('theater-status').textContent = text;
    }

    // 请求期间锁定所有共享同一 revision 的输入控件。
    function setBusy(busy) {
        state.busy = busy;
        const active = Boolean(state.sessionId);
        $('theater-story-select').disabled = busy || active;
        $('theater-start-btn').disabled = busy || active;
        $('theater-end-btn').disabled = busy || !active;
        $('theater-input').disabled = busy || !active || state.inputClosed;
        $('theater-send-btn').disabled = busy || !active || state.inputClosed;
        document.querySelectorAll('.theater-choice-button').forEach(function (button) {
            button.disabled = busy || !active || state.inputClosed;
        });
    }

    // 向公开演出日志追加旁白、玩家输入或猫娘对白。
    function appendTurn(role, text) {
        const normalized = String(text || '').trim();
        if (!normalized) return null;
        const row = document.createElement('div');
        // 服务端角色名统一映射为表现层语义类，确保玩家、旁白和猫娘对白各自命中独立样式。
        const roleClass = {
            user: 'user',
            narrator: 'narration',
            assistant: 'dialogue',
        }[role] || String(role || 'narration');
        row.className = 'theater-turn ' + roleClass;
        row.textContent = normalized;
        $('theater-log').appendChild(row);
        $('theater-log').scrollTop = $('theater-log').scrollHeight;
        return row;
    }

    // 把公开故事卡渲染为启动前下拉选项。
    function renderStoryOptions(stories) {
        const select = $('theater-story-select');
        select.textContent = '';
        stories.forEach(function (story) {
            const option = document.createElement('option');
            option.value = story.id;
            option.textContent = story.title;
            select.appendChild(option);
        });
        select.value = state.storyId;
    }

    // 把剧本作者提供的背景、双方身份和目标放在舞台最上方，避免玩家在不知人设时直接开演。
    function renderStoryIntro(story) {
        const intro = $('theater-story-intro');
        if (!story) {
            intro.hidden = true;
            return;
        }
        const card = story.scenario_card || {};
        $('theater-story-intro-title').textContent = String(story.title || '');
        $('theater-story-intro-brief').textContent = String(card.brief || story.summary || '');

        // 每一项单独控制显隐，兼容尚未补齐开场卡字段的作者剧本。
        function renderRole(rowId, valueId, value) {
            const normalized = String(value || '').trim();
            $(valueId).textContent = normalized;
            $(rowId).hidden = !normalized;
        }
        renderRole('theater-player-role-row', 'theater-player-role', card.player_role);
        renderRole('theater-catgirl-role-row', 'theater-catgirl-role', card.catgirl_role);
        renderRole('theater-story-goal-row', 'theater-story-goal', card.primary_goal);

        // 规则默认折叠，身份与背景保持首屏可见，同时允许玩家随时展开核对边界。
        const rules = $('theater-story-rules');
        rules.textContent = '';
        (Array.isArray(card.rules) ? card.rules : []).forEach(function (rule) {
            const item = document.createElement('li');
            item.textContent = String(rule || '');
            rules.appendChild(item);
        });
        $('theater-story-rules-row').hidden = !rules.children.length;
        intro.hidden = false;
    }

    // 未开场时预览当前故事的首个 Scene 或摘要。
    function previewSelectedStory() {
        const story = state.stories.find(function (item) { return item.id === state.storyId; });
        const scene = story && Array.isArray(story.scenes) ? story.scenes[0] : null;
        $('theater-scene-text').textContent = String(scene && scene.text || story && story.summary || t('theater.ready', '准备中'));
        renderStoryIntro(story);
    }

    // 创建携带稳定 choice_id 的行动或对白按钮。
    function createChoiceButton(option) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'theater-choice-button';
        button.textContent = option.label;
        button.dataset.choiceId = option.choice_id;
        button.addEventListener('click', function () {
            submitInput({ choiceId: option.choice_id, displayText: option.label });
        });
        return button;
    }

    // 按 choice_mode 分开渲染“你可以做”和“你可以说”。
    function renderSuggestions(options) {
        const actionGroup = $('theater-action-choice-group');
        const dialogueGroup = $('theater-dialogue-choice-group');
        const actionList = $('theater-action-choices');
        const dialogueList = $('theater-dialogue-choices');
        actionList.textContent = '';
        dialogueList.textContent = '';
        (Array.isArray(options) ? options : []).forEach(function (option) {
            const target = option.choice_mode === 'dialogue' ? dialogueList : actionList;
            target.appendChild(createChoiceButton(option));
        });
        actionGroup.hidden = !actionList.children.length;
        dialogueGroup.hidden = !dialogueList.children.length;
        $('theater-suggestions').hidden = actionGroup.hidden && dialogueGroup.hidden;
        setBusy(state.busy);
    }

    // 渲染一组公开道具或线索，并在空列表时隐藏分组。
    function renderBoardList(sectionId, listId, values, labelKey) {
        const section = $(sectionId);
        const list = $(listId);
        list.textContent = '';
        (Array.isArray(values) ? values : []).forEach(function (value) {
            const item = document.createElement('li');
            const title = typeof value === 'string' ? value : String(value[labelKey] || value.id || '');
            const hint = typeof value === 'object' ? String(value.public_hint || value.public_text || '') : '';
            item.textContent = hint ? title + ' — ' + hint : title;
            list.appendChild(item);
        });
        section.hidden = !list.children.length;
    }

    // 只消费服务端公开 Board，不在前端推导剧情事实。
    function renderBoard(board) {
        const payload = board || {};
        renderBoardList('theater-board-available-section', 'theater-board-available-props', payload.available_props, 'label');
        renderBoardList('theater-board-used-section', 'theater-board-used-props', payload.used_props, 'label');
        renderBoardList('theater-board-clues-section', 'theater-board-clues', payload.discovered_clues, 'title');
        const visible = ['theater-board-available-section', 'theater-board-used-section', 'theater-board-clues-section']
            .some(function (id) { return !$(id).hidden; });
        $('theater-scenario-board').hidden = !visible;
        document.querySelector('.theater-workspace').dataset.boardVisible = visible ? 'true' : 'false';
    }

    // 把进度类型翻译为玩家可理解的本轮摘要。
    function renderTrace(trace) {
        const panel = $('theater-trace-panel');
        if (!trace || !trace.progress_kind) {
            panel.hidden = true;
            return;
        }
        const summaries = {
            graph_progress: t('theater.traceProgress', '剧情已按你的选择推进。'),
            roleplay_response: t('theater.traceRoleplay', '猫娘回应了你的话，剧情仍停在当前场景。'),
            user_exit: t('theater.traceExit', '你离开了本次小剧场。'),
        };
        $('theater-trace-action').textContent = String(trace.action_label || '');
        $('theater-trace-summary').textContent = summaries[trace.progress_kind] || t('theater.traceProgress', '本轮已完成。');
        $('theater-trace-details').textContent = '';
        panel.hidden = false;
    }

    // 区分正式落幕和玩家主动离场的公开提示。
    function renderEnding(ending) {
        const panel = $('theater-ending-panel');
        if (!ending || !ending.should_end_session) {
            panel.hidden = true;
            return;
        }
        $('theater-ending-text').textContent = ending.reason === 'user_exit'
            ? t('theater.userExitEnded', '你已离开小剧场，本次离场不算剧情结局。')
            : t('theater.endingEnded', '故事已经落幕。');
        panel.hidden = false;
    }

    // 用一次公开响应同步 Scene、日志、Board、Choice、Ending 和 revision。
    function applyPayload(payload, options) {
        const append = !options || options.append !== false;
        state.sessionId = String(payload.session_id || state.sessionId || '');
        state.storyId = String(payload.story_id || state.storyId || '');
        state.stateRevision = Number.isInteger(payload.state_revision) ? payload.state_revision : null;
        state.inputClosed = !payload.can_resume;
        if (state.storyId) $('theater-story-select').value = state.storyId;
        // 刷新恢复可能切换到服务端活动剧本，因此按恢复后的 story_id 同步顶部开场卡。
        renderStoryIntro(state.stories.find(function (item) { return item.id === state.storyId; }));
        const scene = payload.scene || {};
        $('theater-scene-text').textContent = String(scene.text || '');
        if (append) {
            appendTurn('narrator', payload.narration && payload.narration.text);
            appendTurn('assistant', payload.dialogue && payload.dialogue.text);
        }
        renderBoard(payload.scenario_board);
        renderTrace(payload.scenario_trace);
        renderSuggestions(payload.suggestion_options || []);
        renderEnding(payload.ending);
        if (payload.can_resume) {
            rememberSession(state.sessionId);
            setStatus(t('theater.running', '进行中'));
        } else {
            forgetSession();
            setStatus(t('theater.ended', '已结束'));
            // 落幕后释放前端活动指针，让玩家可以立即选择并开始下一份剧本。
            state.sessionId = '';
            state.stateRevision = null;
            state.inputClosed = false;
        }
        setBusy(state.busy);
    }

    // 每次玩家提交生成稳定幂等 ID，网络重试不会重新生成。
    function createClientTurnId() {
        const value = window.crypto && typeof window.crypto.randomUUID === 'function'
            ? window.crypto.randomUUID()
            : Math.random().toString(36).slice(2) + Date.now().toString(36);
        return 'turn_web_' + value;
    }

    // 每次点击开始只生成一个稳定 ID，同一次请求的网络重试必须复用它。
    function createClientStartId() {
        const value = window.crypto && typeof window.crypto.randomUUID === 'function'
            ? window.crypto.randomUUID()
            : Math.random().toString(36).slice(2) + Date.now().toString(36);
        return 'start_web_' + value;
    }

    // 先按本地指针恢复，失败后再查询当前猫娘的服务端 active Session。
    async function restoreActiveSession(preferredSessionId) {
        const preferred = String(preferredSessionId || rememberedSession() || '').trim();
        state.restoreReason = '';
        let result = preferred
            ? await requestJson(api.state + '?session_id=' + encodeURIComponent(preferred))
            : null;
        if (!result || !result.ok || !result.can_resume || result.stale) {
            if (result && ['session_upgrade_required', 'session_version_unsupported'].includes(result.reason)) {
                state.restoreReason = result.reason;
            } else if (preferred) {
                forgetSession();
            }
            result = await requestJson(api.active);
        }
        if (result && ['session_upgrade_required', 'session_version_unsupported'].includes(result.reason)) {
            state.restoreReason = result.reason;
        }
        if (!result || !result.ok || !result.can_resume) return false;
        $('theater-log').textContent = '';
        applyPayload(result);
        return true;
    }

    // revision 冲突时刷新公开快照，并保留尚未成功的自由输入。
    async function recoverRevisionConflict(result, pendingText) {
        if (!result || result.reason !== 'state_revision_conflict' || !result.retryable) return false;
        await restoreActiveSession(state.sessionId);
        if (pendingText) $('theater-input').value = pendingText;
        setStatus(t('theater.sessionUpdated', '场景已在其他窗口推进，请重新选择。'));
        return true;
    }

    // 加载故事列表，随后恢复活动演出或显示故事预览。
    async function loadStories() {
        try {
            const result = await requestJson(api.stories);
            if (!result || !result.ok || !Array.isArray(result.stories) || !result.stories.length) throw new Error('stories');
            state.stories = result.stories;
            state.storyId = String(result.stories[0].id || '');
            renderStoryOptions(result.stories);
            if (!await restoreActiveSession()) {
                previewSelectedStory();
                setStatus(state.restoreReason
                    ? t('theater.sessionUpgradeRequired', '旧版演绎无法继续，请开始一场新演出。')
                    : t('theater.ready', '准备中'));
            }
        } catch (_) {
            setStatus(t('theater.failed', '加载失败'));
        }
    }

    // 使用当前故事启动唯一轻量演绎链。
    async function startSession() {
        if (state.busy) return;
        setBusy(true);
        try {
            const result = await requestJson(api.start, {
                method: 'POST',
                body: {
                    story_id: state.storyId,
                    client_start_id: createClientStartId(),
                    // 只有页面已展示旧版不兼容提示时，本次点击才表示玩家明确同意新开场。
                    replace_incompatible_session: Boolean(state.restoreReason)
                }
            });
            if (!result || !result.ok) throw new Error('start');
            $('theater-log').textContent = '';
            state.inputClosed = false;
            state.restoreReason = '';
            applyPayload(result);
        } catch (_) {
            setStatus(t('theater.failed', '启动失败'));
        } finally {
            setBusy(false);
        }
    }

    // Choice 和自由输入使用互斥字段提交同一结构化回合协议。
    async function submitInput(selection) {
        if (state.busy || !state.sessionId || state.inputClosed) return;
        const input = $('theater-input');
        const selected = selection && selection.choiceId ? selection : null;
        const message = selected ? String(selected.displayText || '') : input.value.trim();
        if (!message) return;
        if (!selected) input.value = '';
        const optimistic = appendTurn('user', message);
        setBusy(true);
        try {
            const body = {
                session_id: state.sessionId,
                input_kind: selected ? 'choice' : 'free_input',
                client_turn_id: createClientTurnId(),
                base_revision: state.stateRevision,
            };
            if (selected) body.choice_id = selected.choiceId;
            else body.message = message;
            const result = await requestJson(api.input, { method: 'POST', body: body });
            if (!result || !result.ok) {
                if (await recoverRevisionConflict(result, selected ? '' : message)) return;
                throw new Error('input');
            }
            applyPayload(result);
        } catch (_) {
            if (optimistic) optimistic.remove();
            if (!selected) input.value = message;
            setStatus(t('theater.failed', '提交失败'));
        } finally {
            setBusy(false);
        }
    }

    // 通过 user_exit 回合离场，不把主动离场标记为作者结局。
    async function endSession() {
        if (state.busy || !state.sessionId) return;
        const optimistic = appendTurn('user', t('theater.leaveAction', '离开小剧场'));
        setBusy(true);
        try {
            const result = await requestJson(api.input, {
                method: 'POST',
                body: {
                    session_id: state.sessionId,
                    input_kind: 'user_exit',
                    client_turn_id: createClientTurnId(),
                    base_revision: state.stateRevision,
                },
            });
            if (!result || !result.ok) {
                if (await recoverRevisionConflict(result, '')) return;
                throw new Error('exit');
            }
            applyPayload(result);
        } catch (_) {
            if (optimistic) optimistic.remove();
            setStatus(t('theater.failed', '离场失败'));
        } finally {
            setBusy(false);
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        initStageToggle();
        $('theater-story-select').addEventListener('change', function () {
            state.storyId = this.value;
            previewSelectedStory();
        });
        $('theater-start-btn').addEventListener('click', startSession);
        $('theater-end-btn').addEventListener('click', endSession);
        $('theater-input-form').addEventListener('submit', function (event) {
            event.preventDefault();
            submitInput();
        });
        setBusy(false);
        loadStories();
    });
})();
