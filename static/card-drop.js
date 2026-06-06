/* 对话掉落卡片 —— 前端开卡演出（原生 JS，无依赖）。
 *
 * 入口：后端 WS 广播 {type:'card_drop_available', lanlan_name} → app-websocket.js
 *   onmessage 分发到 window.handleCardDropAvailable(payload)。
 * 流程：叮一声 + toast → 居中模态 5选1（/api/card-drop/candidates）→ 凝结
 *   （/api/card-drop/draw）→ 按稀有度揭晓演出（音效/特效/编号）。
 * 调试：window.openCardDrop('test') 直接开；或 POST /api/card-drop/test-trigger 走 WS 路径。
 */
(function () {
  'use strict';

  var SOUND_COIN = '/static/sounds/avatar-tools/coin-drop.mp3';
  var PLACEHOLDER_ART = '/static/icons/default_character_card.png';

  // 各稀有度的演出强度
  var RAR = {
    N:   { vol: 0.55, particles: 8,  multi: 1, flash: false, shake: false },
    R:   { vol: 0.72, particles: 16, multi: 2, flash: false, shake: false },
    SR:  { vol: 0.88, particles: 28, multi: 3, flash: true,  shake: false },
    SSR: { vol: 1.0,  particles: 46, multi: 3, flash: true,  shake: true  },
    UR:  { vol: 1.0,  particles: 64, multi: 4, flash: true,  shake: true  }
  };

  var els = null;
  var state = { lanlan: '', candidates: [], picked: -1, busy: false };

  // ---- utils ----
  function playSound(url, vol) {
    try { var a = new Audio(url); a.volume = (vol == null ? 0.9 : vol); a.play().catch(function () {}); }
    catch (e) {}
  }
  function toast(msg, dur) {
    if (typeof window.showStatusToast === 'function') window.showStatusToast(msg, dur || 3500, { priority: 60 });
  }
  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) {
          var detail = (body && body.detail) || ('HTTP ' + r.status);
          var err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
          err.status = r.status; throw err;
        }
        return body;
      });
    });
  }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  // ---- DOM (built once) ----
  function ensureDom() {
    if (els) return els;
    var backdrop = el('div', 'cd-backdrop');
    var modal = el('div', 'cd-modal');
    var close = el('button', 'cd-close', '×');
    close.setAttribute('aria-label', 'close');
    var head = el('div', 'cd-head');
    var body = el('div', 'cd-body');
    modal.appendChild(close); modal.appendChild(head); modal.appendChild(body);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    close.addEventListener('click', closeModal);
    backdrop.addEventListener('click', function (e) { if (e.target === backdrop && !state.busy) closeModal(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && backdrop.classList.contains('cd-open') && !state.busy) closeModal();
    });

    els = { backdrop: backdrop, modal: modal, head: head, body: body };
    return els;
  }

  function openShell() {
    ensureDom();
    els.backdrop.classList.add('cd-open');
  }
  function openModal() { openShell(); loadAuth(); loadCandidates(); }   // 开卡（5选1）
  function closeModal() {
    if (!els) return;
    els.backdrop.classList.remove('cd-open');
    state.candidates = []; state.picked = -1; state.busy = false;
    state.steamPolling = false;
  }

  function setHead(title, sub) {
    els.head.innerHTML = '';
    els.head.appendChild(el('h3', null, title));
    if (sub) els.head.appendChild(el('p', null, sub));
  }

  // ---- step: 5选1 ----
  function loadCandidates() {
    state.busy = true; state.picked = -1;
    setHead('✨ 叮～掉落了一张卡', '选一段记忆，凝结成你的卡');
    els.body.innerHTML = '<div class="cd-msg">正在抽取记忆…</div>';
    api('/api/card-drop/candidates?size=5&lanlan_name=' + encodeURIComponent(state.lanlan))
      .then(function (d) { state.busy = false; state.candidates = (d && d.candidates) || []; renderPicker(); })
      .catch(function (e) { state.busy = false; renderError(e); });
  }

  function renderPicker() {
    els.body.innerHTML = '';
    if (!state.candidates.length) {
      els.body.innerHTML = '<div class="cd-msg">还没有可用的记忆。去和 TA 多聊聊再来铸卡 ✦</div>';
      return;
    }
    var grid = el('div', 'cd-grid');
    state.candidates.forEach(function (c, i) {
      var card = el('button', 'cd-cand');
      card.appendChild(el('div', 'cd-cand-text', escapeHtml(c.text || '')));
      var tag = el('span', 'cd-cand-tag' + (c.is_preset ? ' cd-preset' : ''),
        c.is_preset ? '预设记忆' : '你的记忆');
      card.appendChild(tag);
      card.addEventListener('click', function () { pick(i); });
      if (i === state.picked) card.classList.add('cd-picked');
      grid.appendChild(card);
    });
    els.body.appendChild(grid);

    var actions = el('div', 'cd-actions');
    var reroll = el('button', 'cd-btn cd-btn-ghost', '换一批');
    reroll.addEventListener('click', function () { if (!state.busy) loadCandidates(); });
    var forge = el('button', 'cd-btn cd-btn-primary', '凝结成卡');
    forge.disabled = state.picked < 0;
    forge.addEventListener('click', function () { if (!state.busy) doForge(); });
    actions.appendChild(reroll); actions.appendChild(forge);
    els.body.appendChild(actions);
  }

  function pick(i) {
    state.picked = i;
    var cards = els.body.querySelectorAll('.cd-cand');
    cards.forEach(function (n, idx) { n.classList.toggle('cd-picked', idx === i); });
    var forge = els.body.querySelector('.cd-btn-primary');
    if (forge) forge.disabled = false;
  }

  // ---- step: forging ----
  function doForge() {
    var c = state.candidates[state.picked];
    if (!c) return;
    // 瘦客户端：把选中那条记忆的文本发给云端，云端据此生成（真实记忆 / 预设 同一路径）。
    var payload = { lanlan_name: state.lanlan, source_text: c.text };

    state.busy = true;
    setHead('凝结中…', '');
    els.body.innerHTML = '<div class="cd-charge"><div class="cd-orb"></div><div style="opacity:.8;letter-spacing:.1em">正在凝结这段记忆…</div></div>';
    playSound(SOUND_COIN, 0.5);

    var started = Date.now();
    api('/api/card-drop/draw', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    }).then(function (d) {
      var wait = Math.max(0, 1100 - (Date.now() - started)); // 至少演 1.1s
      setTimeout(function () { state.busy = false; reveal((d && d.card) || {}); }, wait);
    }).catch(function (e) { state.busy = false; renderError(e); });
  }

  // ---- step: reveal ----
  function reveal(card, skipFx) {
    state.lastCard = card;
    var rk = (card.rarity || 'N').toUpperCase();
    var meta = RAR[rk] || RAR.N;
    var cls = 'cd-rar-' + rk;

    setHead('', '');
    els.head.innerHTML = '';

    var wrap = el('div', 'cd-reveal ' + cls);
    wrap.appendChild(el('div', 'cd-halo'));

    var cardEl = el('div', 'cd-card');
    var inner = el('div', 'cd-card-inner');
    var img = el('img'); img.src = card.cover_url || PLACEHOLDER_ART; img.alt = 'card';
    inner.appendChild(img);
    inner.appendChild(el('div', 'cd-rank', rk));
    if (card.serial) inner.appendChild(el('div', 'cd-serial', escapeHtml(card.serial)));
    cardEl.appendChild(inner);
    wrap.appendChild(cardEl);

    wrap.appendChild(el('div', 'cd-rar-label', rk));
    wrap.appendChild(el('h2', null, escapeHtml(card.title || '未命名')));
    var story = (card.summary || card.story_md || '').replace(/[#*`>]/g, '').trim();
    wrap.appendChild(el('div', 'cd-story', escapeHtml(story.slice(0, 90))));

    wrap.appendChild(buildAuthSection());

    var actions = el('div', 'cd-actions');
    var done = el('button', 'cd-btn cd-btn-primary', '收下 ✦');
    done.addEventListener('click', closeModal);
    actions.appendChild(done);
    wrap.appendChild(actions);

    els.body.innerHTML = '';
    els.body.appendChild(wrap);

    if (skipFx) return;
    // 演出：音效（多重叮）+ 屏闪 + 震屏 + 粒子
    for (var m = 0; m < meta.multi; m++) {
      (function (k) { setTimeout(function () { playSound(SOUND_COIN, meta.vol); }, k * 110); })(m);
    }
    if (meta.flash) screenFlash(cls);
    if (meta.shake) els.modal.classList.add('cd-shake'), setTimeout(function () { els.modal.classList.remove('cd-shake'); }, 520);
    spawnParticles(cardEl, meta.particles, cls);
  }

  // ---- 社区登录（卡存进卡册）----
  function loadAuth() {
    return api('/api/card-drop/auth-status')
      .then(function (d) { state.auth = d; })
      .catch(function () { state.auth = { logged_in: false }; });
  }

  function buildAuthSection() {
    var box = el('div', 'cd-auth');
    if (state.auth && state.auth.logged_in) {
      var name = (state.auth.user && state.auth.user.display_name) || '你';
      box.appendChild(el('div', 'cd-auth-ok', '✦ 已存入 ' + escapeHtml(name) + ' 的卡册'));
    } else {
      box.appendChild(el('div', 'cd-auth-hint', '登录猫娘社区，把这张卡存进你的卡册 ✦'));
      var btn = el('button', 'cd-btn cd-btn-ghost cd-auth-btn', '登录 / 注册');
      btn.addEventListener('click', function () { showLogin('login'); });
      box.appendChild(btn);
    }
    return box;
  }

  function showLogin(mode) {
    mode = mode || 'login';
    var isReg = mode === 'register';
    setHead(isReg ? '注册猫娘社区' : '登录猫娘社区', '登录后掉落的卡会存进你的卡册');
    els.body.innerHTML = '';
    var form = el('div', 'cd-login');
    var emailIn = el('input', 'cd-input'); emailIn.type = 'email'; emailIn.placeholder = '邮箱';
    var pwIn = el('input', 'cd-input'); pwIn.type = 'password'; pwIn.placeholder = '密码（≥8 位）';
    form.appendChild(emailIn); form.appendChild(pwIn);
    var nameIn = null;
    if (isReg) { nameIn = el('input', 'cd-input'); nameIn.type = 'text'; nameIn.placeholder = '昵称（可选）'; form.appendChild(nameIn); }
    var errEl = el('div', 'cd-login-err');
    form.appendChild(errEl);

    var submit = el('button', 'cd-btn cd-btn-primary', isReg ? '注册并登录' : '登录');
    function go() {
      var email = emailIn.value.trim(), pw = pwIn.value;
      if (!email || !pw) { errEl.textContent = '请填邮箱和密码'; return; }
      submit.disabled = true; errEl.textContent = '登录中…';
      var body = { email: email, password: pw };
      if (nameIn && nameIn.value.trim()) body.display_name = nameIn.value.trim();
      api('/api/card-drop/' + (isReg ? 'register' : 'login'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      }).then(function (d) {
        state.auth = { logged_in: true, user: d.user };
        toast('✦ 已登录，卡存进卡册了', 3000);
        reveal(state.lastCard || {}, true);
      }).catch(function (e) { submit.disabled = false; errEl.textContent = (e && (e.detail || e.message)) || '登录失败'; });
    }
    submit.addEventListener('click', go);
    pwIn.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });

    var actions = el('div', 'cd-actions');
    actions.appendChild(submit);
    form.appendChild(actions);

    // Steam 登录：开浏览器走云端 OpenID → NEKO 本地回调存 JWT，前端轮询 auth-status
    var steamWrap = el('div', 'cd-steam');
    steamWrap.appendChild(el('div', 'cd-steam-or', '或'));
    var steamBtn = el('button', 'cd-btn cd-btn-ghost cd-steam-btn', '🎮 用 Steam 登录');
    steamBtn.addEventListener('click', function () { steamLogin(steamBtn, errEl); });
    steamWrap.appendChild(steamBtn);
    form.appendChild(steamWrap);

    var toggle = el('button', 'cd-link', isReg ? '已有账号？去登录' : '没有账号？去注册');
    toggle.addEventListener('click', function () { showLogin(isReg ? 'login' : 'register'); });
    var back = el('button', 'cd-link', '← 返回卡片');
    back.addEventListener('click', function () { reveal(state.lastCard || {}, true); });
    var sub = el('div', 'cd-login-sub');
    sub.appendChild(toggle); sub.appendChild(back);
    form.appendChild(sub);

    els.body.appendChild(form);
  }

  function openExternalUrl(url) {
    if (window.electronShell && typeof window.electronShell.openExternal === 'function') {
      window.electronShell.openExternal(url);
    } else {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }

  function steamLogin(btn, errEl) {
    if (state.steamPolling) return;
    if (btn) btn.disabled = true;
    if (errEl) errEl.textContent = '正在打开 Steam 登录…';
    api('/api/card-drop/steam-login').then(function (d) {
      var url = d && d.authorize_url;
      if (!url) throw new Error('no_authorize_url');
      openExternalUrl(url);
      if (errEl) errEl.textContent = '已在浏览器打开 Steam 登录，完成后会自动返回…';
      pollAuthUntilLoggedIn(btn, errEl);
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      if (errEl) errEl.textContent = (e && (e.detail || e.message)) || '打开 Steam 登录失败';
    });
  }

  // 浏览器里完成 Steam 登录后，本地回调存好 JWT；这里轮询 auth-status 直到登录态出现。
  function pollAuthUntilLoggedIn(btn, errEl) {
    state.steamPolling = true;
    var tries = 0, maxTries = 150; // ~5 分钟（2s 间隔）
    (function tick() {
      if (!state.steamPolling) return;
      if (!els || !els.backdrop.classList.contains('cd-open')) { state.steamPolling = false; return; }
      tries++;
      api('/api/card-drop/auth-status').then(function (d) {
        if (!state.steamPolling) return;
        if (d && d.logged_in) {
          state.steamPolling = false;
          state.auth = d;
          toast('✦ 已用 Steam 登录，卡存进卡册了', 3000);
          reveal(state.lastCard || {}, true);
          return;
        }
        if (tries >= maxTries) {
          state.steamPolling = false;
          if (errEl) errEl.textContent = '等待登录超时，可重试';
          if (btn) btn.disabled = false;
          return;
        }
        setTimeout(tick, 2000);
      }).catch(function () {
        if (!state.steamPolling) return;
        if (tries >= maxTries) { state.steamPolling = false; if (btn) btn.disabled = false; return; }
        setTimeout(tick, 2000);
      });
    })();
  }

  function screenFlash(cls) {
    var f = el('div', 'cd-flash ' + cls);
    document.body.appendChild(f);
    setTimeout(function () { f.remove(); }, 820);
  }

  function spawnParticles(anchor, count, cls) {
    var rect = anchor.getBoundingClientRect();
    var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
    for (var i = 0; i < count; i++) {
      var p = el('div', 'cd-particle ' + cls);
      var ang = Math.random() * Math.PI * 2;
      var dist = 90 + Math.random() * 160;
      p.style.left = cx + 'px'; p.style.top = cy + 'px';
      p.style.setProperty('--cd-dx', Math.cos(ang) * dist + 'px');
      p.style.setProperty('--cd-dy', Math.sin(ang) * dist + 'px');
      p.style.setProperty('--cd-dur', (0.7 + Math.random() * 0.6) + 's');
      document.body.appendChild(p);
      (function (node) { setTimeout(function () { node.remove(); }, 1400); })(p);
    }
  }

  function renderError(e) {
    var msg = (e && e.message) || '出错了';
    if (e && e.status === 409) msg = '还没连上云端（client 未注册），稍后再试';
    setHead('哎呀', '');
    els.body.innerHTML = '<div class="cd-msg">' + escapeHtml(msg) + '</div>';
    var actions = el('div', 'cd-actions');
    var retry = el('button', 'cd-btn cd-btn-ghost', '重试');
    retry.addEventListener('click', function () { if (!state.busy) loadCandidates(); });
    var cancel = el('button', 'cd-btn cd-btn-ghost', '关闭');
    cancel.addEventListener('click', closeModal);
    actions.appendChild(retry); actions.appendChild(cancel);
    els.body.appendChild(actions);
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ---- entry points ----
  window.handleCardDropAvailable = function (payload) {
    var lanlan = (payload && payload.lanlan_name)
      || (window.lanlan_config && window.lanlan_config.lanlan_name) || 'Neko';
    state.lanlan = String(lanlan);
    playSound(SOUND_COIN, 0.7);
    toast('✨ 叮～掉落了一张卡', 3500);
    openModal();
  };

  // 调试：直接开（不经 WS）
  window.openCardDrop = function (lanlan) {
    state.lanlan = String(lanlan || (window.lanlan_config && window.lanlan_config.lanlan_name) || 'test');
    openModal();
  };
})();
