/**
 * 串门生命周期的跨窗口桥。
 *
 * Electron 多窗口模式里，/chat 跟随窗通常持有最新的主 WebSocket，真正负责
 * 截图和媒体传输的却是 index Pet 窗。把 visit_state_change 复制给 Pet 窗，
 * 避免邀请已经建好、传输 iframe 却一直没有启动。
 */
(function () {
  'use strict';

  var CHANNEL_NAME = 'neko_visit_display_v1';
  var isChatFollower = /^\/chat(?:_full)?(?:\/|$)/.test(
    (window.location && window.location.pathname) || ''
  );
  var channel = null;

  function currentCatgirl() {
    var config = window.lanlan_config;
    return config && typeof config.lanlan_name === 'string' ? config.lanlan_name : '';
  }

  function safeFrame(frame) {
    if (!frame || frame.type !== 'visit_state_change') return null;
    var copy = { type: 'visit_state_change' };
    [
      'action', 'side', 'visit_id', 'ts', 'peer_crop', 'ends_at',
      'reason', 'transport'
    ].forEach(function (key) {
      var value = frame[key];
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        copy[key] = value;
      }
    });
    if (typeof copy.action !== 'string' || typeof copy.visit_id !== 'string') return null;
    return copy;
  }

  try {
    if (typeof BroadcastChannel === 'function') channel = new BroadcastChannel(CHANNEL_NAME);
  } catch (_) {
    channel = null;
  }

  window.nekoRelayVisitStateToDisplay = function relayVisitStateToDisplay(frame) {
    if (!isChatFollower || !channel) return false;
    var normalized = safeFrame(frame);
    if (!normalized) return false;
    try {
      channel.postMessage({
        type: 'visit_display_state',
        catgirl: currentCatgirl(),
        frame: normalized
      });
      return true;
    } catch (_) {
      return false;
    }
  };

  if (!isChatFollower && channel) {
    channel.onmessage = function (event) {
      var payload = event && event.data;
      if (!payload || payload.type !== 'visit_display_state') return;
      var catgirl = currentCatgirl();
      if (payload.catgirl && catgirl && payload.catgirl !== catgirl) return;
      var frame = safeFrame(payload.frame);
      var bridge = window.nekoVisitParentBridge;
      if (!frame || !bridge || typeof bridge.onStateChange !== 'function') return;
      try { bridge.onStateChange(frame); } catch (_) { /* 下一条权威状态仍可恢复 */ }
    };
  }

  window.addEventListener('pagehide', function () {
    if (!channel || typeof channel.close !== 'function') return;
    try { channel.close(); } catch (_) { /* noop */ }
    channel = null;
  }, { once: true });
})();
