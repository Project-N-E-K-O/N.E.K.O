/*
 * 串门传输页：父页 ↔ iframe 的同步入口与 postMessage（设计稿 §3.3.1 / §4.4）。
 *
 * window.__nekoVisitFrameSink 只在这里一次性构造完整对象 {onFrame, suspend, setAuthToken}；
 * 其它模块只提供实现，不得整体赋值覆盖（否则父页调到的 setAuthToken 会是 undefined、
 * iframe 永远连不上传输 WS）。帧触发不走 postMessage：异步任务里父页后备缓冲已被合成清空。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};

  var LOG_PER_S = 5;
  var logTimes = [];
  var suspended = false;
  var parentMessageHandlers = {};

  function postToParent(msg) {
    try {
      root.parent.postMessage(msg, root.location.origin);
    } catch (_) {
      // 父页已卸载
    }
  }

  /** iframe 没有 preload，console 不会被镜像到 Chat 窗：关键日志转给父页。永不含凭证、票据、正文。 */
  function log(level, msg, code) {
    var now = Date.now();
    while (logTimes.length && now - logTimes[0] > 1000) logTimes.shift();
    if (logTimes.length >= LOG_PER_S) return;
    logTimes.push(now);
    var entry = { t: 'log', level: level === 'error' || level === 'warn' ? level : 'info', msg: String(msg).slice(0, 500) };
    if (code) entry.code = String(code).slice(0, 64);
    postToParent(entry);
  }

  root.__nekoVisitFrameSink = {
    onFrame: function (sourceCanvas, rectPx, tsMs) {
      if (suspended) return false;
      var packer = NVT.packer;
      if (!packer) return false;
      try {
        return packer.onFrame(sourceCanvas, rectPx, tsMs);
      } catch (err) {
        log('warn', 'pack failed: ' + (err && err.name), 'pack_failed');
        return false;
      }
    },
    suspend: function (on) {
      suspended = !!on;
    },
    setAuthToken: function (csrfToken) {
      if (typeof csrfToken !== 'string' || !csrfToken) return;
      if (NVT.controller) NVT.controller.onAuthToken(csrfToken);
    },
  };

  root.addEventListener('message', function (event) {
    if (event.source !== root.parent || event.origin !== root.location.origin) return;
    var data = event.data;
    if (!data || typeof data !== 'object' || typeof data.t !== 'string') return;
    var handler = parentMessageHandlers[data.t];
    if (handler) handler(data);
  });

  NVT.frameSink = {
    postToParent: postToParent,
    log: log,
    isSuspended: function () { return suspended; },
    /** 父页消息：crop / place / hidden / visit_state。 */
    onParentMessage: function (type, handler) {
      parentMessageHandlers[type] = handler;
    },
  };
  NVT.log = log;
})(typeof window !== 'undefined' ? window : globalThis);
