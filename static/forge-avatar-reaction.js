/**
 * forge-avatar-reaction.js — L1 角色联动
 *
 * 订阅 neko-forge-credit-drop：
 * - 5rounds / idle / minigame / like_daily → 短时 applyEmotion
 * - emotion_combo → 不重复改表情（对话情感已设），仅 Live2D playMotion + 轻粒子
 * - TTS 说话中 / prefers-reduced-motion / 拖拽中 → 降级为纯 VFX 或 noop
 * - 真实到账事件 → 交给普通聊天流程，由当前人设和上下文生成回复
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
  window.addEventListener('neko-assistant-speech-cancel', function () { speaking = false; });

  var pendingDrops = [];
  var seenDrops = new Set();
  var dropTimer = null;
  var dropRequestActive = false;

  function currentCharacter() {
    return (window.appState && window.appState.lanlan_name)
      || (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
  }

  function scheduleDrop() {
    if (dropTimer || dropRequestActive || !pendingDrops.length) return;
    dropTimer = setTimeout(function () {
      dropTimer = null;
      void submitNextDrop();
    }, 500);
  }

  async function submitNextDrop() {
    var item = pendingDrops[0];
    if (!item) return;
    if (Date.now() > item.deadline
        || currentCharacter() !== item.lanlan_name) {
      pendingDrops.shift();
      scheduleDrop();
      return;
    }
    if (Date.now() < (item.retryAt || 0)) {
      scheduleDrop();
      return;
    }
    var security = window.nekoLocalMutationSecurity;
    if (!security || typeof security.getMutationHeaders !== 'function') {
      scheduleDrop();
      return;
    }
    dropRequestActive = true;
    try {
      var headers = await security.getMutationHeaders();
      // 获取 token 期间用户仍可切换角色；过期事件不再提交聊天。
      if (Date.now() > item.deadline
          || currentCharacter() !== item.lanlan_name) {
        pendingDrops.shift();
        return;
      }
      var controller = new AbortController();
      var requestTimeout = setTimeout(function () { controller.abort(); }, 10000);
      try {
        var response = await fetch('/api/card-drop/credits/react', {
          method: 'POST', credentials: 'same-origin', signal: controller.signal,
          headers: Object.assign({ 'Content-Type': 'application/json' }, headers),
          body: JSON.stringify({ event_id: item.event_id, rarity: item.rarity, lanlan_name: item.lanlan_name,
            reason: item.reason, active_count: item.active_count, is_career_first: item.is_career_first }),
        });
        if (response.status >= 500) throw new Error('drop_event_unavailable');
        if (response.ok) {
          var result = await response.json();
          if (result.ok === false) throw new Error('drop_event_not_queued');
        }
      } finally {
        clearTimeout(requestTimeout);
      }
      // 忙碌等待和回复生成由正常聊天调度器处理，不在前端另建播报队列。
      pendingDrops.shift();
    } catch (_) {
      // 重试复用同一个 event_id；后端去重可覆盖请求已成功但响应丢失的情况。
      item.retryDelay = Math.min(5000, (item.retryDelay || 500) * 2);
      item.retryAt = Date.now() + item.retryDelay;
    } finally {
      dropRequestActive = false;
      scheduleDrop();
    }
  }

  function queueDropEvent(detail) {
    // play() 本地预览和券数快照没有云端 event_id，不应让猫娘开口。
    var id = detail && detail.event_id;
    if (typeof id !== 'string' || !/^[0-9a-f-]{36}$/i.test(id) || seenDrops.has(id)) return;
    var character = currentCharacter();
    if (!character || pendingDrops.length >= 8) return;
    seenDrops.add(id);
    if (seenDrops.size > 256) seenDrops.delete(seenDrops.values().next().value);
    pendingDrops.push({ event_id: id, rarity: detail.rarity || 'N', lanlan_name: character,
      reason: detail.reason, active_count: detail.active_count, is_career_first: detail.is_career_first,
      deadline: Date.now() + 45000 });
    scheduleDrop();
  }

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
    if (window.forgeDropEffectsEnabled === false) return;
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
    var detail = (event && event.detail) || {};
    try { react(detail); } catch (_) {}
    try { queueDropEvent(detail); } catch (_) {}
  });
})();
