/**
 * app-social-ui.js — Social SSE UI 接入（M6-h follow-up）
 *
 * 监听 NEKO-PC 主进程通过 contextBridge 暴露的 window.nekoSocial 桥
 * （定义见 NEKO-PC/src/preload-common.js setupSocialBridge）：
 *
 * - NOTIFY_UNREAD     → social 按钮右上角红点 badge（唯一实现；preload 不再重复画）
 * - NOTIFY_INCOMING   → 简短 toast 提示（"📩 新消息"）
 * - QUOTA_CHANGED     → 不直接画 UI（snapshot 数据留给后续 quota panel 用）
 * - CONN_STATE        → 不画 UI（log 一行调试）
 *
 * DROP_ANIMATION（🪙 / emoji 飘落）已退役：掉券 UX 统一走 CREDIT_DROP →
 * forge-drop-overlay.js（迷你券卡）。
 *
 * 宿主检测：
 * - 非 Electron / NEKO-PC 宿主时 window.nekoSocial 不存在 → 整体 noop
 * - 有 nekoSocial 但 social_session.json 没配 → 桥仍存在但事件永不 fire，
 *   渲染端 listener 注册了但不会被调，无副作用
 *
 * 加载位置：index.html 在 avatar-ui-buttons.js 之后（social 按钮 ID 生成器
 * 已就绪）。模块用 MutationObserver 监听 social 按钮的挂载/重挂（切换 vrm/mmd
 * manager 会销毁重建按钮），按钮没就绪时先缓存 unread 值，挂载后用缓存值补画
 * badge，所以前后顺序差别不大。
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
  // 之后才挂 social 按钮），且切换 vrm/mmd manager 时按钮会被销毁重挂。缓存未读数：
  // 按钮已在场就立即画，没在场则等 MutationObserver 在挂载时补画。
  let cachedUnread = 0;

  window.nekoSocial.onUnreadCount((data) => {
    cachedUnread = (data && typeof data.value === 'number') ? data.value : 0;
    paintBadge(cachedUnread);
  });

  // social 按钮的 id 在插入 DOM 前就已设好（avatar-ui-buttons.js:419），所以新挂载
  // 的 btnWrapper 一进 DOM 就能被 querySelector 命中。监听挂载/重挂，用缓存的未读数
  // 补画——修复"先挂的按钮画了、后挂的按钮（视图切换/延迟渲染）漏画"的问题。
  const buttonObserver = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue; // 只看元素节点
        if ((node.matches && node.matches('[id$="-btn-social"]')) ||
            (node.querySelector && node.querySelector('[id$="-btn-social"]'))) {
          paintBadge(cachedUnread);
          return;
        }
      }
    }
  });
  buttonObserver.observe(document.body, { childList: true, subtree: true });

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

  // ===== 4) 连接状态（仅调试 log，无 UI） =====

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
