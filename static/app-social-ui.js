/**
 * app-social-ui.js — Social SSE UI 接入（M6-h follow-up）
 *
 * 监听 NEKO-PC 主进程通过 contextBridge 暴露的 window.nekoSocial 桥
 * （定义见 NEKO-PC/src/preload-common.js setupSocialBridge）：
 *
 * - NOTIFY_UNREAD           → 未读通知数（普通消息）
 * - LIKE_DAILY_CLAIMABLE    → 每日点赞可领（粘性：打开社区/已读不消，领取后才消）
 * - 红点显示 = max(未读, 可领?1:0)；挂在圆形 social 按钮上（非 wrapper，避免错位）
 * - NOTIFY_INCOMING         → 简短 toast 提示（"📩 新消息"）
 * - QUOTA_CHANGED           → 不直接画 UI（snapshot 数据留给后续 quota panel 用）
 * - CONN_STATE              → 不画 UI（log 一行调试）
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
 * manager 会销毁重建按钮），按钮没就绪时先缓存值，挂载后补画。
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

  // 未读通知 + 每日点赞可领（粘性）合并成一颗红点
  let cachedUnread = 0;
  let cachedClaimable = false;

  function badgeValue() {
    var unread = cachedUnread > 0 ? cachedUnread : 0;
    var claim = cachedClaimable ? 1 : 0;
    return Math.max(unread, claim);
  }

  function paintBadge() {
    // social 按钮由 avatar-ui-buttons.js apply()，prefix 可能是 'vrm' / 'mmd' / …
    const buttons = document.querySelectorAll('[id$="-btn-social"]');
    if (buttons.length === 0) return false;
    var value = badgeValue();
    buttons.forEach((btn) => {
      // 挂在圆形按钮本身（48×48），与蓝色券角标一致；挂 wrapper 会因 flex 行宽错位
      try {
        var cs = window.getComputedStyle(btn);
        if (cs && cs.position === 'static') btn.style.position = 'relative';
      } catch (_) { /* ignore */ }

      // 清掉旧版挂在 wrapper 上的残留
      var wrapper = btn.parentElement;
      if (wrapper) {
        wrapper.querySelectorAll('.' + BADGE_CLASS).forEach(function (el) {
          if (el.parentElement !== btn) el.remove();
        });
      }

      let badge = btn.querySelector('.' + BADGE_CLASS);
      if (value > 0) {
        if (!badge) {
          badge = document.createElement('span');
          badge.className = BADGE_CLASS;
          badge.style.cssText = buildBadgeStyle();
          btn.appendChild(badge);
        }
        badge.textContent = value > 99 ? '99+' : String(value);
      } else if (badge) {
        badge.remove();
      }
    });
    return true;
  }

  window.nekoSocial.onUnreadCount((data) => {
    cachedUnread = (data && typeof data.value === 'number') ? data.value : 0;
    paintBadge();
  });

  if (typeof window.nekoSocial.onLikeDailyClaimable === 'function') {
    window.nekoSocial.onLikeDailyClaimable((data) => {
      cachedClaimable = !!(data && data.claimable);
      paintBadge();
    });
  }

  // social 按钮的 id 在插入 DOM 前就已设好，新挂载时用缓存补画
  const buttonObserver = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        if ((node.matches && node.matches('[id$="-btn-social"]')) ||
            (node.querySelector && node.querySelector('[id$="-btn-social"]'))) {
          paintBadge();
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
    window.__nekoSocialQuotaSnapshot = snapshot;
  });

  // ===== 4) 连接状态（仅调试 log，无 UI） =====

  window.nekoSocial.onConnState((state) => {
    if (!state) return;
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
