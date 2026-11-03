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
  var idleReconciliation = null;
  var idleRetryTimer = null;

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

  function openDisplayChannel() {
    if (channel) return;
    try {
      if (typeof BroadcastChannel === 'function') channel = new BroadcastChannel(CHANNEL_NAME);
    } catch (_) {
      channel = null;
    }
    if (!isChatFollower && channel) channel.onmessage = onDisplayMessage;
  }
  openDisplayChannel();

  function voiceBlockReason(phase) {
    return ['pending', 'invite_ready', 'joining', 'awaiting_accept'].indexOf(phase) !== -1
      ? 'VISIT_INPUT_REFUSED_NOT_READY' : 'VISIT_VOICE_UNAVAILABLE';
  }

  // 聊天窗从聊天状态判定，Pet 窗从同步过来的媒体桥状态判定。
  window.getNekoVisitVoiceBlockReason = function getNekoVisitVoiceBlockReason() {
    var bridge = window.nekoVisitParentBridge;
    var bridgeBlocked = !!(bridge && typeof bridge.blocksOrdinaryVoice === 'function'
      && bridge.blocksOrdinaryVoice());
    var bridgeReason = bridgeBlocked ? voiceBlockReason(bridge.getPhase()) : null;
    // 缓存期间可能错过新邀请，权威核对结束前不能按旧的空闲状态放行。
    if (idleReconciliation && idleReconciliation.recheckAll
        && isCurrentIdleReconciliation(idleReconciliation)) return bridgeReason || 'VISIT_VOICE_UNAVAILABLE';
    // Pet 的聊天状态可能停在 /chat 接管主连接之前，不能用它覆盖同步过来的结束状态。
    if (window.__NEKO_MULTI_WINDOW__ === true && !isChatFollower && bridge) return bridgeReason;
    var chat = window.nekoVisitChat;
    if (chat && typeof chat.isActive === 'function' && chat.isActive()) {
      return voiceBlockReason(chat.getState().phase);
    }
    return bridgeReason;
  };

  window.isNekoVisitVoiceBlocked = function isNekoVisitVoiceBlocked() {
    return !!window.getNekoVisitVoiceBlockReason();
  };

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

  function stopIdleReconciliation() {
    idleReconciliation = null;
    if (idleRetryTimer !== null) clearTimeout(idleRetryTimer);
    idleRetryTimer = null;
  }

  function isCurrentIdleReconciliation(task) {
    return idleReconciliation === task && currentCatgirl() === task.catgirl
      && window.nekoVisitParentBridge === task.bridge && (task.recheckAll || task.bridge.blocksOrdinaryVoice())
      && task.bridge.getVisitId() === task.visitId && task.bridge.getPhase() === task.phase;
  }

  function checkIdleDisplay(task) {
    if (!isCurrentIdleReconciliation(task)) {
      if (idleReconciliation === task) stopIdleReconciliation();
      return;
    }
    Promise.resolve().then(function () {
      if (!isCurrentIdleReconciliation(task)) return false;
      return task.bridge.recoverFromPageConfig();
    }).then(function (recovered) {
      if (idleReconciliation !== task) return;
      if (recovered || !isCurrentIdleReconciliation(task)) { stopIdleReconciliation(); return; }
      retryIdleDisplay(task);
    }, function () { retryIdleDisplay(task); });
  }

  function retryIdleDisplay(task) {
    if (!isCurrentIdleReconciliation(task)) {
      if (idleReconciliation === task) stopIdleReconciliation();
      return;
    }
    var delay = Math.min(1000 * Math.pow(2, task.attempt++), 30000);
    idleRetryTimer = setTimeout(function () {
      idleRetryTimer = null;
      checkIdleDisplay(task);
    }, delay);
  }

  function reconcileIdleDisplay(recheckAll) {
    if (isChatFollower) return;
    if (idleReconciliation && isCurrentIdleReconciliation(idleReconciliation)) return;
    stopIdleReconciliation();
    var bridge = window.nekoVisitParentBridge;
    if (bridge && typeof bridge.blocksOrdinaryVoice === 'function' && (recheckAll || bridge.blocksOrdinaryVoice())
        && typeof bridge.recoverFromPageConfig === 'function') {
      // 接收窗自行核对当前后端状态，迟到的空闲通知不能清掉新邀请。
      idleReconciliation = {
        bridge: bridge, catgirl: currentCatgirl(), visitId: bridge.getVisitId(), phase: bridge.getPhase(),
        recheckAll: !!recheckAll, attempt: 0
      };
      checkIdleDisplay(idleReconciliation);
    }
  }

  window.nekoRelayVisitIdleToDisplay = function relayVisitIdleToDisplay() {
    if (!isChatFollower) { reconcileIdleDisplay(); return; }
    if (!channel) return;
    try { channel.postMessage({ type: 'visit_display_idle', catgirl: currentCatgirl() }); }
    catch (_) { /* 下次权威状态恢复时重试 */ }
  };

  function onDisplayMessage(event) {
    var payload = event && event.data;
    if (!payload || (payload.type !== 'visit_display_state' && payload.type !== 'visit_display_idle')) return;
    var catgirl = currentCatgirl();
    if (payload.catgirl && catgirl && payload.catgirl !== catgirl) return;
    if (payload.type === 'visit_display_idle') { reconcileIdleDisplay(); return; }
    var frame = safeFrame(payload.frame);
    var bridge = window.nekoVisitParentBridge;
    if (!frame || !bridge || typeof bridge.onStateChange !== 'function') return;
    try { bridge.onStateChange(frame); } catch (_) { /* 下一条权威状态仍可恢复 */ }
  }

  window.addEventListener('pagehide', function () {
    stopIdleReconciliation();
    if (!channel || typeof channel.close !== 'function') return;
    try { channel.close(); } catch (_) { /* noop */ }
    channel = null;
  });

  window.addEventListener('pageshow', function (event) {
    if (!event || !event.persisted) return;
    openDisplayChannel();
    reconcileIdleDisplay(true);
  });
})();
