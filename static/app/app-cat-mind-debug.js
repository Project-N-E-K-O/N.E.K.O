/**
 * app-cat-mind-debug.js - opt-in visual inspector for Cat Mind.
 *
 * The debug-panel setting resolves from window.__NEKO_CAT_MIND_DEBUG__, then
 * same-origin localStorage, then its safe default. It only controls inspector
 * visibility; it never changes Cat Mind state, selection, or NEKO-PC behavior.
 */
(function () {
    'use strict';

    var ACTION_DEFINITIONS = Object.freeze([
        { id: 'cat1_social_ping', label: 'CAT1 轻声回应' },
        { id: 'cat1_eat_snack', label: 'CAT1 吃零食' },
        { id: 'cat1_small_move', label: 'CAT1 小幅移动' },
        { id: 'cat1_play_yarn', label: 'CAT1 玩毛线' },
        { id: 'cat2_nap_feedback', label: 'CAT2 打盹反馈' },
        { id: 'cat3_sleep_feedback', label: 'CAT3 睡眠反馈' },
    ]);
    var RETURN_TRACE_LIMIT = 12;
    var returnTrace = [];
    var lastExecutionTraceKey = '';

    var REASON_LABELS = Object.freeze({
        allowed: '可执行',
        tier_not_allowed: '当前阶段不允许',
        provider_unavailable: '动作能力尚未就绪',
        return_pending: '正在返回猫娘形态',
        drag_pending: '拖拽准备中',
        dragging: '正在拖拽',
        transition_active: '界面转场中',
        active_independent_action: '已有独立动作进行中',
        return_ball_not_visible: '猫咪不可见',
        missing_button: '未找到当前猫咪',
        tier_not_cat1: '当前不是 CAT1',
        return_ball_drag_active: '猫咪正在拖拽',
        missing_art: '猫咪图像尚未就绪',
        unknown_action: '未知动作',
        invalid_cat_runtime: '当前不是有效猫形态',
        chat_surface_dragging: '聊天窗口正在拖拽',
        compact_surface_dragging: '紧凑聊天窗口正在拖拽',
        near_chat_unavailable: '尚未在聊天窗口附近落位',
        small_move_unavailable: '小幅移动当前不可用',
        small_move_no_space: '没有足够的小幅移动空间',
        hover_active: '猫咪正在响应悬停',
        play_yarn_unavailable: '毛线球能力不可用',
        ambient_inactive: '轻声回应未处于可播放状态',
        sleep_feedback_inactive: '睡眠反馈未处于可播放状态',
        tier_mismatch: '阶段与动作不匹配',
        missing_audio_asset: '缺少音频资源',
        audio_disabled: '猫咪音频已关闭',
        action_request_pending: '等待动作适配器确认',
        active_action_pending: '动作正在执行',
        post_action_settle: '动作结束后的静置轮',
        no_eligible_candidate: '没有可执行动作',
        below_action_threshold: '所有合法动作均未达到阈值',
        below_threshold: '未达到动作阈值',
        read_only_candidate: '仅评估候选',
        action_request_dispatched: '已请求执行动作',
    });

    // Provider 是只读的网页表现能力检查；这些名称必须在调试面板中可读，
    // 不能把实际拒绝条件藏在 JSON 或实现细节后面。
    var PROVIDER_CHECK_LABELS = Object.freeze({
        known_action: '动作已识别',
        cat1_tier: '当前为 CAT1',
        return_ball_drag_free: '未拖拽猫咪',
        return_not_pending: '未在返回猫娘',
        transition_idle: '未处于转场',
        no_independent_action: '没有其他独立动作',
        return_ball_visible: '猫咪当前可见',
        return_art_ready: '猫咪图像已就绪',
        audio_enabled: '猫咪音频已开启',
        ambient_audio_ready: '轻声回应音频可用',
        compact_surface_idle: '聊天窗口未被拖拽',
        near_chat: '已在聊天球附近',
        play_yarn_capability: '毛线球可安全控制',
        journey_settled_idle: '猫咪已稳定落位',
        not_compact_top_edge: '未停靠在紧凑聊天顶边',
        hover_inactive: '猫咪未处于悬停回应',
        chat_target_available: '已找到聊天球目标',
        linux_desktop_supported: '当前桌面平台支持移动',
        small_move_container_stable: '猫咪容器稳定可移动',
        small_move_geometry_known: '猫咪和聊天球位置可测量',
        move_vector_space: '有足够的小幅移动空间',
        sleep_tier_matches: '睡眠动作与当前阶段匹配',
        sleep_audio_active: '睡眠音频可用',
    });

    function getContract() {
        return window.NekoCatMindContract || null;
    }

    function isEnabled() {
        var contract = getContract();
        return !!(contract && typeof contract.isDebugEnabled === 'function' &&
            contract.isDebugEnabled());
    }

    function createPanel() {
        var panel = document.createElement('aside');
        panel.id = 'neko-cat-mind-debug-panel';
        panel.setAttribute('aria-live', 'off');
        panel.setAttribute('aria-label', '猫咪状态机调试信息');
        var title = document.createElement('div');
        title.className = 'neko-cat-mind-debug-title';
        title.textContent = '猫咪状态机调试';
        var hint = document.createElement('div');
        hint.className = 'neko-cat-mind-debug-hint';
        hint.textContent = '调试开关：localStorage.neko.catMind.debug = true';
        var body = document.createElement('pre');
        body.className = 'neko-cat-mind-debug-body';
        panel.appendChild(title);
        panel.appendChild(hint);
        panel.appendChild(body);
        document.body.appendChild(panel);
        return body;
    }

    function addStyle() {
        var style = document.createElement('style');
        style.textContent = [
            '#neko-cat-mind-debug-panel{position:fixed;top:12px;right:12px;z-index:2147483000;width:min(360px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto;padding:12px;border:1px solid rgba(112,211,255,.65);border-radius:10px;background:rgba(8,18,31,.92);color:#e9f6ff;box-shadow:0 12px 40px rgba(0,0,0,.35);font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;pointer-events:auto}',
            '#neko-cat-mind-debug-panel .neko-cat-mind-debug-title{font-weight:700;letter-spacing:.08em;color:#8de4ff}',
            '#neko-cat-mind-debug-panel .neko-cat-mind-debug-hint{margin-top:4px;color:#adc0cd;font-size:11px}',
            '#neko-cat-mind-debug-panel .neko-cat-mind-debug-body{margin:10px 0 0;white-space:pre-wrap;word-break:break-word}'
        ].join('');
        document.head.appendChild(style);
    }

    function formatNumber(value) {
        var number = Number(value);
        return Number.isFinite(number) ? number.toFixed(2) : '-';
    }

    function formatMindValue(value) {
        var number = Number(value);
        if (!Number.isFinite(number)) return '-';
        return String(Math.max(0, Math.min(100, Math.round(number * 100)))) + ' / 100';
    }

    function formatAction(actionId) {
        var matched = ACTION_DEFINITIONS.filter(function (item) { return item.id === actionId; })[0];
        return matched ? matched.label + '（' + matched.id + '）' : (actionId || '-');
    }

    function formatReason(reason) {
        if (!reason) return '-';
        return REASON_LABELS[reason]
            ? REASON_LABELS[reason] + '（' + reason + '）'
            : reason;
    }

    function indexByAction(items) {
        var result = {};
        (items || []).forEach(function (item) {
            if (item && item.actionId) result[item.actionId] = item;
        });
        return result;
    }

    function formatCandidateStatus(candidate) {
        if (!candidate) return '尚未进行本轮判断';
        if (candidate.allowed) return '可执行';
        return '已拦截：' + formatReason(candidate.reason);
    }

    function formatProviderDetail(candidate) {
        if (!candidate || !candidate.providerDetail || typeof candidate.providerDetail !== 'object') {
            return '-';
        }
        var detail = candidate.providerDetail;
        var checks = Array.isArray(detail.checks) ? detail.checks : [];
        var failed = checks.filter(function (check) { return check && check.passed === false; })
            .map(function (check) { return PROVIDER_CHECK_LABELS[check.id] || check.id; });
        var passed = checks.filter(function (check) { return check && check.passed === true; })
            .map(function (check) { return PROVIDER_CHECK_LABELS[check.id] || check.id; });
        var facts = detail.facts && typeof detail.facts === 'object' ? detail.facts : {};
        var pieces = [];
        if (detail.failedCheck) pieces.push('拒绝原因：' + formatReason(detail.failedCheck));
        // provider 会短路：被拒绝时只把首个拒绝原因当作真实执行结果；
        // 其余 failed 项只是当前现场快照，不能伪装成 provider 已逐项执行。
        if (candidate.allowed) {
            if (passed.length) pieces.push('已确认：' + passed.join('、'));
        } else if (failed.length) {
            pieces.push('现场还不满足：' + failed.join('、'));
        }
        if (facts.smallMove) {
            var smallMove = facts.smallMove;
            pieces.push('小移动现场：聊天目标' + (smallMove.chatTargetAvailable ? '已找到' : '未找到') +
                (smallMove.chatTargetMode ? '（' + smallMove.chatTargetMode + '）' : '') +
                '，移动空间' + (smallMove.vectorSpaceAvailable ? '足够' : '不足') +
                '，距离范围 ' + (Number(smallMove.minUsableDistancePx) || 0) + '–' + (Number(smallMove.maxDistancePx) || 0) + 'px' +
                (smallMove.linuxDesktopBlocked ? '，当前 Linux 桌面不支持此移动' : ''));
        }
        if (facts.journey) {
            var journey = facts.journey;
            pieces.push('猫咪落位：' + (journey.actionSettled ? '已稳定' : '未稳定') +
                (journey.pairMoveActive ? '，正在移动' : '') +
                (journey.pendingWalk ? '，仍在走位' : '') +
                (journey.edgePeekActive ? '，边缘探头中' : ''));
        }
        return pieces.join('；') || '-';
    }

    function formatReturnEpisodePreview(episode) {
        if (!episode || typeof episode !== 'object') return '当前没有可带回的经历';
        var kindLabels = {
            activity: '刚刚活动过',
            rest_after_activity: '活动后休息过',
            rested: '刚刚休息过',
        };
        var highlightLabels = {
            played_yarn: '玩毛线',
            ate_snack: '吃零食',
            small_move: '小幅移动',
            social_ping: '轻声回应',
        };
        var text = kindLabels[episode.kind] || '无效经历';
        if (episode.highlight && highlightLabels[episode.highlight]) {
            text += '（重点：' + highlightLabels[episode.highlight] + '）';
        }
        return text;
    }

    function addReturnTrace(label, text) {
        returnTrace.push({ label: label || '闭环事件', text: text || '-' });
        if (returnTrace.length > RETURN_TRACE_LIMIT) {
            returnTrace.splice(0, returnTrace.length - RETURN_TRACE_LIMIT);
        }
    }

    function resetReturnTrace() {
        returnTrace = [];
        lastExecutionTraceKey = '';
    }

    function formatExecutionTrace(execution) {
        if (!execution || typeof execution !== 'object') return '';
        var actionId = typeof execution.actionId === 'string' ? execution.actionId : '';
        var state = typeof execution.state === 'string' ? execution.state : '';
        if (!actionId || !state) return '';
        var key = [actionId, execution.requestId || '', execution.runId || '', state, execution.reason || ''].join('|');
        if (key === lastExecutionTraceKey) return '';
        lastExecutionTraceKey = key;
        var labels = {
            accepted: 'runner 已接受',
            started: 'runner 已开始',
            rejected: 'runner 被拒绝',
            result: '已收到终态回执',
            result_before_started: '开始前收到终态回执',
        };
        return formatAction(actionId) + '：' + (labels[state] || state) +
            (execution.reason ? '（' + formatReason(execution.reason) + '）' : '');
    }

    function formatActionResultTrace(detail, snapshot) {
        var result = detail && typeof detail === 'object' ? detail : {};
        var actionId = typeof result.actionId === 'string' ? result.actionId : '';
        var status = typeof result.result === 'string' ? result.result : '';
        var sourceIsCatMind = result.source === 'cat_mind';
        var scheduler = snapshot && snapshot.scheduler ? snapshot.scheduler : {};
        var ignored = scheduler.lastIgnoredActionResult || {};
        var resultRequestId = result.detail && typeof result.detail.requestId === 'string'
            ? result.detail.requestId
            : '';
        var ignoredMatches = ignored.actionId === actionId &&
            (!resultRequestId || ignored.requestId === resultRequestId);
        var labels = {
            done: '完成',
            failed: '失败',
            cancelled: '取消',
            interrupted: '打断',
        };
        var text = formatAction(actionId) + '：' + (labels[status] || status || '无效回执');
        if (!sourceIsCatMind) {
            return text + '（非状态机来源，不记入回归经历）';
        }
        if (ignoredMatches) {
            return text + '（未匹配 request/run，不记入回归经历）';
        }
        if (status === 'done') {
            return text + '（严格匹配；请查看当前活动段／返回预览）';
        }
        return text + '（按规则不记入回归经历）';
    }

    function renderReturnTrace(lines) {
        lines.push('', '闭环记录（仅调试，保留最近 ' + RETURN_TRACE_LIMIT + ' 项）');
        if (!returnTrace.length) {
            lines.push('  暂无；等待状态机请求、动作回执或 return。');
            return;
        }
        returnTrace.forEach(function (item) {
            lines.push('  ' + item.label + '：' + item.text);
        });
    }

    function renderSnapshot(body, snapshot) {
        var state = snapshot && snapshot.state ? snapshot.state : {};
        var fields = state.fields || {};
        var decision = snapshot && snapshot.lastDecision;
        var scheduler = snapshot && snapshot.scheduler ? snapshot.scheduler : {};
        var clock = snapshot && snapshot.clock ? snapshot.clock : {};
        var returnEpisode = snapshot && snapshot.returnEpisode ? snapshot.returnEpisode : {};
        var activeChapter = returnEpisode.activeChapter || {};
        var lastRest = returnEpisode.lastRest || null;
        var candidatesByAction = indexByAction(decision && decision.candidates);
        var scoresByAction = indexByAction(snapshot && snapshot.actionScores);
        var lines = [
            '状态：' + (state.active ? '猫形态运行中' : '未进入猫形态'),
            '进入方式／当前阶段：' + (state.entry || '-') + ' ／ ' + (state.tier || '-'),
            '持续时间：' + (Number(state.durationSeconds) || 0) + ' 秒',
            '',
            '五维状态（0–100）',
            '  食欲（appetite）：' + formatMindValue(fields.appetite),
            '  困意（sleepiness）：' + formatMindValue(fields.sleepiness),
            '  精力（energy）：' + formatMindValue(fields.energy),
            '  社交需求（social_need）：' + formatMindValue(fields.social_need),
            '  刺激需求（stimulation_need）：' + formatMindValue(fields.stimulation_need),
            '',
            '调度状态：' + (scheduler.queued ? '已排队' : '空闲'),
            '待处理触发：' + ((scheduler.pendingTriggers || []).join(', ') || '-'),
            '待确认请求：' + formatAction(scheduler.pendingActionRequest && scheduler.pendingActionRequest.actionId),
            '执行中动作：' + formatAction(scheduler.activeAction && scheduler.activeAction.actionId),
            '冷却动作：' + (Object.keys(scheduler.actionCooldowns || {}).map(formatAction).join('、') || '-'),
            '忽略的结果：' + formatReason(scheduler.lastIgnoredActionResult && scheduler.lastIgnoredActionResult.reason),
            '自主时钟：每 ' + Math.round((Number(clock.tickIntervalMs) || 0) / 1000) + ' 秒检查一次',
            '',
            '回归经历归并（仅本地调试）',
            '当前活动段：' + (activeChapter.interactionSeen ? '有明确互动' : '无明确互动') +
                ' ｜ 已完成动作：' + ((activeChapter.activityKinds || []).map(formatAction).join('、') || '-'),
            '最近休息段：' + (!lastRest
                ? '-'
                : (lastRest.hadActivityBeforeRest ? '活动后休息' : '无活动前置的休息')),
            '返回预览：' + formatReturnEpisodePreview(returnEpisode.preview),
        ];
        renderReturnTrace(lines);
        lines.push('', '本轮决策：' + (decision ? formatAction(decision.outcome) : '尚未判断'));
        if (decision) {
            lines.push('触发来源／判定原因：' + (decision.trigger || '-') + ' ／ ' + formatReason(decision.reason));
            if (decision.triggerTypes && decision.triggerTypes.length) {
                lines.push('触发事实：' + decision.triggerTypes.join(', '));
            }
        }
        lines.push('', '动作评分（每个动作独立计算）');
        ACTION_DEFINITIONS.forEach(function (action) {
            var score = scoresByAction[action.id] || candidatesByAction[action.id] || {};
            var candidate = candidatesByAction[action.id];
            var currentScore = score.score !== null && score.score !== undefined
                ? score.score
                : null;
            var candidateScore = candidate && candidate.score !== null && candidate.score !== undefined
                ? candidate.score
                : null;
            lines.push('【' + action.label + '】');
            lines.push('  理论基础分：' + formatNumber(score.baseScore) + ' ｜ 阈值：' + formatNumber(score.threshold));
            lines.push('  冷却扣分：' + formatNumber(score.cooldownPenalty) +
                (score.cooldownApplied ? ' ｜ 剩余：' + Math.ceil((Number(score.cooldownRemainingMs) || 0) / 1000) + ' 秒' : ' ｜ 未冷却'));
            lines.push('  理论分（基础分－冷却）：' + (currentScore === null ? '-' : formatNumber(currentScore)));
            lines.push('  本轮可用分（仅 provider 允许后计算）：' +
                (candidateScore === null ? '尚未通过动作条件' : formatNumber(candidateScore)));
            lines.push('  本轮候选：' + formatCandidateStatus(candidate));
            lines.push('  动作条件依据：' + formatProviderDetail(candidate));
        });
        lines.push('', '最近事件数：' + (state.recentEventCount || 0));
        body.textContent = lines.join('\n');
    }

    function start() {
        if (!isEnabled() || !window.nekoCatMind || typeof window.nekoCatMind.getDebugSnapshot !== 'function') {
            return;
        }
        addStyle();
        var body = createPanel();
        var render = function (event) {
            var snapshot = event && event.detail && event.detail.snapshot;
            renderSnapshot(body, snapshot || window.nekoCatMind.getDebugSnapshot());
        };
        var eventNames = getContract().EVENT_NAMES || {};
        window.addEventListener(eventNames.STATE_CHANGE, function (event) {
            var snapshot = event && event.detail && event.detail.snapshot;
            var trace = formatExecutionTrace(snapshot && snapshot.lastDecision && snapshot.lastDecision.execution);
            if (trace) addReturnTrace('动作执行', trace);
            render(event);
        });
        window.addEventListener(eventNames.ACTION_REQUEST, function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            addReturnTrace('状态机请求', formatAction(detail.actionId) + '（已发给动作适配器）');
            render();
        });
        window.addEventListener(eventNames.ACTION_RESULT, function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            addReturnTrace('动作回执', formatActionResultTrace(detail, window.nekoCatMind.getDebugSnapshot()));
            render();
        });
        window.addEventListener(eventNames.RETURN_SUMMARY, function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            var summary = detail.summary && typeof detail.summary === 'object' ? detail.summary : {};
            addReturnTrace('回归摘要', formatReturnEpisodePreview(summary.episode) + '（draft 已生成）');
            render();
        });
        window.addEventListener('neko:cat-greeting-check', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            var summary = detail.catMemorySummary && typeof detail.catMemorySummary === 'object'
                ? detail.catMemorySummary
                : null;
            addReturnTrace('问候附件', summary
                ? formatReturnEpisodePreview(summary.episode) + '（已交给 WebSocket）'
                : '没有 episode（保留原问候）');
            render();
        });
        ['live2d-goodbye-click', 'vrm-goodbye-click', 'mmd-goodbye-click', 'pngtuber-goodbye-click'].forEach(function (eventName) {
            window.addEventListener(eventName, function () {
                resetReturnTrace();
                render();
            });
        });
        render();
        // The inspector reads a snapshot every half-second so duration and each
        // action's cooldown/score visibly advance between state transitions. The
        // snapshot path is read-only and never schedules a selector decision.
        if (typeof window.setInterval === 'function') {
            var refreshTimer = window.setInterval(function () {
                render();
            }, 500);
            window.addEventListener('pagehide', function () {
                if (typeof window.clearInterval === 'function') {
                    window.clearInterval(refreshTimer);
                }
            }, { once: true });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
        start();
    }
})();
