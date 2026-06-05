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

  function openModal() {
    ensureDom();
    els.backdrop.classList.add('cd-open');
    loadCandidates();
  }
  function closeModal() {
    if (!els) return;
    els.backdrop.classList.remove('cd-open');
    state.candidates = []; state.picked = -1; state.busy = false;
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
    var payload = { lanlan_name: state.lanlan };
    if (c.kind === 'preset') payload.preset_id = c.preset_id; else payload.fact_id = c.fact_id;

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
  function reveal(card) {
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

    var actions = el('div', 'cd-actions');
    var done = el('button', 'cd-btn cd-btn-primary', '收下 ✦');
    done.addEventListener('click', closeModal);
    actions.appendChild(done);
    wrap.appendChild(actions);

    els.body.innerHTML = '';
    els.body.appendChild(wrap);

    // 演出：音效（多重叮）+ 屏闪 + 震屏 + 粒子
    for (var m = 0; m < meta.multi; m++) {
      (function (k) { setTimeout(function () { playSound(SOUND_COIN, meta.vol); }, k * 110); })(m);
    }
    if (meta.flash) screenFlash(cls);
    if (meta.shake) els.modal.classList.add('cd-shake'), setTimeout(function () { els.modal.classList.remove('cd-shake'); }, 520);
    spawnParticles(cardEl, meta.particles, cls);
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
