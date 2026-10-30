/**
 * visit-chat.js — 串门的聊天面（docs/design/visit-infrastructure.md §5 PR-12 / PR-14 前端监听；
 * §4.5 display socket、§4.6 本机 HTTP、§3.6.6 纯文本台词）。
 *
 * index.html 与 chat.html 都加载（必须排在 resize-drag-and-api.js 之前：它最后会删掉
 * window.__appReactChatWindowParts）。Electron 多窗口时 Pet 窗也是 index.html 且带
 * __NEKO_MULTI_WINDOW__：那里不画任何串门界面（聊天窗才是用户看的那一页），只有 chat 跟随窗
 * （路径 /chat、/chat_full）或单窗口的 index.html 画。
 *
 * 管的事：
 * - 串门状态（visit_state_change / visit_invite）→ compact 输入框上方的串门工具条
 *   （React 的 visitBar 属性）、输入框禁用与占位文字；空闲时不显示旧发起入口，好友邀请到达时
 *   只显示「拒绝 / 接受」，串门建立后再显示当前阶段可用的交互；
 * - 亲人打字：按阶段先拦（邀请等待 / 接待确认 / 收尾 / guest 侧不发、文字留在输入框），
 *   发出去的句子先出一个本地气泡，后端拒绝（status 带回 request_id）时标「未送达」，文字只在
 *   输入框自提交后没再编辑时恢复；收件人开关决定 source（对方猫娘 / 自家猫娘）；
 * - 台词拼接：Map<line_id, {clauses[], ...}>，visit_line_delta 按 i 落位、缺片留「…」不补洞，
 *   visit_line{final} 全文覆盖，visit_line_abort 立即截断，20 s 没有新片又没有 final 就本地截断；
 *   台词块一律 {type:'text', text, plain_text:true}（不走 Markdown、不加载远程图片）；
 * - 确认框：出门前先调邀请预览（只读、不消耗邀请码），确认框只显示对端显示名 + 6 位短码 +
 *   跨区提示，不出现任何技术数字；host 的接待确认（60 s）；
 * - 结束后该场系统消息的折叠区：查看详情（云端用量与双方转录，逐页翻）、导出转录、举报；
 * - 「回家汇报」芯片（PR-14 前端）：react-chat-window:action 的 visit_debrief_choice →
 *   POST /api/visit/debrief/choice，按响应与 visit_debrief 状态帧置灰按钮、追加状态；块渲染后
 *   回 visit_debrief_chip_ack。
 *
 * 不碰 WebSocket 本身（帧由 app-websocket.js 转进来、发帧走 appState.socket），不用 rAF。
 * 写进页面的文字一律 textContent，不拼 HTML。
 */
(function () {
    'use strict';

    window.reactChatWindowHost = window.reactChatWindowHost || {};
    var I = window.__appReactChatWindowParts || (window.__appReactChatWindowParts = {});

    var STREAM_STALL_MS = 20000;
    var TYPING_CLEAR_MS = 8000;
    var RECONNECT_POLL_MS = 5000;
    var GAP_PLACEHOLDER = '…';
    var INVITE_CODE_RE = /^[A-Z2-7]{10}$/;
    var VISIT_ID_RE = /^[A-Za-z0-9_-]{22}$/;
    var DEBRIEF_ACTION = 'visit_debrief_choice';
    var ENDED_ACTION = 'visit_ended_more';
    var DETAILS_ACTION = 'visit_details';
    var EXPORT_ACTION = 'visit_export';
    var REPORT_ACTION = 'visit_report';
    var DIRECT_INVITE_ACTION = 'visit_direct_invite_decision'; // legacy persisted bubbles only
    var REPORT_REASONS = ['harassment', 'sexual', 'privacy', 'spam', 'other'];
    var REPORT_NOTE_MAX = 500;
    // 进行中的阶段（不含 ending：后端已交还输入，亲人恢复普通聊天）
    var WAITING_PHASES = ['pending', 'invite_ready', 'joining', 'awaiting_accept'];
    var OWNED_PHASES = WAITING_PHASES.concat(['active', 'wrap_up']);
    var START_FAILURE_CODES = {
        quota_exceeded: 'VISIT_QUOTA_EXCEEDED',
        login_required: 'VISIT_LOGIN_REQUIRED',
        banned: 'VISIT_BANNED',
        concurrency_limit: 'VISIT_CONCURRENCY_LIMIT',
        tier_not_entitled: 'VISIT_TIER_NOT_ENTITLED',
        cross_region_unsupported: 'VISIT_CROSS_REGION_UNSUPPORTED',
        servers_unreachable: 'VISIT_SERVERS_UNREACHABLE',
        unsupported: 'VISIT_UNSUPPORTED_ON_THIS_MACHINE',
        invite_invalid: 'VISIT_INVITE_INVALID',
        invite_expired: 'VISIT_INVITE_INVALID',
        busy: 'VISIT_E_BUSY',
        peer_lost: 'VISIT_PEER_LOST',
        proto_mismatch: 'VISIT_PROTO_MISMATCH',
        peer_identity_rejected: 'VISIT_PEER_IDENTITY_REJECTED',
        peer_blocked: 'VISIT_PEER_IDENTITY_REJECTED',
        relay_lost: 'VISIT_RELAY_LOST',
        local_page_lost: 'VISIT_RELAY_LOST',
        kicked: 'VISIT_KICKED'
    };
    // Retain terminal setup errors across ended resetting the visible visit,
    // including when WebSocket failure arrives before POST /rooms returns.
    var startFailures = new Map();

    // ── 小工具 ──────────────────────────────────────────────────────

    function acquireForceInteractiveLease() {
        if (typeof window.__nekoAcquireForceInteractiveLease !== 'function') {
            window.__nekoAcquireForceInteractiveLease = function () {
                var state = window.__nekoForceInteractiveLeaseState;
                if (!state || typeof state.count !== 'number') {
                    state = { count: 0, restore: false };
                    window.__nekoForceInteractiveLeaseState = state;
                }
                if (state.count === 0) {
                    state.restore = window.__nekoForceInteractive === true;
                    try { window.__nekoForceInteractive = true; } catch (_) { /* 普通浏览器无需处理 */ }
                }
                state.count += 1;
                var released = false;
                return function () {
                    if (released) return;
                    released = true;
                    state.count = Math.max(0, state.count - 1);
                    if (state.count === 0) {
                        try { window.__nekoForceInteractive = state.restore; } catch (_) { /* noop */ }
                    }
                };
            };
        }
        return window.__nekoAcquireForceInteractiveLease();
    }

    function t(key, fallback, params) {
        var text = '';
        if (typeof window.t === 'function') {
            try { text = window.t(key, params || {}); } catch (_) { text = ''; }
        }
        if (typeof text !== 'string' || !text || text === key) text = fallback || key;
        if (params) {
            Object.keys(params).forEach(function (name) {
                text = text.split('{{' + name + '}}').join(String(params[name]));
            });
        }
        return text;
    }

    function now() {
        return Date.now();
    }

    function currentCatgirl() {
        var cfg = window.lanlan_config;
        return cfg && typeof cfg.lanlan_name === 'string' ? cfg.lanlan_name : '';
    }

    function isChatFollowerPath() {
        var pathname = (window.location && window.location.pathname) || '';
        return /^\/chat(?:_full)?(?:\/|$)/.test(pathname);
    }

    // 画串门界面的页：单窗口 index.html，或多窗口的 chat 跟随窗（Pet 窗也带多窗口标记，必须看路径）
    function isUiWindow() {
        return window.__NEKO_MULTI_WINDOW__ !== true || isChatFollowerPath();
    }

    function getHost() {
        return window.reactChatWindowHost || null;
    }

    function toast(message, duration) {
        if (!message) return;
        if (typeof window.showStatusToast === 'function') window.showStatusToast(message, duration || 4000);
    }

    var compactGeometryRefreshTimer = 0;

    function render() {
        if (typeof I.renderWindow === 'function') I.renderWindow();
        // visitBar is rendered by React into the compact choice portal. Its
        // visual state can change without resizing the base composer, so the
        // transparent Electron window may otherwise keep the previous native
        // mouse island and let these visible buttons click through to the app
        // behind it. Refresh once now and once after React commits the portal.
        refreshCompactGeometry();
    }

    function sendSocketFrame(frame) {
        var S = window.appState;
        var socket = S && S.socket;
        if (!socket || socket.readyState !== 1) return false;
        try {
            socket.send(JSON.stringify(frame));
            return true;
        } catch (_) {
            return false;
        }
    }

    function userSwitchOn() {
        var value = typeof window.visitEnabled !== 'undefined'
            ? window.visitEnabled
            : (window.appState && window.appState.visitEnabled);
        return value === true;
    }

    // ── 本机 HTTP（回环 + CSRF；读端点同样带 token）─────────────────────

    var fallbackCsrfToken = '';

    async function fallbackMutationHeaders(forceRefresh) {
        if (fallbackCsrfToken && forceRefresh !== true) {
            return { 'X-CSRF-Token': fallbackCsrfToken };
        }
        try {
            var response = await fetch('/api/config/page_config', { cache: 'no-store' });
            if (!response.ok) return {};
            var data = await response.json();
            var token = data && data.autostart_csrf_token;
            if (typeof token !== 'string' || !token) return {};
            fallbackCsrfToken = token;
            return { 'X-CSRF-Token': token };
        } catch (_) {
            return {};
        }
    }

    async function mutationHeaders(forceRefresh) {
        var security = window.nekoLocalMutationSecurity;
        if (security && typeof security.getMutationHeaders === 'function') {
            try {
                if (forceRefresh === true && typeof security.refreshToken === 'function') {
                    await security.refreshToken();
                }
                var headers = await security.getMutationHeaders();
                if (headers && typeof headers['X-CSRF-Token'] === 'string'
                        && headers['X-CSRF-Token']) return headers;
            } catch (_) { /* 拿不到：后端 403，按失败处理 */ }
        }
        // 独立聊天窗、后端刚重启或脚本加载顺序变化时，公共 helper 可能暂时
        // 还没建立。串门不能因此静默等到邀请码超时，直接从同源 page_config
        // 补取一次当前进程的 token。
        return fallbackMutationHeaders(forceRefresh === true);
    }

    async function isStaleCsrf(response) {
        if (!response || response.status !== 403) return false;
        try {
            var body = await response.clone().json();
            return !!(body && body.error_code === 'csrf_validation_failed');
        } catch (_) {
            return false;
        }
    }

    async function api(method, path, body) {
        var send = async function () {
            var headers = Object.assign({}, await mutationHeaders(false));
            var init = { method: method, cache: 'no-store', headers: headers };
            if (body !== undefined) {
                headers['Content-Type'] = 'application/json';
                init.body = JSON.stringify(body);
            }
            return fetch(path, init);
        };
        var response;
        try {
            response = await send();
            if (await isStaleCsrf(response)) {
                // 后端重启会换 token；无论公共 helper 是否已加载，都强制刷新，
                // 然后只重放一次原请求。
                var refreshed = await mutationHeaders(true);
                var retryHeaders = Object.assign({}, refreshed);
                var retryInit = { method: method, cache: 'no-store', headers: retryHeaders };
                if (body !== undefined) {
                    retryHeaders['Content-Type'] = 'application/json';
                    retryInit.body = JSON.stringify(body);
                }
                response = await fetch(path, retryInit);
            }
        } catch (_) {
            return { status: 0, body: null };
        }
        var parsed = null;
        try { parsed = await response.json(); } catch (_) { parsed = null; }
        return { status: response.status, body: parsed };
    }

    function bodyCode(result) {
        var body = result && result.body;
        if (!body || typeof body !== 'object') return '';
        return String(body.code || body.error || body.reason || '');
    }

    // ── 对话框（宿主 DOM；Electron 透明窗里要登记成命中区）─────────────

    var openDialogs = [];

    function makeButton(label, variant, onClick) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'visit-dialog-btn' + (variant ? ' is-' + variant : '');
        btn.textContent = label;
        btn.addEventListener('click', function (event) {
            event.stopPropagation();
            onClick();
        });
        return btn;
    }

    /**
     * spec: {title, lines:[string|{text, tone}], body?:Node, buttons:[{id,label,variant}], dismissible}
     * → {root, close(result), setLines(lines), setButtons(buttons), result: Promise}
     */
    // Electron compact 窗口的命中区按 DOM 收集：裸 DOM 对话框开 / 关后要通知它重算，否则新按钮点不到、关掉后留一块透明命中区
    function refreshCompactGeometry() {
        function dispatch() {
            try {
                window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-refresh'));
            } catch (_) { /* 非 compact 窗口没有几何桥 */ }
        }
        dispatch();
        if (compactGeometryRefreshTimer) return;
        compactGeometryRefreshTimer = setTimeout(function () {
            compactGeometryRefreshTimer = 0;
            dispatch();
        }, 0);
    }

    function openDialog(spec) {
        var overlay = document.createElement('div');
        overlay.className = 'visit-dialog-overlay';
        var dialog = document.createElement('div');
        dialog.className = 'visit-dialog';
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        // 宿主页弹窗在 React 根之外：登记成 Electron 透明窗的原生命中岛（与小剧场弹窗同一套）
        dialog.setAttribute('data-compact-geometry-owner', 'surface');
        dialog.setAttribute('data-compact-geometry-item', 'visitDialog');
        var title = document.createElement('div');
        title.className = 'visit-dialog-title';
        title.textContent = spec.title || '';
        dialog.setAttribute('aria-label', spec.title || '');
        dialog.appendChild(title);
        var linesBox = document.createElement('div');
        linesBox.className = 'visit-dialog-lines';
        dialog.appendChild(linesBox);
        if (spec.body) dialog.appendChild(spec.body);
        var actions = document.createElement('div');
        actions.className = 'visit-dialog-actions';
        dialog.appendChild(actions);
        overlay.appendChild(dialog);

        var settle;
        var handle = {
            root: dialog,
            closed: false,
            result: new Promise(function (resolve) { settle = resolve; }),
            close: function (value) {
                if (handle.closed) return;
                handle.closed = true;
                if (typeof document.removeEventListener === 'function') document.removeEventListener('keydown', onKeydown, true);
                if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                releaseInteractive();
                refreshCompactGeometry();
                var index = openDialogs.indexOf(handle);
                if (index >= 0) openDialogs.splice(index, 1);
                settle(value === undefined ? null : value);
            },
            setLines: function (lines) {
                while (linesBox.firstChild) linesBox.removeChild(linesBox.firstChild);
                (lines || []).forEach(function (line) {
                    if (line === null || line === undefined || line === '') return;
                    var p = document.createElement('p');
                    var spec2 = typeof line === 'string' ? { text: line } : line;
                    p.className = 'visit-dialog-line' + (spec2.tone ? ' is-' + spec2.tone : '');
                    p.textContent = spec2.text;
                    linesBox.appendChild(p);
                });
            },
            setButtons: function (buttons) {
                while (actions.firstChild) actions.removeChild(actions.firstChild);
                (buttons || []).forEach(function (b) {
                    var btn = makeButton(b.label, b.variant, function () {
                        if (typeof b.onClick === 'function') {
                            b.onClick(handle);
                        } else {
                            handle.close(b.id);
                        }
                    });
                    if (b.disabled) btn.disabled = true;
                    btn.setAttribute('data-visit-dialog-button', b.id);
                    actions.appendChild(btn);
                });
            }
        };
        handle.setLines(spec.lines);
        handle.setButtons(spec.buttons);
        if (spec.dismissible !== false) {
            overlay.addEventListener('click', function (event) {
                if (event.target === overlay) handle.close(null);
            });
        }
        // 焦点留在对话框后面的页面上时 overlay 收不到按键：Escape 挂在 document 上（只认最上面那个对话框）
        function onKeydown(event) {
            if (event.key !== 'Escape' || spec.dismissible === false) return;
            if (openDialogs[openDialogs.length - 1] !== handle) return;
            handle.close(null);
        }
        if (typeof document.addEventListener === 'function') document.addEventListener('keydown', onKeydown, true);
        (document.body || document.documentElement).appendChild(overlay);
        // macOS / Windows 的 Electron compact 窗口会把命中区之外的鼠标透传给桌面。
        // 对话框虽然已经画出来，但 native hit region 的异步重排存在一个空窗期；这时
        // 输入框仍能拿到键盘焦点，按钮点击却会落到后面的应用。对话框存活期间接管整窗
        // 输入，关闭后恢复打开前的状态（和串门人设面板使用同一条桌面桥）。
        var releaseInteractive = acquireForceInteractiveLease();
        refreshCompactGeometry();
        openDialogs.push(handle);
        // 焦点移进对话框（有输入框的由调用方再聚焦到输入框）
        var firstButton = actions.firstChild;
        if (firstButton && typeof firstButton.focus === 'function') {
            try { firstButton.focus(); } catch (_) { /* noop */ }
        }
        return handle;
    }

    function confirmDialog(text, options) {
        options = options || {};
        return openDialog({
            title: options.title || t('visit.common.confirmTitle', '确认'),
            lines: [text],
            buttons: [
                { id: 'cancel', label: t('visit.common.cancel', '取消') },
                { id: 'ok', label: options.okLabel || t('visit.common.ok', '确定'), variant: options.danger ? 'danger' : 'primary' }
            ]
        }).result.then(function (value) { return value === 'ok'; });
    }

    // ── 串门状态 ────────────────────────────────────────────────────

    var visit = blankVisit();
    var lastEnded = null;
    var releaseEnabled = false;
    var starting = false;
    var pendingDirectInvitation = null; // {messageId, payload, busy}
    var directInvitationPresenting = new Set();
    var resolvedDirectInvitationIds = new Set();
    var typing = null;           // {name, until}
    var typingTimer = 0;
    var reconnectTimer = 0;
    var peerAvatarUrl = '';
    var inviteDialog = null;     // {visitId, handle, timer}
    var endedVisitIds = [];      // 本页见过 ended 的场次（最多 32 个）
    var boundCatgirl = '';       // 最近一次 bind 时这一页的角色名
    var visitStateKnown = false; // 首次恢复完成前，空白状态不代表后端已经空闲。
    var previewedPeer = null;    // 出门确认时预览到的对端 {visitId, name, shortCode, crossRegion}

    function markEnded(visitId) {
        if (!visitId || endedVisitIds.indexOf(visitId) !== -1) return;
        endedVisitIds.push(visitId);
        if (endedVisitIds.length > 32) endedVisitIds.shift();
    }

    function blankVisit() {
        return {
            visitId: null, side: null, phase: null,
            peerName: '', peerShortId: '', peerHumanLabel: '',
            inviteCode: '', inviteExpiresAt: 0, targetUserId: '', crossRegion: false,
            peerHidden: false, reconnecting: false, cancelRequested: false, endingSoonAt: 0,
            recipient: 'guest_cat', ownName: '',
            // 这一场真的开始过（见过 active / wrap_up）：结束时才留查看详情 / 导出 / 举报
            everStarted: false
        };
    }

    function noteStarted() {
        if (visit.phase === 'active' || visit.phase === 'wrap_up') visit.everStarted = true;
    }

    // 快照能否说明这一场开始过：active / wrap_up 算；ending 要看房间是否建过（激活时才建）或已有台词——
    // 取消 / 激活前失败也会先到 ending
    function snapshotStarted(state) {
        if (state.phase === 'active' || state.phase === 'wrap_up') return true;
        if (state.phase !== 'ending') return false;
        return (!!state.room && typeof state.room === 'object')
            || (Array.isArray(state.transcript) && state.transcript.length > 0);
    }

    function phaseOf(action) {
        if (action === 'started' || action === 'departed') return 'active';
        return action;
    }

    function inVisit() {
        return !!visit.visitId && visit.phase !== null && visit.phase !== 'ended';
    }

    function publicVisitState() {
        return {
            visitId: visit.visitId || '',
            phase: visit.cancelRequested && OWNED_PHASES.indexOf(visit.phase) !== -1 ? 'cancelling' : visit.phase || '',
            side: visit.side || '', ready: visitStateKnown,
            active: inVisit(), inviteExpiresAt: visit.inviteExpiresAt || 0,
            targetUserId: visit.targetUserId || ''
        };
    }

    function dispatchVisitStatus(sourceFrame) {
        var detail = publicVisitState();
        var action = String(sourceFrame && sourceFrame.action || '');
        if (action === 'ended') {
            detail.visitId = String(sourceFrame.visit_id || '');
            detail.phase = 'ended';
            detail.active = false;
        }
        detail.action = action || detail.phase;
        try {
            window.dispatchEvent(new CustomEvent('neko-visit-status-change', { detail: detail }));
        } catch (_) { /* noop */ }
    }

    /** 串门是否接管了输入框（ending 不算：后端已交还，亲人恢复普通聊天）。 */
    I.isVisitChatActive = function isVisitChatActive() {
        return inVisit() && (OWNED_PHASES.indexOf(visit.phase) !== -1 || visit.reconnecting);
    };

    function ownCatName() {
        return visit.ownName || currentCatgirl() || t('visit.speaker.ownCat', '她');
    }

    function peerCatName() {
        return visit.peerName || t('visit.speaker.peerCat', '对方的猫娘');
    }

    function userName() {
        var cfg = window.lanlan_config || {};
        return String(cfg.master_display_name || cfg.master_nickname || window.master_display_name || '').trim()
            || t('visit.speaker.you', '你');
    }

    // ── 工具条 / 输入框 ─────────────────────────────────────────────

    function barAction(id, label, variant, extra) {
        return Object.assign({ id: id, label: label, variant: variant || 'secondary' }, extra || {});
    }

    function badgeText() {
        var host = visit.side === 'host';
        if (visit.reconnecting) return { text: t('visit.bar.reconnecting', '连接不稳，正在重连'), tone: 'warning' };
        switch (visit.phase) {
        case 'pending':
            return { text: host ? t('visit.bar.preparingInvite', '正在准备邀请…') : t('visit.bar.joining', '正在出门…'), tone: 'waiting' };
        case 'invite_ready':
            return { text: t('visit.bar.waitingGuest', '等客人来'), tone: 'waiting' };
        case 'joining':
            return { text: host ? t('visit.bar.guestArriving', '客人在路上') : t('visit.bar.joining', '正在出门…'), tone: 'waiting' };
        case 'awaiting_accept':
            return {
                text: host
                    ? t('visit.bar.atTheDoor', '{{name}} 到门口了', { name: peerCatName() })
                    : t('visit.bar.waitingDoor', '等对方开门'),
                tone: 'waiting'
            };
        case 'active':
            if (visit.peerHidden) return { text: t('visit.bar.peerHidden', '离开了一下'), tone: 'warning' };
            return {
                text: host
                    ? t('visit.bar.hosting', '{{name}} 在做客', { name: peerCatName() })
                    : t('visit.bar.away', '她在 {{name}} 家', { name: peerCatName() }),
                tone: 'active'
            };
        case 'wrap_up':
            return { text: t('visit.wrapUp.badge', '道别中'), tone: 'waiting' };
        case 'ending':
            return { text: t('visit.bar.ending', '正在收尾'), tone: 'waiting' };
        default:
            return null;
        }
    }

    function idleBar() {
        if (pendingDirectInvitation) {
            return {
                tone: 'waiting',
                actions: [
                    barAction('direct_decline', t('visit.directInvite.decline', '拒绝'), 'secondary', { disabled: pendingDirectInvitation.busy === true }),
                    barAction('direct_accept', t('visit.directInvite.accept', '接受'), 'primary', { disabled: pendingDirectInvitation.busy === true })
                ]
            };
        }
        return null;
    }

    function visitBar() {
        if (!inVisit()) return idleBar();
        // 建房、等待对方与出门途中都不占用聊天框上方的操作区。
        // 邀请的撤回与邀请码备用流程统一留在喵宇宙面板里；真正开始串门后再显示过程操作。
        if (WAITING_PHASES.indexOf(visit.phase) !== -1) return null;
        var badge = badgeText();
        var bar = { badge: badge ? badge.text : undefined, tone: badge ? badge.tone : 'active', actions: [] };
        var host = visit.side === 'host';
        if (typing && typing.until > now()) {
            bar.detail = t('visit.bar.typing', '{{name}} 正在输入…', { name: typing.name });
        } else if (visit.phase === 'active' && visit.endingSoonAt) {
            bar.detail = t('visit.bar.endingSoon', '快到时间了');
        }
        if (visit.phase === 'active') {
            if (host) {
                bar.actions.push(barAction('to_guest', t('visit.bar.toGuest', '发给 {{name}}', { name: peerCatName() }),
                    'secondary', { pressed: visit.recipient === 'guest_cat' }));
                bar.actions.push(barAction('to_own', t('visit.bar.toOwn', '发给 {{name}}', { name: ownCatName() }),
                    'secondary', { pressed: visit.recipient === 'own_cat' }));
                bar.actions.push(barAction('recall', t('visit.bar.seeOff', '送客')));
            } else {
                bar.actions.push(barAction('recall', t('visit.bar.recall', '叫她回来'), 'primary'));
            }
        } else if (visit.phase === 'wrap_up') {
            // 收尾卡住时的第二次点击：硬结束
            bar.actions.push(barAction('end_now', t('visit.bar.endNow', '立即结束'), 'danger'));
        }
        return bar;
    }

    function inputPlaceholder() {
        if (!inVisit()) return '';
        if (visit.side === 'guest' && visit.phase !== 'ending') return t('visit.input.placeholderAway', '她出门了，可以叫她回来');
        if (WAITING_PHASES.indexOf(visit.phase) !== -1) return t('visit.input.placeholderWaiting', '客人还没到等等再聊吧');
        if (visit.reconnecting) return t('visit.input.placeholderReconnecting', '正在重连，稍等一下');
        if (visit.phase === 'wrap_up') return t('visit.wrapUp.composerPlaceholder', '她们正在道别…');
        if (visit.phase === 'active') {
            return visit.recipient === 'own_cat'
                ? t('visit.input.placeholderToOwn', '对 {{name}} 说…', { name: ownCatName() })
                : t('visit.input.placeholderToGuest', '对 {{name}} 说…', { name: peerCatName() });
        }
        return '';
    }

    /** buildRenderProps 读：没在串门且没有入口时 bar 为 null、不覆盖任何既有属性。 */
    I.getVisitChatView = function getVisitChatView() {
        if (!isUiWindow()) return null;
        var bar = visitBar();
        var disabled = inVisit() && visit.phase !== 'ending'
            && (visit.side === 'guest' || visit.phase === 'wrap_up'
                || (visit.reconnecting && WAITING_PHASES.indexOf(visit.phase) === -1));
        return {
            bar: bar,
            composerDisabled: disabled,
            inputPlaceholder: inputPlaceholder()
        };
    };

    // ── 亲人打字 ────────────────────────────────────────────────────

    var pendingLocal = [];   // [{requestId, messageId, text, submitSeq}] 已发出、还没等到回显的本地气泡（先进先出）
    var submitSeq = 0;       // 每次交出去一句 +1：被拒那句之后又提交过，就不把旧文字塞回输入框
    var visitAckedTextSession = false;
    var lateEchoBubbles = new Map();   // request_id → 收尾时标了「未送达」的本地气泡（等迟到的回显认领）   // 「文本会话已开」是串门路由的 ack 给的（没有真的普通会话）

    // 串门收尾：把只由串门 ack 置上的文本会话标记撤掉，下一句普通聊天照常显式发开会话请求
    function releaseVisitTextSession() {
        if (!visitAckedTextSession) return;
        visitAckedTextSession = false;
        var appState = window.appState;
        if (appState && appState.isTextSessionActive === true) appState.isTextSessionActive = false;
    }

    function blockReason() {
        if (!I.isVisitChatActive()) return null;
        if (visit.side === 'guest') return 'VISIT_INPUT_REFUSED_AWAY';
        if (visit.phase === 'wrap_up') return 'VISIT_INPUT_REFUSED_WRAPUP';
        if (visit.reconnecting || WAITING_PHASES.indexOf(visit.phase) !== -1) return 'VISIT_INPUT_REFUSED_NOT_READY';
        return null;
    }

    // 与后端 sanitize.strip_control_chars 同一规则：删 Cc / Cs（留 \n \t）与 Cf（留 ZWNJ / ZWJ），再看剩下的是否全是空白
    function relayCleanIsEmpty(text) {
        var cleaned = String(text || '')
            .replace(/[\p{Cc}\p{Cs}]/gu, function (ch) { return ch === '\n' || ch === '\t' ? ch : ''; })
            .replace(/\p{Cf}/gu, function (ch) { return ch === '\u200c' || ch === '\u200d' ? ch : ''; });
        return !cleaned.trim();
    }

    function refusalToast(code) {
        if (code === 'VISIT_INPUT_REFUSED_WRAPUP') return t('visit.wrapUp.toastRefused', '她们正在道别，这句没有发出去');
        return t('visit.status.' + code, '');
    }

    function restoreDraft(requestId) {
        // 只在输入框此刻是空的（提交后没再编辑）时恢复：React 侧 rollbackDraft 只填空输入框
        if (typeof I.rollbackLastDraft === 'function') I.rollbackLastDraft(requestId);
    }

    function localBubbleMessage(messageId, text, status) {
        var to = visit.recipient === 'own_cat' ? ownCatName() : peerCatName();
        var author = t('visit.speaker.toArrow', '{{from}} → {{to}}', { from: userName(), to: to });
        return {
            id: messageId,
            role: 'user',
            author: author,
            time: typeof window.getCurrentTimeString === 'function' ? window.getCurrentTimeString() : '',
            createdAt: now(),
            avatarLabel: String(userName()).slice(0, 1).toUpperCase(),
            // 本地气泡同样纯文本（§3.6.6）
            blocks: [{ type: 'text', text: text, plain_text: true }],
            status: status || 'sending'
        };
    }

    /**
     * React composer 的提交（handleComposerSubmit 在串门接管输入时转进来）。
     * 返回 true 表示这句交出去了（发 WS），false 表示被拦下、文字留在输入框。
     */
    I.submitVisitChatText = function submitVisitChatText(detail) {
        var text = detail && typeof detail.text === 'string' ? detail.text : '';
        var requestId = detail && detail.requestId ? String(detail.requestId) : ('req-' + now());
        if (!text.trim()) return false;
        if (relayCleanIsEmpty(text)) {
            // 只剩零宽空格 / 双向控制符这类会被后端清洗掉的字符：发出去后端会静默丢掉，本地气泡永远等不到回显
            setTimeout(function () { restoreDraft(requestId); }, 0);
            return false;
        }
        var refused = blockReason();
        if (refused) {
            toast(refusalToast(refused));
            // React 提交后会清空输入框：下一拍把原文放回去（不调 sendTextPayload，不发 WS）
            setTimeout(function () { restoreDraft(requestId); }, 0);
            return false;
        }
        var send = window.appButtons && typeof window.appButtons.sendTextPayload === 'function'
            ? window.appButtons.sendTextPayload
            : (typeof window.sendTextPayload === 'function' ? window.sendTextPayload : null);
        if (!send) {
            setTimeout(function () { restoreDraft(requestId); }, 0);
            return false;
        }
        var messageId = 'visit-local-' + requestId;
        var appState = window.appState;
        // 页面还没有普通文本会话：这句会先发开会话请求，串门路由只回 ack、并不起普通会话
        if (appState && appState.isTextSessionActive !== true) visitAckedTextSession = true;
        var host = getHost();
        if (host && typeof host.appendMessage === 'function') {
            host.appendMessage(localBubbleMessage(messageId, text.trim(), 'sending'));
        }
        submitSeq += 1;
        pendingLocal.push({ requestId: requestId, messageId: messageId, text: text.trim(), submitSeq: submitSeq });
        try {
            Promise.resolve(send(text, {
                source: 'neko_visit:' + visit.recipient,
                visitId: visit.visitId || '',
                requestId: requestId,
                // 串门只收文字：待发截图留在输入区，不上传、不清掉
                ignoreComposerAttachments: true,
                submitMethod: detail.submitMethod,
                visitLine: true,
                skipAvatarInteractionDeferral: true
            })).then(function (ok) {
                // 前端自己没发出去（会话没起来 / WS 未连等）：同样标「未送达」
                if (ok === false) markNotDelivered(requestId, true);
            }, function () { markNotDelivered(requestId, true); });
        } catch (_) {
            markNotDelivered(requestId, true);
        }
        return true;
    };

    function takePending(requestId) {
        for (var i = 0; i < pendingLocal.length; i++) {
            if (pendingLocal[i].requestId === requestId) return pendingLocal.splice(i, 1)[0];
        }
        return null;
    }

    function markNotDelivered(requestId, restore) {
        var entry = takePending(requestId);
        if (!entry) return false;
        var host = getHost();
        if (host && typeof host.updateMessage === 'function') {
            var message = localBubbleMessage(entry.messageId, entry.text, 'failed');
            message.blocks.push({ type: 'status', tone: 'warning', text: t('visit.input.notDelivered', '未送达') });
            host.updateMessage(entry.messageId, { blocks: message.blocks, status: 'failed' });
        }
        // 之后又提交过别的句子：输入框空着也不是「没动过」，不恢复（React 侧另外只填空输入框）
        if (restore && entry.submitSeq === submitSeq) {
            restoreDraft(requestId);
        } else if (typeof I.clearPendingRollbackDraft === 'function') {
            I.clearPendingRollbackDraft(requestId);
        }
        return true;
    }

    // 后端回显的本侧亲人句认领本地气泡：按回显带的 request_id 认领（丢过一次回显也不会错位）；
    // 回显带了 id 却不在等的列表里（别的窗口发的 / 早已标掉）就不认领。只有不带 id 的旧后端才按先进先出
    function claimLocalBubble(entry) {
        var claimed = null;
        if (entry && entry.requestId) {
            claimed = takePending(entry.requestId);
            if (!claimed && lateEchoBubbles.has(entry.requestId)) {
                claimed = lateEchoBubbles.get(entry.requestId);
                lateEchoBubbles.delete(entry.requestId);
            }
        } else if (!entry || !entry.fromSnapshot) {
            claimed = pendingLocal.shift() || null;
        }
        if (claimed && typeof I.clearPendingRollbackDraft === 'function') I.clearPendingRollbackDraft(claimed.requestId);
        return claimed;
    }

    // ── 台词拼接 ────────────────────────────────────────────────────

    // (visit_id, line_id) → {clauses[], text, final, truncated, messageId, speaker, addressee, stallTimer}
    // line_id 每场重新计数：按场次分开存，漏了阶段帧时新一场的 h:1 不会撞上上一场留着的 h:1
    var lines = new Map();

    function lineKey(visitId, lineId) {
        return String(visitId || '') + '|' + String(lineId || '');
    }

    function lineEntry(frame) {
        var id = String(frame.line_id || '');
        if (!id) return null;
        var entryVisit = String(frame.visit_id || visit.visitId || '');
        var key = lineKey(entryVisit, id);
        var entry = lines.get(key);
        if (!entry) {
            entry = {
                // line_id 每场重新计数（h:1、g:1…）：气泡 id 带上场次，下一场的同号台词不会改到上一场的气泡
                lineId: id, visitId: entryVisit, clauses: [], text: '', final: false,
                truncated: false, messageId: 'visit-line-' + entryVisit + ':' + id, speaker: frame.speaker || {}, addressee: frame.addressee || {},
                stallTimer: 0, rendered: false, key: key
            };
            lines.set(key, entry);
            while (lines.size > 512) {
                var oldest = lines.keys().next().value;
                var old = lines.get(oldest);
                if (old && old.stallTimer) clearTimeout(old.stallTimer);
                lines.delete(oldest);
            }
        }
        if (frame.speaker) entry.speaker = frame.speaker;
        if (frame.addressee) entry.addressee = frame.addressee;
        if (!entry.requestId && typeof frame.request_id === 'string' && frame.request_id) entry.requestId = frame.request_id;
        if (entry.visitId && entry.visitId === visit.visitId) visit.everStarted = true;
        // 全序键 (lp, side_rank)：同 lp 时 host 在前（§3.6.6 ①），两侧页面显示顺序一致
        if (typeof entry.lp !== 'number' && Number.isInteger(frame.lp)) entry.lp = frame.lp;
        var side = frame.speaker && frame.speaker.side;
        if (side === 'host' || side === 'guest') entry.sideRank = side === 'host' ? 0 : 1;
        return entry;
    }

    function lineOrder(a, b) {
        return a.lp !== b.lp ? a.lp - b.lp : (a.sideRank || 0) - (b.sideRank || 0);
    }

    /**
     * 新上屏的一行按 (lp, side_rank) 落位：这一场已上屏、键比它大的行里最早的那条之前。
     * 没有更晚的行（绝大多数情况）就返回 undefined，照常追加到末尾。
     */
    function lineSortKey(entry) {
        if (typeof entry.lp !== 'number') return undefined;
        var next = null;
        lines.forEach(function (other) {
            if (other === entry || !other.rendered || other.visitId !== entry.visitId || typeof other.lp !== 'number') return;
            if (lineOrder(other, entry) > 0 && (!next || lineOrder(other, next) < 0)) next = other;
        });
        // 没有更晚的行、而这一场已经留了结束消息（空闲快照抢在最后几句前面收了尾）：排到结束消息之前
        var anchorId = next ? next.messageId
            : (endedVisitIds.indexOf(entry.visitId) !== -1 ? endedMessageId(entry.visitId) : '');
        if (!anchorId) return undefined;
        var host = getHost();
        var messages = host && typeof host.getState === 'function' ? ((host.getState() || {}).messages || []) : [];
        var nextMessage = null;
        for (var i = 0; i < messages.length; i++) {
            if (messages[i] && messages[i].id === anchorId) { nextMessage = messages[i]; break; }
        }
        if (!nextMessage || typeof nextMessage.sortKey !== 'number') return undefined;
        var before = -Infinity;
        messages.forEach(function (message) {
            var key = message && message.sortKey;
            if (typeof key === 'number' && key < nextMessage.sortKey && key > before) before = key;
        });
        return before === -Infinity ? nextMessage.sortKey - 1 : (before + nextMessage.sortKey) / 2;
    }

    /** 已到的分片按 i 拼起来：中间缺的片留一个占位、不补洞。 */
    function assembleClauses(clauses) {
        var out = [];
        for (var i = 0; i < clauses.length; i++) {
            out.push(typeof clauses[i] === 'string' ? clauses[i] : GAP_PLACEHOLDER);
        }
        return out.join('');
    }

    function speakerAuthor(speaker, addressee) {
        speaker = speaker || {};
        if (speaker.self === true) {
            if (speaker.kind === 'human') {
                // 用这一行自己的 side（本侧）比：重载后 /state 还没回来时 visit.side 还是空的
                var ownSide = speaker.side || visit.side;
                var toOwn = addressee && addressee.side === ownSide && addressee.kind === 'cat';
                return t('visit.speaker.toArrow', '{{from}} → {{to}}', {
                    from: userName(), to: toOwn ? ownCatName() : peerCatName()
                });
            }
            return ownCatName();
        }
        var name = String(speaker.name || '').trim();
        if (speaker.kind === 'human') {
            return name || visit.peerHumanLabel || t('visit.speaker.peerHuman', '对方的家里人');
        }
        return name || peerCatName();
    }

    function participantName(participant) {
        participant = participant || {};
        var explicit = String(participant.name || '').trim();
        if (explicit) return explicit;
        var own = participant.side && participant.side === visit.side;
        if (participant.kind === 'cat') return own ? ownCatName() : peerCatName();
        if (participant.kind === 'human') {
            return own ? userName() : (visit.peerHumanLabel || t('visit.speaker.peerHuman', '对方的家里人'));
        }
        return '';
    }

    /**
     * 人名已由气泡头展示；兼容旧端或模型偶发模仿历史标记而输出的
     * ``[名字] 正文`` / ``【名字】：正文``。句首名称后是空白、冒号或横线时，按无意义
     * 标签整体去掉；名称在句中，或句首后直接跟逗号等有语义标点时，只拆掉括号、保留
     * 名字。其它方括号内容保留。流式首片只到 ``[na`` 时先不上屏，避免前缀闪一下。
     */
    function withoutRepeatedSpeakerPrefix(text, speaker, addressee) {
        var raw = String(text || '');
        var candidate = raw.replace(/^\s+/, '');
        var names = [participantName(speaker), participantName(addressee)].filter(function (name, index, all) {
            return !!name && all.indexOf(name) === index;
        });
        for (var n = 0; n < names.length; n++) {
            var prefixes = ['[' + names[n] + ']', '【' + names[n] + '】'];
            for (var i = 0; i < prefixes.length; i++) {
                var prefix = prefixes[i];
                if (candidate.indexOf(prefix) === 0) {
                    var rest = candidate.slice(prefix.length);
                    if (!rest) return '';
                    if (/^[\s:：\-—]+/.test(rest)) return rest.replace(/^[\s:：\-—]+/, '');
                    return names[n] + rest;
                }
                if (candidate && prefix.indexOf(candidate) === 0) return '';
            }
        }
        var normalized = raw;
        names.forEach(function (name) {
            normalized = normalized.split('[' + name + ']').join(name);
            normalized = normalized.split('【' + name + '】').join(name);
        });
        return normalized;
    }

    function renderLine(entry) {
        if (!isUiWindow()) return;
        // 实时的自家猫娘台词已由后端提交到标准 gemini_response 链路，不再画第二个
        // 专用气泡。页面重载时普通助手消息不会恢复，必须用 transcript 快照补回历史台词。
        if (!entry.fromSnapshot && entry.speaker
                && entry.speaker.self === true && entry.speaker.kind === 'cat') return;
        // 协议偶尔会在上游模型失败时收到空的 delta / final。空内容不是一条可见消息，
        // 不应占出一个只有作者和时间的气泡；带“被打断”提示的空行仍保留提示。
        var visibleText = withoutRepeatedSpeakerPrefix(entry.text, entry.speaker, entry.addressee);
        if (relayCleanIsEmpty(visibleText) && !entry.truncated) {
            if (entry.final) removeRenderedLine(entry);
            return;
        }
        var adapter = window.appChat;
        if (!adapter || typeof adapter.renderVisitLine !== 'function') return;
        var notice = entry.truncated ? t('visit.stream.truncated', '（被打断）') : '';
        var claimed = null;
        if (!entry.rendered && entry.final && (!entry.fromSnapshot || entry.requestId)
                && entry.speaker && entry.speaker.self === true && entry.speaker.kind === 'human') {
            // 本侧亲人句：认领发送时的本地气泡，原地换成后端清洗后的那一句。快照重放的行只按 request_id 认领
            // （不带 id 的历史行按顺序猜会占掉刚发出的那句的气泡）
            claimed = claimLocalBubble(entry);
            if (claimed) entry.messageId = claimed.messageId;
        }
        adapter.renderVisitLine({
            messageId: entry.messageId,
            // 第一次上屏（含认领本地气泡）时按全序键落位；之后的分片 / 终稿原地更新，不再挪
            sortKey: entry.rendered ? undefined : lineSortKey(entry),
            speaker: entry.speaker,
            author: speakerAuthor(entry.speaker, entry.addressee),
            avatarUrl: entry.speaker && entry.speaker.self !== true && entry.speaker.kind === 'cat' ? peerAvatarUrl : '',
            text: visibleText,
            notice: notice,
            replace: !!claimed
        });
        entry.rendered = true;
    }

    function removeRenderedLine(entry) {
        if (!entry || !entry.rendered) return;
        var host = getHost();
        if (host && typeof host.removeMessage === 'function') host.removeMessage(entry.messageId);
        entry.rendered = false;
    }

    function armStall(entry) {
        if (entry.stallTimer) clearTimeout(entry.stallTimer);
        entry.stallTimer = setTimeout(function () {
            entry.stallTimer = 0;
            if (entry.final) return;
            // 20 s 没有新片、也没等到 final：本地截断（之后 final 到了照样全文覆盖）
            entry.truncated = true;
            entry.text = assembleClauses(entry.clauses);
            renderLine(entry);
        }, STREAM_STALL_MS);
    }

    function onLineDelta(frame) {
        var entry = lineEntry(frame);
        if (!entry || entry.final) return;  // 收口之后迟到的片不再动气泡
        var i = Number(frame.i);
        if (!Number.isInteger(i) || i < 0 || i > 4096) return;
        if (entry.aborted && i >= entry.clauses.length) return;   // 截断点之后的片不再上屏
        entry.clauses[i] = String(frame.text || '');
        entry.text = assembleClauses(entry.clauses);
        if (typing && frame.speaker && frame.speaker.name === typing.name) typing = null;
        renderLine(entry);
        armStall(entry);
    }

    function onLineFinal(frame, fromSnapshot) {
        var entry = lineEntry(frame);
        if (!entry) return;
        if (fromSnapshot) {
            entry.fromSnapshot = true;
        } else if (entry.fromSnapshot && entry.rendered) {
            // 快照先把这一句上了屏、实时终稿后到：是刚发出的那句（读快照时后端已记下、回显还在路上）。
            // 认领它的本地气泡并撤掉，不留一条一直「发送中」、收尾时又被标未送达的重复气泡
            entry.fromSnapshot = false;
            if (entry.speaker && entry.speaker.self === true && entry.speaker.kind === 'human') {
                var claimed = claimLocalBubble(entry);
                var host = getHost();
                if (claimed && host && typeof host.removeMessage === 'function') host.removeMessage(claimed.messageId);
            }
        }
        if (entry.stallTimer) { clearTimeout(entry.stallTimer); entry.stallTimer = 0; }
        entry.final = true;
        entry.text = String(frame.text || '');
        entry.truncated = frame.truncated === true;
        if (typing && frame.speaker && frame.speaker.name === typing.name) typing = null;
        // authoritative final 为空时，撤掉此前 delta 形成的半截气泡；否则会留下“空回复”。
        if (relayCleanIsEmpty(entry.text) && !entry.truncated) {
            removeRenderedLine(entry);
            return;
        }
        renderLine(entry);
    }

    function onLineAbort(frame) {
        var entry = lineEntry(frame);
        if (!entry || entry.final) return;
        if (entry.stallTimer) { clearTimeout(entry.stallTimer); entry.stallTimer = 0; }
        if (frame.reason === 'rejected') {
            // 后端整行作废（协议校验没过）：不会再有可靠终稿，已开出的半截气泡撤掉
            var host = getHost();
            if (entry.rendered && host && typeof host.removeMessage === 'function') host.removeMessage(entry.messageId);
            lines.delete(entry.key);
            return;
        }
        var done = Number(frame.i_done);
        // 分片可丢：i_done 比本页收到的多时补齐长度，缺的片照样显示占位，之后到的迟片还能落位
        if (Number.isInteger(done) && done >= 0 && done <= 4096) entry.clauses.length = done;
        entry.aborted = true;
        entry.truncated = true;
        entry.text = assembleClauses(entry.clauses);
        renderLine(entry);
    }

    function onTyping(frame) {
        var speaker = frame.speaker || {};
        if (frame.on === true) {
            typing = { name: speakerAuthor(speaker), until: now() + TYPING_CLEAR_MS };
        } else {
            typing = null;
        }
        if (typingTimer) clearTimeout(typingTimer);
        typingTimer = setTimeout(function () { typing = null; typingTimer = 0; render(); }, TYPING_CLEAR_MS);
        render();
    }

    // ── 状态帧 ─────────────────────────────────────────────────────

    function clearReconnectPoll() {
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = 0; }
    }

    function needsVisitStatePoll() {
        return visit.reconnecting || (visit.cancelRequested && inVisit());
    }

    function pollReconnect() {
        clearReconnectPoll();
        reconnectTimer = setTimeout(async function () {
            reconnectTimer = 0;
            if (!needsVisitStatePoll()) return;
            var catgirl = currentCatgirl();
            if (!catgirl) return;
            var polledVisitId = visit.visitId;
            var seqAtSend = stateFrameSeq;
            var result = await api('GET', '/api/visit/state?catgirl=' + encodeURIComponent(catgirl));
            var body = result.body;
            // 请求在路上时这一场已经结束、新一场开始：旧请求的快照不能拿来收新一场
            if (visit.visitId !== polledVisitId) return;
            if (result.status === 200 && body && typeof body === 'object' && needsVisitStatePoll()) {
                // ended 帧可能在断线里丢了：快照说没在串门（或已是另一场）就收掉这一场；同一场则按快照校正阶段
                applyState(body, stateFrameSeq !== seqAtSend);
                dispatchVisitStatus();
            }
            if (needsVisitStatePoll()) pollReconnect();
        }, RECONNECT_POLL_MS);
    }

    // 切换角色：清掉上一位角色的串门 UI 状态（不走 onEnded：不往新角色刚清空的聊天里写结束消息）
    function resetForCharacterSwitch() {
        visitStateKnown = false;
        openDialogs.slice().forEach(function (handle) { handle.close(null); });
        // 人设面板是另一个模块的对话框：一并关掉，免得在新角色的页面上改 / 确认上一位的人设
        if (typeof window.closeVisitPersonaPreview === 'function') window.closeVisitPersonaPreview();
        clearReconnectPoll();
        releaseVisitTextSession();
        lines.forEach(function (entry) { if (entry.stallTimer) clearTimeout(entry.stallTimer); });
        lines.clear();
        pendingLocal = [];
        visit = blankVisit();
        typing = null;
        peerAvatarUrl = '';
        previewedPeer = null;
        lastEnded = null;
        render();
    }

    // 换到另一场：清掉上一场的状态、台词拼接、待认领的本地气泡与访客头像（line_id 按场次计数）
    function startNewVisit(visitId) {
        visit = blankVisit();
        visit.visitId = visitId;
        peerAvatarUrl = '';
        applyPreviewedPeer();
        // 这一场的台词（漏了阶段帧时先到的）留着、并算作开始过；别的场次的清掉
        lines.forEach(function (entry, key) {
            if (entry.visitId === visitId) {
                visit.everStarted = true;
                return;
            }
            if (entry.stallTimer) clearTimeout(entry.stallTimer);
            lines.delete(key);
        });
        pendingLocal = [];
    }

    // guest 侧的状态帧不带对端身份：出门确认时预览到的显示名 / 短码按场次带进来
    function applyPreviewedPeer() {
        if (!previewedPeer || previewedPeer.visitId !== visit.visitId) return;
        if (!visit.peerName && previewedPeer.name) visit.peerName = previewedPeer.name;
        if (!visit.peerShortId && previewedPeer.shortCode) visit.peerShortId = previewedPeer.shortCode;
        if (previewedPeer.crossRegion) visit.crossRegion = true;
    }

    function onStateChange(frame) {
        var visitId = typeof frame.visit_id === 'string' ? frame.visit_id : '';
        if (!visitId) return;
        var action = String(frame.action || '');
        if (action === 'ended' && endedVisitIds.indexOf(visitId) !== -1) {
            // 空闲快照已先替这一场收过尾（留过结束消息）：迟到的 ended 帧不再重建这一场、不重复追加
            render();
            return;
        }
        // 另一场的迟到帧（这一场还在进行）：不动
        // pending 与 invite_ready 是新一场的权威起点（bind 重放只补 invite_ready、不补 pending）：可以换场
        if (visit.visitId && visit.visitId !== visitId && inVisit() && action !== 'pending' && action !== 'invite_ready') return;
        if (visit.visitId !== visitId) {
            // 漏收了上一场的 ended、新一场的 pending 先到：上一场照样留下结束消息、标掉还在等回显的本地气泡
            if (visit.visitId && inVisit()) {
                var previousVisit = visit.visitId;
                var previousSide = visit.side;
                onEnded({ visit_id: previousVisit, side: previousSide, reason: '' });
                // invite_ready 也可作为新场次的权威起点；先让显示端回收旧 iframe，
                // 否则它会按旧 visit_id 丢掉稍后转发的新 invite_ready。
                relayEndedToDisplay(previousVisit, previousSide);
            }
            startNewVisit(visitId);
        }
        if (frame.side === 'host' || frame.side === 'guest') {
            visit.side = frame.side;
            rememberSide(visitId, frame.side);
        }
        if (typeof frame.peer_name === 'string' && frame.peer_name) visit.peerName = frame.peer_name;
        if (typeof frame.peer_short_id === 'string' && frame.peer_short_id) visit.peerShortId = frame.peer_short_id;
        if (typeof frame.peer_human_label === 'string' && frame.peer_human_label) visit.peerHumanLabel = frame.peer_human_label;
        if (typeof frame.cross_region === 'boolean') visit.crossRegion = frame.cross_region;
        if (typeof frame.target_user_id === 'string') visit.targetUserId = frame.target_user_id;
        visit.ownName = currentCatgirl();
        applyPreviewedPeer();
        // 后端只给已激活的场次推 ending（额度不足、登录失效等激活前失败走状态横幅 + ended）：
        // 漏收 started 时 ending 本身就是开始过的证据，结束后照样留下带查看详情的结束消息。
        if (action === 'ending') visit.everStarted = true;
        switch (action) {
        case 'invite_ready':
            visit.phase = 'invite_ready';
            if (typeof frame.invite_code === 'string') visit.inviteCode = frame.invite_code;
            if (typeof frame.invite_expires_at === 'number') visit.inviteExpiresAt = frame.invite_expires_at;
            break;
        case 'peer_hidden':
            visit.peerHidden = true;
            break;
        case 'peer_visible':
            visit.peerHidden = false;
            break;
        case 'peer_crop':
            break;
        case 'reconnecting':
            visit.reconnecting = true;
            pollReconnect();
            break;
        case 'video_reconnecting':
            break;
        case 'voice_off':
            // 只给父页收语音旁路用（OD-15 v4），聊天面不变；跟随窗收到时同样转给 Pet 窗的父页（重复收到无害）
            if (typeof window.nekoRelayVisitStateToDisplay === 'function') {
                window.nekoRelayVisitStateToDisplay(frame);
            }
            return;
        case 'ending_soon':
            visit.endingSoonAt = typeof frame.ends_at === 'number' ? frame.ends_at : now() / 1000;
            break;
        case 'ended':
            onEnded(frame);
            render();
            relayEndedToDisplay(visitId, frame.side);
            return;
        case 'pending':
        case 'joining':
        case 'awaiting_accept':
        case 'started':
        case 'departed':
        case 'wrap_up':
        case 'ending':
            visit.phase = phaseOf(action);
            noteStarted();
            if (action === 'ending') {
                // 后端在接管释放之后才推 ending：输入框从这里起交还普通聊天，只由串门 ack 置上的会话标记就此撤掉，
                // 之后普通聊天起的真会话不会在 ended 时被误撤
                releaseVisitTextSession();
            }
            if (action !== 'pending') visit.inviteCode = '';
            if (visit.phase !== 'awaiting_accept') closeInviteDialog(visitId);
            break;
        default:
            return;   // 不认识的阶段（新版本后端）：不动
        }
        if (action === 'started' || action === 'departed' || action === 'wrap_up' || action === 'ending') {
            visit.reconnecting = false;
            if (needsVisitStatePoll()) pollReconnect();
            else clearReconnectPoll();
        }
        visitStateKnown = true;
        render();
        // Electron 多窗口里 /chat 往往是最新的主 WebSocket，而媒体 iframe
        // 必须由 Pet 窗创建；把已经校验并应用的生命周期状态同步过去。
        if (typeof window.nekoRelayVisitStateToDisplay === 'function') {
            window.nekoRelayVisitStateToDisplay(frame);
        }
    }

    function onEnded(frame) {
        visitStateKnown = true;
        if (!visit.everStarted && !startFailures.has(frame.visit_id)) {
            rememberStartFailure(frame.visit_id, START_FAILURE_CODES[frame.reason] || '', { reason: frame.reason });
        }
        markEnded(frame.visit_id);
        peerAvatarUrl = '';
        if (previewedPeer && previewedPeer.visitId === frame.visit_id) previewedPeer = null;
        closeInviteDialog(frame.visit_id);
        clearReconnectPoll();
        var ended = {
            visitId: frame.visit_id, side: visit.side || frame.side, reason: frame.reason || '',
            peerName: visit.peerName, started: visit.everStarted === true
        };
        // 还挂着的本地气泡：这一场已经结束，等不到回显了。记下 request_id → 气泡：空闲快照抢在回显前面时，
        // 迟到的回显还能认领这个已标「未送达」的气泡、原地改回已发出，不再另出一条
        pendingLocal.slice().forEach(function (entry) {
            lateEchoBubbles.set(entry.requestId, entry);
            while (lateEchoBubbles.size > 50) lateEchoBubbles.delete(lateEchoBubbles.keys().next().value);
            markNotDelivered(entry.requestId, false);
        });
        lines.forEach(function (entry) {
            if (entry.stallTimer) clearTimeout(entry.stallTimer);
            entry.stallTimer = 0;
        });
        releaseVisitTextSession();
        visit = blankVisit();
        typing = null;
        if (ended.started) {
            lastEnded = ended;
            appendEndedMessage(ended);
        }
    }

    // ── 结束后该场系统消息（折叠区：查看详情 / 导出 / 举报）──────────────

    function endedMessageId(visitId) {
        return 'visit-ended-' + visitId;
    }

    function appendEndedMessage(ended) {
        if (!isUiWindow() || !ended.visitId) return;
        var host = getHost();
        if (!host || typeof host.appendMessage !== 'function') return;
        var text = ended.peerName
            ? t('visit.ended.bodyWith', '和 {{name}} 的串门结束了', { name: ended.peerName })
            : t('visit.ended.body', '这场串门结束了');
        host.appendMessage({
            id: endedMessageId(ended.visitId),
            role: 'system',
            author: currentCatgirl() || 'visit',
            time: typeof window.getCurrentTimeString === 'function' ? window.getCurrentTimeString() : '',
            createdAt: now(),
            blocks: [
                { type: 'text', text: text, plain_text: true },
                { type: 'buttons', buttons: [
                    { id: 'more', label: t('visit.ended.more', '更多…'), action: ENDED_ACTION, variant: 'secondary',
                      payload: { visit_id: ended.visitId } }
                ] }
            ],
            status: 'sent'
        });
    }

    function ensureEndedMessage(ended) {
        var exists = messagesSnapshot().some(function (m) { return m && m.id === endedMessageId(ended.visitId); });
        if (!exists) {
            if (ended.side) rememberSide(ended.visitId, ended.side);
            lastEnded = ended;
            appendEndedMessage(ended);
        }
    }

    function expandEndedMessage(message, visitId) {
        var host = getHost();
        if (!host || typeof host.updateMessage !== 'function' || !message) return;
        var blocks = (message.blocks || []).filter(function (block) { return block.type !== 'buttons'; });
        blocks.push({ type: 'buttons', buttons: [
            { id: 'details', label: t('visit.details.open', '查看详情'), action: DETAILS_ACTION, payload: { visit_id: visitId } },
            { id: 'export', label: t('visit.export.button', '导出转录'), action: EXPORT_ACTION, payload: { visit_id: visitId } },
            { id: 'report', label: t('visit.report.button', '举报'), action: REPORT_ACTION, variant: 'danger', payload: { visit_id: visitId } }
        ] });
        host.updateMessage(message.id, { blocks: blocks });
    }

    // ── 详情视图（云端；一次一页，按 next_cursor 翻）─────────────────────

    function detailsUrl(visitId, cursor) {
        var url = '/api/visit/details/' + encodeURIComponent(visitId) + '?catgirl=' + encodeURIComponent(currentCatgirl());
        if (cursor) url += '&cursor=' + encodeURIComponent(cursor);
        return url;
    }

    function detailsError(status, code) {
        if (status === 403) return t('visit.details.forbidden', '你不是这场串门的参与者，看不了');
        if (status === 404) return t('visit.details.notFound', '找不到这场串门的记录');
        if (status === 409 && code === 'VISIT_LOGIN_REQUIRED') return t('visit.status.VISIT_LOGIN_REQUIRED', '请先登录');
        return t('visit.details.unavailable', '暂时连不上串门服务，稍后再试');
    }

    function versionLabel(role) {
        return role === 'host' ? t('visit.details.hostCopy', '邀请方记录') : t('visit.details.guestCopy', '来访方记录');
    }

    function rowNode(row, requesterRole) {
        var box = document.createElement('div');
        box.className = 'visit-details-row is-' + String(row.status || '');
        var mark = document.createElement('span');
        mark.className = 'visit-details-mark';
        var statusKey = { agree: 'agree', only_host: 'onlyHost', only_guest: 'onlyGuest', differ: 'differ' }[row.status] || 'agree';
        mark.textContent = t('visit.details.status.' + statusKey, row.status || '');
        box.appendChild(mark);
        var versions = row.status === 'differ' ? ['host', 'guest'] : [row.host ? 'host' : 'guest'];
        versions.forEach(function (role) {
            var copy = row[role];
            if (!copy) return;
            var item = document.createElement('div');
            item.className = 'visit-details-copy';
            if (row.status === 'differ') {
                var label = document.createElement('span');
                label.className = 'visit-details-copy-label';
                label.textContent = versionLabel(role);
                item.appendChild(label);
            }
            var who = document.createElement('span');
            who.className = 'visit-details-from';
            who.textContent = transcriptSpeakerLabel(row.side, copy.from, requesterRole);
            item.appendChild(who);
            var text = document.createElement('span');
            text.className = 'visit-details-text';
            text.textContent = String(copy.text || '') + (copy.truncated ? ' ' + t('visit.stream.truncated', '（被打断）') : '');
            item.appendChild(text);
            box.appendChild(item);
        });
        return box;
    }

    /** 查看详情：GET /api/visit/details/{visit_id}（无末尾斜杠），带 next_cursor 就出「下一页」。 */
    async function openDetails(visitId) {
        var body = document.createElement('div');
        body.className = 'visit-details';
        var summary = document.createElement('div');
        summary.className = 'visit-details-summary';
        var list = document.createElement('div');
        list.className = 'visit-details-lines';
        body.appendChild(summary);
        body.appendChild(list);
        var dialog = openDialog({
            title: t('visit.details.title', '这场串门的详情'),
            lines: [t('visit.details.loading', '正在读取…')],
            body: body,
            buttons: [{ id: 'close', label: t('visit.common.close', '关闭') }]
        });
        var requesterRole = null;
        var loadPage = async function (cursor) {
            var result = await api('GET', detailsUrl(visitId, cursor));
            if (dialog.closed) return;
            if (result.status !== 200 || !result.body) {
                dialog.setLines([{ text: detailsError(result.status, bodyCode(result)), tone: 'warning' }]);
                return;
            }
            var data = result.body;
            if (data.requester_role === 'host' || data.requester_role === 'guest') requesterRole = data.requester_role;
            if (!cursor) {
                dialog.setLines([]);
                var duration = Math.max(0, Number(data.duration_s) || 0);
                var parts = [
                    t('visit.details.duration', '时长：{{m}} 分 {{s}} 秒', { m: Math.floor(duration / 60), s: duration % 60 }),
                    t('visit.details.freeMinutes', '扣减的免费分钟：{{n}}', { n: Number(data.free_minutes_deducted) || 0 })
                ];
                var usage = data.usage;
                parts.push(usage
                    ? t('visit.details.usage', '用量：输入 {{in}} tokens，输出 {{out}} tokens；语音 {{req}} 次、{{chars}} 字', {
                        in: Number(usage.llm_input_tokens) || 0, out: Number(usage.llm_output_tokens) || 0,
                        req: Number(usage.tts_requests) || 0, chars: Number(usage.tts_chars) || 0
                    })
                    : t('visit.details.usageNone', '用量：暂无记录'));
                if (data.uploaded && (data.uploaded.host === false || data.uploaded.guest === false)) {
                    parts.push(t('visit.details.uploadedPartial', '目前只有一方的记录传上来了'));
                }
                parts.forEach(function (line) {
                    var p = document.createElement('p');
                    p.textContent = line;
                    summary.appendChild(p);
                });
                var heading = document.createElement('p');
                heading.className = 'visit-details-heading';
                heading.textContent = t('visit.details.transcript', '完整转录');
                summary.appendChild(heading);
            }
            (Array.isArray(data.lines) ? data.lines : []).forEach(function (row) {
                list.appendChild(rowNode(row || {}, requesterRole));
            });
            var next = typeof data.next_cursor === 'string' && data.next_cursor ? data.next_cursor : '';
            var buttons = [];
            if (next) {
                buttons.push({
                    id: 'next', label: t('visit.details.nextPage', '下一页'), variant: 'primary',
                    onClick: function () { dialog.setButtons([{ id: 'close', label: t('visit.common.close', '关闭') }]); loadPage(next); }
                });
            }
            buttons.push({ id: 'close', label: t('visit.common.close', '关闭') });
            dialog.setButtons(buttons);
        };
        await loadPage('');
        return dialog;
    }

    // ── 导出转录（按响应 source 分两种形态）──────────────────────────────

    function transcriptSpeakerLabel(side, from, mySide) {
        var kind = String(from || '').indexOf('human') !== -1 ? 'human' : 'cat';
        if (mySide !== 'host' && mySide !== 'guest') {
            // 不知道本侧是哪边：不猜「我 / 对方」，按邀请方 / 来访方标
            if (side === 'host') return kind === 'cat' ? t('visit.export.hostCat', '邀请方的猫娘') : t('visit.export.hostHuman', '邀请方的家里人');
            return kind === 'cat' ? t('visit.export.guestCat', '来访方的猫娘') : t('visit.export.guestHuman', '来访方的家里人');
        }
        var mine = side === mySide;
        if (kind === 'cat') return mine ? t('visit.export.ownCat', '我家猫娘') : t('visit.export.peerCat', '对方的猫娘');
        return mine ? t('visit.export.ownHuman', '我') : t('visit.export.peerHuman', '对方的家里人');
    }

    function formatTs(ts) {
        var value = Number(ts);
        if (!Number.isFinite(value) || value <= 0) return '';
        try {
            return new Date(value * 1000).toLocaleString();
        } catch (_) {
            return '';
        }
    }

    /** 按 source 分支：spool / memory 是本地完整形态，cloud / upload 是精简形态（缺的字段不显示、不补假值）。 */
    function buildTranscriptText(doc) {
        var out = [t('visit.export.fileTitle', '串门转录') + (doc && doc.visit_id ? ' ' + doc.visit_id : '')];
        var lines = Array.isArray(doc && doc.lines) ? doc.lines : [];
        var full = doc && (doc.source === 'spool' || doc.source === 'memory');
        if (full) {
            // 本地完整形态：没有 role，本侧是 speaker_kind 所在一侧的反面无从得知——按记录里本侧亲人 / 猫娘标记判断
            var mySide = doc.side || sideOfVisit(doc.visit_id);
            lines.forEach(function (line) {
                var label = transcriptSpeakerLabel(line.side, line.speaker_kind === 'human' ? 'human' : 'cat', mySide);
                var stamp = formatTs(line.ts);
                out.push((stamp ? '[' + stamp + '] ' : '') + label + '：' + String(line.text || '')
                    + (line.truncated ? ' ' + t('visit.stream.truncated', '（被打断）') : ''));
            });
        } else {
            var role = doc && (doc.role === 'host' || doc.role === 'guest') ? doc.role : null;
            lines.forEach(function (line) {
                var label = transcriptSpeakerLabel(line.side, line.from, role);
                var stamp = formatTs(line.ts);
                out.push((stamp ? '[' + stamp + '] ' : '') + label + '：' + String(line.text || '')
                    + (line.truncated ? ' ' + t('visit.stream.truncated', '（被打断）') : ''));
            });
        }
        return out.join('\n');
    }

    // 本侧按场次记（本地完整形态的转录不带本侧）：页面上见过的每场记一笔，最多留 64 场
    var SIDES_KEY = 'nekoVisitSides';

    function rememberSide(visitId, side) {
        if (!VISIT_ID_RE.test(String(visitId || '')) || (side !== 'host' && side !== 'guest')) return;
        try {
            var map = JSON.parse(localStorage.getItem(SIDES_KEY) || '{}') || {};
            if (map[visitId] === side) return;
            delete map[visitId];
            map[visitId] = side;
            var keys = Object.keys(map);
            while (keys.length > 64) delete map[keys.shift()];
            localStorage.setItem(SIDES_KEY, JSON.stringify(map));
        } catch (_) { /* noop */ }
    }

    function sideOfVisit(visitId) {
        if (visit.visitId === visitId && visit.side) return visit.side;
        if (lastEnded && lastEnded.visitId === visitId && lastEnded.side) return lastEnded.side;
        try {
            var map = JSON.parse(localStorage.getItem(SIDES_KEY) || '{}') || {};
            var side = map[visitId];
            return side === 'host' || side === 'guest' ? side : null;
        } catch (_) {
            return null;
        }
    }

    async function exportTranscript(visitId) {
        var url = '/api/visit/transcript?catgirl=' + encodeURIComponent(currentCatgirl()) + '&visit_id=' + encodeURIComponent(visitId);
        var result = await api('GET', url);
        if (result.status === 404) {
            toast(t('visit.export.gone', '本机副本已清理，登录并联网后可从云端导出'), 6000);
            return null;
        }
        if (result.status === 502) {
            toast(t('visit.export.incomplete', '云端记录不完整，暂时没法导出'), 6000);
            return null;
        }
        if (result.status !== 200 || !result.body) {
            toast(t('visit.export.failed', '导出失败，请稍后再试'));
            return null;
        }
        var text = buildTranscriptText(result.body);
        try {
            var blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
            var href = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = href;
            a.download = 'visit-' + visitId + '.txt';
            (document.body || document.documentElement).appendChild(a);
            a.click();
            a.parentNode.removeChild(a);
            setTimeout(function () { URL.revokeObjectURL(href); }, 1000);
        } catch (_) {
            toast(t('visit.export.failed', '导出失败，请稍后再试'));
        }
        return text;
    }

    // ── 举报（本机 POST /api/visit/report，后端代转）─────────────────────

    async function openReport(visitId, sourceMessage) {
        var body = document.createElement('div');
        body.className = 'visit-report';
        var select = document.createElement('select');
        select.className = 'visit-report-reason';
        REPORT_REASONS.forEach(function (reason) {
            var option = document.createElement('option');
            option.value = reason;
            option.textContent = t('visit.report.reason.' + reason, reason);
            select.appendChild(option);
        });
        var note = document.createElement('textarea');
        note.className = 'visit-report-note';
        note.maxLength = REPORT_NOTE_MAX;
        note.placeholder = t('visit.report.notePlaceholder', '补充说明（可选，最多 500 字）');
        var includeLabel = document.createElement('label');
        includeLabel.className = 'visit-report-include';
        var include = document.createElement('input');
        include.type = 'checkbox';
        include.checked = true;
        var includeText = document.createElement('span');
        includeText.textContent = t('visit.report.includeTranscript', '附上这场的对话记录');
        includeLabel.appendChild(include);
        includeLabel.appendChild(includeText);
        body.appendChild(select);
        body.appendChild(note);
        body.appendChild(includeLabel);
        var submit;
        var dialog = openDialog({
            title: t('visit.report.title', '举报这场串门'),
            lines: [],
            body: body,
            buttons: [
                { id: 'cancel', label: t('visit.common.cancel', '取消') },
                { id: 'submit', label: t('visit.report.submit', '提交举报'), variant: 'danger', onClick: function (handle) { submit(handle); } }
            ]
        });
        var submitButtons = function (busy) {
            return [
                { id: 'cancel', label: t('visit.common.cancel', '取消'), disabled: busy },
                { id: 'submit', label: t('visit.report.submit', '提交举报'), variant: 'danger', disabled: busy,
                  onClick: function (h) { submit(h); } }
            ];
        };
        submit = async function (handle) {
            handle.setButtons(submitButtons(true));
            var result = await api('POST', '/api/visit/report', {
                visit_id: visitId,
                reason: select.value,
                note: String(note.value || '').slice(0, REPORT_NOTE_MAX),
                include_transcript: !!include.checked
            });
            if (handle.closed) return;
            var code = bodyCode(result);
            var queuedLogin = result.status === 409 && code === 'VISIT_LOGIN_REQUIRED' && result.body && result.body.queued === true;
            var terminal = true;
            if (result.status === 200) {
                toast(t('visit.report.done', '举报已提交'));
                markReportButton(sourceMessage, t('visit.report.doneButton', '已举报'));
            } else if (result.status === 202 && result.body && result.body.queued === true) {
                toast(t('visit.report.queued', '举报已记录，网络恢复后自动提交'), 6000);
                markReportButton(sourceMessage, t('visit.report.queuedButton', '已排队'));
            } else if (result.status === 409 && code === 'already_queued') {
                toast(t('visit.report.already', '这场已经举报过了'));
                markReportButton(sourceMessage, t('visit.report.queuedButton', '已排队'));
            } else if (queuedLogin) {
                // 已先落盘排队，只是要原账号重新登录才能送出
                toast(t('visit.report.queuedLogin', '举报已记录，重新登录原来的账号后会自动提交'), 6000);
                markReportButton(sourceMessage, t('visit.report.queuedButton', '已排队'));
            } else if (result.status === 404) {
                toast(t('visit.details.notFound', '找不到这场串门的记录'));
            } else {
                terminal = false;
            }
            if (terminal) {
                handle.close('done');
                return;
            }
            // 没保存下来（网络 / 403 / 5xx / 没登录）：框留着，选的原因和补充说明都还在，可以直接再提交
            handle.setLines([{
                text: result.status === 409 && code === 'VISIT_LOGIN_REQUIRED'
                    ? t('visit.status.VISIT_LOGIN_REQUIRED', '请先登录')
                    : t('visit.report.persistFailed', '举报没能保存，请重试'),
                tone: 'warning'
            }]);
            handle.setButtons(submitButtons(false));
        };
        return dialog;
    }

    function markReportButton(message, label) {
        var host = getHost();
        if (!message || !host || typeof host.updateMessage !== 'function') return;
        var blocks = (message.blocks || []).map(function (block) {
            if (block.type !== 'buttons') return block;
            return Object.assign({}, block, {
                buttons: block.buttons.map(function (button) {
                    if (button.action !== REPORT_ACTION) return button;
                    return Object.assign({}, button, { label: label, disabled: true });
                })
            });
        });
        host.updateMessage(message.id, { blocks: blocks });
    }

    // ── 发起：邀请客人（host）/ 去别人家（guest）──────────────────────────

    async function withPersonaRetry(request, catgirl) {
        var result = await request();
        if (result.status === 409 && bodyCode(result) === 'VISIT_PERSONA_UNREVIEWED') {
            if (typeof window.openVisitPersonaPreview !== 'function') {
                toast(t('visit.persona.unreviewed', '出门前先看一眼她的串门自我介绍'));
                return result;
            }
            // 确认串门人设之前不重发；确认后自动重试一次
            // 面板开的是发起请求的那位（别的窗口中途换了角色也不串）；确认后角色已换就不再重试
            var confirmed = await window.openVisitPersonaPreview(catgirl || currentCatgirl(), { requireConfirm: true });
            if (confirmed !== true) return result;
            if (catgirl && currentCatgirl() !== catgirl) return result;
            result = await request();
        }
        return result;
    }

    function startErrorText(result) {
        var code = bodyCode(result);
        var body = result.body || {};
        if (result.status === 0) return t('visit.error.unreachable', '连不上本机服务');
        if (result.status === 403 && code === 'csrf_validation_failed') {
            return t('visit.error.localAuth', '本机串门凭证已失效，请重试');
        }
        if (code === 'VISIT_DISABLED') return t('visit.error.disabled', '「允许串门」还没有打开，请先在设置里打开');
        if (code === 'VISIT_PERSONA_UNREVIEWED') return t('visit.persona.unreviewed', '出门前先看一眼她的串门自我介绍');
        if (code === 'VISIT_QUOTA_EXCEEDED') return t('visit.status.VISIT_QUOTA_EXCEEDED', '今天的串门时间用完了');
        if (code === 'VISIT_UNSUPPORTED_ON_THIS_MACHINE'
                && ((body.details && body.details.reason) || body.reason) === 'insecure_context') {
            return t('visit.error.insecure_context', '自定义后端地址需要 https 或 localhost 才能串门');
        }
        if (code === 'VISIT_INVITE_INVALID') {
            var reason = (body.details && body.details.reason) || body.reason || '';
            if (reason === 'invite_expired') return t('visit.preview.expired', '邀请码已过期');
            if (reason === 'peer_blocked') return t('visit.join.blocked', '你已屏蔽此人');
            return t('visit.preview.invalid', '邀请码无效');
        }
        if (code.indexOf('VISIT_') === 0) {
            var text = t('visit.status.' + code, '');
            if (text) return text;
        }
        if (code === 'voice_session_active') return t('visit.error.voiceSession', '先结束语音通话再串门');
        if (code === 'already_visiting') return t('visit.error.alreadyVisiting', '她已经在串门了');
        return t('visit.error.generic', '没能完成，请稍后再试');
    }

    function rememberStartFailure(visitId, code, details) {
        if (!VISIT_ID_RE.test(visitId || '')) return;
        startFailures.set(visitId, { code: code, details: { reason: details && details.reason } });
        while (startFailures.size > 20) startFailures.delete(startFailures.keys().next().value);
    }

    function startFailureResult(visitId) {
        var failure = startFailures.get(visitId);
        if (!failure) return null;
        return {
            ok: false, visit_id: visitId, code: failure.code,
            error: failure.code ? startErrorText({ status: 409, body: failure })
                : t('visit.error.generic', '没能完成，请稍后再试')
        };
    }

    // 构图偏好由设置分组（visit-settings.js，只在 index.html）写进 localStorage；chat.html 直接读同一个键
    function readCrop() {
        if (window.nekoVisitSettings && typeof window.nekoVisitSettings.getCrop === 'function') {
            return window.nekoVisitSettings.getCrop();
        }
        try {
            var value = localStorage.getItem('nekoVisitCrop');
            if (value === 'upper' || value === 'full') return value;
        } catch (_) { /* noop */ }
        return 'full';
    }

    // 建房 / 入房请求在途时换了角色：后端照样收下了那位，这一页却已不管她——立刻把那一场硬结束，免得空占角色与邀请码
    async function abandonIfCharacterSwitched(result, catgirl) {
        var visitId = result && result.status === 202 && result.body ? String(result.body.visit_id || '') : '';
        if (!visitId || currentCatgirl() === catgirl) return false;
        await api('POST', '/api/visit/route/end', { lanlan_name: catgirl, visit_id: visitId, reason: 'route_end' });
        return true;
    }

    function currentHostRoomUrl(catgirl) {
        return '/api/visit/rooms/current?catgirl=' + encodeURIComponent(catgirl);
    }

    /** 把恢复接口 / 建房响应变成本页的权威阶段；返回恢复到的场次，格式不对则 null。 */
    function applyHostRoomResponse(result, catgirl, seqAtSend) {
        if (!result || (result.status !== 200 && result.status !== 202) || currentCatgirl() !== catgirl) return null;
        // HTTP 恢复/建房响应没有服务端帧序号；请求期间只要收到过更新帧，页面状态就比它新，绝不能倒退。
        if (typeof seqAtSend === 'number' && stateFrameSeq !== seqAtSend) return null;
        var body = result.body || {};
        var visitId = typeof body.visit_id === 'string' ? body.visit_id : '';
        var phase = body.phase === 'invite_ready' ? 'invite_ready' : (body.phase === 'pending' ? 'pending' : '');
        if (!VISIT_ID_RE.test(visitId) || !phase) return null;
        var frame = { type: 'visit_state_change', action: phase, side: 'host', visit_id: visitId };
        if (phase === 'invite_ready' && typeof body.invite_code === 'string') frame.invite_code = body.invite_code;
        if (typeof body.invite_expires_at === 'number') frame.invite_expires_at = body.invite_expires_at;
        if (typeof body.target_user_id === 'string') frame.target_user_id = body.target_user_id;
        onStateChange(frame);
        return {
            visitId: visitId, phase: phase,
            hasCode: INVITE_CODE_RE.test(String(body.invite_code || '')),
            targetUserId: String(body.target_user_id || '')
        };
    }

    /** 建房是异步的；即使 invite_ready 的 WS 帧丢了，也从受保护的本机接口把原邀请码补回来。 */
    function scheduleInviteRecovery(catgirl, visitId, attempt) {
        var delays = [300, 700, 1200, 2000, 3000, 3000];
        var index = Number.isInteger(attempt) ? attempt : 0;
        if (index >= delays.length) return;
        setTimeout(async function () {
            if (currentCatgirl() !== catgirl || visit.visitId !== visitId
                    || (visit.phase !== 'pending' && visit.phase !== 'invite_ready') || visit.inviteCode) return;
            var seqAtSend = stateFrameSeq;
            var current = await api('GET', currentHostRoomUrl(catgirl));
            if (currentCatgirl() !== catgirl || visit.visitId !== visitId) return;
            if (stateFrameSeq !== seqAtSend) {
                if (visit.phase === 'pending' && !visit.inviteCode) {
                    scheduleInviteRecovery(catgirl, visitId, index + 1);
                }
                return;
            }
            if (current.status === 404) {
                // /rooms/current only covers pending host invitations.  A 404
                // may mean the visit ended or advanced, so reconcile against
                // the authoritative state instead of leaving stale controls.
                var stateResult = await api('GET', '/api/visit/state?catgirl=' + encodeURIComponent(catgirl));
                if (currentCatgirl() !== catgirl || visit.visitId !== visitId || stateFrameSeq !== seqAtSend) return;
                if (stateResult.status === 200 && stateResult.body && typeof stateResult.body === 'object') {
                    applyState(stateResult.body, false);
                    if (visit.visitId !== visitId || visit.phase !== 'pending' || visit.inviteCode) return;
                }
            }
            var restored = applyHostRoomResponse(current, catgirl, seqAtSend);
            if (restored && restored.hasCode) return;
            if (visit.visitId === visitId && visit.phase === 'pending') {
                scheduleInviteRecovery(catgirl, visitId, index + 1);
            }
        }, delays[index]);
    }

    function recoverCurrentHostInviteIfWaiting(catgirl) {
        if (currentCatgirl() !== catgirl || visit.side !== 'host' || !VISIT_ID_RE.test(String(visit.visitId || ''))) return;
        if ((visit.phase === 'pending' || visit.phase === 'invite_ready') && !visit.inviteCode && !visit.targetUserId) {
            scheduleInviteRecovery(catgirl, visit.visitId, 0);
        }
    }

    async function startInvite(targetUserId) {
        var catgirl = currentCatgirl();
        if (!catgirl) return { ok: false, error: t('visit.error.noCharacter', '当前没有可串门的角色') };
        targetUserId = String(targetUserId || '');
        if (targetUserId && !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(targetUserId)) {
            return { ok: false, error: '好友账号格式无效，请刷新好友列表后重试' };
        }
        if (starting) return { ok: false, error: t('visit.error.starting', '正在准备串门房间，请稍候') };
        starting = true;
        render();
        try {
            // 页面错过 invite_ready 或重新载入时，先恢复仍有效的原房间；绝不为同一场再建一个。
            var currentSeq = stateFrameSeq;
            var current = await api('GET', currentHostRoomUrl(catgirl));
            if (currentCatgirl() !== catgirl) {
                return { ok: false, error: t('visit.error.characterSwitched', '角色已切换，请重新邀请') };
            }
            var currentFailure = startFailureResult(current.body && current.body.visit_id);
            if (currentFailure) return currentFailure;
            if (stateFrameSeq !== currentSeq) {
                if (String(visit.targetUserId || '') !== targetUserId) {
                    return { ok: false, error: '当前已有另一条串门邀请，请先结束后再继续' };
                }
                recoverCurrentHostInviteIfWaiting(catgirl);
                return { ok: true, visit_id: visit.visitId };
            }
            var restored = applyHostRoomResponse(current, catgirl, currentSeq);
            if (restored) {
                if (restored.targetUserId !== targetUserId) {
                    return { ok: false, error: '当前已有另一条串门邀请，请先结束后再邀请这位好友' };
                }
                if (!targetUserId && !restored.hasCode) scheduleInviteRecovery(catgirl, restored.visitId, 0);
                return { ok: true, visit_id: restored.visitId };
            }
            // 只有明确不存在待客邀请码时才新建。读取失败不能被当成“不存在”，否则可能重复建房。
            if (current.status !== 404) {
                var currentError = startErrorText(current);
                toast(currentError, 6000);
                return { ok: false, error: currentError };
            }
            var crop = readCrop();
            var createSeq = stateFrameSeq;
            var result = await withPersonaRetry(function () {
                var room = { catgirl: catgirl, crop: crop };
                if (targetUserId) room.target_user_id = targetUserId;
                return api('POST', '/api/visit/rooms', room);
            }, catgirl);
            if (await abandonIfCharacterSwitched(result, catgirl)) {
                return { ok: false, error: t('visit.error.characterSwitched', '角色已切换，请重新邀请') };
            }
            var createFailure = startFailureResult(result.body && result.body.visit_id);
            if (createFailure) return createFailure;
            if (stateFrameSeq !== createSeq) {
                if (String(visit.targetUserId || '') !== targetUserId) {
                    return { ok: false, error: '当前已有另一条串门邀请，请先结束后再继续' };
                }
                recoverCurrentHostInviteIfWaiting(catgirl);
                return { ok: true, visit_id: visit.visitId };
            }
            var created = applyHostRoomResponse(result, catgirl, createSeq);
            if (created) {
                if (!targetUserId && !created.hasCode) scheduleInviteRecovery(catgirl, created.visitId, 0);
                return { ok: true, visit_id: created.visitId };
            }
            // GET 与 POST 之间另一窗口可能刚建好房间：再恢复一次，而不是停在 already_visiting。
            if (result.status === 409 && ((result.body || {}).reason === 'already_visiting')) {
                var raceSeq = stateFrameSeq;
                var raced = await api('GET', currentHostRoomUrl(catgirl));
                var racedFailure = startFailureResult(raced.body && raced.body.visit_id);
                if (racedFailure) return racedFailure;
                if (stateFrameSeq !== raceSeq) {
                    if (String(visit.targetUserId || '') !== targetUserId) {
                        return { ok: false, error: '当前已有另一条串门邀请，请先结束后再继续' };
                    }
                    recoverCurrentHostInviteIfWaiting(catgirl);
                    return { ok: true, visit_id: visit.visitId };
                }
                var recovered = applyHostRoomResponse(raced, catgirl, raceSeq);
                if (recovered) {
                    if (recovered.targetUserId !== targetUserId) {
                        return { ok: false, error: '当前已有另一条串门邀请，请先结束后再邀请这位好友' };
                    }
                    if (!targetUserId && !recovered.hasCode) scheduleInviteRecovery(catgirl, recovered.visitId, 0);
                    return { ok: true, visit_id: recovered.visitId };
                }
            }
            var resultError = startErrorText(result);
            toast(resultError, 6000);
            return { ok: false, error: resultError };
        } finally {
            starting = false;
            render();
        }
    }

    /** 独立串门浮窗使用：创建或恢复本角色的邀请码，并等待异步建房完成。 */
    async function createInviteCode() {
        if (visit.inviteCode && (visit.phase === 'pending' || visit.phase === 'invite_ready')) {
            return {
                invite_code: visit.inviteCode, expires_at: visit.inviteExpiresAt || null,
                visit_id: visit.visitId || null, phase: visit.phase
            };
        }
        var started = await startInvite();
        if (!started || started.ok !== true) {
            throw new Error((started && started.error) || '没能创建串门房间，请稍后再试');
        }
        var expectedVisitId = started.visit_id || visit.visitId;
        return new Promise(function (resolve, reject) {
            var deadline = now() + 15000;
            var timer = setInterval(function () {
                var failure = startFailureResult(expectedVisitId);
                if (failure) {
                    clearInterval(timer);
                    reject(new Error(failure.error));
                    return;
                }
                if (visit.visitId === expectedVisitId && visit.inviteCode
                        && (visit.phase === 'pending' || visit.phase === 'invite_ready')) {
                    clearInterval(timer);
                    resolve({
                        invite_code: visit.inviteCode, expires_at: visit.inviteExpiresAt || null,
                        visit_id: visit.visitId || null, phase: visit.phase
                    });
                    return;
                }
                if (now() >= deadline) {
                    clearInterval(timer);
                    reject(new Error('没有拿到串门邀请码，请稍后再试'));
                }
            }, 120);
        });
    }

    /** 好友面板使用：房间直接绑定接收账号，不生成、传递或兑换邀请码。 */
    async function createDirectInvitation(targetUserId) {
        targetUserId = String(targetUserId || '');
        if (visit.targetUserId === targetUserId && visit.visitId && visit.phase === 'invite_ready') {
            return {
                target_user_id: targetUserId, expires_at: visit.inviteExpiresAt || null,
                visit_id: visit.visitId, phase: visit.phase
            };
        }
        var started = await startInvite(targetUserId);
        if (!started || started.ok !== true) {
            throw new Error((started && started.error) || '没能创建定向串门房间，请稍后再试');
        }
        var expectedVisitId = started.visit_id || visit.visitId;
        try {
            return await new Promise(function (resolve, reject) {
                var deadline = now() + 15000;
                var timer = setInterval(function () {
                    var failure = startFailureResult(expectedVisitId);
                    if (failure) {
                        clearInterval(timer);
                        reject(new Error(failure.error));
                        return;
                    }
                    if (visit.targetUserId === targetUserId && visit.visitId === expectedVisitId && visit.phase === 'invite_ready') {
                        clearInterval(timer);
                        resolve({
                            target_user_id: targetUserId, expires_at: visit.inviteExpiresAt || null,
                            visit_id: visit.visitId, phase: visit.phase
                        });
                        return;
                    }
                    if (now() >= deadline) {
                        clearInterval(timer);
                        reject(new Error('定向串门房间没有准备好，请稍后再试'));
                    }
                }, 120);
            });
        } catch (error) {
            var cleanupNeeded = !startFailures.has(expectedVisitId) && visit.visitId === expectedVisitId
                && visit.targetUserId === targetUserId && WAITING_PHASES.indexOf(visit.phase) !== -1;
            // The local room may have been created even if its invite_ready frame
            // missed the 15 s UI deadline. Retire that room before unlocking the hub.
            if (cleanupNeeded) {
                await endRoute('route_end');
            }
            throw error;
        }
    }

    function normalizeInviteCode(raw) {
        return String(raw || '').toUpperCase().replace(/[^A-Z2-7]/g, '').slice(0, 10);
    }

    function previewErrorText(result) {
        var code = bodyCode(result);
        if (result.status === 404 || code === 'invite_invalid') return t('visit.preview.invalid', '邀请码无效');
        if (result.status === 410 || code === 'invite_expired') return t('visit.preview.expired', '邀请码已过期');
        if (result.status === 403 && code === 'VISIT_BANNED') return t('visit.preview.banned', '你的串门功能暂时受限');
        if (code === 'VISIT_QUOTA_EXCEEDED') return t('visit.status.VISIT_QUOTA_EXCEEDED', '今天的串门时间用完了');
        if (code === 'VISIT_CONCURRENCY_LIMIT') return t('visit.status.VISIT_CONCURRENCY_LIMIT', '同时进行的串门或邀请太多了，请先结束一个再试');
        if (result.status === 429) return t('visit.preview.rateLimited', '试得太频繁了，过一会儿再试');
        if (result.status === 503) return t('visit.preview.unreachable', '暂时连不上串门服务，稍后再试');
        if (code === 'VISIT_LOGIN_REQUIRED') return t('visit.status.VISIT_LOGIN_REQUIRED', '请先登录');
        if (code === 'invite_code_format') return t('visit.join.codeFormat', '邀请码是 10 位，由字母和数字 2–7 组成');
        return t('visit.error.generic', '没能完成，请稍后再试');
    }

    /** 先预览（只读、不消耗邀请码），预览失败不弹确认框、不发 join。 */
    async function previewThenConfirm(inviteCode, catgirl, directVisitId) {
        var previewUrl = directVisitId
            ? '/api/visit/direct/' + encodeURIComponent(directVisitId) + '/preview'
            : '/api/visit/invites/' + encodeURIComponent(inviteCode) + '/preview';
        var preview = await api('GET', previewUrl);
        // 预览期间别的窗口换了角色：确认框与 join 都钉在发起时那位，已不是当前角色就作罢
        if (currentCatgirl() !== catgirl) return null;
        if (preview.status !== 200 || !preview.body || !VISIT_ID_RE.test(String(preview.body.visit_id || ''))) {
            toast(previewErrorText(preview), 6000);
            return null;
        }
        var data = preview.body;
        var hostName = String(data.host_display_name || '');
        var shortCode = String(data.host_short_code || '');
        var linesOut = [t('visit.join.confirmBody', '让 {{cat}} 去 {{host}}（{{code}}）家串门？', {
            cat: catgirl, host: hostName, code: shortCode
        })];
        if (data.cross_region === true) linesOut.push({ text: t('visit.join.crossRegion', '对方在另一个地区，连接可能不太稳定'), tone: 'warning' });
        var buttons = [{ id: 'cancel', label: t('visit.common.cancel', '取消') }];
        if (data.locally_blocked === true) {
            linesOut.push({ text: t('visit.join.blocked', '你已屏蔽此人'), tone: 'warning' });
        } else {
            buttons.push({ id: 'go', label: t('visit.join.confirm', '出门'), variant: 'primary' });
        }
        var answer = await openDialog({ title: t('visit.join.confirmTitle', '让她出门吗？'), lines: linesOut, buttons: buttons }).result;
        if (answer !== 'go' || data.locally_blocked === true) return null;
        if (currentCatgirl() !== catgirl) return null;
        return data;
    }

    async function joinWithCode(inviteCode) {
        var catgirl = currentCatgirl();
        if (!catgirl) return null;
        var data = await previewThenConfirm(inviteCode, catgirl);
        if (!data) return null;
        var visitId = String(data.visit_id);
        previewedPeer = {
            visitId: visitId, name: String(data.host_display_name || ''),
            shortCode: String(data.host_short_code || ''), crossRegion: data.cross_region === true
        };
        var result = await withPersonaRetry(function () {
            return api('POST', '/api/visit/rooms/' + encodeURIComponent(visitId) + '/join', {
                catgirl: catgirl, invite_code: inviteCode, confirm: true, crop: readCrop()
            });
        }, catgirl);
        if (await abandonIfCharacterSwitched(result, catgirl)) return null;
        if (result.status !== 202) toast(startErrorText(result), 6000);
        return result;
    }

    async function joinDirect(visitId, alreadyConfirmed) {
        visitId = String(visitId || '');
        if (!VISIT_ID_RE.test(visitId)) throw new Error('定向串门邀请格式无效');
        var catgirl = currentCatgirl();
        if (!catgirl) return null;
        var data;
        if (alreadyConfirmed === true) {
            var preview = await api('GET', '/api/visit/direct/' + encodeURIComponent(visitId) + '/preview');
            if (currentCatgirl() !== catgirl) return null;
            if (preview.status !== 200 || !preview.body || String(preview.body.visit_id || '') !== visitId) {
                toast(previewErrorText(preview), 6000);
                return preview;
            }
            if (preview.body.locally_blocked === true) {
                toast(t('visit.join.blocked', '你已屏蔽此人'), 6000);
                return { status: 409, body: { code: 'peer_blocked' } };
            }
            data = preview.body;
        } else {
            data = await previewThenConfirm('', catgirl, visitId);
        }
        if (!data) return null;
        if (String(data.visit_id || '') !== visitId) throw new Error('定向串门邀请不匹配');
        previewedPeer = {
            visitId: visitId, name: String(data.host_display_name || ''),
            shortCode: String(data.host_short_code || ''), crossRegion: data.cross_region === true
        };
        var result = await withPersonaRetry(function () {
            return api('POST', '/api/visit/rooms/' + encodeURIComponent(visitId) + '/join-direct', {
                catgirl: catgirl, confirm: true, crop: readCrop()
            });
        }, catgirl);
        if (await abandonIfCharacterSwitched(result, catgirl)) return null;
        if (result.status !== 202) toast(startErrorText(result), 6000);
        return result;
    }

    function directInviteMessageId(notificationId) {
        return 'visit-direct-invite-' + String(notificationId || '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 80);
    }

    async function markDirectInviteRead(notificationId) {
        if (!notificationId) return;
        try {
            await api('POST', '/api/community/visit-invitations/' + encodeURIComponent(notificationId) + '/read', {});
        } catch (_) { /* 已读同步失败不应阻断用户接受或拒绝 */ }
    }

    function notifyDirectInviteResolved(payload, accepted) {
        try {
            window.dispatchEvent(new CustomEvent('neko-social-incoming-resolved', {
                detail: {
                    id: String(payload.notification_id || ''),
                    actorId: String(payload.actor_id || ''),
                    visitId: String(payload.visit_id || ''),
                    expiresAt: Number(payload.expires_at) || 0,
                    accepted: accepted === true
                }
            }));
        } catch (_) { /* noop */ }
    }

    async function reportDirectInviteDecision(payload, accepted) {
        var notificationId = String(payload.notification_id || '');
        if (!notificationId) return { status: 400, body: {} };
        return api('POST', '/api/community/visit-invitations/'
            + encodeURIComponent(notificationId) + '/respond', { accepted: accepted === true });
    }

    function clearPendingDirectInvitation(payload, accepted) {
        if (!pendingDirectInvitation || pendingDirectInvitation.payload !== payload) return;
        var notificationId = String(payload.notification_id || '');
        if (notificationId) {
            resolvedDirectInvitationIds.add(notificationId);
            while (resolvedDirectInvitationIds.size > 128) {
                resolvedDirectInvitationIds.delete(resolvedDirectInvitationIds.values().next().value);
            }
        }
        pendingDirectInvitation = null;
        notifyDirectInviteResolved(payload, accepted);
        render();
        refreshCompactGeometry();
    }

    function directInvitationExpired(payload) {
        var expiresAt = Number(payload && payload.expires_at) || 0;
        return expiresAt > 0 && expiresAt <= now() / 1000;
    }

    async function decideDirectInvitation(decision) {
        var pending = pendingDirectInvitation;
        if (!pending || pending.busy === true) return;
        var payload = pending.payload || {};
        var visitId = String(payload.visit_id || '');
        if (!VISIT_ID_RE.test(visitId) || (decision !== 'accept' && decision !== 'decline')) return;
        if (payload.catgirl && payload.catgirl !== currentCatgirl()) {
            pendingDirectInvitation = null;
            render();
            refreshCompactGeometry();
            await presentDirectInvitation({
                id: payload.notification_id,
                actor_id: payload.actor_id,
                visit_id: payload.visit_id,
                expires_at: payload.expires_at
            });
            return;
        }
        if (directInvitationExpired(payload)) {
            await markDirectInviteRead(String(payload.notification_id || ''));
            clearPendingDirectInvitation(payload, false);
            toast(t('visit.directInvite.expired', '这次邀请已经失效了'), 5000);
            return;
        }
        pending.busy = true;
        render();
        if (decision === 'decline') {
            try {
                var declined = await reportDirectInviteDecision(payload, false);
                if (declined.status === 404 || declined.status === 410) {
                    await markDirectInviteRead(String(payload.notification_id || ''));
                    clearPendingDirectInvitation(payload, false);
                    toast(t('visit.directInvite.expired', '这次邀请已经失效了'), 5000);
                    return;
                }
                if (declined.status < 200 || declined.status >= 300) throw new Error('response_failed');
            } catch (_) {
                pending.busy = false;
                render();
                toast(t('visit.directInvite.responseFailed', '拒绝结果没有发出去，请再试一次'), 5000);
                return;
            }
            clearPendingDirectInvitation(payload, false);
            return;
        }
        var result;
        try {
            result = await joinDirect(visitId, true);
        } catch (_) {
            pending.busy = false;
            render();
            toast(t('visit.directInvite.unavailable', '现在还出不了门，可以再试一次'), 5000);
            return;
        }
        if (result && result.status === 202) {
            clearPendingDirectInvitation(payload, true);
            // 房间连接本身已经让邀请方知道“接受”；社区结果只负责把通知收口，失败不反悔这次串门。
            reportDirectInviteDecision(payload, true).then(function (response) {
                if (!response || response.status < 200 || response.status >= 300) {
                    markDirectInviteRead(String(payload.notification_id || ''));
                }
            }).catch(function () {
                markDirectInviteRead(String(payload.notification_id || ''));
            });
            return;
        }
        pending.busy = false;
        render();
        if (result && (result.status === 404 || result.status === 410)) {
            await markDirectInviteRead(String(payload.notification_id || ''));
            clearPendingDirectInvitation(payload, false);
            toast(t('visit.directInvite.expired', '这次邀请已经失效了'), 5000);
            return;
        }
        toast(t('visit.directInvite.unavailable', '现在还出不了门，可以再试一次'), 5000);
    }

    async function onDirectInviteAction(_message, action) {
        // Compatibility for a persisted bubble produced by an earlier build:
        // transfer its payload to the floating bar and handle the same decision.
        var payload = action && action.payload && typeof action.payload === 'object' ? action.payload : {};
        pendingDirectInvitation = { messageId: '', payload: payload, busy: false };
        await decideDirectInvitation(String(payload.decision || ''));
    }

    /** 把喵宇宙定向邀请变成当前猫娘的一条聊天提问，而不是悬浮窗卡片。 */
    async function presentDirectInvitation(incoming) {
        incoming = incoming && typeof incoming === 'object' ? incoming : {};
        var visitId = String(incoming.visit_id || incoming.visitId || '');
        var notificationId = String(incoming.notification_id || incoming.id || '');
        if (!isUiWindow() || !releaseEnabled || !userSwitchOn()
                || !VISIT_ID_RE.test(visitId) || !notificationId) return false;
        if (resolvedDirectInvitationIds.has(notificationId)) {
            await markDirectInviteRead(notificationId);
            return true;
        }
        if (inVisit()) {
            if (visit.visitId === visitId) {
                await markDirectInviteRead(notificationId);
                return true;
            }
            // 正在另一场串门（含邀请方等待回应）时保留未读通知。等当前场结束后，
            // 轮询会再次展示；此时不能打断语音，因为串门栏也没有接受/拒绝按钮。
            return false;
        }
        var messageId = directInviteMessageId(notificationId);
        if (directInvitationPresenting.has(messageId)) return true;
        var host = getHost();
        var catgirl = currentCatgirl();
        if (!host || !catgirl) return false;
        if (pendingDirectInvitation && pendingDirectInvitation.payload
                && String(pendingDirectInvitation.payload.visit_id || '') === visitId) {
            if (pendingDirectInvitation.payload.catgirl !== catgirl) {
                pendingDirectInvitation = null;
                render();
                refreshCompactGeometry();
            } else {
                render();
                if (!pendingDirectInvitation.questionDelivered) {
                    var retried = await api('POST', '/api/visit/direct/' + encodeURIComponent(visitId) + '/deliver-question', {
                        catgirl: catgirl
                    });
                    pendingDirectInvitation.questionDelivered = retried.status === 200
                        && retried.body && retried.body.delivered === true;
                }
                return true;
            }
        }
        directInvitationPresenting.add(messageId);
        try {
            // 先由 Connect 用当前登录账号核验定向房间；聊天里不展示伪造或已失效的通知。
            var preview = await api('GET', '/api/visit/direct/' + encodeURIComponent(visitId) + '/preview');
            if (preview.status !== 200 || !preview.body || String(preview.body.visit_id || '') !== visitId) {
                if (preview.status === 410) await markDirectInviteRead(notificationId);
                return false;
            }
            if (currentCatgirl() !== catgirl) return false;
            var payload = {
                notification_id: notificationId,
                actor_id: String(incoming.actor_id || incoming.actorId || ''),
                visit_id: visitId,
                expires_at: Number(incoming.expires_at || incoming.expiresAt) || Number(preview.body.expires_at) || 0,
                catgirl: catgirl
            };
            // 先把最终问句生成好，再交给标准助手回复链路；该链路统一负责
            // 气泡、语音、口型、自动展开与 turn 生命周期。
            var generated = await api('GET', '/api/visit/direct/' + encodeURIComponent(visitId)
                + '/question?catgirl=' + encodeURIComponent(catgirl));
            if (generated.status !== 200 || !generated.body || !String(generated.body.question || '').trim()) {
                return false;
            }
            if (currentCatgirl() !== catgirl) return false;
            pendingDirectInvitation = {
                messageId: messageId, payload: payload, busy: false, questionDelivered: false
            };
            render();
            refreshCompactGeometry();
            var delivered = await api('POST', '/api/visit/direct/' + encodeURIComponent(visitId) + '/deliver-question', {
                catgirl: catgirl
            });
            pendingDirectInvitation.questionDelivered = delivered.status === 200
                && delivered.body && delivered.body.delivered === true;
            return true;
        } finally {
            directInvitationPresenting.delete(messageId);
        }
    }

    // 多窗口模式下，喵宇宙按钮位于 Pet 窗口，真正的聊天消息却只能由
    // /chat 跟随窗写入。接收方聊天窗自己读取未读邀请；SSE 只负责提速，
    // 轮询负责覆盖本地 Verse 断流、窗口晚启动和通知重放丢失。
    var directInvitationPollInFlight = false;
    async function pollDirectInvitations() {
        if (!isUiWindow() || !releaseEnabled || !userSwitchOn() || directInvitationPollInFlight) return false;
        directInvitationPollInFlight = true;
        try {
            var result = await api('GET', '/api/community/visit-hub');
            var incoming = result.status === 200 && result.body && result.body.incoming_invite;
            var notificationIds = result.status === 200 && result.body
                && Array.isArray(result.body.incoming_invite_ids)
                ? result.body.incoming_invite_ids.map(String) : [];
            if (pendingDirectInvitation) {
                var pendingPayload = pendingDirectInvitation.payload || {};
                var pendingNotificationId = String(pendingPayload.notification_id || '');
                if (pendingNotificationId && notificationIds.indexOf(pendingNotificationId) !== -1) {
                    // The notification is still authoritative, but presentation may be incomplete:
                    // retry a failed delivery or regenerate the question after a character switch.
                    return await presentDirectInvitation(pendingPayload);
                }
                if (result.status === 200 && result.body && result.body.notifications_ok === true) {
                    clearPendingDirectInvitation(pendingPayload, false);
                } else {
                    return false;
                }
            }
            if (!incoming || !incoming.visit_id || !incoming.id) {
                return false;
            }
            return await presentDirectInvitation(incoming);
        } finally {
            directInvitationPollInFlight = false;
        }
    }

    async function startJoin() {
        if (starting) return;
        render();
        var body = document.createElement('div');
        body.className = 'visit-join';
        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'visit-join-code';
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.maxLength = 16;
        input.placeholder = 'ABCDEFGH23';
        input.addEventListener('input', function () {
            var cleaned = normalizeInviteCode(input.value);
            if (cleaned !== input.value) input.value = cleaned;
        });
        body.appendChild(input);
        var dialog = openDialog({
            title: t('visit.join.title', '去别人家串门'),
            lines: [t('visit.join.codeLabel', '输入对方给你的 10 位邀请码')],
            body: body,
            buttons: [
                { id: 'cancel', label: t('visit.common.cancel', '取消') },
                { id: 'next', label: t('visit.join.next', '下一步'), variant: 'primary', onClick: function (handle) {
                    var code = normalizeInviteCode(input.value);
                    if (!INVITE_CODE_RE.test(code)) {
                        handle.setLines([t('visit.join.codeLabel', '输入对方给你的 10 位邀请码'),
                            { text: t('visit.join.codeFormat', '邀请码是 10 位，由字母和数字 2–7 组成'), tone: 'warning' }]);
                        return;
                    }
                    handle.close(code);
                } }
            ]
        });
        try { input.focus(); } catch (_) { /* noop */ }
        var code = await dialog.result;
        if (!code) return;
        starting = true;
        render();
        try {
            await joinWithCode(code);
        } finally {
            starting = false;
            render();
        }
    }

    // ── host 的接待确认（visit_invite，60 s）───────────────────────────

    function closeInviteDialog(visitId) {
        if (!inviteDialog) return;
        if (visitId && inviteDialog.visitId !== visitId) return;
        if (inviteDialog.timer) clearInterval(inviteDialog.timer);
        inviteDialog.handle.close(null);
        inviteDialog = null;
    }

    async function answerInvite(visitId, accept) {
        var result = await api('POST', '/api/visit/rooms/' + encodeURIComponent(visitId) + '/accept', {
            catgirl: currentCatgirl(), accept: !!accept
        });
        if (result.status === 404) toast(t('visit.invite.expired', '接待时间已经过了'));
        return result;
    }

    function onInvite(frame) {
        var visitId = String(frame.visit_id || '');
        if (!visitId || !isUiWindow()) return;
        // 已结束的那一场的邀请（迟到 / 重放）：不复活、不弹框
        if (endedVisitIds.indexOf(visitId) !== -1) return;
        // visit_invite 是这一场的权威起点（bind 重放只补邀请、不补之前的阶段帧）：
        // 页面还挂着旧一场时先给它收尾，再进新一场，之后 started 等帧才对得上
        if (visit.visitId !== visitId) {
            if (visit.visitId && inVisit()) onEnded({ visit_id: visit.visitId, side: visit.side, reason: '' });
            startNewVisit(visitId);
        }
        visit.side = 'host';
        visit.phase = 'awaiting_accept';
        visitStateKnown = true;
        if (frame.peer_name) visit.peerName = String(frame.peer_name);
        if (frame.peer_short_id) visit.peerShortId = String(frame.peer_short_id);
        if (frame.peer_human_label) visit.peerHumanLabel = String(frame.peer_human_label);
        visit.crossRegion = frame.cross_region === true;
        render();
        // 重连后 bind 会重推同一份邀请：已经弹着就不再弹
        if (inviteDialog && inviteDialog.visitId === visitId) return;
        closeInviteDialog();
        var expiresAt = Number(frame.expires_at) || (now() / 1000 + 60);
        var lineList = function () {
            var left = Math.max(0, Math.round(expiresAt - now() / 1000));
            var out = [t('visit.invite.body', '{{name}}（{{code}}）来串门了，要接待吗？', {
                name: String(frame.peer_name || peerCatName()), code: String(frame.peer_short_id || '')
            })];
            if (frame.cross_region === true) out.push({ text: t('visit.join.crossRegion', '对方在另一个地区，连接可能不太稳定'), tone: 'warning' });
            if (failed) out.push({ text: failedText || t('visit.error.generic', '没能完成，请稍后再试'), tone: 'warning' });
            out.push({ text: t('visit.invite.countdown', '{{s}} 秒后自动婉拒', { s: left }), tone: 'muted' });
            return out;
        };
        var inviteButtons = function (busy) {
            return [
                { id: 'decline', label: t('visit.invite.decline', '婉拒'), disabled: busy, onClick: function (h) { answer(h, false); } },
                { id: 'accept', label: t('visit.invite.accept', '接待'), variant: 'primary', disabled: busy, onClick: function (h) { answer(h, true); } }
            ];
        };
        var failed = false;
        var failedText = '';
        var answer = async function (h, accept) {
            h.setButtons(inviteButtons(true));
            var result = await answerInvite(visitId, accept);
            if (h.closed) return;
            var answerCode = bodyCode(result);
            if (result.status === 200 || result.status === 404 || (result.status === 409 && answerCode !== 'VISIT_DISABLED')) {
                h.close(accept ? 'accept' : 'decline');
                return;
            }
            if (answerCode === 'VISIT_DISABLED') {
                // 「允许串门」已关：不接待，但邀请还没定，婉拒照样能点
                failedText = t('visit.error.disabled', '「允许串门」还没有打开，请先在设置里打开');
            }
            // 本机请求一时失败（网络 / 5xx）：框留着、按钮恢复，期限内可以再点
            failed = true;
            h.setLines(lineList());
            h.setButtons(inviteButtons(false));
        };
        var handle = openDialog({
            title: t('visit.invite.title', '有客人来了'),
            lines: lineList(),
            dismissible: false,
            buttons: inviteButtons(false)
        });
        var entry = { visitId: visitId, handle: handle, timer: 0 };
        entry.timer = setInterval(function () {
            if (now() / 1000 >= expiresAt) {
                closeInviteDialog(visitId);
                return;
            }
            handle.setLines(lineList());
        }, 1000);
        inviteDialog = entry;
        handle.result.then(function () {
            if (inviteDialog === entry) {
                if (entry.timer) clearInterval(entry.timer);
                inviteDialog = null;
            }
        });
    }

    // ── 工具条按钮 ─────────────────────────────────────────────────

    async function endRoute(reason) {
        var visitId = visit.visitId;
        if (!visitId) return null;
        var result = await api('POST', '/api/visit/route/end', {
            lanlan_name: currentCatgirl(), visit_id: visitId, reason: reason
        });
        if (result.status === 409 && bodyCode(result) === 'VISIT_RECALL_ALREADY') {
            toast(t('visit.wrapUp.recallAlready', '她们已经在道别了'));
        } else if (result.status !== 200 && result.status !== 404) {
            toast(t('visit.error.generic', '没能完成，请稍后再试'));
        }
        return result;
    }

    async function cancelDirectInvitation(visitId) {
        visitId = String(visitId || '');
        if (!VISIT_ID_RE.test(visitId) || visit.visitId !== visitId
                || visit.side !== 'host' || !visit.targetUserId
                || WAITING_PHASES.indexOf(visit.phase) === -1) return false;
        var catgirl = currentCatgirl();
        var result = await endRoute('route_end');
        if (currentCatgirl() === catgirl && visit.visitId === visitId && result) {
            if (result.status === 404) {
                handleFrame({ type: 'visit_state_change', action: 'ended', visit_id: visitId, side: visit.side });
            } else if (result.status === 200 && inVisit()) {
                // 200 只确认开始收尾；持续核对后端，释放接管后再恢复普通聊天。
                visit.cancelRequested = true;
                pollReconnect();
                dispatchVisitStatus();
                render();
            }
        }
        return !!result && (result.status === 200 || result.status === 404);
    }

    I.handleVisitBarAction = function handleVisitBarAction(actionId) {
        switch (actionId) {
        case 'direct_decline':
            decideDirectInvitation('decline');
            return;
        case 'direct_accept':
            decideDirectInvitation('accept');
            return;
        case 'to_guest':
            visit.recipient = 'guest_cat';
            render();
            return;
        case 'to_own':
            visit.recipient = 'own_cat';
            render();
            return;
        case 'recall':
            endRoute('recall');
            return;
        case 'end_now':
            endRoute('route_end');
            return;
        default:
        }
    };

    // ── 回家汇报（debrief）芯片 ──────────────────────────────────────

    var debriefSeen = new Set();   // 已渲染过的块 request_id（重放去重）

    function debriefRequestIdOf(messageId) {
        // appendReactChatBlocks 的消息 id = 'plugin-blocks-' + request_id + '-' + 时间戳 + '-' + 序号
        var match = /^plugin-blocks-(.+)-\d+-\d+$/.exec(String(messageId || ''));
        return match && parseDebriefRequestId(match[1]) ? match[1] : '';
    }

    function messagesSnapshot() {
        var host = getHost();
        var state = host && typeof host.getState === 'function' ? host.getState() : null;
        return state && Array.isArray(state.messages) ? state.messages : [];
    }

    function findDebriefMessages(predicate) {
        return messagesSnapshot().filter(function (message) {
            var requestId = debriefRequestIdOf(message && message.id);
            return requestId && predicate(requestId, message);
        });
    }

    function parseDebriefRequestId(requestId) {
        var m = /^visit-debrief(-preview|-failed)?:([A-Za-z0-9_-]{22})(?::(\d+))?$/.exec(String(requestId || ''));
        if (!m) return null;
        return {
            kind: m[1] === '-preview' ? 'preview' : (m[1] === '-failed' ? 'failed' : 'chips'),
            visitId: m[2],
            seq: m[3] !== undefined ? Number(m[3]) : null
        };
    }

    /**
     * 改一条芯片 / 预览块 / 失败块：按钮置灰（disable: 'all' | [choice...]）并（可选）追加一条状态。
     * 同一条状态不重复追加。
     */
    function patchDebriefMessage(message, disable, statusText, tone) {
        var host = getHost();
        if (!message || !host || typeof host.updateMessage !== 'function') return;
        var blocks = (message.blocks || []).map(function (block) {
            if (block.type !== 'buttons') return block;
            return Object.assign({}, block, {
                buttons: block.buttons.map(function (button) {
                    var choice = button.payload && button.payload.choice;
                    var off = disable === 'all' || (Array.isArray(disable) && disable.indexOf(choice) !== -1);
                    return off ? Object.assign({}, button, { disabled: true }) : button;
                })
            });
        });
        if (statusText) {
            var last = blocks[blocks.length - 1];
            if (!(last && last.type === 'status' && last.text === statusText)) {
                blocks.push({ type: 'status', tone: tone || 'info', text: statusText });
            }
        }
        message.blocks = blocks;
        host.updateMessage(message.id, { blocks: blocks });
    }

    function abandonStatusText(written, unconfirmed) {
        unconfirmed = unconfirmed || {};
        written = written || {};
        // 不明优先：有结果不明的写入就不能说「没有写入」
        if (unconfirmed.facts || unconfirmed.cache) return t('visit.debrief.abandonedUnknown', '已放弃：有一部分可能已写入，未能确认');
        if (written.facts) return t('visit.debrief.abandonedPartial', '已放弃：事实已写入，日记没有写入');
        return t('visit.debrief.abandoned', '已放弃，没有写入');
    }

    function sameVisitFailures(visitId) {
        return findDebriefMessages(function (requestId) {
            var parsed = parseDebriefRequestId(requestId);
            return parsed && parsed.kind === 'failed' && parsed.visitId === visitId;
        });
    }

    function seqOfMessage(message) {
        var parsed = parseDebriefRequestId(debriefRequestIdOf(message.id));
        return parsed ? parsed.seq : null;
    }

    function previewMessages(visitId) {
        return findDebriefMessages(function (requestId) { return requestId === 'visit-debrief-preview:' + visitId; });
    }

    function chipMessages(visitId) {
        return findDebriefMessages(function (requestId) { return requestId === 'visit-debrief:' + visitId; });
    }

    function removeSupersededChipMessages(currentRequestId) {
        var host = getHost();
        if (!host || typeof host.removeMessage !== 'function') return;
        findDebriefMessages(function (requestId) {
            var parsed = parseDebriefRequestId(requestId);
            return requestId !== currentRequestId && parsed && parsed.kind === 'chips';
        }).forEach(function (message) {
            host.removeMessage(message.id);
        });
    }

    function greyFailuresUpTo(visitId, seq, inclusive, statusText) {
        sameVisitFailures(visitId).forEach(function (message) {
            var s = seqOfMessage(message);
            if (s === null) return;
            if (inclusive ? s <= seq : s < seq) patchDebriefMessage(message, 'all', statusText, 'info');
        });
    }

    function onDebriefFrame(frame) {
        var visitId = String(frame.visit_id || '');
        if (!VISIT_ID_RE.test(visitId) || !isUiWindow()) return;
        var phase = frame.phase;
        var seq = Number(frame.seq);
        var committing = t('visit.debrief.committing', '正在记…暂时没写进去会自动再试');
        if (phase === 'committing') {
            previewMessages(visitId).forEach(function (m) { patchDebriefMessage(m, ['forget'], committing, 'info'); });
            if (Number.isInteger(seq)) greyFailuresUpTo(visitId, seq, true, committing);
        } else if (phase === 'commit_failed') {
            previewMessages(visitId).forEach(function (m) { patchDebriefMessage(m, 'all'); });
            if (Number.isInteger(seq)) greyFailuresUpTo(visitId, seq, false);
        } else if (phase === 'voided') {
            previewMessages(visitId).forEach(function (m) {
                patchDebriefMessage(m, 'all', t('visit.debrief.previewVoided', '这份记录已作废，没有写入'), 'warning');
            });
        } else if (phase === 'preview') {
            chipMessages(visitId).forEach(function (m) { patchDebriefMessage(m, 'all'); });
        } else if (phase === 'chosen') {
            var target = findDebriefMessages(function (requestId) { return requestId === frame.request_id; });
            var choice = frame.choice;
            var text = '';
            if (choice === 'diary') text = t('visit.debrief.savedDiary', '已记成日记');
            else if (choice === 'forget') text = t('visit.debrief.forgot', '好的，这次不记');
            else if (choice === 'abandoned') {
                var parsed = parseDebriefRequestId(frame.request_id);
                text = parsed && parsed.kind === 'failed' ? abandonStatusText(frame.written, frame.unconfirmed) : '';
            }
            target.forEach(function (m) { patchDebriefMessage(m, 'all', text, choice === 'diary' ? 'success' : 'info'); });
            if (choice === 'abandoned') greyFailuresUpTo(visitId, Number.isInteger(seq) ? seq : Infinity, true);
        }
    }

    /**
     * app-websocket.js 的 chat_blocks 分支先问这里：串门芯片 / 预览块 / 失败块按 request_id 去重、
     * 渲染后回 visit_debrief_chip_ack。返回 true 表示已处理（渲染或判重跳过）。
     */
    function handleChatBlocks(response) {
        var requestId = response && typeof response.request_id === 'string' ? response.request_id : '';
        var parsed = parseDebriefRequestId(requestId);
        if (!parsed) return false;
        if (!isUiWindow()) return true;
        var seenOnThisConnection = debriefSeen.has(requestId);
        var already = seenOnThisConnection || findDebriefMessages(function (id) { return id === requestId; }).length > 0;
        // 重连后 debriefSeen 会清空：即使当前 request_id 已经在历史里，也借这次
        // 权威重放把旧版本遗留的多张初始问题收口。相同连接上的迟到重复帧则不再
        // 触碰更新的问题，避免旧 request_id 反过来移除新卡片。
        if (parsed.kind === 'chips' && !seenOnThisConnection) removeSupersededChipMessages(requestId);
        if (!already) {
            // 每次串门都会产生一个新的“是否记日记”问题。用户连续串门但没有立刻
            // 选择时，旧问题仍保留在 spool 里供以后处理，却不应在聊天窗里堆成一列。
            // 只收起更早的初始问题；已经进入预览 / 写入失败流程的卡片继续保留。
            if (typeof window.appendReactChatBlocks !== 'function' || !window.appendReactChatBlocks(response)) return true;
            if (parsed.kind === 'failed') {
                // 新版失败块到了：同一场更早的失败块按钮全部置灰（只留当前版可点）
                greyFailuresUpTo(parsed.visitId, parsed.seq, false);
            }
        }
        debriefSeen.add(requestId);
        sendSocketFrame({ action: 'visit_debrief_chip_ack', visit_id: parsed.visitId, request_id: requestId });
        return true;
    }

    async function postDebriefChoice(payload) {
        // 请求体 = 被点按钮的 payload 原样 + 发起页角色名（不挑字段重拼，免得丢 seq）
        var body = Object.assign({}, payload, { lanlan_name: currentCatgirl() });
        return api('POST', '/api/visit/debrief/choice', body);
    }

    async function onDebriefAction(message, action) {
        var payload = action && action.payload && typeof action.payload === 'object' ? action.payload : {};
        var visitId = String(payload.visit_id || '');
        var choice = String(payload.choice || '');
        if (!VISIT_ID_RE.test(visitId) || !choice) return;
        var requestId = debriefRequestIdOf(message && message.id);
        var parsed = parseDebriefRequestId(requestId) || { kind: 'chips', visitId: visitId, seq: null };
        var fromFailure = parsed.kind === 'failed';
        var committing = t('visit.debrief.committing', '正在记…暂时没写进去会自动再试');

        if (choice === 'abandon') {
            var unconfirmed = payload.unconfirmed || {};
            var written = payload.written || {};
            var question = '';
            if (unconfirmed.facts || unconfirmed.cache) question = t('visit.debrief.abandonUnknownConfirm', '有一部分可能已经写进记忆、没能确认，放弃后也不会撤回。确定放弃吗？');
            else if (written.facts && !written.cache) question = t('visit.debrief.abandonPartialConfirm', '事实已经写入，撤不回来；日记没有写入。确定放弃吗？');
            if (question && !(await confirmDialog(question, { danger: true }))) return;
        }

        // 点下立即置灰
        if (parsed.kind === 'chips') {
            patchDebriefMessage(message, 'all', choice === 'diary' ? t('visit.debrief.generating', '正在整理…') : '', 'info');
        } else if (fromFailure && choice === 'confirm') {
            patchDebriefMessage(message, 'all', committing, 'info');
        } else {
            patchDebriefMessage(message, 'all');
        }

        var result = await postDebriefChoice(payload);
        var status = result.status;
        var body = result.body || {};
        var error = String(body.error || body.code || '');

        if (status === 200) {
            if (body.applied === 'preview') {
                // 预览块平常由后端 chat_blocks 推来；推送没到这一页（显示连接断了 / 写失败）时，
                // 用应答里同一份块补上屏（按 request_id 去重：推送先到 / 后到都只留一份）。前端不自己拼预览
                var frame = body.chat_blocks;
                if (frame && frame.type === 'chat_blocks' && frame.request_id === 'visit-debrief-preview:' + visitId) {
                    handleChatBlocks(frame);
                    onDebriefFrame({ visit_id: visitId, phase: 'preview' });
                }
                return;
            }
            if (body.applied === 'diary') {
                patchDebriefMessage(message, 'all', t('visit.debrief.savedDiary', '已记成日记'), 'success');
                return;
            }
            if (body.applied === 'forget') {
                patchDebriefMessage(message, 'all', t('visit.debrief.forgot', '好的，这次不记'), 'info');
                return;
            }
            if (body.applied === 'abandoned') {
                patchDebriefMessage(message, 'all', abandonStatusText(body.written, body.unconfirmed), 'info');
                greyFailuresUpTo(visitId, parsed.seq === null ? Infinity : parsed.seq, true);
                return;
            }
            return;
        }
        if (status === 409 && error === 'already_chosen') {
            if (body.completed === true) {
                patchDebriefMessage(message, 'all');
            } else if (body.state === 'commit_failed') {
                patchDebriefMessage(message, 'all');
            } else if (body.state === 'committing') {
                if (parsed.kind === 'preview') {
                    // 记忆服务恢复后还能从这里手动再试一次
                    restoreButtons(message, ['confirm']);
                    patchDebriefMessage(message, ['forget'], committing, 'info');
                } else {
                    patchDebriefMessage(message, 'all', committing, 'info');
                }
            }
            return;
        }
        if (status === 409 && error === 'not_previewed') {
            // 预览已失效：芯片恢复可点，需要重新点「记成日记」
            restoreButtons(message, 'all');
            return;
        }
        if (status === 409 && (error === 'stale_failure' || error === 'not_failed')) {
            // 点的是旧版失败块：只置灰它和更早的失败块，新版失败块保持可点，不弹错误
            patchDebriefMessage(message, 'all');
            if (parsed.seq !== null) greyFailuresUpTo(visitId, parsed.seq, true);
            return;
        }
        if (status === 502 && error === 'commit_failed') {
            // 「写入失败」块由后端推来；预览块两钮置灰
            previewMessages(visitId).forEach(function (m) { patchDebriefMessage(m, 'all'); });
            patchDebriefMessage(message, 'all');
            return;
        }
        if (status === 503) {
            if (choice === 'diary') {
                restoreButtons(message, 'all');
                toast(t('visit.choice.retryLater', '这会儿没整理出来，稍后再点一次'));
            } else if (choice === 'confirm' && fromFailure) {
                // 已转后台退避：失败块保持置灰、只显示「正在记」
                patchDebriefMessage(message, 'all', committing, 'info');
            } else if (choice === 'confirm') {
                restoreButtons(message, ['confirm']);
                patchDebriefMessage(message, ['forget'], committing, 'info');
            } else {
                restoreButtons(message, 'all');
                toast(t('visit.error.generic', '没能完成，请稍后再试'));
            }
            return;
        }
        if (status === 404) {
            patchDebriefMessage(message, 'all', t('visit.choice.expired', '这次串门的记录已经过期了'), 'warning');
            return;
        }
        // 其他（400 / 网络错）：恢复可点，让用户再试
        restoreButtons(message, 'all');
        toast(t('visit.error.generic', '没能完成，请稍后再试'));
    }

    function restoreButtons(message, which) {
        var host = getHost();
        if (!message || !host || typeof host.updateMessage !== 'function') return;
        var blocks = (message.blocks || []).map(function (block) {
            if (block.type !== 'buttons') return block;
            return Object.assign({}, block, {
                buttons: block.buttons.map(function (button) {
                    var choice = button.payload && button.payload.choice;
                    if (which === 'all' || (Array.isArray(which) && which.indexOf(choice) !== -1)) {
                        var copy = Object.assign({}, button);
                        delete copy.disabled;
                        return copy;
                    }
                    return button;
                })
            });
        }).filter(function (block, index, all) {
            // 去掉点下时追加的「正在整理…」
            return !(block.type === 'status' && index === all.length - 1
                && block.text === t('visit.debrief.generating', '正在整理…'));
        });
        message.blocks = blocks;
        host.updateMessage(message.id, { blocks: blocks });
    }

    function findMessage(messageId) {
        var list = messagesSnapshot();
        for (var i = 0; i < list.length; i++) {
            if (list[i] && list[i].id === messageId) return list[i];
        }
        return null;
    }

    window.addEventListener('react-chat-window:action', function (event) {
        var detail = event && event.detail;
        var action = detail && detail.action;
        var message = detail && detail.message;
        if (!action || !message) return;
        // 拿页上最新的那一份（React 回调里的 message 可能是旧快照）
        var current = findMessage(message.id) || message;
        var payload = action.payload || {};
        if (action.action === DIRECT_INVITE_ACTION) {
            onDirectInviteAction(current, action);
        } else if (action.action === DEBRIEF_ACTION) {
            onDebriefAction(current, action);
        } else if (action.action === ENDED_ACTION) {
            expandEndedMessage(current, String(payload.visit_id || ''));
        } else if (action.action === DETAILS_ACTION && VISIT_ID_RE.test(String(payload.visit_id || ''))) {
            openDetails(String(payload.visit_id));
        } else if (action.action === EXPORT_ACTION && VISIT_ID_RE.test(String(payload.visit_id || ''))) {
            exportTranscript(String(payload.visit_id));
        } else if (action.action === REPORT_ACTION && VISIT_ID_RE.test(String(payload.visit_id || ''))) {
            openReport(String(payload.visit_id), current);
        }
    });

    window.addEventListener('neko-social-invite-response', function (event) {
        var detail = event && event.detail || {};
        if (detail.accepted === false && String(detail.visitId || '') === visit.visitId
                && visit.side === 'host' && WAITING_PHASES.indexOf(visit.phase) !== -1) {
            endRoute('route_end');
        }
    });

    window.addEventListener('neko-visit-first-frame', function (event) {
        var detail = event && event.detail;
        if (detail && typeof detail.dataURL === 'string' && detail.dataURL.indexOf('data:image/') === 0
                && (!detail.visitId || detail.visitId === visit.visitId)) {
            peerAvatarUrl = detail.dataURL;
            // 台词可能先于第一帧到：已上屏的对端猫娘气泡补上头像（之后的行渲染时自然带上）
            lines.forEach(function (entry) {
                if (entry.rendered && entry.visitId === visit.visitId && entry.speaker
                        && entry.speaker.self !== true && entry.speaker.kind === 'cat') {
                    renderLine(entry);
                }
            });
        }
    });

    // ── 状态码（app-websocket.js 的 status 分支转进来）────────────────────

    var INPUT_REFUSALS = [
        'VISIT_INPUT_REFUSED_AWAY', 'VISIT_INPUT_REFUSED_WRAPUP', 'VISIT_INPUT_REFUSED_NOT_READY',
        'VISIT_INPUT_REFUSED_RATE', 'VISIT_INPUT_REFUSED_SOURCE', 'VISIT_E_BUSY', 'VISIT_E_UNAUTHORIZED'
    ];

    /**
     * 返回 false 表示这条提示不用再弹 toast（例如 bind 被拒这种不带 request_id 的未授权）；
     * 返回字符串表示用它替换默认文案。
     */
    function handleStatus(code, details) {
        details = details || {};
        // VISIT_E_BUSY also refuses individual inputs; only ended establishes
        // that a busy status actually terminated room setup.
        if (code !== 'VISIT_E_BUSY' && Object.values(START_FAILURE_CODES).indexOf(code) !== -1) {
            rememberStartFailure(details.visit_id, code, details);
        }
        var requestId = typeof details.request_id === 'string' ? details.request_id : '';
        if (INPUT_REFUSALS.indexOf(code) !== -1 && requestId && !markNotDelivered(requestId, true)) {
            // 被拒的不是串门句（未 bind 的页面在串门期间发普通聊天、阶段切换的空档等）：按普通句收尾——
            // 原文放回输入框（React 侧只填空输入框），乐观气泡标失败，回复在途标记一并撤掉
            restoreDraft(requestId);
            if (window.appButtons && typeof window.appButtons.markTextSubmissionRefused === 'function') {
                window.appButtons.markTextSubmissionRefused(requestId);
            }
        }
        // bind 被拒（不带 request_id）：Docker / 局域网访问每次连上都会这样，不弹提示
        if (code === 'VISIT_E_UNAUTHORIZED' && !requestId) return false;
        if (code === 'VISIT_UNSUPPORTED_ON_THIS_MACHINE' && details.reason === 'insecure_context') {
            return t('visit.error.insecure_context', '自定义后端地址需要 https 或 localhost 才能串门');
        }
        return true;
    }

    // ── 页面重载 / 重新 bind：按 GET /api/visit/state 校正 ─────────────────

    /**
     * app-websocket.js 在 visit_bind 之后读一次 state 交给这里。``stale``：读的期间已经收到过
     * visit_state_change，阶段交给帧驱动，只补对端信息。
     */
    function phaseFromState(phase) {
        return ['pending', 'invite_ready', 'joining', 'awaiting_accept', 'active', 'wrap_up', 'ending'].indexOf(phase) !== -1
            ? phase : null;
    }

    function relayEndedToDisplay(visitId, side) {
        if (!visitId) return;
        var frame = {
            type: 'visit_state_change', action: 'ended', visit_id: visitId, side: side || ''
        };
        // 网页单窗口需要直接归还媒体桥；Electron 聊天窗则转发给 Pet 窗。
        if (!isChatFollowerPath() && window.nekoVisitParentBridge
                && typeof window.nekoVisitParentBridge.onStateChange === 'function') {
            try { window.nekoVisitParentBridge.onStateChange(frame); } catch (_) { /* 不阻止聊天面的收尾 */ }
        }
        if (typeof window.nekoRelayVisitStateToDisplay === 'function') window.nekoRelayVisitStateToDisplay(frame);
    }

    function relayStateSnapshotToDisplay(state, visitId) {
        if (!visitId || typeof window.nekoRelayVisitStateToDisplay !== 'function') return;
        var phase = phaseFromState(state.phase);
        if (!phase) return;
        var frame = {
            type: 'visit_state_change',
            action: phase === 'active' ? 'started' : phase,
            side: state.side || state.role || '',
            visit_id: visitId
        };
        // GET /api/visit/state keeps the media layout on room.peer_crop.  Older
        // snapshots used peer.crop, so retain that as a compatibility fallback.
        var peerCrop = state.room && (state.room.peer_crop === 'upper' || state.room.peer_crop === 'full')
            ? state.room.peer_crop
            : (state.peer && (state.peer.crop === 'upper' || state.peer.crop === 'full') ? state.peer.crop : '');
        if (peerCrop) frame.peer_crop = peerCrop;
        window.nekoRelayVisitStateToDisplay(frame);
        // 这一帧可能先于 Pet 窗自己的重载恢复建出 iframe，恢复随即作废，它本该套上的出门样式 / 对端显隐
        // 也就没了：按后端 started → departed 的顺序补上出门，再按快照同步对端显隐（建 iframe 会清掉这两样，必须排在阶段帧之后）
        if (frame.side === 'guest' && (phase === 'active' || phase === 'wrap_up')) {
            window.nekoRelayVisitStateToDisplay({
                type: 'visit_state_change', action: 'departed', side: frame.side, visit_id: visitId
            });
        }
        if (state.peer && typeof state.peer.hidden === 'boolean') {
            window.nekoRelayVisitStateToDisplay({
                type: 'visit_state_change', action: state.peer.hidden ? 'peer_hidden' : 'peer_visible',
                side: frame.side, visit_id: visitId
            });
        }
    }

    // 快照里的对端信息与重载前的台词并进这一场；onlyFill：只填空缺字段（帧带来的更新不覆盖）
    function mergeSnapshotDetails(state, onlyFill) {
        if (typeof state.target_user_id === 'string' && (!onlyFill || !visit.targetUserId)) {
            visit.targetUserId = state.target_user_id;
        }
        if (typeof state.invite_expires_at === 'number' && (!onlyFill || !visit.inviteExpiresAt)) {
            visit.inviteExpiresAt = state.invite_expires_at;
        }
        var peer = state.peer;
        if (peer && typeof peer === 'object') {
            if (peer.cat_name && (!onlyFill || !visit.peerName)) visit.peerName = String(peer.cat_name);
            if (peer.short_id && (!onlyFill || !visit.peerShortId)) visit.peerShortId = String(peer.short_id);
            if (peer.human_label && (!onlyFill || !visit.peerHumanLabel)) visit.peerHumanLabel = String(peer.human_label);
            if (!onlyFill) visit.peerHidden = peer.hidden === true;
        }
        if (onlyFill) {
            if (state.cross_region === true) visit.crossRegion = true;
        } else {
            visit.crossRegion = state.cross_region === true;
        }
        // 重载前的台词按 visit_line 同形重放（已有的 line_id 不重复上屏）
        (Array.isArray(state.transcript) ? state.transcript : []).forEach(function (frame) {
            if (!frame || frame.type !== 'visit_line') return;
            var existing = lines.get(lineKey(frame.visit_id || visit.visitId, frame.line_id));
            // 已收口的行不动；只拼了分片（final 断线时丢了）或本地截断的行，用快照里的可靠终稿覆盖
            if (existing && existing.final) return;
            onLineFinal(frame, true);
        });
    }

    function applyState(state, stale) {
        if (!state || typeof state !== 'object') return;
        if (!stale) visitStateKnown = true;
        releaseEnabled = state.enabled === true;
        if (!releaseEnabled && pendingDirectInvitation) {
            pendingDirectInvitation = null;
            refreshCompactGeometry();
        }
        var snapshotVisit = state.active === true && typeof state.visit_id === 'string' && state.visit_id
            && endedVisitIds.indexOf(state.visit_id) === -1 ? state.visit_id : '';
        // 断线或页面重载时可能只读到激活前失败的 ending 快照；
        // 与实时帧一样直接回到初始入口，不复活一个从未开始的场次。
        var sameVisitEnding = visit.visitId === snapshotVisit;
        if (snapshotVisit && state.phase === 'ending' && !snapshotStarted(state)
            && (!stale || sameVisitEnding)) {
            if (visit.visitId && visit.visitId !== snapshotVisit && inVisit()) {
                var previousEndingVisit = visit.visitId;
                var previousEndingSide = visit.side;
                onEnded({ visit_id: previousEndingVisit, side: previousEndingSide, reason: '' });
                relayEndedToDisplay(previousEndingVisit, previousEndingSide);
            }
            if (visit.visitId === snapshotVisit) {
                onEnded({ visit_id: snapshotVisit, side: state.side || state.role, reason: '' });
            } else {
                markEnded(snapshotVisit);
            }
            // 不先转发 ending：显示端会为它补建一个永远收不到 ended 的 iframe。
            relayEndedToDisplay(snapshotVisit, state.side || state.role);
            render();
            return;
        }
        if (state.active === true && endedVisitIds.indexOf(state.visit_id) !== -1 && snapshotStarted(state)) {
            // ended 帧先到时本页还不知道这场开始过（没留结束消息）：按快照补上，不复活这一场
            ensureEndedMessage({ visitId: state.visit_id, side: state.side || state.role || null,
                peerName: state.peer && state.peer.cat_name ? String(state.peer.cat_name) : '' });
        }
        if (stale) {
            // 读的期间到过 visit_state_change：帧定下的阶段为准；帧没定下阶段（peer_hidden / ending_soon /
            // reconnecting 等）时按快照补上。同一场的对端信息与台词照样并进来
            if (snapshotVisit && (!visit.visitId || visit.visitId === snapshotVisit)) {
                if (!visit.visitId) startNewVisit(snapshotVisit);
                if (visit.phase === null && phaseFromState(state.phase)) {
                    visit.side = state.side || state.role || visit.side;
                    visit.phase = phaseFromState(state.phase);
                    if (visit.phase === 'ending') releaseVisitTextSession();
                }
                if (snapshotStarted(state)) visit.everStarted = true;
                visit.ownName = currentCatgirl();
                mergeSnapshotDetails(state, true);
            }
            render();
            return;
        }
        if (snapshotVisit) {
            if (visit.visitId !== snapshotVisit) {
                // 断线期间别的窗口结束了这一场、又开了新一场：上一场照样留下结束消息、标掉还在等回显的本地气泡
                if (inVisit()) {
                    var previousVisit = visit.visitId;
                    var previousSide = visit.side;
                    onEnded({ visit_id: previousVisit, side: previousSide, reason: '' });
                    // 显示端必须先退掉旧 iframe，之后才会接受没有 pending 的新场次快照。
                    relayEndedToDisplay(previousVisit, previousSide);
                }
                startNewVisit(snapshotVisit);
            }
            visit.side = state.side || state.role || visit.side;
            if (visit.side) rememberSide(snapshotVisit, visit.side);
            visit.phase = phaseFromState(state.phase) || visit.phase;
            // 快照只在接管释放后才报 ending：与 ending 帧同一副作用，撤掉只由串门 ack 置上的会话标记
            if (visit.phase === 'ending') releaseVisitTextSession();
            if (snapshotStarted(state)) visit.everStarted = true;
            visit.reconnecting = state.reconnecting === true;
            visit.ownName = currentCatgirl();
            mergeSnapshotDetails(state, false);
            if (needsVisitStatePoll()) pollReconnect();
            // 实时 pending 可能正好落在 Pet / chat WebSocket 换手的空窗；本地场次完成
            // 收口 / 切换后再同步权威快照，确保显示端不会因旧 visit_id 而丢弃它。
            relayStateSnapshotToDisplay(state, snapshotVisit);
        } else if (inVisit() && (state.active !== true || state.visit_id !== visit.visitId)) {
            // 断线期间那一场已经结束、ended 帧没收到：走与 ended 帧同一个收尾（留下查看详情 / 导出 / 举报）
            var endedVisit = visit.visitId;
            var endedSide = visit.side;
            onEnded({ visit_id: endedVisit, side: endedSide, reason: '' });
            relayEndedToDisplay(endedVisit, endedSide);
        } else if (state.active !== true && typeof state.visit_id === 'string' && state.visit_id) {
            // 页面本地状态已经空了，Pet 窗仍可能留着重载前的 iframe；权威空闲快照照样负责回收。
            relayEndedToDisplay(state.visit_id, state.side || state.role);
        }
        if (state.active === false && typeof window.nekoRelayVisitIdleToDisplay === 'function') {
            window.nekoRelayVisitIdleToDisplay();
        }
        render();
    }

    // ── visit_bind 之后：读一次 state，告诉 pacer / 聊天面这一页的串门状况 ─────────

    var stateFrameSeq = 0;   // 本页收到的 visit_state_change / visit_invite 计数

    function dispatchBound(detail) {
        try {
            window.dispatchEvent(new CustomEvent('neko-visit-bound', { detail: detail }));
        } catch (_) { /* noop */ }
    }

    /**
     * app-websocket.js 发出 visit_bind 之后调。派发 neko-visit-bound{socket, visit_id?, active?}：
     * active 只在这一场已经开始（active / wrap_up / ending）时为 true，其余显式 false；读不到 state
     * 时不带 active（pacer 保持原状）；读的期间已经收到过 visit_state_change 时也不带 active——
     * 迟到的旧快照不能盖掉帧刚驱动的状态（例如 started 已到、快照还是 awaiting_accept）。
     * Pet 窗（不画界面）照样要派发：pacer 就在那一页。
     */
    async function afterBind(socket, catgirl, retryAttempt) {
        // 新连接：bind 会重放所有没定下来的 debrief 块；页上还在的按 request_id 跳过，被消息上限裁掉的要重新上屏
        if (!retryAttempt) {
            debriefSeen.clear();
            var boundName = catgirl || currentCatgirl();
            // 换了角色（app-character.js 清空聊天后为新角色连上）：上一位的串门由后端收尾，这一页不再替它补结束消息
            if (boundName && boundCatgirl && boundName !== boundCatgirl) resetForCharacterSwitch();
            if (boundName) boundCatgirl = boundName;
        }
        var seqAtSend = stateFrameSeq;
        var name = catgirl || currentCatgirl();
        var result = name
            ? await api('GET', '/api/visit/state?catgirl=' + encodeURIComponent(name))
            : { status: 0, body: null };
        var S = window.appState;
        // 这期间换了连接，或连接已清空（切换角色时先置 null、再清聊天）：旧快照不能再往新聊天里写
        if (S && S.socket !== socket) return;
        var state = result.status === 200 && result.body && typeof result.body === 'object' ? result.body : null;
        if (!state) {
            dispatchBound({ socket: socket });
            // 进行中的场次重载后不会被 bind 重放阶段：读不到就隔一会儿再读，免得一直停在普通聊天
            var attempt = typeof retryAttempt === 'number' ? retryAttempt : 0;
            var delays = [2000, 5000, 10000];
            if (attempt < delays.length) {
                setTimeout(function () {
                    var S2 = window.appState;
                    if (S2 && S2.socket !== socket) return;
                    afterBind(socket, catgirl, attempt + 1);
                }, delays[attempt]);
            }
            return;
        }
        var stale = stateFrameSeq !== seqAtSend;
        applyState(state, stale);
        dispatchVisitStatus();
        var detail = { socket: socket };
        if (typeof state.visit_id === 'string' && state.visit_id) detail.visit_id = state.visit_id;
        var startedPhases = ['active', 'wrap_up', 'ending'];
        if (!stale) {
            detail.active = state.active === true && startedPhases.indexOf(state.phase) !== -1;
        } else if (state.active === true && visit.visitId === state.visit_id && startedPhases.indexOf(visit.phase) !== -1) {
            // 读的期间到的是不带阶段的帧（peer_hidden 等）、这一场仍在进行：pacer 也得知道已开始（它只在 started 帧上置位）
            detail.active = true;
        }
        dispatchBound(detail);
    }

    // 台词带来的场次与本页不同（漏了新一场的阶段帧）：台词只在进行中的场次里有，按它换进这一场——
    // 旧一场先收尾；读 state 期间到的，在途的旧快照作废。已结束那一场迟到的台词照常上屏、不换场
    function reconcileLineVisit(frame) {
        var lineVisit = typeof frame.visit_id === 'string' ? frame.visit_id : '';
        if (!lineVisit || endedVisitIds.indexOf(lineVisit) !== -1) return;
        visitStateKnown = true;
        if (lineVisit === visit.visitId) {
            // 同一场、还停在等待阶段（漏了 started）：台词就是已开始的证据，推进到 active，在途的旧快照作废
            if (visit.phase === null || WAITING_PHASES.indexOf(visit.phase) !== -1) {
                stateFrameSeq += 1;
                visit.phase = 'active';
                visit.everStarted = true;
                closeInviteDialog(lineVisit);
                render();
            }
            return;
        }
        stateFrameSeq += 1;
        if (visit.visitId && inVisit()) onEnded({ visit_id: visit.visitId, side: visit.side, reason: '' });
        startNewVisit(lineVisit);
        var speaker = frame.speaker || {};
        if (speaker.side === 'host' || speaker.side === 'guest') {
            visit.side = speaker.self === true ? speaker.side : (speaker.side === 'host' ? 'guest' : 'host');
            rememberSide(lineVisit, visit.side);
        }
        visit.phase = 'active';
        visit.everStarted = true;
        visit.ownName = currentCatgirl();
        render();
    }

    // ── 帧入口 ─────────────────────────────────────────────────────

    function handleFrame(frame) {
        if (!frame || typeof frame.type !== 'string') return;
        switch (frame.type) {
        case 'visit_state_change':
            stateFrameSeq += 1;
            onStateChange(frame);
            dispatchVisitStatus(frame);
            break;
        case 'visit_invite':
            // 邀请同样是权威的换场：读 state 期间到了它，读回来的快照就作废（不能把刚换进来的新一场又收掉）
            stateFrameSeq += 1;
            onInvite(frame);
            break;
        case 'visit_line_delta':
            reconcileLineVisit(frame);
            onLineDelta(frame);
            break;
        case 'visit_line':
            reconcileLineVisit(frame);
            onLineFinal(frame);
            break;
        case 'visit_line_abort':
            onLineAbort(frame);
            break;
        case 'visit_typing':
            onTyping(frame);
            break;
        case 'visit_debrief':
            onDebriefFrame(frame);
            break;
        default:
        }
    }

    // 开关在别处被改（设置弹窗）：工具条入口跟着变；只在变化时重画
    var lastSwitch = null;
    setInterval(function () {
        var on = userSwitchOn();
        if (on !== lastSwitch) {
            lastSwitch = on;
            if (!on && pendingDirectInvitation) {
                pendingDirectInvitation = null;
                refreshCompactGeometry();
            }
            render();
        }
    }, 1500);

    window.nekoVisitChat = {
        handleFrame: handleFrame,
        handleStatus: handleStatus,
        handleChatBlocks: handleChatBlocks,
        applyState: applyState,
        afterBind: afterBind,
        isActive: function () { return I.isVisitChatActive(); },
        getState: publicVisitState,
        // 喵宇宙面板的备用邀请码入口复用正式预览、确认与加入流程。
        joinWithCode: joinWithCode,
        joinDirect: joinDirect,
        presentDirectInvitation: presentDirectInvitation,
        pollDirectInvitations: pollDirectInvitations,
        createInviteCode: createInviteCode,
        createDirectInvitation: createDirectInvitation,
        cancelDirectInvitation: cancelDirectInvitation,
        // 测试用：只读快照与内部入口
        _test: {
            assembleClauses: assembleClauses,
            buildTranscriptText: buildTranscriptText,
            normalizeInviteCode: normalizeInviteCode,
            relayCleanIsEmpty: relayCleanIsEmpty,
            openDetails: openDetails,
            joinWithCode: joinWithCode,
            joinDirect: joinDirect,
            presentDirectInvitation: presentDirectInvitation,
            pollDirectInvitations: pollDirectInvitations,
            cancelDirectInvitation: cancelDirectInvitation,
            onDirectInviteAction: onDirectInviteAction,
            startInvite: startInvite,
            exportTranscript: exportTranscript,
            onDebriefAction: onDebriefAction,
            state: function () { return { visit: Object.assign({}, visit), pending: pendingLocal.slice(), lines: lines }; },
            dialogs: function () { return openDialogs.slice(); }
        }
    };

    if (isUiWindow()) {
        setTimeout(pollDirectInvitations, 500);
        setInterval(pollDirectInvitations, 5000);
        var socialNotifications = window.nekoSocial;
        if (socialNotifications && typeof socialNotifications.onNotifyIncoming === 'function') {
            socialNotifications.onNotifyIncoming(function (notification) {
                var payload = notification && notification.payload;
                if (notification && (notification.kind === 'visit_invite'
                        || payload && payload.type === 'visit_invite')) {
                    pollDirectInvitations();
                }
            });
        }
    }
})();
