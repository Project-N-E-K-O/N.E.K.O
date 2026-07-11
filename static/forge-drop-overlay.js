/**
 * forge-drop-overlay.js — 铸造券掉落瞬间 UX（方案二：迷你券卡飞入）
 *
 * 入口：
 *   window.nekoForgeDrop.play(payload)
 *   payload: { rarity, is_career_first, reason, available?, active_count? }
 *
 * 同时：
 *   - 「猫娘社区」按钮右下角蓝色券数角标（.neko-social-forge-badge）
 *   - 掉券卡片飞向该按钮中心
 *   - 启动时 GET /api/card-drop/credits 拉初始券数
 *
 * 硬约束：必须在 Pet 透明窗内渲染（独立 Toast 窗在部分 macOS 上不可见）。
 * pointer-events: none，不破坏穿透 hitTest。
 */
(function () {
  'use strict';

  if (window.__nekoForgeDropOverlayInstalled__) return;
  window.__nekoForgeDropOverlayInstalled__ = true;

  var T = window.NekoForgeDropTokens || null;
  var QUEUE_GAP_MS = 400;
  var HOLD_MS = 2800;
  var FLY_MS = 700;
  var queue = Promise.resolve();
  var cachedCredits = 0;
  var forgeBadgeObserver = null;
  // 浮动按钮栏未聚焦时会被 display:none；此时 getBoundingClientRect=0 → 会误飞左上角。
  // 缓存上次可见位置，并在隐藏时用 style.left/top 估算。
  var lastSocialCenter = null;

  function tokens() {
    return T || {
      normalizeRarity: function (r) { return String(r || 'N').toUpperCase(); },
      rarityColor: function () { return '#9aa6bd'; },
      rarityGlow: function () { return 'rgba(154,166,189,.5)'; },
      reasonText: function () { return '一个小小的奇遇'; },
      SOUND_PATHS: {},
    };
  }

  function ensureStyles() {
    // 版本号覆盖，避免 Pet 残留旧样式（右下角 HUD / 错误 transform 飞出）。
    var STYLE_ID = 'neko-forge-drop-styles';
    var STYLE_VER = 'v6-hidden-btn-fly';
    var existing = document.getElementById(STYLE_ID);
    if (existing && existing.getAttribute('data-ver') === STYLE_VER) return;
    if (existing) try { existing.remove(); } catch (_) {}
    // 清掉上一版右下角 HUD（若还在）
    try {
      var oldHud = document.getElementById('neko-forge-credit-hud');
      if (oldHud) oldHud.remove();
    } catch (_) {}
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.setAttribute('data-ver', STYLE_VER);
    style.textContent = [
      '@keyframes nekoForgeCardPop{',
      '  0%{opacity:0;transform:scale(.3)}',
      '  70%{opacity:1;transform:scale(1.08)}',
      '  100%{opacity:1;transform:scale(1)}',
      '}',
      '@keyframes nekoForgeCardFloat{',
      '  0%,100%{transform:translateY(0)}',
      '  50%{transform:translateY(-4px)}',
      '}',
      '@keyframes nekoForgeSpark{',
      '  0%{opacity:0;transform:translate(0,0) scale(.4)}',
      '  30%{opacity:1}',
      '  100%{opacity:0;transform:translate(var(--sx),var(--sy)) scale(.2)}',
      '}',
      '@keyframes nekoForgeBadgePop{',
      '  0%{transform:scale(.2);opacity:0}',
      '  60%{transform:scale(1.15);opacity:1}',
      '  100%{transform:scale(1);opacity:1}',
      '}',
      '.neko-forge-drop-layer{position:fixed;inset:0;pointer-events:none;z-index:2147483640;overflow:hidden}',
      // left/top 像素定位飞出，避免 translate(-50%) 混算跑偏。
      '.neko-forge-card{position:fixed;width:128px;height:76px;margin:0;box-sizing:border-box;',
      '  border-radius:14px;padding:10px 12px;pointer-events:none;user-select:none;',
      '  font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;',
      '  background:linear-gradient(145deg,rgba(18,20,36,.96),rgba(10,12,24,.94));',
      '  display:flex;flex-direction:column;justify-content:space-between;',
      '  transform-origin:center center;',
      '  transition:left .7s cubic-bezier(.4,0,.2,1),top .7s cubic-bezier(.4,0,.2,1),',
      '    opacity .7s cubic-bezier(.4,0,.2,1),transform .7s cubic-bezier(.4,0,.2,1);',
      '  animation:nekoForgeCardPop .4s cubic-bezier(.2,.9,.25,1) both}',
      '.neko-forge-card.holding{animation:nekoForgeCardFloat 4s ease-in-out infinite}',
      // flying 状态由 JS 内联 left/top/opacity/transform 驱动，避免 animation:none 打断 transition
      '.neko-forge-card .rk{font-size:22px;font-weight:800;letter-spacing:.5px;line-height:1}',
      '.neko-forge-card .label{font-size:11px;color:#c9d2e0;margin-top:2px}',
      '.neko-forge-card .why{font-size:10px;color:#9aa6bd;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '.neko-forge-card .meta{font-size:10px;color:#8fa0b8;display:flex;justify-content:space-between;gap:6px}',
      '.neko-forge-card .ribbon{position:absolute;top:-8px;left:10px;font-size:9px;font-weight:700;',
      '  color:#1a1200;background:linear-gradient(90deg,#ffe08a,#ffb627);padding:2px 7px;border-radius:8px;',
      '  box-shadow:0 2px 8px rgba(255,182,39,.45)}',
      '.neko-forge-spark{position:absolute;font-size:12px;pointer-events:none;',
      '  animation:nekoForgeSpark .7s ease-out forwards}',
      // 挂在「猫娘社区」按钮右下角的蓝色券数角标
      '.neko-social-forge-badge{position:absolute;bottom:-4px;right:-4px;min-width:16px;height:16px;',
      '  padding:0 4px;border-radius:8px;background:#39b7f5;color:#fff;font-size:10px;font-weight:700;',
      '  line-height:16px;text-align:center;box-shadow:0 0 0 2px rgba(255,255,255,.85);',
      '  pointer-events:none;z-index:11;box-sizing:border-box;',
      '  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;',
      '  animation:nekoForgeBadgePop 240ms ease-out}',
      '.neko-social-forge-badge.hidden{display:none}',
      '.neko-social-forge-badge.bump{animation:nekoForgeBadgePop 280ms ease-out}',
      // 隐藏上一版右下角 HUD（若样式残留）
      '.neko-forge-credit-hud{display:none!important}',
      '@media (prefers-reduced-motion: reduce){',
      '  .neko-forge-card,.neko-forge-card.holding,.neko-forge-spark{animation:none!important;opacity:1;transition:none!important}',
      '}',
    ].join('');
    (document.head || document.documentElement).appendChild(style);
  }

  function findSocialButton() {
    return document.querySelector('[id$="-btn-social"]');
  }

  function findFloatingButtonsContainer() {
    return document.querySelector('[id$="-floating-buttons"]');
  }

  function findSocialAnchor() {
    var btn = findSocialButton();
    if (!btn) return null;
    // 角标挂在按钮本身（或可定位的 wrapper）上
    return btn;
  }

  function rememberSocialCenter(x, y) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    if (x <= 0 && y <= 0) return;
    lastSocialCenter = { x: x, y: y };
  }

  /** 从可见 DOM 读按钮中心；不可见时返回 null。 */
  function readVisibleSocialCenter() {
    var btn = findSocialButton();
    if (!btn) return null;
    var rect = btn.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return null;
    var center = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    rememberSocialCenter(center.x, center.y);
    return center;
  }

  /**
   * 按钮栏被隐藏时：用 floating-buttons 的 style.left/top + 子项序号估算 social 中心。
   * Live2D/VRM/MMD 都会持续写 left/top，即使 display:none。
   */
  function estimateHiddenSocialCenter() {
    var btn = findSocialButton();
    var container = findFloatingButtonsContainer();
    if (!container) return null;

    var left = parseFloat(container.style.left);
    var top = parseFloat(container.style.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) return null;

    // 估算 scale（transform: scale(s)）
    var scale = 1;
    try {
      var tr = container.style.transform || '';
      var m = tr.match(/scale\(([^)]+)\)/);
      if (m) scale = Math.max(0.3, parseFloat(m[1]) || 1);
    } catch (_) {}

    var btnSize = 48 * scale;
    var gap = 12 * scale;
    var index = 0;
    if (btn && btn.parentElement && container.contains(btn.parentElement)) {
      var kids = Array.prototype.slice.call(container.children);
      index = Math.max(0, kids.indexOf(btn.parentElement));
    } else {
      // 默认顺序：mic, agent, social, settings, goodbye → social=2
      index = 2;
    }

    var x = left + (40 * scale) / 2; // 工具栏约 80px 宽（含标签），按钮约居中偏左
    // 更稳：按钮本身约 48px，wrapper 左对齐
    x = left + btnSize / 2;
    var y = top + index * (btnSize + gap) + btnSize / 2;
    rememberSocialCenter(x, y);
    return { x: x, y: y };
  }

  function ensureForgeBadge() {
    ensureStyles();
    var btn = findSocialButton();
    if (!btn) return null;
    try {
      var cs = window.getComputedStyle(btn);
      if (cs && cs.position === 'static') btn.style.position = 'relative';
    } catch (_) {}
    var badge = btn.querySelector('.neko-social-forge-badge');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'neko-social-forge-badge hidden';
      btn.appendChild(badge);
    }
    return badge;
  }

  function renderForgeBadge(count, bump) {
    ensureStyles();
    var badge = ensureForgeBadge();
    if (!badge) return;
    var n = Math.max(0, Number(count) || 0);
    cachedCredits = n;
    var wantHidden = n <= 0;
    var wantText = wantHidden ? '' : (n > 99 ? '99+' : String(n));
    if (badge.classList.contains('hidden') === wantHidden && badge.textContent === wantText && !bump) {
      return;
    }
    badge.classList.toggle('hidden', wantHidden);
    badge.textContent = wantText;
    if (bump && !wantHidden) {
      badge.classList.remove('bump');
      void badge.offsetWidth;
      badge.classList.add('bump');
    }
  }

  function startForgeBadgeObserver() {
    if (forgeBadgeObserver) return;
    forgeBadgeObserver = new MutationObserver(function () {
      if (cachedCredits > 0) renderForgeBadge(cachedCredits, false);
    });
    forgeBadgeObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  function getFlyTarget() {
    // 1) 可见时直接读真实中心（并缓存）
    var visible = readVisibleSocialCenter();
    if (visible) return visible;

    // 2) 隐藏时用 style.left/top 估算（避免 getBoundingClientRect=0 → 飞向左上角）
    var estimated = estimateHiddenSocialCenter();
    if (estimated) return estimated;

    // 3) 上次可见位置
    if (lastSocialCenter) return lastSocialCenter;

    // 4) 最后兜底：角色右侧中部（比 0,0 合理）
    return {
      x: Math.max(80, window.innerWidth * 0.62),
      y: Math.max(80, window.innerHeight * 0.42),
    };
  }

  function spawnSparks(layer, originX, originY, rarity, color) {
    var count = rarity === 'UR' ? 10 : rarity === 'SSR' ? 8 : rarity === 'SR' ? 5 : rarity === 'R' ? 3 : 1;
    for (var i = 0; i < count; i++) {
      var spark = document.createElement('div');
      spark.className = 'neko-forge-spark';
      spark.textContent = '✦';
      spark.style.left = originX + 'px';
      spark.style.top = originY + 'px';
      spark.style.color = color;
      var angle = (Math.PI * 2 * i) / count + Math.random() * 0.4;
      var dist = 36 + Math.random() * 48;
      spark.style.setProperty('--sx', Math.cos(angle) * dist + 'px');
      spark.style.setProperty('--sy', Math.sin(angle) * dist + 'px');
      spark.style.animationDelay = (i * 30) + 'ms';
      layer.appendChild(spark);
      (function (node) {
        setTimeout(function () { try { node.remove(); } catch (_) {} }, 900);
      })(spark);
    }
  }

  function playOne(payload) {
    return new Promise(function (resolve) {
      try {
        ensureStyles();
        var t = tokens();
        var rarity = t.normalizeRarity(payload && payload.rarity);
        var color = t.rarityColor(rarity);
        var glow = t.rarityGlow(rarity);
        var why = (payload && payload.is_career_first)
          ? '生涯第一张锻造券'
          : t.reasonText(payload && payload.reason);
        var active = typeof (payload && payload.active_count) === 'number'
          ? payload.active_count
          : (cachedCredits > 0 ? cachedCredits + 1 : 1);

        var layer = document.querySelector('.neko-forge-drop-layer');
        if (!layer) {
          layer = document.createElement('div');
          layer.className = 'neko-forge-drop-layer';
          (document.body || document.documentElement).appendChild(layer);
        }

        var CARD_W = 128;
        var CARD_H = 76;
        var originX = window.innerWidth * 0.5;
        var originY = window.innerHeight * 0.42;
        var startLeft = Math.round(originX - CARD_W / 2);
        var startTop = Math.round(originY - CARD_H / 2);

        var card = document.createElement('div');
        card.className = 'neko-forge-card';
        card.style.left = startLeft + 'px';
        card.style.top = startTop + 'px';
        card.style.border = '1.5px solid ' + color;
        card.style.boxShadow = '0 12px 36px rgba(0,0,0,.45), 0 0 28px ' + glow;
        if (payload && payload.is_career_first) {
          var ribbon = document.createElement('div');
          ribbon.className = 'ribbon';
          ribbon.textContent = '生涯首券';
          card.appendChild(ribbon);
        }
        var top = document.createElement('div');
        var rk = document.createElement('div');
        rk.className = 'rk';
        rk.style.color = color;
        rk.textContent = rarity;
        var label = document.createElement('div');
        label.className = 'label';
        label.textContent = '锻造券 ×1';
        top.appendChild(rk);
        top.appendChild(label);
        card.appendChild(top);
        var whyEl = document.createElement('div');
        whyEl.className = 'why';
        whyEl.textContent = why;
        card.appendChild(whyEl);
        var meta = document.createElement('div');
        meta.className = 'meta';
        meta.innerHTML = '<span>+1</span><span>持有 ' + active + '</span>';
        card.appendChild(meta);
        layer.appendChild(card);

        spawnSparks(layer, originX, originY, rarity, color);

        var reduced = false;
        try {
          reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (_) {}
        if (window.__nekoPetInteracting__) reduced = true;

        function brieflyRevealFloatingButtons() {
          // 掉券飞出时短暂显示浮动栏，让用户看清「猫娘社区」终点；结束后恢复原 display。
          var container = findFloatingButtonsContainer();
          if (!container) return null;
          var prev = {
            display: container.style.display,
            visibility: container.style.visibility,
            opacity: container.style.opacity,
          };
          try {
            container.style.setProperty('display', 'flex', 'important');
            container.style.visibility = 'visible';
            container.style.opacity = '1';
          } catch (_) {}
          // 显示后再读一次真实中心
          setTimeout(readVisibleSocialCenter, 0);
          return prev;
        }

        function restoreFloatingButtons(prev) {
          if (!prev) return;
          var container = findFloatingButtonsContainer();
          if (!container) return;
          try {
            if (prev.display) container.style.display = prev.display;
            else container.style.removeProperty('display');
            container.style.visibility = prev.visibility || '';
            container.style.opacity = prev.opacity || '';
          } catch (_) {}
        }

        var hold = reduced ? 1200 : HOLD_MS;
        setTimeout(function () {
          var prevBtnStyle = null;
          try {
            // 0) 短暂亮起浮动栏，避免隐藏时 rect=0 飞向左上，也让终点可见
            prevBtnStyle = brieflyRevealFloatingButtons();

            // 1) 停掉 float 动画，冻结当前像素位置（避免 animation 与 transition 抢 transform）
            card.classList.remove('holding');
            card.style.animation = 'none';
            var cur = card.getBoundingClientRect();
            card.style.left = Math.round(cur.left) + 'px';
            card.style.top = Math.round(cur.top) + 'px';
            card.style.transform = 'scale(1)';
            card.style.opacity = '1';
            void card.offsetWidth;

            var target = getFlyTarget();
            var endLeft = Math.round(target.x - CARD_W / 2);
            var endTop = Math.round(target.y - CARD_H / 2);
            var flyMs = reduced ? 200 : FLY_MS;

            // 2) 下一帧再开 transition，保证浏览器从冻结位置插值到终点
            requestAnimationFrame(function () {
              requestAnimationFrame(function () {
                try {
                  card.style.transition = [
                    'left ' + flyMs + 'ms cubic-bezier(.4,0,.2,1)',
                    'top ' + flyMs + 'ms cubic-bezier(.4,0,.2,1)',
                    'transform ' + flyMs + 'ms cubic-bezier(.4,0,.2,1)',
                    // 淡出稍晚，先看清飞向社区按钮的轨迹
                    'opacity ' + Math.round(flyMs * 0.55) + 'ms cubic-bezier(.4,0,.2,1) ' + Math.round(flyMs * 0.45) + 'ms',
                  ].join(',');
                  card.style.left = endLeft + 'px';
                  card.style.top = endTop + 'px';
                  card.style.transform = 'scale(.45)';
                  card.style.opacity = '0';
                } catch (_) {}
              });
            });

            setTimeout(function () {
              try { card.remove(); } catch (_) {}
              renderForgeBadge(active, true);
              // 角标 bump 后再收起浮动栏，稍留一点时间让用户看到数字变化
              setTimeout(function () { restoreFloatingButtons(prevBtnStyle); }, 600);
              resolve();
            }, flyMs + 40);
          } catch (_) {
            try { card.remove(); } catch (_) {}
            renderForgeBadge(active, true);
            restoreFloatingButtons(prevBtnStyle);
            resolve();
          }
        }, hold);

        if (!reduced) {
          setTimeout(function () {
            try { card.classList.add('holding'); } catch (_) {}
          }, 420);
        }
      } catch (_) {
        resolve();
      }
    });
  }

  function play(payload) {
    queue = queue
      .then(function () { return playOne(payload || {}); })
      .then(function () {
        return new Promise(function (r) { setTimeout(r, QUEUE_GAP_MS); });
      })
      .catch(function () {});
    return queue;
  }

  function fetchCredits() {
    try {
      fetch('/api/card-drop/credits', { credentials: 'include' })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) {
          if (!data) return;
          var count = typeof data.count === 'number'
            ? data.count
            : (Array.isArray(data.credits) ? data.credits.length : 0);
          renderForgeBadge(count, false);
        })
        .catch(function () {});
    } catch (_) {}
  }

  function onCreditDropEvent(event) {
    var detail = (event && event.detail) || {};
    if (typeof detail.active_count === 'number') {
      // 动画飞入结束后再 bump；这里先缓存，避免角标抢先跳
      cachedCredits = Math.max(0, detail.active_count - 1);
    }
    play(detail);
  }

  function boot() {
    ensureStyles();
    startForgeBadgeObserver();
    renderForgeBadge(cachedCredits || 0, false);
    fetchCredits();
    window.addEventListener('neko-forge-credit-drop', onCreditDropEvent);
    // 按钮可见时持续刷新缓存位置，隐藏后飞出仍能对准
    try {
      setInterval(function () { readVisibleSocialCenter(); }, 1000);
    } catch (_) {}
    // 浮动栏刚创建时立刻记一次
    try {
      window.addEventListener('live2d-floating-buttons-ready', function () {
        setTimeout(readVisibleSocialCenter, 50);
        setTimeout(readVisibleSocialCenter, 500);
      });
    } catch (_) {}
    setTimeout(readVisibleSocialCenter, 300);
  }

  window.nekoForgeDrop = {
    play: play,
    setCredits: function (n) { renderForgeBadge(n, true); },
    refreshCredits: fetchCredits,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
