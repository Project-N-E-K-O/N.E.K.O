/**
 * app-social-ui.js — Social SSE UI 接入（M6-h follow-up）
 *
 * 监听 NEKO-PC 主进程通过 contextBridge 暴露的 window.nekoSocial 桥
 * （定义见 NEKO-PC/src/preload-common.js setupSocialBridge），把 5 个
 * SSE 事件渲染到 Pet UI：
 *
 * - NOTIFY_UNREAD     → social 按钮右上角红点 badge（vrm-btn-social /
 *                       mmd-btn-social 通配 selector）
 * - NOTIFY_INCOMING   → 简短 toast 提示（"📩 新消息"）
 * - QUOTA_CHANGED     → 不直接画 UI（snapshot 数据留给后续 quota panel 用）
 * - DROP_ANIMATION    → body 顶部飘下一个 🪙 + CSS keyframes 旋转下坠
 * - CONN_STATE        → 不画 UI（log 一行调试）
 *
 * 宿主检测：
 * - 非 Electron / NEKO-PC 宿主时 window.nekoSocial 不存在 → 整体 noop
 * - 有 nekoSocial 但 social_session.json 没配 → 桥仍存在但事件永不 fire，
 *   渲染端 listener 注册了但不会被调，无副作用
 *
 * 加载位置：index.html 在 avatar-ui-buttons.js 之后（social 按钮 ID 生成器
 * 已就绪），但模块自带 500ms × 30 次轮询，按钮没渲染时缓存 unread 值，
 * 渲染好以后回画一次 badge，所以前后顺序差别不大。
 */

(function () {
  'use strict';

  if (!window.nekoSocial) {
    // 浏览器直开 NEKO frontend / 没 contextBridge 桥 — pipeline 不存在，模块 noop
    return;
  }

  // ===== 1) 红点 badge =====

  const BADGE_CLASS = 'neko-social-unread-badge';

  function buildBadgeStyle() {
    return [
      'position: absolute',
      'top: -4px',
      'right: -4px',
      'min-width: 18px',
      'height: 18px',
      'padding: 0 5px',
      'background: #ff4d4f',
      'color: #ffffff',
      'font-size: 11px',
      'font-weight: 600',
      'border-radius: 9px',
      'display: flex',
      'align-items: center',
      'justify-content: center',
      'pointer-events: none',
      'box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25)',
      'z-index: 10',
      'box-sizing: border-box',
      'line-height: 1',
    ].join(';');
  }

  function paintBadge(value) {
    // social 按钮由 avatar-ui-buttons.js apply()，prefix 可能是 'vrm' 或 'mmd'。
    // 用 attribute selector 通配两种 manager 同时挂载的情况，画到两个按钮上。
    const buttons = document.querySelectorAll('[id$="-btn-social"]');
    if (buttons.length === 0) return false;
    buttons.forEach((btn) => {
      const wrapper = btn.parentElement; // btnWrapper（avatar-ui-buttons.js line 408 起创建，position:relative）
      if (!wrapper) return;
      let badge = wrapper.querySelector('.' + BADGE_CLASS);
      if (value > 0) {
        if (!badge) {
          badge = document.createElement('span');
          badge.className = BADGE_CLASS;
          badge.style.cssText = buildBadgeStyle();
          wrapper.appendChild(badge);
        }
        badge.textContent = value > 99 ? '99+' : String(value);
      } else if (badge) {
        badge.remove();
      }
    });
    return true;
  }

  // 首次 onUnreadCount 触发时按钮可能还没渲染（avatar-ui-buttons 在 vrm/mmd-init
  // 之后才挂 social 按钮），缓存值 + 短时间轮询直到画上。
  let cachedUnread = 0;
  let retryHandle = null;

  function scheduleRetry() {
    if (retryHandle != null) return;
    let retries = 0;
    retryHandle = setInterval(() => {
      retries += 1;
      const painted = paintBadge(cachedUnread);
      if (painted || retries >= 30) {
        clearInterval(retryHandle);
        retryHandle = null;
      }
    }, 500);
  }

  window.nekoSocial.onUnreadCount((data) => {
    cachedUnread = (data && typeof data.value === 'number') ? data.value : 0;
    if (!paintBadge(cachedUnread)) {
      scheduleRetry();
    }
  });

  // ===== 2) 新通知 toast =====

  window.nekoSocial.onNotifyIncoming((notif) => {
    // notif 内含 { kind, title, body, payload?, unread_count? }
    // unread_count 由 sse-client.js handleEvent 'notify' 分支派生为独立的 NOTIFY_UNREAD
    // broadcast，所以这里只负责 toast 提示。
    if (!notif) return;
    const title = notif.title || (notif.kind === 'reward' ? '获得奖励' : '新消息');
    try {
      if (typeof window.electronToast === 'function') {
        window.electronToast('📩 ' + title);
      } else if (typeof window.showToast === 'function') {
        window.showToast('📩 ' + title);
      }
    } catch (_) { /* silent */ }
  });

  // ===== 3) 配额变化（不画 UI；保留 hook 给后续 quota panel） =====

  window.nekoSocial.onQuotaChanged((snapshot) => {
    if (!snapshot) return;
    // 把最新 snapshot 挂到 window 给其他模块拿（不主动触发任何 UI）
    window.__nekoSocialQuotaSnapshot = snapshot;
  });

  // ===== 4) DROP_ANIMATION =====

  const DROP_KEYFRAMES_ID = 'neko-quota-drop-keyframes';
  const DROP_ANIMATION_NAME = 'neko-quota-drop';

  function ensureDropKeyframes() {
    if (document.getElementById(DROP_KEYFRAMES_ID)) return;
    const style = document.createElement('style');
    style.id = DROP_KEYFRAMES_ID;
    style.textContent =
      '@keyframes ' + DROP_ANIMATION_NAME + ' {' +
      '  0%   { transform: translateY(-40px) rotate(0deg);   opacity: 0; }' +
      '  20%  { transform: translateY(20px)  rotate(20deg);  opacity: 1; }' +
      '  80%  { transform: translateY(180px) rotate(300deg); opacity: 1; }' +
      '  100% { transform: translateY(240px) rotate(360deg); opacity: 0; }' +
      '}';
    document.head.appendChild(style);
  }

  function playDropAnimation(payload) {
    ensureDropKeyframes();
    // payload: { reason: 'quota_bonus_increased', delta?: number, snapshot?: {...} }
    // 当前只播单枚硬币动画；delta > 1 时连播几枚做"多枚"提示。
    const count = Math.max(1, Math.min(5, payload && typeof payload.delta === 'number' ? payload.delta : 1));
    for (let i = 0; i < count; i += 1) {
      setTimeout(() => {
        const coin = document.createElement('div');
        coin.textContent = '🪙';
        coin.style.cssText = [
          'position: fixed',
          'top: -20px',
          'right: ' + (32 + i * 16) + 'px',
          'font-size: 36px',
          'z-index: 99999',
          'pointer-events: none',
          'user-select: none',
          'animation: ' + DROP_ANIMATION_NAME + ' 1.6s ease-out forwards',
        ].join(';');
        document.body.appendChild(coin);
        setTimeout(() => { try { coin.remove(); } catch (_) {} }, 1900);
      }, i * 120); // 错开 120ms 播下一枚
    }
  }

  window.nekoSocial.onDropAnimation(playDropAnimation);

  // ===== 5) 连接状态（仅调试 log，无 UI） =====

  window.nekoSocial.onConnState((state) => {
    if (!state) return;
    // 写到 window 上方便用户从 console 看一眼
    window.__nekoSocialConnState = state;
    try {
      if (state.connected) {
        console.info('[neko-social] SSE connected');
      } else {
        console.warn('[neko-social] SSE disconnected:', state.error || 'unknown');
      }
    } catch (_) { /* silent */ }
  });
})();
