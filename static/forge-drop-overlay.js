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
 *   - 券数和到期时间只接收 PC 主进程推送的云端快照
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
  var HOLD_MS = 3200;
  var FLY_MS = 700;
  // 只有这两档贴角色右下方；其余稀有度沿用屏幕中央的原始演出。
  var AVATAR_ANCHORED_RARITIES = { N: true, R: true };
  var ANCHOR_CARD_MAX_W = 280;
  var ANCHOR_CARD_MIN_W = 180;
  var CENTER_CARD_MAX_W = 360;
  var CARD_MARGIN = 12;
  var CARD_ASPECT = 1192 / 445;
  var queue = Promise.resolve();
  var cachedCredits = 0;
  var creditStateRevision = 0;
  var expiryRefreshTimer = null;
  var forgeBadgeObserver = null;
  var dropSoundAudioByRarity = {};
  var dropEffectsGeneration = 0;
  var activeAnimationCompleters = {};
  // 浮动按钮栏未聚焦时会被 display:none；此时 getBoundingClientRect=0 → 会误飞左上角。
  // 缓存上次可见位置，并在隐藏时用 style.left/top 估算。
  var lastSocialCenter = null;

  function tokens() {
    return T || {
      normalizeRarity: function (r) { return String(r || 'N').toUpperCase(); },
      rarityColor: function () { return '#9aa6bd'; },
      rarityGlow: function () { return 'rgba(154,166,189,.5)'; },
      ticketPath: function () { return '/static/assets/forge-tickets/forge-ticket-n.png?v=20260717-hd'; },
      reasonText: function () { return '一个小小的奇遇'; },
      SOUND_PATHS: {},
    };
  }

  function preloadTicketArt() {
    var t = tokens();
    var paths = t.TICKET_PATHS || {};
    ['N', 'R', 'SR', 'SSR', 'UR'].forEach(function (rarity) {
      try {
        var src = paths[rarity] || (typeof t.ticketPath === 'function' ? t.ticketPath(rarity) : '');
        if (!src) return;
        var image = new Image();
        image.decoding = 'async';
        image.src = src;
      } catch (_) {}
    });
  }

  function createDropSoundAudio(rarity) {
    try {
      var t = tokens();
      var src = (t.SOUND_PATHS || {})[rarity];
      if (!src || typeof window.Audio !== 'function') return null;
      var audio = new window.Audio(src);
      audio.preload = 'auto';
      audio.load();
      dropSoundAudioByRarity[rarity] = audio;
      return audio;
    } catch (_) {
      return null;
    }
  }

  function preloadDropSounds() {
    ['N', 'R', 'SR', 'SSR', 'UR'].forEach(function (rarity) {
      if (!dropSoundAudioByRarity[rarity]) createDropSoundAudio(rarity);
    });
  }

  function playDropSound(rarity) {
    try {
      if (window.forgeDropEffectsEnabled === false) return;
      var normalized = tokens().normalizeRarity(rarity);
      var audio = dropSoundAudioByRarity[normalized] || createDropSoundAudio(normalized);
      if (!audio) return;
      audio.currentTime = 0;
      var playResult = audio.play();
      if (playResult && typeof playResult.catch === 'function') {
        playResult.catch(function () {});
      }
    } catch (_) {}
  }

  function ensureStyles() {
    // 版本号覆盖，避免 Pet 残留旧样式（右下角 HUD / 错误 transform 飞出）。
    var STYLE_ID = 'neko-forge-drop-styles';
    var STYLE_VER = 'v10-avatar-lower-right';
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
      '  0%{opacity:0;transform:translateY(18px) scale(.55)}',
      '  55%{opacity:1;transform:translateY(-3px) scale(1.1)}',
      '  78%{opacity:1;transform:translateY(0) scale(.98)}',
      '  100%{opacity:1;transform:scale(1)}',
      '}',
      '@keyframes nekoForgeAuraPop{',
      '  0%{opacity:0;transform:scale(.72)}',
      '  55%{opacity:.72;transform:scale(1.08)}',
      '  100%{opacity:.38;transform:scale(1.04)}',
      '}',
      '@keyframes nekoForgeBadgePop{',
      '  0%{transform:scale(.2);opacity:0}',
      '  60%{transform:scale(1.15);opacity:1}',
      '  100%{transform:scale(1);opacity:1}',
      '}',
      '.neko-forge-drop-layer{position:fixed;inset:0;pointer-events:none;z-index:2147483640;overflow:hidden}',
      // left/top 像素定位飞出，避免 translate(-50%) 混算跑偏。
      '.neko-forge-card{position:fixed;width:360px;height:134px;margin:0;box-sizing:border-box;',
      '  padding:0;pointer-events:none;user-select:none;background:transparent;display:block;isolation:isolate;',
      '  transform-origin:center center;',
      '  transition:left .7s cubic-bezier(.4,0,.2,1),top .7s cubic-bezier(.4,0,.2,1),',
      '    opacity .7s cubic-bezier(.4,0,.2,1),transform .7s cubic-bezier(.4,0,.2,1);',
      '  animation:nekoForgeCardPop .56s cubic-bezier(.2,.9,.25,1)}',
      // flying 状态由 JS 内联 left/top/opacity/transform 驱动，避免 animation:none 打断 transition
      '.neko-forge-card .ticket-aura-art{position:absolute;inset:0;z-index:0;display:block;',
      '  width:100%;height:100%;object-fit:contain;opacity:.38;transform:scale(1.04);',
      '  filter:blur(18px) saturate(1.35);animation:nekoForgeAuraPop .56s cubic-bezier(.2,.9,.25,1);',
      '  -webkit-user-drag:none;user-select:none}',
      '.neko-forge-card .ticket-art{position:relative;z-index:1;display:block;width:100%;height:100%;',
      '  object-fit:contain;-webkit-user-drag:none;user-select:none}',
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
      '  .neko-forge-card,.neko-forge-card .ticket-aura-art{animation:none!important;transition:none!important}',
      '  .neko-forge-card{opacity:1!important}',
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
      btn.style.overflow = 'visible';
      if (btn.parentElement) btn.parentElement.style.overflow = 'visible';
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
    var n = Math.max(0, Number(count) || 0);
    // The authoritative refresh can finish before the floating button exists.
    // Cache first so the MutationObserver can render once the button mounts.
    cachedCredits = n;
    var badge = ensureForgeBadge();
    if (!badge) return;
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

  function normalizeAvatarBounds(bounds) {
    if (!bounds) return null;
    var left = Number(bounds.left != null ? bounds.left : bounds.x);
    var top = Number(bounds.top != null ? bounds.top : bounds.y);
    var right = Number(bounds.right != null ? bounds.right : left + Number(bounds.width));
    var bottom = Number(bounds.bottom != null ? bounds.bottom : top + Number(bounds.height));
    if (![left, top, right, bottom].every(Number.isFinite)) return null;
    if (right <= left || bottom <= top) return null;
    return {
      left: left,
      top: top,
      right: right,
      bottom: bottom,
      width: right - left,
      height: bottom - top,
    };
  }

  /** 读取当前模型的真实屏幕边界，四种模型共用同一套掉券定位。 */
  function getActiveAvatarBounds() {
    var configuredType = String(window.lanlan_config && window.lanlan_config.model_type || '').toLowerCase();
    var live3dSubType = String(window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase();
    var managers = {
      live2d: window.live2dManager,
      vrm: window.vrmManager,
      mmd: window.mmdManager,
    };
    function getPngtuberBounds() {
      var pngAvatar = document.querySelector(
        '#pngtuber-container .pngtuber-image:not([style*="display: none"]), ' +
        '#pngtuber-container .pngtuber-layered-canvas:not([style*="display: none"])'
      );
      if (!pngAvatar) return null;
      try { return normalizeAvatarBounds(pngAvatar.getBoundingClientRect()); } catch (_) { return null; }
    }
    // 当前配置明确为 PNGtuber 时必须优先使用它的 DOM；其他 manager 可能仍是
    // 上一次模型切换留下的实例，不能让旧边界抢走当前模型的掉券定位。
    if (configuredType === 'pngtuber') {
      var configuredPngBounds = getPngtuberBounds();
      if (configuredPngBounds) return configuredPngBounds;
    }
    var activeLive3dManager = live3dSubType === 'mmd' ? managers.mmd : managers.vrm;
    var order = configuredType === 'live3d'
      ? [activeLive3dManager, live3dSubType === 'mmd' ? managers.vrm : managers.mmd]
      : (configuredType && managers[configuredType]
        ? [managers[configuredType]]
        : [managers.live2d, managers.vrm, managers.mmd]);
    for (var i = 0; i < order.length; i += 1) {
      var manager = order[i];
      if (!manager || typeof manager.getModelScreenBounds !== 'function') continue;
      try {
        var bounds = normalizeAvatarBounds(manager.getModelScreenBounds());
        if (bounds) return bounds;
      } catch (_) {}
    }

    // PNGtuber 没有统一的 manager 边界 API，直接使用当前可见形象的 DOM 边界。
    return getPngtuberBounds();
  }

  function clampCardCoordinate(value, size, viewportSize, margin) {
    var max = Math.max(0, viewportSize - size);
    var min = Math.min(margin, max);
    return Math.round(Math.max(min, Math.min(value, max)));
  }

  /**
   * N/R 是高频掉落，贴到角色右下方以免反复挡住画面中央；
   * SR 及以上保持原来的屏幕中央大卡演出，保留稀有掉落的仪式感。
   */
  function shouldAnchorToAvatar(rarity) {
    return AVATAR_ANCHORED_RARITIES[String(rarity || '').toUpperCase()] === true;
  }

  function resolveCardWidth(rarity, avatarBounds) {
    var availableWidth = Math.max(1, window.innerWidth - CARD_MARGIN * 2);
    if (!shouldAnchorToAvatar(rarity)) {
      return Math.max(1, Math.min(CENTER_CARD_MAX_W, availableWidth));
    }
    var avatarCardWidth = avatarBounds ? avatarBounds.width * 0.55 : ANCHOR_CARD_MAX_W;
    return Math.max(1, Math.min(
      ANCHOR_CARD_MAX_W,
      availableWidth,
      Math.max(ANCHOR_CARD_MIN_W, avatarCardWidth)
    ));
  }

  function getCardPlacement(cardWidth, cardHeight, avatarBounds) {
    var margin = CARD_MARGIN;
    if (!avatarBounds) {
      return {
        left: clampCardCoordinate(
          window.innerWidth * 0.5 - cardWidth / 2,
          cardWidth,
          window.innerWidth,
          margin
        ),
        top: clampCardCoordinate(
          window.innerHeight * 0.42 - cardHeight / 2,
          cardHeight,
          window.innerHeight,
          margin
        ),
      };
    }
    // 券卡从角色右下侧探出：保留少量重叠，视觉上仍与 N.E.K.O 绑定。
    var overlapX = cardWidth * 0.18;
    var liftY = Math.max(28, Math.min(64, avatarBounds.height * 0.08));
    return {
      left: clampCardCoordinate(
        avatarBounds.right - overlapX,
        cardWidth,
        window.innerWidth,
        margin
      ),
      top: clampCardCoordinate(
        avatarBounds.bottom - cardHeight - liftY,
        cardHeight,
        window.innerHeight,
        margin
      ),
    };
  }

  function playOne(payload) {
    return new Promise(function (resolve) {
      var completed = false;
      function complete() {
        if (completed) return;
        completed = true;
        var eventId = payload && payload.event_id;
        if (typeof eventId === 'string' && eventId) delete activeAnimationCompleters[eventId];
        if (typeof eventId === 'string' && eventId) {
          try {
            window.dispatchEvent(new window.CustomEvent(
              'neko-forge-credit-animation-complete',
              { detail: { event_id: eventId } }
            ));
          } catch (_) {}
        }
        resolve();
      }
      if (payload && typeof payload.event_id === 'string' && payload.event_id) {
        activeAnimationCompleters[payload.event_id] = complete;
      }
      try {
        var payloadRevision = Number(payload && payload.__credit_state_revision) || 0;
        if (window.forgeDropEffectsEnabled === false) {
          // 排队期间可能已有权威刷新推进过 revision；此时这张队尾券的
          // active_count 已经陈旧，不能用它覆盖角标。
          var entryRevisionFresh = !payloadRevision || payloadRevision === creditStateRevision;
          if (payload && typeof payload.active_count === 'number' && entryRevisionFresh) {
            renderForgeBadge(payload.active_count, true);
          }
          complete();
          return;
        }
        var playGeneration = dropEffectsGeneration;
        ensureStyles();
        var t = tokens();
        var rarity = t.normalizeRarity(payload && payload.rarity);
        playDropSound(rarity);
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

        var avatarBounds = shouldAnchorToAvatar(rarity) ? getActiveAvatarBounds() : null;
        var CARD_W = resolveCardWidth(rarity, avatarBounds);
        var CARD_H = Math.round(CARD_W / CARD_ASPECT);
        var placement = getCardPlacement(CARD_W, CARD_H, avatarBounds);
        var startLeft = placement.left;
        var startTop = placement.top;

        var card = document.createElement('div');
        card.className = 'neko-forge-card';
        card.style.left = startLeft + 'px';
        card.style.top = startTop + 'px';
        card.style.width = CARD_W + 'px';
        card.style.height = CARD_H + 'px';
        card.setAttribute('role', 'img');
        card.setAttribute('aria-label', rarity + ' 锻造券，' + why + '，持有 ' + active);
        var ticketSrc = typeof t.ticketPath === 'function'
          ? t.ticketPath(rarity)
          : '/static/assets/forge-tickets/forge-ticket-n.png?v=20260717-hd';
        var ticketAuraArt = document.createElement('img');
        ticketAuraArt.className = 'ticket-aura-art';
        ticketAuraArt.src = ticketSrc;
        ticketAuraArt.alt = '';
        ticketAuraArt.draggable = false;
        ticketAuraArt.setAttribute('aria-hidden', 'true');
        card.appendChild(ticketAuraArt);
        var ticketArt = document.createElement('img');
        ticketArt.className = 'ticket-art';
        ticketArt.src = ticketSrc;
        ticketArt.alt = '';
        ticketArt.draggable = false;
        card.appendChild(ticketArt);
        layer.appendChild(card);

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
          if (playGeneration !== dropEffectsGeneration || window.forgeDropEffectsEnabled === false) {
            try { card.remove(); } catch (_) {}
            if (!payloadRevision || payloadRevision === creditStateRevision) {
              renderForgeBadge(active, true);
            }
            complete();
            return;
          }
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
              // 动画排队期间可能已经通过 /credits 拿到更新的权威数量，
              // 或已收到后续掉券事件。旧动画不得把角标回写成过时值。
              if (!payloadRevision || payloadRevision === creditStateRevision) {
                renderForgeBadge(active, true);
              }
              // 角标 bump 后再收起浮动栏，稍留一点时间让用户看到数字变化
              setTimeout(function () { restoreFloatingButtons(prevBtnStyle); }, 600);
              complete();
            }, flyMs + 40);
          } catch (_) {
            try { card.remove(); } catch (_) {}
            if (!payloadRevision || payloadRevision === creditStateRevision) {
              renderForgeBadge(active, true);
            }
            restoreFloatingButtons(prevBtnStyle);
            complete();
          }
        }, hold);

      } catch (_) {
        complete();
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

  function clearTimer(timer) {
    if (timer) try { clearTimeout(timer); } catch (_) {}
  }

  function scheduleExpiryClear(nextExpiresAt) {
    clearTimer(expiryRefreshTimer);
    expiryRefreshTimer = null;
    if (!nextExpiresAt) return;
    var now = Date.now();
    var earliest = Date.parse(nextExpiresAt);
    if (!Number.isFinite(earliest) || earliest <= now) return;
    if (!earliest) return;
    // setTimeout 的有效上限约 2^31-1ms；券通常当日到期，这里仍做封顶。
    var delay = Math.min(0x7fffffff, Math.max(1000, earliest - now + 1000));
    expiryRefreshTimer = setTimeout(function () {
      expiryRefreshTimer = null;
      creditStateRevision += 1;
      // 到期只说明最早的一张券失效，不能据此推断全部券都已清空。
      // 请求 Electron 主进程重新读取云端权威状态；本体不访问本地券账本。
      requestCreditStateRefresh();
    }, delay);
  }

  function onCreditDropEvent(event) {
    var detail = (event && event.detail) || {};
    creditStateRevision += 1;
    var queuedDetail = Object.assign({}, detail, {
      __credit_state_revision: creditStateRevision,
    });
    if (typeof detail.active_count === 'number') {
      // 动画飞入结束后再 bump；这里先缓存，避免角标抢先跳
      cachedCredits = Math.max(0, detail.active_count - 1);
    }
    // 关闭效果时仍经 playOne 的 disabled 分支，以便立即派发动画完成 ACK。
    play(queuedDetail);
  }

  function requestCreditStateRefresh() {
    // 到期 / 晚挂载必须走 PC 的云端权威刷新（CREDIT_STATE_REFRESH）。
    // 不要回放 SSE 缓存快照：那只会重播过期的 lastForgeCredit。
    try {
      window.dispatchEvent(new window.CustomEvent('neko-forge-credit-state-refresh'));
    } catch (_) {}
  }

  function onCreditStateEvent(event) {
    var detail = (event && event.detail) || {};
    creditStateRevision += 1;
    renderForgeBadge(
      typeof detail.active_count === 'number' ? detail.active_count : 0,
      false
    );
    scheduleExpiryClear(detail.next_expires_at);
  }

  function onDropEffectsChanged(event) {
    if (!event || !event.detail || event.detail.enabled !== false) return;
    dropEffectsGeneration += 1;
    Object.keys(activeAnimationCompleters).forEach(function (eventId) {
      try { activeAnimationCompleters[eventId](); } catch (_) {}
    });
    try {
      var layer = document.querySelector('.neko-forge-drop-layer');
      if (layer) layer.replaceChildren();
    } catch (_) {}
    Object.keys(dropSoundAudioByRarity).forEach(function (rarity) {
      try {
        var audio = dropSoundAudioByRarity[rarity];
        if (audio) {
          audio.pause();
          audio.currentTime = 0;
        }
      } catch (_) {}
    });
  }

  function boot() {
    ensureStyles();
    preloadTicketArt();
    preloadDropSounds();
    startForgeBadgeObserver();
    renderForgeBadge(cachedCredits || 0, false);
    window.addEventListener('neko-forge-credit-drop', onCreditDropEvent);
    window.addEventListener('neko-forge-credit-state', onCreditStateEvent);
    window.addEventListener('neko-forge-drop-effects-changed', onDropEffectsChanged);
    requestCreditStateRefresh();
    // 按钮可见时持续刷新缓存位置，隐藏后飞出仍能对准
    try {
      setInterval(function () { readVisibleSocialCenter(); }, 1000);
    } catch (_) {}
    // 浮动栏刚创建时立刻记一次
    try {
      window.addEventListener('live2d-floating-buttons-ready', function () {
        setTimeout(readVisibleSocialCenter, 50);
        setTimeout(readVisibleSocialCenter, 500);
        setTimeout(requestCreditStateRefresh, 80);
      });
    } catch (_) {}
    setTimeout(readVisibleSocialCenter, 300);
  }

  window.nekoForgeDrop = {
    play: play,
    setCredits: function (n) {
      creditStateRevision += 1;
      renderForgeBadge(n, true);
    },
    refreshCredits: function () { return Promise.resolve(false); },
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
