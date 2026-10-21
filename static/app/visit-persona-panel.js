/**
 * visit-persona-panel.js — 串门人设预览与编辑（docs/design/visit-infrastructure.md §5 PR-12、
 * §4.6 GET | PUT /api/visit/persona、POST /api/visit/persona/regenerate；OD-10 v3）。
 *
 * 串门人设是她去别人家时带的自我介绍（不放原始角色卡、不含亲人的信息）。出门 / 建房之前
 * 必须由用户看过并确认（reviewed:true）。本面板：
 * - 读：GET /api/visit/persona?catgirl=；state 'missing' → 触发一次生成，'generating' → 轮询；
 *   error 'persona_sensitive_overlap'（自动生成的稿子混进了私人内容）→ 提示手写、给空编辑框；
 * - 写：改过正文「保存并确认」→ PUT {text, reviewed:true}；没改「确认」→
 *   PUT {reviewed:true, persona_version}（版本号是 GET 拿到的那份，盘上已换稿时后端 409）；
 * - 角色卡改过而人设是手写的（card_changed && edited）→ 提示「角色卡已变更，是否重新生成」；
 * - 私人段落清单并排展示「不会带出门」，检查不完整 / 清单基于旧卡片时如实提示。
 *
 * 导出 window.openVisitPersonaPreview(catgirl, {requireConfirm}) → Promise<boolean>（是否已确认）：
 * 首次打开「允许串门」时（visit-settings.js）与出门 / 建房收到 409 VISIT_PERSONA_UNREVIEWED 时
 * （visit-chat.js，确认后自动重试原请求一次）弹出。index.html 与 chat.html 都加载。
 * 所有文字一律 textContent 写入。
 */
(function () {
    'use strict';

    if (typeof window.openVisitPersonaPreview === 'function') return;

    var POLL_MS = 2000;
    var POLL_MAX = 90;

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

    async function mutationHeaders() {
        var security = window.nekoLocalMutationSecurity;
        if (security && typeof security.getMutationHeaders === 'function') {
            try {
                return await security.getMutationHeaders();
            } catch (_) { /* 后端会 403 */ }
        }
        return {};
    }

    async function request(method, url, body) {
        var send = async function () {
            // 读写三个端点都过本机来源校验：每次都带 X-CSRF-Token
            var headers = Object.assign({}, await mutationHeaders());
            var init = { method: method, cache: 'no-store', headers: headers };
            if (body !== undefined) {
                headers['Content-Type'] = 'application/json';
                init.body = JSON.stringify(body);
            }
            return fetch(url, init);
        };
        var response;
        try {
            response = await send();
            if (response.status === 403) {
                var stale = false;
                try {
                    var probe = await response.clone().json();
                    stale = !!(probe && probe.error_code === 'csrf_validation_failed');
                } catch (_) { stale = false; }
                var security = window.nekoLocalMutationSecurity;
                if (stale && security && typeof security.refreshToken === 'function') {
                    await security.refreshToken();
                    response = await send();
                }
            }
        } catch (_) {
            return { status: 0, body: null };
        }
        var parsed = null;
        try { parsed = await response.json(); } catch (_) { parsed = null; }
        return { status: response.status, body: parsed };
    }

    function personaUrl(catgirl) {
        return '/api/visit/persona?catgirl=' + encodeURIComponent(catgirl);
    }

    function regenerateUrl(catgirl) {
        return '/api/visit/persona/regenerate?catgirl=' + encodeURIComponent(catgirl);
    }

    function codeOf(result) {
        var body = result && result.body;
        return body && typeof body === 'object' ? String(body.code || body.error || '') : '';
    }

    // Electron compact 窗口按 DOM 收集命中区：面板开 / 关后通知它重算
    function refreshCompactGeometry() {
        try {
            window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-refresh'));
        } catch (_) { /* 非 compact 窗口没有几何桥 */ }
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function wait(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    var openPanel = null;

    /**
     * 打开面板；同一时刻只开一个。同一角色再调用时复用已开的那个、返回它的结果；
     * 换了角色就先关掉旧面板（它的结果为 false），再为新角色开一个。
     */
    /** 关掉已开的面板（结果 false）：切换角色时由聊天面调用，旧角色的面板不能留着让人改 / 确认。 */
    window.closeVisitPersonaPreview = function closeVisitPersonaPreview() {
        if (openPanel && typeof openPanel.close === 'function') openPanel.close(false);
    };

    window.openVisitPersonaPreview = function openVisitPersonaPreview(catgirl, options) {
        options = options || {};
        if (!catgirl) return Promise.resolve(false);
        if (openPanel) {
            if (openPanel.catgirl === catgirl) return openPanel.result;
            // 别的窗口换了角色：确认旧角色的面板不能放行新角色的串门
            openPanel.close(false);
        }

        var overlay = el('div', 'visit-dialog-overlay visit-persona-overlay');
        var dialog = el('div', 'visit-dialog visit-persona');
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('data-compact-geometry-owner', 'surface');
        dialog.setAttribute('data-compact-geometry-item', 'visitDialog');
        var title = el('div', 'visit-dialog-title', t('visit.persona.title', '串门自我介绍'));
        dialog.setAttribute('aria-label', title.textContent);
        var intro = el('p', 'visit-dialog-line', t('visit.persona.intro',
            '这是她去别人家时带的自我介绍，不含你的信息；对方的猫娘能看到，也可能复述其中内容。'));
        var notice = el('div', 'visit-dialog-lines');
        var textarea = el('textarea', 'visit-persona-text');
        textarea.rows = 8;
        textarea.placeholder = t('visit.persona.editHint', '用几句话介绍她自己：性格、爱好、说话的样子');
        var privateBox = el('div', 'visit-persona-private');
        var actions = el('div', 'visit-dialog-actions');
        var body = el('div', 'visit-persona-body');
        dialog.appendChild(title);
        body.appendChild(intro);
        if (options.requireConfirm) {
            body.appendChild(el('p', 'visit-dialog-line is-warning', t('visit.persona.unreviewed', '出门前先看一眼她的串门自我介绍，确认后才能串门')));
        }
        body.appendChild(notice);
        body.appendChild(textarea);
        body.appendChild(privateBox);
        dialog.appendChild(body);
        dialog.appendChild(actions);
        overlay.appendChild(dialog);
        (document.body || document.documentElement).appendChild(overlay);
        // Electron 的透明桌面窗默认把未登记区域的鼠标透传给后面的应用。
        // 串门弹窗打开期间临时接管输入，避免按钮看得见却点到后面的窗口。
        var releaseInteractive = acquireForceInteractiveLease();
        refreshCompactGeometry();

        var settle;
        var panel = {
            result: new Promise(function (resolve) { settle = resolve; }),
            closed: false,
            catgirl: catgirl,
            close: null
        };
        openPanel = panel;
        var current = null;      // 最近一次 GET 的结果
        var loadedText = '';

        function close(confirmed) {
            if (panel.closed) return;
            panel.closed = true;
            if (typeof document.removeEventListener === 'function') document.removeEventListener('keydown', onKeydown, true);
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            releaseInteractive();
            refreshCompactGeometry();
            if (openPanel === panel) openPanel = null;
            settle(confirmed === true);
        }
        panel.close = close;

        function setNotice(lines) {
            while (notice.firstChild) notice.removeChild(notice.firstChild);
            (lines || []).forEach(function (line) {
                if (!line) return;
                var spec = typeof line === 'string' ? { text: line } : line;
                notice.appendChild(el('p', 'visit-dialog-line' + (spec.tone ? ' is-' + spec.tone : ''), spec.text));
            });
            refreshCompactGeometry();
        }

        function setButtons(buttons) {
            while (actions.firstChild) actions.removeChild(actions.firstChild);
            buttons.forEach(function (b) {
                var btn = el('button', 'visit-dialog-btn' + (b.variant ? ' is-' + b.variant : ''), b.label);
                btn.type = 'button';
                btn.setAttribute('data-visit-persona-button', b.id);
                if (b.disabled) btn.disabled = true;
                btn.addEventListener('click', function (event) {
                    event.stopPropagation();
                    b.onClick();
                });
                actions.appendChild(btn);
            });
            refreshCompactGeometry();
        }

        function renderPrivate(data) {
            while (privateBox.firstChild) privateBox.removeChild(privateBox.firstChild);
            var sections = Array.isArray(data && data.private_sections) ? data.private_sections : [];
            if (sections.length) {
                privateBox.appendChild(el('p', 'visit-persona-private-title', t('visit.persona.privateSections', '这些原卡内容不会带出门：')));
                var list = el('ul', 'visit-persona-private-list');
                sections.forEach(function (section) { list.appendChild(el('li', '', String(section))); });
                privateBox.appendChild(list);
            }
            if (data && data.sections_outdated === true) {
                privateBox.appendChild(el('p', 'visit-dialog-line is-warning', t('visit.persona.sectionsOutdated', '这份私人段落清单基于改动之前的角色卡，新加的内容没有列进来，请自己再看一遍')));
            }
            if (data && data.scan_complete === false) {
                privateBox.appendChild(el('p', 'visit-dialog-line is-warning', t('visit.persona.scanIncomplete', '自动检查不完整，请自己再看一遍有没有私人信息')));
            }
            refreshCompactGeometry();
        }

        function closeButton() {
            return { id: 'close', label: t('visit.common.close', '关闭'), onClick: function () { close(false); } };
        }

        function editingButtons() {
            var changed = textarea.value.trim() !== loadedText.trim();
            var buttons = [closeButton(), {
                id: 'regenerate', label: t('visit.persona.regenerate', '重新生成'),
                onClick: function () { regenerate(); }
            }];
            if (changed || !loadedText) {
                buttons.push({
                    id: 'save', label: t('visit.persona.saveConfirm', '保存并确认'), variant: 'primary',
                    disabled: !textarea.value.trim(),
                    onClick: function () { save(true); }
                });
            } else {
                buttons.push({
                    id: 'confirm', label: t('visit.persona.confirm', '确认'), variant: 'primary',
                    onClick: function () { save(false); }
                });
            }
            setButtons(buttons);
        }

        textarea.addEventListener('input', editingButtons);

        function show(data) {
            current = data;
            var lines = [];
            if (data.reviewed === true) lines.push({ text: t('visit.persona.reviewed', '已确认，可以去串门'), tone: 'success' });
            if (data.card_changed === true && data.edited === true) {
                lines.push({ text: t('visit.persona.cardChanged', '角色卡已经改过，要按新卡重新生成吗？'), tone: 'warning' });
            }
            if (data.error === 'persona_sensitive_overlap') {
                lines.push({ text: t('visit.persona.privateOverlap', '自动生成的自我介绍里混进了私人内容，请自己写一段'), tone: 'warning' });
            } else if (data.error === 'llm_unavailable') {
                lines.push({ text: t('visit.persona.llmUnavailable', '暂时没能自动生成，可以自己写，也可以稍后重新生成'), tone: 'warning' });
            }
            setNotice(lines);
            loadedText = typeof data.text === 'string' ? data.text : '';
            textarea.value = loadedText;
            textarea.disabled = false;
            renderPrivate(data);
            editingButtons();
        }

        async function load() {
            setNotice([t('visit.persona.loading', '正在读取…')]);
            textarea.disabled = true;
            setButtons([closeButton()]);
            for (var attempt = 0; attempt < POLL_MAX && !panel.closed; attempt++) {
                var result = await request('GET', personaUrl(catgirl));
                if (panel.closed) return;
                if (result.status !== 200 || !result.body) {
                    var code = codeOf(result);
                    setNotice([{ text: code === 'unknown_catgirl'
                        ? t('visit.persona.unknownCatgirl', '找不到这个角色')
                        : t('visit.persona.unavailable', '暂时读不到串门自我介绍，稍后再试'), tone: 'warning' }]);
                    setButtons([closeButton()]);
                    return;
                }
                var data = result.body;
                if (data.state === 'missing' && !data.error && attempt === 0) {
                    // 还没有：触发一次生成，之后轮询；生成没起来（网络 / 403 / 5xx）就别空等，给重试
                    var started = await request('POST', regenerateUrl(catgirl), {});
                    if (panel.closed) return false;
                    if (started.status !== 202 && codeOf(started) !== 'persona_generating') {
                        setNotice([{ text: t('visit.persona.unavailable', '暂时读不到串门自我介绍，稍后再试'), tone: 'warning' }]);
                        textarea.disabled = false;
                        setButtons([closeButton(), {
                            id: 'regenerate', label: t('visit.persona.regenerate', '重新生成'),
                            onClick: function () { regenerate(); }
                        }]);
                        return false;
                    }
                    setNotice([t('visit.persona.generating', '正在为她写串门自我介绍…')]);
                    await wait(POLL_MS);
                    continue;
                }
                if (data.state === 'generating' || (data.state === 'missing' && !data.error)) {
                    setNotice([t('visit.persona.generating', '正在为她写串门自我介绍…')]);
                    await wait(POLL_MS);
                    continue;
                }
                show(data);
                return true;
            }
            if (!panel.closed) {
                setNotice([{ text: t('visit.persona.unavailable', '暂时读不到串门自我介绍，稍后再试'), tone: 'warning' }]);
                setButtons([closeButton()]);
            }
        }

        async function regenerate() {
            setButtons([closeButton()]);
            var result = await request('POST', regenerateUrl(catgirl), {});
            if (panel.closed) return;
            if (result.status !== 202 && codeOf(result) !== 'persona_generating') {
                setNotice([{ text: t('visit.persona.unavailable', '暂时读不到串门自我介绍，稍后再试'), tone: 'warning' }]);
                editingButtons();
                return;
            }
            await load();
        }

        async function save(withText) {
            var body = { reviewed: true };
            if (withText) {
                body.text = textarea.value.trim();
            } else {
                body.persona_version = current && current.persona_version ? current.persona_version : '';
            }
            setButtons([closeButton()]);
            var result = await request('PUT', personaUrl(catgirl), body);
            if (panel.closed) return;
            if (result.status === 200 && result.body && result.body.reviewed === true) {
                close(true);
                return;
            }
            var code = codeOf(result);
            var hits = result.body && Array.isArray(result.body.hits) ? result.body.hits.map(String).join('、') : '';
            var message;
            if (code === 'persona_sensitive_overlap') {
                message = hits
                    ? t('visit.persona.privateHits', '这些内容像是私人信息，请删掉再保存：{{hits}}', { hits: hits })
                    : t('visit.persona.privateOverlap', '自动生成的自我介绍里混进了私人内容，请自己写一段');
            } else if (code === 'persona_too_long') {
                message = t('visit.persona.tooLong', '太长了，请写短一点');
            } else if (code === 'persona_changed' || code === 'persona_card_changed') {
                // 重新读取成功后在原有提示后面追加；读失败时 load 自己的错误提示保留
                if (await load()) {
                    notice.appendChild(el('p', 'visit-dialog-line is-warning',
                        t('visit.persona.changed', '这段内容刚刚被更新过，已重新读取，请再确认一次')));
                    refreshCompactGeometry();
                }
                return;
            } else if (code === 'persona_generating') {
                message = t('visit.persona.generating', '正在为她写串门自我介绍…');
            } else {
                message = t('visit.persona.saveFailed', '没能保存，请稍后再试');
            }
            setNotice([{ text: message, tone: 'warning' }]);
            editingButtons();
        }

        // 焦点可能还在打开面板的按钮上：Escape 挂在 document 上，关面板时摘掉
        function onKeydown(event) {
            if (event.key === 'Escape') close(false);
        }
        if (typeof document.addEventListener === 'function') document.addEventListener('keydown', onKeydown, true);
        dialog.tabIndex = -1;
        try { dialog.focus(); } catch (_) { /* noop */ }
        load();
        return panel.result;
    };
})();
