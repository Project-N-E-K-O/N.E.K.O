/* 共享 SSE 通信（#2822 通道）：run 完成事件 + 状态/日志刷新信号。

- EventSource 懒加载单例：连接 /plugin/{plugin_id}/ui-api/events
- type:"run"（后端 runs bus 桥接）→ 按 run_id 完成 call()/callPlugin()
  的等待，替代前端紧轮询 /runs/{id}
- type:"logs" / type:"status"（插件经 /ui-api/push 推送）→ 分发给页面注册的 handler
- 兜底：awaitRun 内 2s 慢轮询 + 总超时，SSE 断线/丢帧时仍能完成请求

用法：
    UISSE.on('status', function(){ loadRuntimeStatus(); loadAttention(); });
    var status = await UISSE.awaitRun(runId, { fetchStatus: async (id) => 'succeeded'|null });
*/
(function () {
  'use strict';
  var es = null;
  var runHandlers = {};   // run_id -> callback(status)
  var typeHandlers = {};  // type -> callback(data)

  function ensureEs() {
    if (es) return es;
    var m = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
    var pluginId = m ? m[1] : 'qq_auto_reply';
    try {
      es = new EventSource('/plugin/' + encodeURIComponent(pluginId) + '/ui-api/events');
    } catch (e) {
      es = null;
      return null;
    }
    es.onmessage = function (ev) {
      var data;
      try { data = JSON.parse(ev.data); } catch (e) { return; }
      if (!data || typeof data.type !== 'string') return;
      if (data.type === 'run') {
        var cb = runHandlers[data.run_id];
        if (cb) { delete runHandlers[data.run_id]; cb(data.status); }
        return;
      }
      var h = typeHandlers[data.type];
      if (h) h(data);
    };
    // 断线时浏览器自动重连；期间的漏帧由各调用方的慢轮询兜底
    return es;
  }

  /**
   * 等待一个 run 到达终端状态。
   * opts:
   *   timeout     总超时（默认 20000ms）
   *   pollInterval 兜底轮询间隔（默认 2000ms）
   *   fetchStatus async (runId) -> 终端状态串 'succeeded'|'failed'|'canceled'|'timeout'，未终态返回 null/undefined
   * resolve: 终端状态串；reject: Error(status 或 'timeout')
   */
  function awaitRun(runId, opts) {
    opts = opts || {};
    var timeout = opts.timeout || 20000;
    var pollInterval = opts.pollInterval || 2000;
    var fetchStatus = opts.fetchStatus;
    var deadline = Date.now() + timeout;
    return new Promise(function (resolve, reject) {
      var done = false;
      var timer = null;
      var iv = null;
      function finish(fn, arg) {
        if (done) return;
        done = true;
        if (timer) clearTimeout(timer);
        if (iv) clearInterval(iv);
        if (runHandlers[runId]) delete runHandlers[runId];
        fn(arg);
      }
      // SSE 提前完成：run 事件在同一同步块内注册，不可能在注册前到达
      ensureEs();
      runHandlers[runId] = function (status) {
        if (status === 'succeeded') finish(resolve, status);
        else finish(reject, new Error(status));
      };
      timer = setTimeout(function () { finish(reject, new Error('timeout')); }, timeout);
      // 慢轮询兜底（SSE 断线/丢帧时保证完成）
      if (typeof fetchStatus === 'function') {
        iv = setInterval(async function () {
          if (done) return;
          if (Date.now() > deadline) { finish(reject, new Error('timeout')); return; }
          try {
            var status = await fetchStatus(runId);
            if (!status) return;
            if (status === 'succeeded') finish(resolve, status);
            else finish(reject, new Error(status));
          } catch (e) { /* 单次轮询失败忽略，下一 tick 重试 */ }
        }, pollInterval);
      }
    });
  }

  function on(type, handler) {
    if (typeof handler === 'function') {
      typeHandlers[type] = handler;
      ensureEs();  // 注册处理器即建立 EventSource——页面只 on('status', ...) 就能收实时刷新
    }
  }

  window.UISSE = { ensureEs: ensureEs, awaitRun: awaitRun, on: on };
})();
