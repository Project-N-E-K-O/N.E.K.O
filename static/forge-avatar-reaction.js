/**
 * forge-avatar-reaction.js — L1 角色联动
 *
 * 订阅 neko-forge-credit-drop：
 * - 5rounds / idle / minigame / like_daily → 短时 applyEmotion
 * - emotion_combo → 不重复改表情（对话情感已设），仅 Live2D playMotion + 轻粒子
 * - TTS 说话中 / prefers-reduced-motion / 拖拽中 → 降级为纯 VFX 或 noop
 */
(function () {
  'use strict';

  if (window.__nekoForgeAvatarReactionInstalled__) return;
  window.__nekoForgeAvatarReactionInstalled__ = true;

  var REASON_EMOTION = {
    '5rounds': 'happy',
    idle: 'surprised',
    minigame: 'excited',
    like_daily: 'happy',
  };

  var speaking = false;
  var lastReactionAt = 0;
  var DEBOUNCE_MS = 800;

  window.addEventListener('neko-assistant-speech-start', function () { speaking = true; });
  window.addEventListener('neko-assistant-speech-end', function () { speaking = false; });

  function reducedMotion() {
    try {
      if (window.__nekoPetInteracting__) return true;
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (_) {
      return false;
    }
  }

  function spawnLightVfx(rarity) {
    try {
      if (typeof document === 'undefined' || !document.body) return;
      var count = rarity === 'UR' || rarity === 'SSR' ? 4 : 2;
      for (var i = 0; i < count; i++) {
        var el = document.createElement('div');
        el.textContent = '✦';
        el.style.cssText = [
          'position:fixed',
          'left:' + (48 + Math.random() * 40) + '%',
          'top:' + (36 + Math.random() * 20) + '%',
          'font-size:14px',
          'color:#ffe08a',
          'pointer-events:none',
          'z-index:2147483630',
          'opacity:0',
          'transition:opacity .2s, transform .9s ease-out',
        ].join(';');
        document.body.appendChild(el);
        (function (node, idx) {
          setTimeout(function () {
            node.style.opacity = '1';
            node.style.transform = 'translateY(-28px)';
          }, 20 + idx * 40);
          setTimeout(function () { try { node.remove(); } catch (_) {} }, 1000);
        })(el, i);
      }
    } catch (_) {}
  }

  function react(detail) {
    var now = Date.now();
    if (now - lastReactionAt < DEBOUNCE_MS) return;
    lastReactionAt = now;

    var reason = (detail && detail.reason) || '';
    var rarity = String((detail && detail.rarity) || 'N').toUpperCase();
    var skipHeavy = speaking || reducedMotion();

    if (skipHeavy) {
      spawnLightVfx(rarity);
      return;
    }

    if (reason === 'emotion_combo') {
      try {
        if (window.LanLan1 && typeof window.LanLan1.playMotion === 'function') {
          window.LanLan1.playMotion('happy');
        }
      } catch (_) {}
      spawnLightVfx(rarity);
      return;
    }

    var emotion = REASON_EMOTION[reason];
    if (emotion && typeof window.applyEmotion === 'function') {
      try { window.applyEmotion(emotion); } catch (_) {}
    }
    if (rarity === 'SR' || rarity === 'SSR' || rarity === 'UR') {
      spawnLightVfx(rarity);
    }
  }

  window.addEventListener('neko-forge-credit-drop', function (event) {
    try { react((event && event.detail) || {}); } catch (_) {}
  });
})();
