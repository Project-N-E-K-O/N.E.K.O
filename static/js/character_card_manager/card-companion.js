// Part responsibility: character-card design companion UI, requests, form application, and live form synchronization.

function _cardAssistIsReservedKey(key) {
    const k = String(key == null ? '' : key).trim();
    if (!k) return false;
    if (k === '档案名') return true;
    try {
        if (typeof isCharacterReservedFieldName === 'function') {
            return isCharacterReservedFieldName(k);
        }
    } catch (_) { /* 极端兜底：helper 不可用就只挡 '档案名' */ }
    return false;
}

// 当前用作"开发猫"占位的猫娘 profile name。/api/characters/catgirl/{name}/card-face
// 命中就用真实卡面，不命中走 fallback 圆圈。未来替换开发猫只需要改这里。
const CARD_COMPANION_DEV_CAT_NAME = 'YUI';

function _cardAssistT(key, fallback, vars) {
    if (window.t && typeof window.t === 'function') {
        try {
            const v = window.t(key, vars || undefined);
            if (typeof v === 'string' && v && v !== key) return v;
        } catch (_) { /* fall through */ }
    }
    return fallback;
}

function _cardAssistCurrentLocale() {
    try {
        const lang = (typeof getCurrentUiLanguage === 'function') ? getCurrentUiLanguage() : '';
        return lang || 'en';
    } catch (_) { return 'en'; }
}

// 收集表单上所有用户可见的字段 name（textarea + input），保留出现顺序、去重、
// 跳过保留 key。Apply 时是按 `textarea[name=...]` 精确匹配的，所以必须把
// 模板真实使用的 key（en 模板用 "Gender"/"Age" 之类、zh 用 "性别"/"年龄" 之类）
// 喂给 LLM，否则生成出来的中文 key 会以"新增字段"形式平行插入，旧字段不会被覆盖。
function _cardAssistCollectFieldKeys(form) {
    const keys = [];
    if (!form) return keys;
    const seen = new Set();
    form.querySelectorAll('textarea[name], input[name]').forEach(function (el) {
        const k = el.getAttribute('name');
        if (!k || _cardAssistIsReservedKey(k)) return;
        if (seen.has(k)) return;
        seen.add(k);
        keys.push(k);
    });
    return keys;
}

function _cardAssistCollectCurrentFormData(form) {
    const data = {};
    if (!form) return data;
    const fd = new FormData(form);
    for (const [k, v] of fd.entries()) {
        if (!k || _cardAssistIsReservedKey(k)) continue;
        const val = typeof v === 'string' ? v.trim() : v;
        if (val) data[k] = val;
    }
    return data;
}

// card-assist 的 4 个端点会真去打 LLM、花用户配额，后端按统一守卫（issue #1479）
// 要求带本地 Origin/CSRF 头，挡掉恶意网页用 no-cors 伪造 JSON 偷跑配额（Codex
// #3328998416）。本页（character_card_manager.html，独立页）不加载 app-prompt-shared.js
// → 没有 window.nekoLocalMutationSecurity，所以这里自包含地拿 X-CSRF-Token：
//   1) 主 app 上下文里若已有统一安全助手就直接用（带刷新逻辑）；
//   2) 独立页兜底：从 /api/config/page_config 取 autostart_csrf_token（与本页已加载的
//      tutorial/core/universal-manager.js 同一套来源），缓存一次即可（per-instance 常量）。
// 取不到就返回空头——后端会 403，_cardAssistFetch 下面的错误通路照常当失败处理，不会
// 静默成功。Origin 头由浏览器对同源 POST 自动带上，与本页 tutorial 上报走的是同一条路。
let _cardAssistCsrfToken = null;
async function _cardAssistCsrfHeaders() {
    try {
        const sec = window.nekoLocalMutationSecurity;
        if (sec && typeof sec.getMutationHeaders === 'function') {
            const h = await sec.getMutationHeaders();
            if (h && typeof h === 'object') return h;
        }
    } catch (_) { /* fall through to page_config */ }
    if (_cardAssistCsrfToken) return { 'X-CSRF-Token': _cardAssistCsrfToken };
    try {
        const r = await fetch('/api/config/page_config', { cache: 'no-store' });
        if (r.ok) {
            const d = await r.json();
            if (d && typeof d.autostart_csrf_token === 'string' && d.autostart_csrf_token) {
                _cardAssistCsrfToken = d.autostart_csrf_token;
                return { 'X-CSRF-Token': _cardAssistCsrfToken };
            }
        }
    } catch (_) { /* 取不到 → 空头，后端 403 由错误通路兜住 */ }
    return {};
}

async function _cardAssistFetch(path, payload) {
    const csrfHeaders = await _cardAssistCsrfHeaders();
    const resp = await fetch(path, {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, csrfHeaders),
        body: JSON.stringify(payload || {}),
    });
    let body = null;
    try { body = await resp.json(); } catch (_) { body = null; }
    if (!resp.ok || !body || body.success === false) {
        const err = (body && (body.message || body.error)) || ('HTTP ' + resp.status);
        const e = new Error(err);
        // 后端目前用 {success, error: "<machine_code>", message: "..."} 形状，
        // body.error 就是机器码；兼容性预留 body.code（其他接口可能这么写）。
        e.code = body && (body.code || body.error);
        throw e;
    }
    return body;
}

// ========== 入口：打开/复用 companion 面板 ==========

function openCardAssistCompanion(form, originalName, isNew) {
    if (window._cardCompanion) {
        const existing = window._cardCompanion;
        if (existing.form === form) {
            // 同一只猫娘 → 把已有面板拉回前台
            _companionSetMinimized(existing, false);
            if (existing.inputEl) {
                try { existing.inputEl.focus(); } catch (_) {}
            }
            return;
        }
        // 切换到不同的猫娘 → 销毁旧面板再开新的
        _companionTeardown(existing);
        _companionDestroy(existing);
        window._cardCompanion = null;
    }
    const state = _companionCreate(form, originalName, isNew);
    window._cardCompanion = state;
    document.body.appendChild(state.overlay);
    _companionAttachFormWatchers(state);
    _companionGreet(state);
    setTimeout(() => { if (state.inputEl) state.inputEl.focus(); }, 80);
}

function _companionCreate(form, originalName, isNew) {
    const state = {
        form: form,
        originalName: originalName,
        isNew: !!isNew,
        devCatName: CARD_COMPANION_DEV_CAT_NAME,
        // 状态机：
        //   awaiting_description → 还在等用户给一句话描述
        //   asking_questions     → AI 抛出了 N 道澄清问题，正在轮流回答
        //   generating           → 正在调 /generate 写草稿（极短瞬态）
        //   chat                 → 草稿已应用，自由对话 + 局部 patch
        mode: 'awaiting_description',
        description: '',
        pendingQuestions: [],
        currentQuestionIdx: 0,
        collectedAnswers: {},
        // /chat 调用时发回去的对话历史（OpenAI 格式）
        chatHistory: [],
        // 表单监视：detach 列表 + 上次快照
        formWatchHandlers: [],
        formWatchSnapshot: {},
        // DOM refs
        overlay: null,
        threadEl: null,
        inputEl: null,
        sendBtnEl: null,
        quickRowEl: null,
        avatarToggleEl: null,
        dragCleanup: null,
        expandedPanelRect: null,
        minimizeTransitionTimer: null,
        minimizedClickSuppressTimer: null,
        suppressNextMinimizedClick: false,
        minimized: false,
        busy: false,
    };
    state.overlay = _companionBuildPanel(state);
    return state;
}

function _companionDestroy(state) {
    if (state.overlay && state.overlay.parentNode) {
        state.overlay.parentNode.removeChild(state.overlay);
    }
}

function _companionSetMinimized(state, minimized) {
    if (!state || !state.overlay) return;
    const overlay = state.overlay;
    if (state.minimizeTransitionTimer) {
        clearTimeout(state.minimizeTransitionTimer);
        state.minimizeTransitionTimer = null;
    }
    if (state.minimizedClickSuppressTimer) {
        clearTimeout(state.minimizedClickSuppressTimer);
        state.minimizedClickSuppressTimer = null;
    }
    overlay.classList.remove('card-companion-collapsing', 'card-companion-expanding');
    const shouldMinimize = !!minimized;
    const currentlyMinimized = !!state.minimized;
    if (shouldMinimize === currentlyMinimized) return;
    state.minimized = shouldMinimize;

    if (shouldMinimize) {
        const panelRect = overlay.getBoundingClientRect();
        const avatarRect = state.avatarToggleEl
            ? state.avatarToggleEl.getBoundingClientRect()
            : panelRect;
        state.expandedPanelRect = {
            left: panelRect.left,
            top: panelRect.top,
            width: panelRect.width,
            height: panelRect.height,
        };
        overlay.style.left = panelRect.left + 'px';
        overlay.style.top = panelRect.top + 'px';
        overlay.style.width = panelRect.width + 'px';
        overlay.style.height = panelRect.height + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
        overlay.style.minWidth = '0px';
        overlay.style.maxWidth = 'none';
        overlay.style.minHeight = '0px';
        overlay.style.maxHeight = 'none';
        overlay.getBoundingClientRect();
        overlay.classList.add('card-companion-collapsing');
        overlay.style.left = avatarRect.left + 'px';
        overlay.style.top = avatarRect.top + 'px';
        overlay.style.width = avatarRect.width + 'px';
        overlay.style.height = avatarRect.height + 'px';
        state.minimizeTransitionTimer = setTimeout(function () {
            overlay.classList.remove('card-companion-collapsing');
            overlay.classList.add('card-companion-minimized');
            state.minimizeTransitionTimer = null;
        }, 260);
    } else {
        const currentRect = overlay.getBoundingClientRect();
        const targetRect = state.expandedPanelRect || currentRect;
        overlay.style.left = currentRect.left + 'px';
        overlay.style.top = currentRect.top + 'px';
        overlay.style.width = currentRect.width + 'px';
        overlay.style.height = currentRect.height + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
        overlay.classList.add('card-companion-expanding');
        overlay.classList.remove('card-companion-minimized');
        overlay.getBoundingClientRect();
        overlay.style.left = targetRect.left + 'px';
        overlay.style.top = targetRect.top + 'px';
        overlay.style.width = targetRect.width + 'px';
        overlay.style.height = targetRect.height + 'px';
        state.minimizeTransitionTimer = setTimeout(function () {
            overlay.classList.remove('card-companion-expanding');
            state.minimizeTransitionTimer = null;
        }, 260);
    }
    if (state.avatarToggleEl) {
        const title = shouldMinimize
            ? _cardAssistT('character.aiCompanionExpand', '展开')
            : _cardAssistT('character.aiCompanionMinimize', '收起');
        state.avatarToggleEl.title = title;
        state.avatarToggleEl.setAttribute('aria-label', title);
        state.avatarToggleEl.setAttribute('aria-expanded', shouldMinimize ? 'false' : 'true');
    }
}

function _companionTeardown(state) {
    if (!state) return;
    // ⚠ 先把 _companionSetBusy 给「未落库新卡」禁掉的 Save 无条件恢复：用户在 LLM 请求
    // 还在飞时点 × 关掉 companion = 主动结束 AI 流程，但 teardown 只置 closed / 摘监听、
    // 迟到响应的 guard 又会直接 return，于是 Save 会一直灰着直到那次请求超时（最多 60s）。
    // 这里在关闭时强制放开，避免表单还在页面上却存不了（Codex #3331627614 / CR #3331629488）。
    try {
        if (state.form) {
            const saveBtn = state.form.querySelector('#save-button');
            if (saveBtn) saveBtn.disabled = false;
        }
    } catch (_) { /* form 可能已 detach，忽略 */ }
    // closed flag：所有 in-flight 的 await 拿到 response 后会 check 这个，
    // 避免 companion 已经关掉/切到别只猫娘了，迟到的 LLM 结果还在静默改表单。
    state.closed = true;
    if (state.minimizeTransitionTimer) {
        clearTimeout(state.minimizeTransitionTimer);
        state.minimizeTransitionTimer = null;
    }
    if (state.minimizedClickSuppressTimer) {
        clearTimeout(state.minimizedClickSuppressTimer);
        state.minimizedClickSuppressTimer = null;
    }
    if (typeof state.dragCleanup === 'function') {
        try { state.dragCleanup(); } catch (_) {}
        state.dragCleanup = null;
    }
    if (state.form && state.formWatchHandlers) {
        state.formWatchHandlers.forEach(function (pair) {
            try { state.form.removeEventListener(pair[0], pair[1]); } catch (_) {}
        });
        state.formWatchHandlers = [];
    }
}

function _companionBuildPanel(state) {
    const overlay = document.createElement('aside');
    overlay.className = 'card-companion-panel';
    overlay.setAttribute('role', 'complementary');
    // 阻止面板上的点击冒泡到外层的"点击外部关闭"之类的逻辑（虽然没有，但
    // 防御一下）
    overlay.addEventListener('click', function (e) { e.stopPropagation(); });
    overlay.addEventListener('click', function (e) {
        if (!state.minimized) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        if (state.suppressNextMinimizedClick) {
            state.suppressNextMinimizedClick = false;
            if (state.minimizedClickSuppressTimer) {
                clearTimeout(state.minimizedClickSuppressTimer);
                state.minimizedClickSuppressTimer = null;
            }
            return;
        }
        _companionSetMinimized(state, false);
    }, true);

    // --- header ---
    const header = document.createElement('div');
    header.className = 'card-companion-header';
    header.title = _cardAssistT('character.aiCompanionDragHint', '拖动窗口');

    const avatar = document.createElement('div');
    avatar.className = 'card-companion-avatar';
    avatar.title = _cardAssistT('character.aiCompanionMinimize', '收起');
    avatar.setAttribute('role', 'button');
    avatar.setAttribute('tabindex', '0');
    avatar.setAttribute('aria-label', avatar.title);
    avatar.setAttribute('aria-expanded', 'true');
    avatar.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        _companionSetMinimized(state, !state.minimized);
    });
    avatar.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        e.stopPropagation();
        _companionSetMinimized(state, !state.minimized);
    });
    state.avatarToggleEl = avatar;
    const avatarImg = document.createElement('img');
    avatarImg.alt = state.devCatName;
    // 不加 ?t=Date.now() cache-bust：companion avatar 是个稳定的静态图，让
    // 浏览器 HTTP cache + 后端 ETag 接管 —— 多次开关 companion / 切换猫娘
    // 不用每次都拉一次图。如果未来要在 card-face 改了之后立刻刷新，应该用
    // 一个稳定的 cache key（如卡面文件 mtime / hash）而不是 Date.now()。
    avatarImg.src = '/api/characters/catgirl/' + encodeURIComponent(state.devCatName) + '/card-face';
    avatarImg.onerror = function () {
        avatarImg.remove();
        const fallback = document.createElement('div');
        fallback.className = 'card-companion-avatar-fallback';
        fallback.textContent = (state.devCatName || 'AI').slice(0, 2);
        avatar.appendChild(fallback);
    };
    avatar.appendChild(avatarImg);

    const titleWrap = document.createElement('div');
    titleWrap.className = 'card-companion-title';
    const nameEl = document.createElement('div');
    nameEl.className = 'card-companion-name';
    nameEl.textContent = state.devCatName;
    const subEl = document.createElement('div');
    subEl.className = 'card-companion-sub';
    subEl.textContent = _cardAssistT('character.aiCompanionSub', '设定捏人助手 · 暂代开发猫');
    titleWrap.appendChild(nameEl);
    titleWrap.appendChild(subEl);

    const headerPaw = document.createElement('img');
    headerPaw.className = 'card-companion-header-paw';
    headerPaw.src = '/static/icons/paw_ui.png';
    headerPaw.alt = '';
    headerPaw.draggable = false;

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'card-companion-close';
    closeBtn.title = _cardAssistT('character.aiCompanionClose', '关闭');
    closeBtn.innerHTML = '&times;';
    closeBtn.addEventListener('click', function () {
        _companionTeardown(state);
        _companionDestroy(state);
        if (window._cardCompanion === state) window._cardCompanion = null;
    });

    header.appendChild(avatar);
    header.appendChild(titleWrap);
    header.appendChild(headerPaw);
    header.appendChild(closeBtn);
    overlay.appendChild(header);
    _companionAttachWindowDrag(state, overlay, header);

    // --- thread ---
    const thread = document.createElement('div');
    thread.className = 'card-companion-thread';
    overlay.appendChild(thread);
    state.threadEl = thread;

    // --- input bar ---
    const inputBar = document.createElement('div');
    inputBar.className = 'card-companion-input-bar';

    const inputRow = document.createElement('div');
    inputRow.className = 'card-companion-input-row';

    const input = document.createElement('textarea');
    input.className = 'card-companion-input';
    input.rows = 1;
    input.placeholder = _cardAssistT('character.aiCompanionPlaceholder',
        '说点什么…（Enter 发送、Shift+Enter 换行）');
    input.addEventListener('input', function () { _cardAssistAutoResize(input); });
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            _companionSubmit(state);
        }
    });
    inputRow.appendChild(input);
    state.inputEl = input;

    const sendBtn = document.createElement('button');
    sendBtn.type = 'button';
    sendBtn.className = 'card-companion-send';
    sendBtn.textContent = _cardAssistT('character.aiCompanionSend', '发送');
    sendBtn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        _companionSubmit(state);
    });
    inputRow.appendChild(sendBtn);
    state.sendBtnEl = sendBtn;

    inputBar.appendChild(inputRow);

    // 一些预设的快捷指令，方便用户不用手动输入
    const quickRow = document.createElement('div');
    quickRow.className = 'card-companion-quick-row';
    const quickActions = [
        { label: _cardAssistT('character.aiCompanionQuickAdvice', '💡 给点建议'),
          send: _cardAssistT('character.aiCompanionQuickAdviceMsg',
                '看一下当前的角色设定，给我几条具体的改进建议吧。'),
          requireMode: 'chat', adviceOnly: true },
        { label: _cardAssistT('character.aiCompanionQuickCheck', '🔍 帮我审一下'),
          send: _cardAssistT('character.aiCompanionQuickCheckMsg',
                '审一下角色设定有没有矛盾、空泛或者重复的地方。'),
          requireMode: 'chat', adviceOnly: true },
        { label: _cardAssistT('character.aiCompanionQuickRegen', '🎲 重写整张卡'),
          send: _cardAssistT('character.aiCompanionQuickRegenMsg',
                '把所有可见字段都按原本的角色定位重新写一遍。'),
          requireMode: 'chat', fullRewrite: true },
    ];
    quickActions.forEach(function (qa) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'card-companion-quick-chip';
        chip.textContent = qa.label;
        chip.dataset.requireMode = qa.requireMode || '';
        chip.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (chip.disabled) return;
            // 「重写整张卡」用 locale 无关的 flag 标明全量重写意图，别让后端去正则匹配本地化
            // 文案——ja/ko/pt/ru/es/zh-TW 的「重写」措辞匹配不到，后端 _complete_full_rewrite_actions
            // 补全通路就不会触发，部分 action 列表会被当部分重写存下去（Codex #3333137718）。
            state._pendingFullRewrite = !!qa.fullRewrite;
            state._pendingAdviceOnly = !!qa.adviceOnly;
            input.value = qa.send;
            _companionSubmit(state);
        });
        quickRow.appendChild(chip);
    });
    inputBar.appendChild(quickRow);
    state.quickRowEl = quickRow;

    overlay.appendChild(inputBar);

    return overlay;
}

function _companionAttachWindowDrag(state, overlay, handle) {
    if (!overlay || !handle) return;
    let dragging = false;
    let startClientX = 0;
    let startClientY = 0;
    let startLeft = 0;
    let startTop = 0;
    let dragLeft = 0;
    let dragTop = 0;
    let activePointerId = null;
    let movedEnoughToDrag = false;

    function suppressMinimizedClickOnce() {
        state.suppressNextMinimizedClick = true;
        if (state.minimizedClickSuppressTimer) {
            clearTimeout(state.minimizedClickSuppressTimer);
        }
        state.minimizedClickSuppressTimer = setTimeout(function () {
            state.suppressNextMinimizedClick = false;
            state.minimizedClickSuppressTimer = null;
        }, 600);
    }

    function clampWindow(left, top) {
        const rect = overlay.getBoundingClientRect();
        const margin = 8;
        const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
        const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
        return {
            left: Math.min(Math.max(left, margin), maxLeft),
            top: Math.min(Math.max(top, margin), maxTop),
        };
    }

    function placeWindow(left, top) {
        const next = clampWindow(left, top);
        overlay.style.left = next.left + 'px';
        overlay.style.top = next.top + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
    }

    function onPointerMove(e) {
        if (!dragging) return;
        if (activePointerId !== null && e.pointerId !== undefined && e.pointerId !== activePointerId) return;
        const deltaX = e.clientX - startClientX;
        const deltaY = e.clientY - startClientY;
        if (state.minimized && !movedEnoughToDrag && Math.hypot(deltaX, deltaY) > 5) {
            movedEnoughToDrag = true;
        }
        e.preventDefault();
        const next = clampWindow(startLeft + deltaX, startTop + deltaY);
        dragLeft = next.left;
        dragTop = next.top;
        overlay.style.left = dragLeft + 'px';
        overlay.style.top = dragTop + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
    }

    function stopDrag() {
        if (!dragging) return;
        const wasMinimizedDrag = state.minimized && movedEnoughToDrag;
        dragging = false;
        activePointerId = null;
        overlay.style.left = dragLeft + 'px';
        overlay.style.top = dragTop + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
        overlay.style.transform = '';
        movedEnoughToDrag = false;
        overlay.classList.remove('card-companion-dragging');
        window.requestAnimationFrame(function () {
            if (!dragging) overlay.style.transition = '';
        });
        if (wasMinimizedDrag) {
            suppressMinimizedClickOnce();
        }
        window.removeEventListener('pointermove', onPointerMove);
        window.removeEventListener('pointerup', stopDrag);
        window.removeEventListener('pointercancel', stopDrag);
    }

    function onPointerDown(e) {
        if (e.button !== undefined && e.button !== 0) return;
        const interactive = e.target && e.target.closest && e.target.closest('button, a, input, textarea, select, [role="button"]');
        if (interactive && !state.minimized) return;
        const rect = overlay.getBoundingClientRect();
        dragging = true;
        activePointerId = e.pointerId;
        movedEnoughToDrag = false;
        startClientX = e.clientX;
        startClientY = e.clientY;
        startLeft = rect.left;
        startTop = rect.top;
        dragLeft = startLeft;
        dragTop = startTop;
        overlay.style.transition = 'none';
        overlay.style.left = startLeft + 'px';
        overlay.style.top = startTop + 'px';
        overlay.style.right = 'auto';
        overlay.style.bottom = 'auto';
        overlay.style.transform = '';
        overlay.classList.add('card-companion-dragging');
        if (handle.setPointerCapture && activePointerId !== null) {
            try { handle.setPointerCapture(activePointerId); } catch (_) {}
        }
        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', stopDrag);
        window.addEventListener('pointercancel', stopDrag);
        e.preventDefault();
    }

    function onResize() {
        const rect = overlay.getBoundingClientRect();
        placeWindow(rect.left, rect.top);
    }

    handle.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('resize', onResize);
    state.dragCleanup = function () {
        stopDrag();
        handle.removeEventListener('pointerdown', onPointerDown);
        window.removeEventListener('resize', onResize);
    };
}

function _companionGreet(state) {
    // 入口分流：
    //   - 空白卡（profile name 之外没有任何已填字段）→ 走多轮 Asking 流程
    //     （awaiting_description → clarify → generate → chat）
    //   - 已经有 tag 的卡 → 跳过 Asking，直接进入 chat（AI Design）。
    //     问候里列出现有字段做摘要，提示用 "💡/🔍" quick chip 让 AI 基于
    //     已有 tag 给建议；不预先消耗一次 LLM 调用。
    //
    // _cardAssistCollectCurrentFormData 已经用 _cardAssistIsReservedKey 过滤掉保留字段
    // （档案名/voice_id/lighting 等），所以只剩"真·角色设定字段"。空对象 → 空白卡。
    const existingData = _cardAssistCollectCurrentFormData(state.form);
    const filledKeys = Object.keys(existingData);
    if (!filledKeys.length) {
        _companionAppendAssistant(state,
            _cardAssistT('character.aiCompanionGreeting',
                '喵～我是设定捏人助手，先告诉我你想要一只什么样的猫娘呀？一句话描述就好，我会再问几个细节，然后帮你把整张卡写好喵。'));
        // mode 默认就是 awaiting_description（_companionCreate 里初始化的）
    } else {
        _companionEnterDesignMode(state, existingData, filledKeys);
    }
    _companionUpdateQuickAvailability(state);
}

// 已有 tag 的卡：跳过 Asking，直接进 chat。第一条 assistant 气泡把现有字段
// 列一下，让用户和 AI 都对得上号，再提示一下后续怎么交互。
function _companionEnterDesignMode(state, existingData, filledKeys) {
    state.mode = 'chat';
    // 太多字段时只列前 12 条，剩下的折叠成"…还有 N 项"，避免气泡铺一屏
    const MAX_LIST = 12;
    const head = filledKeys.slice(0, MAX_LIST);
    const lines = head.map(function (k) {
        return '• ' + k + '：' + _companionTruncate(String(existingData[k] || ''), 30);
    });
    if (filledKeys.length > MAX_LIST) {
        // i18next 风格的 {{n}} 占位符，跟 repo 里其它 60+ 处 {{var}} 一致。
        // _cardAssistT 把 vars 透传给 window.t(key, vars) → i18next 标准插值。
        // fallback 字符串里把数字直接内联，避免 i18next 没加载时 {{n}} 字面量
        // 漏出给用户。
        const remaining = filledKeys.length - MAX_LIST;
        lines.push('• ' + _cardAssistT('character.aiCompanionDesignMore',
            '…（还有 ' + remaining + ' 项）',
            { n: remaining }));
    }
    const greeting = _cardAssistT('character.aiCompanionDesignGreeting',
        '喵～我是设定捏人助手。看到你这只猫娘已经有点雏形啦，我先看看你已经填了什么：') +
        '\n\n' + lines.join('\n') +
        '\n\n' + _cardAssistT('character.aiCompanionDesignAsk',
        '想让我帮你做点啥呢？直接告诉我就行（比如「让她更傲娇一点」、「招牌台词换一句」），也可以点下面的「🔍 帮我审一下」让我整体看看~');
    _companionAppendAssistant(state, greeting);
    // 注：不预先塞 chatHistory，让 quick chip 触发的"帮我审一下"消息成为第一条
    // user 输入，对话上下文更自然；current_card / target_field_keys 在每次
    // /chat 调用前都重新收集，AI 永远看得到最新表单状态。
}

function _companionUpdateQuickAvailability(state) {
    // companion 已 teardown（state.closed）后绝不再碰表单控件：teardown 已无条件把未落库新卡
    // 的 Save 恢复了，迟到的 in-flight finally（如 _companionRunClarify 的 _companionSetBusy(false)）
    // 不能借这里按「未落库新卡 + 非 chat 模式」规则把它又禁回去——否则 companion 已销毁、详情
    // 面板还开着，用户再也点不动 Save（Codex #3333702549）。
    if (!state || state.closed) return;
    // 详情表单 Save 的禁用集中在这里（busy 变化 + 每次 mode 切换都会调到，是唯一同步点）。
    // 防竞态：**未落库新卡**在「打 LLM（busy）」或「澄清问答流程（asking_questions，答最后一题
    // 就触发生成）」时禁掉 Save——堵住「用户在草稿还没生成完的窗口里手动 Save 把新卡建出来」与
    // 生成竞态：那一下会用旧快照建卡，且若走 popup / 有卡面分支会 closeCatgirlPanel 把面板连同
    // AI 字段一起带走、事后救不回（Codex #3329022313 / #3329817833 / #3333137733）。
    // ⚠ awaiting_description（首启 + 澄清失败回退）**不禁**：此刻没有任何在途生成，禁 Save 是过宽
    // 的——零竞态收益，只会把用户困住：澄清失败（如没配 API）后想放弃 AI、手动建卡却点不动，得先
    // 关 companion 才行（Codex #3333683160）。手动 Save 真撞上后续生成的竞态，本就由 busy +
    // _companionTryAutoSave 的 wait/replay 兜底，不靠在这里禁 awaiting_description 的 Save。进入
    // chat 模式（草稿已落表单）同样放开，save-then-chat 由 _companionRunChat 的 dataset.submitting
    // 短路守住。已落库卡完全不禁。关 companion 时 _companionTeardown 会无条件恢复，不会卡死。
    try {
        if (state.form) {
            const sb = state.form.querySelector('#save-button');
            if (sb) {
                const unsavedNewCard = state.isNew === true && !state.form._autoCreated;
                sb.disabled = unsavedNewCard && (!!state.busy || state.mode === 'asking_questions');
            }
        }
    } catch (_) { /* form 可能已 detach，忽略 */ }
    if (!state.quickRowEl) return;
    state.quickRowEl.querySelectorAll('.card-companion-quick-chip').forEach(function (chip) {
        const req = chip.dataset.requireMode || '';
        const ok = !req || state.mode === req;
        chip.disabled = !ok || state.busy;
        chip.classList.toggle('card-companion-quick-chip-disabled', !ok);
    });
}

function _companionSetBusy(state, busy) {
    state.busy = !!busy;
    if (state.sendBtnEl) state.sendBtnEl.disabled = !!busy;
    if (state.inputEl) state.inputEl.disabled = !!busy;
    _companionUpdateQuickAvailability(state);  // 内含详情表单 Save 的禁用同步
}

async function _companionSubmit(state) {
    if (state.busy) return;
    const txt = (state.inputEl && state.inputEl.value ? state.inputEl.value : '').trim();
    if (!txt) return;
    state.inputEl.value = '';
    _cardAssistAutoResize(state.inputEl);
    await _companionHandleUserText(state, txt);
}

async function _companionHandleUserText(state, text) {
    _companionAppendUser(state, text);
    if (state.mode === 'awaiting_description') {
        state.description = text;
        await _companionRunClarify(state);
    } else if (state.mode === 'asking_questions') {
        // 用户没点 chip，而是在输入框敲了自定义答案 → 当作当前问题的回答
        const q = state.pendingQuestions[state.currentQuestionIdx];
        if (q) {
            state.collectedAnswers[q.id] = text;
            state.currentQuestionIdx++;
            _companionRenderNextQuestion(state);
        }
    } else {
        // chat 模式：进 /api/card-assist/chat
        state.chatHistory.push({ role: 'user', content: text });
        await _companionRunChat(state);
    }
}

async function _companionRunClarify(state) {
    _companionSetBusy(state, true);
    const typing = _companionAppendTyping(state);
    try {
        // form 找不到（用户切走了详情面板 / 关掉了）→ 早 return，不要白白吃一次
        // LLM 调用。同样的 short-circuit 在 _companionRunGenerate / _companionRunChat
        // 也加了，否则即使后端把 reply + actions 返回回来，前端 apply 阶段也只能
        // 弹「⚠ 角色表单不在屏幕上了」，钱白花、用户体验冲突。
        if (!_companionEnsureLiveForm(state)) {
            typing.remove();
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionFormGone',
                    '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。'));
            return;
        }
        const resp = await _cardAssistFetch('/api/card-assist/clarify', {
            description: state.description,
            current_card: _cardAssistCollectCurrentFormData(state.form),
            target_field_keys: _cardAssistCollectFieldKeys(state.form),
            locale: _cardAssistCurrentLocale(),
        });
        // 用户在 in-flight 期间关掉了 companion → 静默丢掉迟到的结果，绝不
        // 静默地把字段写进 form（form 还活着，但 user intent 已是"取消"）。
        if (state.closed) return;
        typing.remove();
        state.pendingQuestions = resp.questions || [];
        state.currentQuestionIdx = 0;
        if (!state.pendingQuestions.length) {
            // 没出问题就直接跳到 generate
            await _companionRunGenerate(state);
            return;
        }
        state.mode = 'asking_questions';
        _companionUpdateQuickAvailability(state);
        _companionRenderNextQuestion(state);
    } catch (err) {
        typing.remove();
        _companionAppendError(state, err);
        state.mode = 'awaiting_description';
        _companionUpdateQuickAvailability(state);
    } finally {
        _companionSetBusy(state, false);
    }
}

function _companionRenderNextQuestion(state) {
    if (state.currentQuestionIdx >= state.pendingQuestions.length) {
        _companionRunGenerate(state);
        return;
    }
    const q = state.pendingQuestions[state.currentQuestionIdx];
    const total = state.pendingQuestions.length;
    const prefix = '【' + (state.currentQuestionIdx + 1) + '/' + total + ' · ' + (q.header || '') + '】';
    // 捕获 chip 创建时的「这是第几题」snapshot；用户后续通过输入框回答 / 点更新
    // 的 chip 推进进度后，老 bubble 上的 chip 仍可见可点。stale chip 点击如果
    // 不防一手，会把旧答案塞进 collectedAnswers 并再次 ++currentQuestionIdx —
    // 跳过下一题、覆盖原本写好的答案。
    const ownIdx = state.currentQuestionIdx;
    const ownQid = q.id;
    _companionAppendAssistant(state, q.label, {
        prefix: prefix,
        chips: (q.options || []).map(function (opt) {
            return {
                label: opt,
                onClick: function () {
                    // 已经被其他途径推进过 → stale chip，no-op
                    if (state.currentQuestionIdx !== ownIdx) return;
                    state.collectedAnswers[ownQid] = opt;
                    _companionAppendUser(state, opt);
                    state.currentQuestionIdx++;
                    _companionRenderNextQuestion(state);
                }
            };
        }),
        allowCustom: q.allowCustom !== false,
        // 同 chip 的 ownIdx 防 race —— 自定义输入框按 Enter 也要先确认这条
        // bubble 还对应当前题，否则老 bubble 的输入会把答案塞给"现在的题"
        // 并再次 ++idx 跳过下一题。
        customSubmit: function (v) {
            if (state.currentQuestionIdx !== ownIdx) return;
            state.collectedAnswers[ownQid] = v;
            _companionAppendUser(state, v);
            state.currentQuestionIdx++;
            _companionRenderNextQuestion(state);
        },
    });
}

async function _companionRunGenerate(state) {
    _companionSetBusy(state, true);
    const typing = _companionAppendTyping(state,
        _cardAssistT('character.aiCompanionGenerating', '正在帮你写草稿…'));
    try {
        if (!_companionEnsureLiveForm(state)) {
            typing.remove();
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionFormGone',
                    '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。'));
            return;
        }
        const resp = await _cardAssistFetch('/api/card-assist/generate', {
            description: state.description,
            answers: state.collectedAnswers,
            current_card: _cardAssistCollectCurrentFormData(state.form),
            target_field_keys: _cardAssistCollectFieldKeys(state.form),
            locale: _cardAssistCurrentLocale(),
        });
        // closed-companion guard：用户在 in-flight 期间关掉了 companion → 绝
        // 不静默地往 form 写字段并 autoSave，关闭即取消。
        if (state.closed) return;
        typing.remove();
        const fields = resp.fields || {};
        const fieldKeys = Object.keys(fields);
        if (!fieldKeys.length) {
            _companionAppendAssistant(state,
                _cardAssistT('character.aiCompanionEmptyDraft',
                    '草稿空空的喵，我们再聊几句吧～'));
            state.mode = 'chat';
            _companionUpdateQuickAvailability(state);
            return;
        }
        // 直接应用到表单
        // ⚠ /generate 的 await 期间 state.form 可能被 rebuild（用户改名/保存先完成、旧
        // form detach）。这里 apply 前再 ensure 一次，把 draft 写进**当前活着**的 form；
        // 接不上就报 form-gone，绝不写进 detached DOM——否则字段写了个寂寞，紧接着的
        // _companionTryAutoSave rebind 到新 form 又没有 in-flight replay 通路，会把不带
        // draft 的表单存下去、助手却报「已应用」（Codex #3332998069）。
        if (!_companionEnsureLiveForm(state)) {
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionFormGone',
                    '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。'));
            return;
        }
        const applyRes = _cardAssistApplyToForm(state.form, fields, fieldKeys,
                                                 state.originalName, state.isNew);
        _companionRefreshFormSnapshot(state);
        await _companionTryAutoSave(state);
        // 用一条 assistant 气泡总结。区分 update vs create，让用户能立即看出
        // LLM 用的 key 是不是和表单里的字段对上号了 —— 如果 created 一大堆，
        // 说明 LLM 没听话用错 key，老字段没被覆盖。
        const lines = [];
        if (applyRes.updated.length) {
            lines.push(_cardAssistT('character.aiCompanionDraftUpdated', '✎ 改写') + '：' +
                applyRes.updated.map(function (k) { return k; }).join('、'));
        }
        if (applyRes.created.length) {
            lines.push(_cardAssistT('character.aiCompanionDraftCreated', '+ 新增') + '：' +
                applyRes.created.map(function (k) { return k; }).join('、'));
        }
        if (applyRes.skipped.length) {
            lines.push(_cardAssistT('character.aiCompanionDraftSkipped', '⤬ 跳过') + '：' +
                applyRes.skipped.map(function (k) { return k; }).join('、'));
        }
        const msg = _cardAssistT('character.aiCompanionDraftReady',
            '草稿写好啦，已经填进表单了喵～你随时改、随时跟我说要调啥都行：') +
            (lines.length ? '\n' + lines.join('\n') : '');
        _companionAppendAssistant(state, msg);
        // 进入聊天模式，把 description + 生成结果当上下文塞进 chatHistory。
        // seed 通过 i18n 走当前 locale —— 之前硬编码中文会让英文 locale 用户
        // 走完澄清问答后被 LLM 镜像成中文回复。
        state.mode = 'chat';
        const seedDescribe = _cardAssistT('character.aiCompanionSeedDescribe',
            'Generate a catgirl card based on this description: ');
        const seedGenerated = _cardAssistT('character.aiCompanionSeedGenerated',
            'Generated and filled into the form: ');
        state.chatHistory.push({ role: 'user',
            content: seedDescribe + state.description });
        state.chatHistory.push({ role: 'assistant',
            content: seedGenerated + JSON.stringify(fields) });
        _companionUpdateQuickAvailability(state);
    } catch (err) {
        typing.remove();
        _companionAppendError(state, err);
        state.mode = 'chat';
        _companionUpdateQuickAvailability(state);
    } finally {
        _companionSetBusy(state, false);
    }
}

async function _companionRunChat(state) {
    _companionSetBusy(state, true);
    // ⚠ full_rewrite 一次性消费：在任何 early-return（form-gone 等）**之前**就读出并清掉，
    // 否则这次点「重写整张卡」若撞上 form rebuild、没接上 live form 而提前 return，标记会
    // 残留、被下一条普通聊天消息误当成整卡重写（CodeRabbit #3333410664）。
    const fullRewrite = state._pendingFullRewrite === true;
    state._pendingFullRewrite = false;
    // 「给建议 / 帮我审一下」属于只读分析，不该顺手自动改表单；和 full_rewrite 一样做一次性消费，
    // 避免某次 advice 请求 early-return 后把标记泄漏到下一条普通聊天消息（本次回归）。
    const adviceOnly = state._pendingAdviceOnly === true;
    state._pendingAdviceOnly = false;
    const typing = _companionAppendTyping(state);
    try {
        if (!_companionEnsureLiveForm(state)) {
            typing.remove();
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionFormGone',
                    '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。'));
            return;
        }
        // 新卡首存正在飞行中（用户在 chat 模式点了 Save、又紧接着发消息）：别在它收尾前打
        // LLM / 改表单。否则 saveCatgirlFromPanel 已用「编辑前的快照」序列化，这次应用的 chat
        // 编辑会在「首存成功关面板 / 开卡面」分支里随面板一起没掉、_companionTryAutoSave 又
        // rebind 不上（Codex #3333457418）。短路并提示等保存收尾——用户消息还在 chatHistory 里，
        // 存好后再发一次即可（生成/问答流程下 Save 本就被禁，这里只兜 chat 模式这条路）。
        if (state.isNew && !state.form._autoCreated
                && state.form.dataset.submitting === 'true') {
            typing.remove();
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionSaveInProgress',
                    '⏳ 正在保存这张新卡，存好了我再帮你改喵～稍等一下再发一次吧。'));
            return;
        }
        // full_rewrite 已在函数开头一次性读出并清掉（见上），这里直接透传（Codex #3333137718）。
        const resp = await _cardAssistFetch('/api/card-assist/chat', {
            messages: state.chatHistory,
            current_card: _cardAssistCollectCurrentFormData(state.form),
            target_field_keys: _cardAssistCollectFieldKeys(state.form),
            dev_cat_name: state.devCatName,
            locale: _cardAssistCurrentLocale(),
            advice_only: adviceOnly,
            full_rewrite: fullRewrite,
        });
        // closed-companion guard：同 clarify/generate，关掉 companion 之后
        // 迟到的 reply + actions 都丢弃，不再静默改 form。
        if (state.closed) return;
        typing.remove();
        const reply = (resp.reply || '').trim();
        if (reply) {
            _companionAppendAssistant(state, reply);
            state.chatHistory.push({ role: 'assistant', content: reply });
        }
        const actions = Array.isArray(resp.actions) ? resp.actions : [];
        const summary = _companionApplyActions(state, actions);
        if (summary) {
            _companionAppendSystem(state, summary);
            _companionRefreshFormSnapshot(state);
            await _companionTryAutoSave(state);
        }
    } catch (err) {
        typing.remove();
        _companionAppendError(state, err);
    } finally {
        _companionSetBusy(state, false);
    }
}

function _companionApplyActions(state, actions) {
    if (!actions || !actions.length) return '';
    // form 可能已经被 buildCatgirlDetailForm 重新渲染过 → 尝试按 id 重新接上当前
    // 活着的同名表单。接不上才真的报错。
    if (!_companionEnsureLiveForm(state)) {
        return _cardAssistT('character.aiCompanionFormGone',
            '⚠ 角色表单不在屏幕上了，没法应用。请重新打开这只猫娘的详情面板再试。');
    }
    const updatedTags = [];
    const createdTags = [];
    const removedTags = [];
    const skippedTags = [];
    actions.forEach(function (a) {
        if (!a || !a.type || !a.field_key) return;
        if (_cardAssistIsReservedKey(a.field_key)) {
            skippedTags.push(a.field_key);
            return;
        }
        if (a.type === 'remove_field') {
            const ta = _findFieldTextareaByName(state.form, a.field_key);
            if (ta) {
                const wrapper = ta.closest('.field-row-wrapper');
                if (wrapper) wrapper.remove();
                removedTags.push(a.field_key);
            } else {
                skippedTags.push(a.field_key);
            }
            return;
        }
        // refine_field 严格要求字段已存在 —— LLM 偶尔会把目标字段名打错 / 大
        // 小写漂移（"Personality archetype" vs "Personality Archetype"），
        // 如果直接走 ApplyToForm 那条「找不到就创建」分支，会静默新建一条
        // 重复字段然后被 autoSave 持久化，留下脏 schema。把这种 typo case
        // 当 skipped 处理，让用户能看见。
        // add_field 反过来：本意就是新增，找不到才正常。
        if (a.type === 'refine_field') {
            if (!_findFieldTextareaByName(state.form, a.field_key)) {
                skippedTags.push(a.field_key);
                return;
            }
        }
        const single = {};
        single[a.field_key] = a.value;
        const res = _cardAssistApplyToForm(state.form, single, [a.field_key],
                                           state.originalName, state.isNew);
        res.updated.forEach(function (k) { updatedTags.push(k); });
        res.created.forEach(function (k) { createdTags.push(k); });
        res.skipped.forEach(function (k) { skippedTags.push(k); });
    });
    // 把刚 apply 出来的 4 类结果挂到 state，供 _companionTryAutoSave 在 wait→
    // rebuild 路径上 replay 时区分"该重写的字段"和"该重新删除的字段" —— snapshot
    // 自己只记得到值，不记得到"故意删除"这种 intent，必须显式传过去。
    state._lastApplyResult = {
        updated: updatedTags.slice(),
        created: createdTags.slice(),
        removed: removedTags.slice(),
        skipped: skippedTags.slice(),
    };
    // remove_field 这条分支直接删 DOM 行，不像 _cardAssistApplyToForm 末尾那样会把
    // Save / Cancel 亮出来。已保存卡这两个按钮默认 display:none（见 buildCatgirlDetailForm
    // 里 `if (!isNew) ...style.display = 'none'`），一旦后面 _companionTryAutoSave 失败、
    // 系统气泡提示「请手动点 Save 重试」时，按钮却还藏着 → 用户无从重试，被删字段在
    // reload 后复活。所以只要真发生了删除（纯 remove、没有 update/create 顺带亮按钮的场景）
    // 就把 Save / Cancel 显式亮出来，让那条 fallback 提示是可操作的。
    if (removedTags.length && state.form) {
        const sb = state.form.querySelector('#save-button');
        const cb = state.form.querySelector('#cancel-button');
        if (sb) sb.style.display = '';
        if (cb) cb.style.display = '';
    }
    const parts = [];
    if (updatedTags.length) parts.push('✎ ' + updatedTags.join(', '));
    if (createdTags.length) parts.push('+ ' + createdTags.join(', '));
    if (removedTags.length) parts.push('🗑 ' + removedTags.join(', '));
    if (skippedTags.length) parts.push('⤬ ' + skippedTags.join(', ') +
        '（' + _cardAssistT('character.aiCompanionSkipped', '未匹配/已保留') + '）');
    if (!parts.length) return '';
    return _cardAssistT('character.aiCompanionAppliedPrefix', '已应用：') + parts.join('  ·  ');
}

async function _companionTryAutoSave(state) {
    // 任何"对 form 动手"的入口前都先确保 form 还活着，否则保存的是个 detached
    // 表单 → FormData 拿到空值 / PUT 把字段全清光。
    if (!_companionEnsureLiveForm(state)) return;

    // ⚠ 关键：空白卡（state.isNew === true 且后端还没收到过 POST）下，绝对不能
    // 调 saveCatgirlFromPanel —— 它的"首次保存成功"分支会触发 closeCatgirlPanel()
    // （见 05-card-form-and-actions.js 中的详情面板逻辑），把整个详情面板收起来，用户跟
    // companion 聊到一半画面被甩走。
    //
    // 解决方案：新卡用户必须先**手动**点一次 Save 把卡建出来；之后 state.isNew
    // 翻成 false（或者 form._autoCreated 标记起来），auto-save 就可以接管走 PUT。
    // 手动 Save 后老的 saveCatgirlFromPanel 流程会 buildCatgirlDetailForm
    // 重新渲染表单，companion 会在下一次 _companionEnsureLiveForm 时自动跟过去。
    // ⚠ 例外：若此刻**已有一次手动 Save 在飞行中**（dataset.submitting === 'true'），说明用户
    // 正在把这张新卡建出来。这时绝不能直接 return —— 否则在「用户点 Save 时 /generate 还在飞」
    // 的竞态里，那次 Save 已经用「AI 写字段**之前**」的旧快照序列化好了，保存成功后会用旧快照
    // rebuild / 关面板，把 AI 刚写进去、用户已看到「已应用」的字段静默丢掉（Codex #3329022313）。
    // 所以这种情况要落到下面的 wait/replay：等那次 Save 收尾、把 AI 字段 replay 到 rebuild 出来
    // 的已保存卡表单上再存一遍。等待之后会再确认卡是否真落库（见 saveCatgirlFromPanel 前的二次 guard）。
    if (state.isNew && !state.form._autoCreated
            && state.form.dataset.submitting !== 'true') {
        // 一只新卡里只提示一次，避免 AI 改几次就刷几条 toast。
        if (!state._warnedNewCardSaveHint) {
            state._warnedNewCardSaveHint = true;
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionNewCardSaveHint',
                    '💡 新卡片要先点一下下面的 Save 才能让我自动保存，' +
                    '现在字段都已经写进表单了喵～'));
        }
        return;
    }
    if (typeof saveCatgirlFromPanel === 'function') {
        // saveCatgirlFromPanel 自己已经 toast 了错误（HTTP 非 2xx / success:false
        // / 网络异常都会经 showMessage(... ,'error')），但它**不抛**而是 `return false`。
        // 之前 catch{} 把 return value 丢了 —— 用户在 toast 之外看到 companion 的
        // 系统气泡仍然显示「✎ 已应用」，体感是「字段改了 + 已保存」，实际上后端
        // 拒绝了。这里读回 ok 值，false 时再补一条 system 错误气泡兜底。
        //
        // ⚠ saveCatgirlFromPanel 的 `return false` 有**两种**语义混用：
        //   (1) form.dataset.submitting === 'true' 的 debounce skip —— 表示有
        //       另一个 save 正在飞行中；
        //   (2) HTTP / validation / 网络异常的真·失败。
        //
        // 上一轮 (3bf0b171) 把 (1) 当失败误报；上一轮的 fix (722ada87) 简单粗暴
        // 改成 "in-flight 就 return"。但**那条捷径会丢数据**：
        //   T0: 用户手动 Save → saveCatgirlFromPanel 用 T0 的 form 数据起 POST
        //   T1: companion 把 AI 的新字段写进**同一个**form 的 textarea
        //   T2: companion tryAutoSave → 看到 dataset.submitting='true' → return
        //   T3: 后端 success → buildCatgirlDetailForm() 用 server 返回的数据
        //       (T0 的快照) 重建 form → companion 写进去的 T1 字段被抹掉
        //   T4: 既没存进后端、也不在 form 里、companion 也不知道要重试 —— 静默丢失
        //
        // 修法：不再 return，改成**等 in-flight save 收尾**（轮询 dataset.submitting
        // 翻 'false'），然后 _companionEnsureLiveForm 接上可能 rebuild 出来的新
        // form。如果 form 实例真的换了，比对 formWatchSnapshot（这次 tryAutoSave
        // 之前刚 refresh 过、代表 companion 期望的状态）和新 form 的实际字段值，
        // 把丢失/被抹掉的字段 replay 一遍，然后再调 saveCatgirlFromPanel 把
        // companion 的修改真正落盘。
        // ⚠ snapshot 和 lastApplyResult 必须在 wait/rebind 之前**defensive 拷贝**：
        // wait loop 里的每次 _companionEnsureLiveForm 在切到新 form 后会调
        // _companionAttachFormWatchers，那个函数会重写 state.formWatchSnapshot
        // = _cardAssistCollectCurrentFormData(<新 form>)，把"companion 期望状态"
        // 直接覆盖成"后端刚 rebuild 出来的旧值"。等下面的 diff 拿到时 snapshot
        // 已经和当前 form 一模一样、永远比不出差异、replay 哑火 → 数据丢失。
        // lastApplyResult 同理在某些重入路径上可能被覆盖，也先快照下来。
        const formBeforeWait = state.form;
        const expectedSnapshot = Object.assign({}, state.formWatchSnapshot || {});
        const lastApply = state._lastApplyResult || {};
        const expectedRemovals = (lastApply.removed || []).slice();
        const WAIT_TIMEOUT_MS = 8000;
        const POLL_MS = 100;
        let waited = 0;
        // ⚠ 必须盯 **formBeforeWait 自己** 的 dataset.submitting 清掉，而不是 state.form：
        // 那次手动保存的 PUT 一旦成功就会触发 buildCatgirlDetailForm 重建，旧 form 随之
        // detach、_companionEnsureLiveForm 会把 state.form 重绑到**新** form，新 form 的
        // submitting 从没被置位 → 若拿 state.form 当条件，循环会在重建一发生就**提前 break**。
        // 但此时手动保存还没收尾（saveCatgirlFromPanel 的 finally 清 submitting 之前，已保存卡
        // 分支还要 await loadCharacterData + 再跑一次 buildCatgirlDetailForm）。companion 抢在
        // 它收尾前 replay + 自存，就会被那次后续重建覆盖 → 退回这条 wait/replay 本来要消灭的
        // 静默丢失（Codex #3328951294 P1）。saveCatgirlFromPanel 的 finally 清的是原始 form
        // 引用（== formBeforeWait）的 submitting，detach 之后该 dataset 仍可读，所以这里安全。
        while (waited < WAIT_TIMEOUT_MS && formBeforeWait.dataset.submitting === 'true') {
            await new Promise(function (r) { setTimeout(r, POLL_MS); });
            waited += POLL_MS;
        }
        // 超时仍在 submitting 就放弃，避免 hang 死 —— 但**不能静默退出**：那次慢保存收尾时
        // 会用较旧的请求快照重建表单，把 companion 刚写进去的改动/删除覆盖掉，而用户只看到
        // 之前那条「已应用」气泡、误以为存好了（Codex #3328963563）。所以补一条失败气泡讲清楚，
        // 并尽量把 Save/Cancel 亮出来给一个手动兜底入口（若那次保存最终失败、没重建表单，这俩
        // 按钮就是真正的重试路径）。
        if (formBeforeWait.dataset.submitting === 'true') {
            console.warn('[card-companion] auto-save waited 8s for in-flight save, giving up');
            const tsb = formBeforeWait.querySelector('#save-button');
            const tcb = formBeforeWait.querySelector('#cancel-button');
            if (tsb) tsb.style.display = '';
            if (tcb) tcb.style.display = '';
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionAutoSaveFailed',
                    '⚠ 自动保存失败了喵——表单里的字段已经写好，请看下弹出的错误提示再手动点 Save 重试。'));
            return;
        }
        // 手动保存确认收尾后，再接上它可能 rebuild 出来的新 form（接不上 = 面板没了 → 放弃）
        if (!_companionEnsureLiveForm(state)) return;
        // form 实例换过 → 用 BEFORE-WAIT 那份 snapshot + removed 名单把 companion
        // 期望的状态重新灌进新 form。两条独立通道：
        //   1) 字段值 replay：跳过那些「companion 故意删除」的 key，避免把刚删
        //      掉的字段又写回去；只 apply 真正有 diff 的 key（避免误覆盖 server）。
        //   2) 删除 replay：对 server rebuild 后又冒出来的 removed 字段重新执行
        //      DOM 删除。snapshot 自己不携带"我删过它"的信息，所以必须靠
        //      expectedRemovals 显式记下。
        if (state.form !== formBeforeWait) {
            const removalSet = expectedRemovals.length
                ? new Set(expectedRemovals) : null;
            if (Object.keys(expectedSnapshot).length) {
                const replayValues = {};
                const replayKeys = [];
                Object.keys(expectedSnapshot).forEach(function (k) {
                    if (removalSet && removalSet.has(k)) return;
                    const ta = _findFieldTextareaByName(state.form, k);
                    const cur = ((ta && ta.value) || '').trim();
                    const want = (expectedSnapshot[k] || '').trim();
                    if (want && cur !== want) {
                        replayValues[k] = expectedSnapshot[k];
                        replayKeys.push(k);
                    }
                });
                if (replayKeys.length) {
                    _cardAssistApplyToForm(state.form, replayValues, replayKeys,
                        state.originalName, state.isNew);
                }
            }
            expectedRemovals.forEach(function (k) {
                const ta = _findFieldTextareaByName(state.form, k);
                if (!ta) return;
                const wrapper = ta.closest('.field-row-wrapper');
                if (wrapper) wrapper.remove();
            });
            // replay 里若含「删除」：rebuilt 的已保存卡表单 Save/Cancel 默认是藏着的
            //（手动 save 成功后又被隐藏）。若只 replay 了删除、没 replay 任何字段值，
            // 上面的 _cardAssistApplyToForm 不会被调到、不会顺带亮按钮；紧接着的
            // autosave 一旦失败、提示用户「手动点 Save 重试」时按钮却不可见 → 删除丢失
            //（Codex #3328942158）。跟直连 remove_field 路径一样，这里把 Save/Cancel 亮出。
            if (expectedRemovals.length) {
                const rsb = state.form.querySelector('#save-button');
                const rcb = state.form.querySelector('#cancel-button');
                if (rsb) rsb.style.display = '';
                if (rcb) rcb.style.display = '';
            }
            // 重新刷一遍 watch snapshot 把 "我们刚 replay 完的状态" 当成新的
            // baseline，避免后面 form-watch listener 把 replay 误判成"用户手改"
            // 弹一堆系统气泡。
            _companionRefreshFormSnapshot(state);
        }
        // 一次性消耗掉 lastApplyResult，避免下次 tryAutoSave 误用过期数据
        state._lastApplyResult = null;
        // 二次 guard（配合上面「新卡 + in-flight save 时不 return」的放行）：等那次手动 Save
        // 收尾后，若卡**仍未落库**（state 还是 isNew 且非 _autoCreated，说明那次 Save 失败 /
        // 没建成），绝不能调 saveCatgirlFromPanel —— 它会 POST 建卡 + closeCatgirlPanel 甩走
        // 面板，正是新卡 guard 要避免的。这种情况 AI 字段已 replay 在表单里，提示用户手动 Save 即可。
        if (state.isNew && !state.form._autoCreated) {
            if (!state._warnedNewCardSaveHint) {
                state._warnedNewCardSaveHint = true;
                _companionAppendSystem(state,
                    _cardAssistT('character.aiCompanionNewCardSaveHint',
                        '💡 新卡片要先点一下下面的 Save 才能让我自动保存，' +
                        '现在字段都已经写进表单了喵～'));
            }
            return;
        }
        let ok = true;
        try {
            // _autoCreated 的卡其实已经 POST 到后端了 → 对它来说这次只是 PUT 更新。但若仍
            // 把 isNew=true 传进去，saveCatgirlFromPanel 的**保存成功后 UI 分支**会按原始
            // isNew 去 closeCatgirlPanel / 开卡面制作弹窗，把正在进行的 companion 聊天打断
            //（Codex #3328942156）。它的请求方法本就按内部 effectiveIsNew(=isNew && !_autoCreated)
            // 走 PUT、不受这里影响；这里按"是否已落库"把 _autoCreated 当作已保存卡传进去，
            // 让 post-save 走原地刷新而不是甩走面板。走到这一步：要么本就是已保存卡 / _autoCreated，
            // 要么是「新卡 + in-flight save」竞态等完后卡已落库（未落库的已被上面的二次 guard 拦掉）。
            const effectiveIsNew = state.isNew && !state.form._autoCreated;
            const ret = await saveCatgirlFromPanel(state.form, state.originalName, effectiveIsNew);
            if (ret === false) ok = false;
        } catch (e) {
            console.warn('[card-companion] auto-save after action failed:', e);
            ok = false;
        }
        if (!ok) {
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionAutoSaveFailed',
                    '⚠ 自动保存失败了喵——表单里的字段已经写好，请看下弹出的错误提示再手动点 Save 重试。'));
        }
    }
}

// ========== 表单监视：用户在面板外手改字段时给个 system 提示 ==========

function _companionAttachFormWatchers(state) {
    if (!state.form) return;
    // 如果之前 attach 过、但 form 实例换了，先把旧的 listener 清掉，避免旧表单
    // 还在 DOM 树里时双触发。
    if (state.formWatchHandlers && state.formWatchHandlers.length) {
        state.formWatchHandlers.forEach(function (pair) {
            try { state.form.removeEventListener(pair[0], pair[1]); } catch (_) {}
        });
        state.formWatchHandlers = [];
    }
    state.formWatchSnapshot = _cardAssistCollectCurrentFormData(state.form);
    const inputHandler = function (e) {
        const t = e.target;
        if (!t || !t.name) return;
        if (t.tagName !== 'TEXTAREA' && t.tagName !== 'INPUT') return;
        if (_cardAssistIsReservedKey(t.name)) return;
        // 防抖：用户停手 600ms 才看是不是真的改了
        clearTimeout(t._companionWatchTimer);
        t._companionWatchTimer = setTimeout(function () {
            const newVal = (t.value || '').trim();
            const oldVal = (state.formWatchSnapshot[t.name] || '').trim();
            if (newVal === oldVal) return;  // companion 自己改的 → snapshot 已同步，会在这里跳过
            state.formWatchSnapshot[t.name] = newVal;
            _companionAppendSystem(state,
                _cardAssistT('character.aiCompanionUserEdited', '你刚改了') +
                ' 「' + t.name + '」' +
                (oldVal
                    ? '：' + _companionTruncate(oldVal, 20) + ' → ' + _companionTruncate(newVal, 20)
                    : '：' + _companionTruncate(newVal, 40)));
        }, 600);
    };
    state.form.addEventListener('input', inputHandler);
    state.formWatchHandlers.push(['input', inputHandler]);
}

function _companionRefreshFormSnapshot(state) {
    if (!state.form) return;
    state.formWatchSnapshot = _cardAssistCollectCurrentFormData(state.form);
}

// 切换猫娘 / 关掉再开详情面板 / 重命名 / 任何让 buildCatgirlDetailForm 重新跑过
// 的操作，都会让 state.form 指向一个 detach 掉的旧 DOM 实例。companion 自己
// 是侧栏，活得比 form 长。每次要"动 form"之前调一下这个 helper，会按
//   1) state.form 还在 DOM 里 → 直接用
//   2) document.getElementById('catgirl-form-' + originalName) → 重新绑定 + 重挂监听
//   3) 都没有 → 返回 false，调用方据此让 companion 给个明确提示
// 实现这套自动跟随，用户不用关掉 companion 再开一次。
function _companionEnsureLiveForm(state) {
    if (!state) return false;
    if (state.form && state.form.isConnected) return true;
    // 找 live form 的两条路（按顺序回退）：
    //   1) 有 originalName → 按 `catgirl-form-${originalName}` 精确查（已保存卡
    //      常态：切猫娘 / 关再开）
    //   2) 上一步失败 / originalName 为空 → 用 DOM 选择器在当前 catgirl panel 里
    //      找那个唯一 form。详情面板同时只能有一个 form，所以选择器命中唯一。
    //      这一支专门覆盖两个场景：
    //        a. 「空白新卡 → 填档案名 → 手动 Save → form id 从 catgirl-form-new
    //           变成 catgirl-form-<actualName>」（originalName='', id 漂移）
    //        b. **重命名**：用户在 companion 开着的情况下改了档案名，
    //           saveCatgirlFromPanel 用新名 rebuild 表单，旧 id 找不到，但新
    //           form 已经挂在 panel 里、companion 应该顺势跟过去
    //      然后下面的 sync 逻辑会把 state.originalName 回填成新 form 的真实名字。
    let liveForm = null;
    if (state.originalName) {
        liveForm = document.getElementById('catgirl-form-' + state.originalName);
    }
    if (!liveForm) {
        // path-2 选择器回退：在当前 catgirl panel 里找那个唯一 form。
        // 安全性（Codex #3328901017）：打开/切换到「别的卡」必须先 closeCatgirlPanel
        //（openCatgirlPanel 顶部 _catgirlPanelOpen 互斥），而 closeCatgirlPanel 会直接
        // teardown+destroy 掉 companion。所以能走到这一支时 companion 必然还活着 = 详情
        // 面板从未被关过 = 当前可见的唯一 form 一定是「同一张卡」的 in-place rebuild
        //（改档案名字段后 saveCatgirlFromPanel 重建、新卡首存 popup 被拦走
        // rebuildSavedCatgirlPanel），绝不会抓到另一张卡 → 不会误绑。
        liveForm = document.querySelector('.catgirl-panel-right form[id^="catgirl-form-"]');
    }
    if (!liveForm) return false;
    // 拿到了"现行"的同名表单。把 state.form 换过去并重挂 watcher。
    // 注意：旧 form 上的 listener 已随 DOM 卸载消失，无需手动 remove —— 但
    // _companionAttachFormWatchers 内部已经做了 defensive removeEventListener。
    state.form = liveForm;
    // ⚠ 必须同步 isNew / originalName ——`buildCatgirlDetailForm` 重建表单时
    // 会把 `_isNew` / `_catgirlName` 设到最新值（比如用户首次保存新卡后表单
    // 以 isNew=false 重建、或者用户做了重命名）。companion 这边如果继续用
    // 创建时的旧 `state.isNew`：
    //   - `_companionTryAutoSave` 里的 `if (state.isNew && !state.form._autoCreated)`
    //     永久命中 → 自动保存永远 bail，新卡保存提示反复弹
    //   - 一旦走到 saveCatgirlFromPanel，`effectiveIsNew=true` 会触发 POST 而
    //     不是 PUT，造成同名 catgirl 409 / 重复
    const liveIsNew = liveForm._isNew === true;
    const wasNew = state.isNew;
    state.isNew = liveIsNew;
    if (liveForm._catgirlName) state.originalName = liveForm._catgirlName;
    // 新卡变成已保存卡的瞬间清掉 "先点 Save" 一次性提示标记；万一未来某次状态
    // 再翻回新卡（实操路径几乎不可能但便宜），提示能再次出现。
    if (wasNew && !liveIsNew) state._warnedNewCardSaveHint = false;
    state.formWatchHandlers = [];
    _companionAttachFormWatchers(state);
    return true;
}

function _companionTruncate(s, n) {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n) + '…' : s;
}

function _cardAssistNormalizeDisplayText(text) {
    let s = String(text == null ? '' : text);
    // Companion bubbles render plain text, so stray markdown markers look broken.
    // Strip the common emphasis markers and normalize markdown bullet prefixes.
    s = s.replace(/^\s{0,3}#{1,6}\s+/gm, '');
    s = s.replace(/^\s*[*-]\s+/gm, '• ');
    s = s.replace(/\*\*([^*]+)\*\*/g, '$1');
    s = s.replace(/__([^_]+)__/g, '$1');
    s = s.replace(/(^|[^\w])\*([^*\n]+)\*(?=[^\w]|$)/g, '$1$2');
    s = s.replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, '$1$2');
    return s;
}

// ========== Bubble 工厂 ==========

function _companionScrollToBottom(state) {
    if (!state.threadEl) return;
    // 用 microtask 让 DOM commit 完再算 scrollHeight，否则会拿到 stale 值
    setTimeout(function () {
        state.threadEl.scrollTop = state.threadEl.scrollHeight;
    }, 0);
}

function _companionAppendAssistant(state, text, opts) {
    opts = opts || {};
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble card-companion-bubble-assistant';

    if (opts.prefix) {
        const tag = document.createElement('div');
        tag.className = 'card-companion-bubble-prefix';
        tag.textContent = opts.prefix;
        bubble.appendChild(tag);
    }

    const body = document.createElement('div');
    body.className = 'card-companion-bubble-body';
    body.textContent = _cardAssistNormalizeDisplayText(text);
    body.style.whiteSpace = 'pre-wrap';
    bubble.appendChild(body);

    if (opts.chips && opts.chips.length) {
        const row = document.createElement('div');
        row.className = 'card-companion-bubble-chips';
        opts.chips.forEach(function (c) {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'card-companion-chip';
            chip.textContent = c.label;
            chip.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                // 一次性 chip：点完整行禁用
                row.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
                const customInput = bubble.querySelector('.card-companion-bubble-custom-input');
                if (customInput) customInput.disabled = true;
                if (typeof c.onClick === 'function') c.onClick();
            });
            row.appendChild(chip);
        });
        bubble.appendChild(row);
    }

    if (opts.allowCustom) {
        const customRow = document.createElement('div');
        customRow.className = 'card-companion-bubble-custom';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'card-companion-bubble-custom-input';
        input.placeholder = _cardAssistT('character.aiCompanionInlineCustom', '或者自己填一个…');
        input.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            const v = (input.value || '').trim();
            if (!v) return;
            input.disabled = true;
            bubble.querySelectorAll('.card-companion-chip').forEach(function (c) { c.disabled = true; });
            // opts.customSubmit 由调用方提供时优先用它（_companionRenderNextQuestion
            // 走这一支以便施加 stale-bubble ownIdx 防 race —— 否则用户在老 bubble
            // 的自定义输入框里按 Enter 会走通用 _companionHandleUserText，把答案
            // 塞给当前题、再 ++ idx 跳过下一题。chip 那一支也是这个思路）。
            if (typeof opts.customSubmit === 'function') {
                opts.customSubmit(v);
            } else {
                _companionHandleUserText(state, v);
            }
        });
        customRow.appendChild(input);
        bubble.appendChild(customRow);
    }

    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

function _companionAppendUser(state, text) {
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble card-companion-bubble-user';
    const body = document.createElement('div');
    body.className = 'card-companion-bubble-body';
    body.textContent = text || '';
    body.style.whiteSpace = 'pre-wrap';
    bubble.appendChild(body);
    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

function _companionAppendSystem(state, text) {
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble-system';
    bubble.textContent = _cardAssistNormalizeDisplayText(text);
    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

function _companionAppendTyping(state, label) {
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble card-companion-bubble-assistant card-companion-typing';
    const body = document.createElement('div');
    body.className = 'card-companion-bubble-body';
    body.innerHTML = (label ? _cardAssistEscapeHtml(label) + ' ' : '') +
        '<span class="card-companion-typing-dot"></span>' +
        '<span class="card-companion-typing-dot"></span>' +
        '<span class="card-companion-typing-dot"></span>';
    bubble.appendChild(body);
    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

function _companionAppendError(state, err) {
    let msg = (err && err.message) || String(err || '');
    if (err && err.code === 'assist_api_not_configured') {
        msg = _cardAssistT('character.aiAssistApiMissing',
            '辅助 API 尚未配置。请在「API Key 设置」里完成配置后再试。');
    }
    const bubble = document.createElement('div');
    bubble.className = 'card-companion-bubble-system card-companion-error';
    bubble.textContent = '⚠ ' + msg;
    state.threadEl.appendChild(bubble);
    _companionScrollToBottom(state);
    return bubble;
}

// 在表单里查找名为 `key` 的字段 textarea。
//   1. 精确 [name=key] 命中
//   2. trim 后命中（应对 characters.json 里手抖留下的首尾空格）
//   3. 全表扫描 + trimmed 小写对比（应对 zh/en locale 漂移、大小写不一致）
// 命中返回 textarea，未命中返回 null —— 调用方据此决定 update vs create。
function _findFieldTextareaByName(form, key) {
    if (!form || !key) return null;
    const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s);
    let ta = form.querySelector('textarea[name="' + esc(key) + '"]');
    if (ta) return ta;
    const trimmed = String(key).trim();
    if (trimmed && trimmed !== key) {
        ta = form.querySelector('textarea[name="' + esc(trimmed) + '"]');
        if (ta) return ta;
    }
    const lower = trimmed.toLowerCase();
    const all = form.querySelectorAll('textarea[name]');
    for (let i = 0; i < all.length; i++) {
        const el = all[i];
        const n = (el.getAttribute('name') || '').trim();
        if (n === trimmed) return el;
        if (n.toLowerCase() === lower) return el;
    }
    return null;
}

// 给一个 field-row-wrapper 闪一下绿色渐变 + 自动滚到视野中央，让用户能立刻
// 跟上 companion 改了哪一行。
// opts:
//   scrollIntoView (bool, default false)
//     true 时把 row 平滑滚到容器中央。批量 apply 时只对"第一行"传 true，
//     避免视野被多次 yank 来 yank 去
//   focusTextarea (HTMLElement | null, default null)
//     传一个 textarea 进来，闪烁结束前若用户没在其它输入框打字，会顺手把光标
//     落在它上面 —— 对"AI 改了这个字段、你想接着调"场景挺顺手；
//     用户正在 companion 输入框里打字的话不抢焦点。
function _cardAssistFlashRow(wrapperEl, opts) {
    if (!wrapperEl) return;
    opts = opts || {};
    wrapperEl.classList.remove('card-assist-row-flash');
    // force reflow so re-applying class re-triggers the animation
    void wrapperEl.offsetWidth;
    wrapperEl.classList.add('card-assist-row-flash');
    setTimeout(function () {
        wrapperEl.classList.remove('card-assist-row-flash');
    }, 1500);

    if (opts.scrollIntoView) {
        // 用 microtask 让 DOM 把新插入的 row 算进 layout，再 scrollIntoView 才
        // 不会拿到 0 高度的 stale rect。
        setTimeout(function () {
            try {
                wrapperEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            } catch (_) {
                // 老浏览器 fallback：没有 smooth 也照样要滚到位
                wrapperEl.scrollIntoView();
            }
        }, 0);
    }

    if (opts.focusTextarea) {
        // 用户正在 companion 输入框 / 其它表单输入框里打字 → 不抢焦点
        const active = document.activeElement;
        const userIsTypingElsewhere = active &&
            (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT') &&
            active !== opts.focusTextarea;
        if (!userIsTypingElsewhere) {
            try { opts.focusTextarea.focus({ preventScroll: true }); }
            catch (_) { try { opts.focusTextarea.focus(); } catch (__) {} }
        }
    }
}

// 将一组 generated[key] = value 写到表单。返回 {updated: [key,...], created: [key,...]}
// 让上层能区分"改了已有"和"插了新行"，给用户更准确的反馈。
function _cardAssistApplyToForm(form, generated, selectedKeys, originalName, isNew) {
    const result = { updated: [], created: [], skipped: [] };
    if (!form || !selectedKeys || !selectedKeys.length) return result;
    // 防御：form 已经从 DOM 卸载（用户切了猫娘 / 关掉了详情面板）的情况下，
    // 写值不会有任何视觉效果。给上层一个明确信号。
    if (!form.isConnected) {
        selectedKeys.forEach((k) => result.skipped.push(k));
        return result;
    }
    const addFieldArea = form.querySelector('.add-field-area');
    // 批量应用时只对"第一个真正落到 form 上的字段"做 scrollIntoView，避免一次
    // generate 写 9 个字段把视野往下连甩 9 次。
    let didScroll = false;
    selectedKeys.forEach(function (key) {
        if (!key || _cardAssistIsReservedKey(key)) {
            result.skipped.push(key);
            return;
        }
        const value = String(generated[key] == null ? '' : generated[key]);
        const textarea = _findFieldTextareaByName(form, key);
        if (textarea) {
            textarea.value = value;
            if (typeof _panelRequestTextareaAutoResize === 'function') {
                _panelRequestTextareaAutoResize(textarea);
            }
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            textarea.dispatchEvent(new Event('change', { bubbles: true }));
            const row = textarea.closest('.field-row-wrapper') || textarea.parentNode;
            _cardAssistFlashRow(row, {
                scrollIntoView: !didScroll,
                focusTextarea: didScroll ? null : textarea,
            });
            didScroll = true;
            result.updated.push(textarea.getAttribute('name') || key);
            return;
        }
        // 字段不存在 → 复用「新增设定」分支的 DOM 构造
        const wrapper = document.createElement('div');
        wrapper.className = 'field-row-wrapper custom-row';

        const labelEl = document.createElement('label');
        if (typeof _panelSetFieldLabel === 'function') {
            _panelSetFieldLabel(labelEl, key);
        } else {
            labelEl.textContent = key;
        }
        wrapper.appendChild(labelEl);

        const fr = document.createElement('div');
        fr.className = 'field-row';
        const textareaEl = document.createElement('textarea');
        textareaEl.name = key;
        textareaEl.rows = 1;
        textareaEl.value = value;
        fr.appendChild(textareaEl);
        wrapper.appendChild(fr);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn sm delete';
        const delLabel = (window.t && typeof window.t === 'function')
            ? window.t('character.deleteField')
            : '删除设定';
        delBtn.innerHTML = '<img src="/static/icons/delete.png" alt="" class="delete-icon"> <span data-i18n="character.deleteField">' + delLabel + '</span>';
        delBtn.addEventListener('click', function () {
            wrapper.remove();
            // 镜像普通自定义字段删除路径（见 buildCatgirlDetailForm 里 ~5041）：删掉 AI
            // 新建的字段后也要把 Save / Cancel 亮出来。否则已保存卡上这次删除既不触发
            // autosave、也没有可见的手动保存入口，reload 后字段复活（Codex #3328901018）。
            const sBtn = form.querySelector('#save-button');
            const cBtn = form.querySelector('#cancel-button');
            if (sBtn) sBtn.style.display = '';
            if (cBtn) cBtn.style.display = '';
        });
        wrapper.appendChild(delBtn);

        if (addFieldArea && addFieldArea.parentNode === form) {
            form.insertBefore(wrapper, addFieldArea);
        } else {
            form.appendChild(wrapper);
        }
        if (typeof _panelAttachTextareaAutoResize === 'function') {
            _panelAttachTextareaAutoResize(textareaEl);
        }
        if (typeof _panelRequestTextareaAutoResize === 'function') {
            _panelRequestTextareaAutoResize(textareaEl);
        }
        if (!isNew && originalName && typeof panelAttachAutoSaveListener === 'function') {
            panelAttachAutoSaveListener(textareaEl, originalName);
        }
        textareaEl.dispatchEvent(new Event('input', { bubbles: true }));
        _cardAssistFlashRow(wrapper, {
            scrollIntoView: !didScroll,
            focusTextarea: didScroll ? null : textareaEl,
        });
        didScroll = true;
        result.created.push(key);
    });
    // 让用户看到 Save / Cancel
    const sb = form.querySelector('#save-button');
    const cb = form.querySelector('#cancel-button');
    if (sb) sb.style.display = '';
    if (cb) cb.style.display = '';
    return result;
}

function _cardAssistAutoResize(textarea) {
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = (textarea.scrollHeight + 2) + 'px';
}

function _cardAssistEscapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ===================== 云存档同步与生命周期 =====================

function getCurrentUiLanguage() {
    if (window.i18n && typeof window.i18n.language === 'string' && window.i18n.language.trim()) {
        return window.i18n.language.trim();
    }
    const saved = localStorage.getItem('i18nextLng');
    if (typeof saved === 'string' && saved.trim()) return saved.trim();
    return '';
}

function hasUnsavedNewCatgirlDraft() {
    const form = document.getElementById('catgirl-form-new');
    if (!form) return false;
    const nameInput = form.querySelector('input[name="档案名"]');
    return !!(nameInput && nameInput.value && nameInput.value.trim());
}
