/*
 * 串门传输页 ↔ 本机后端：WS /api/visit/transport/ws（设计稿 §3.5.7 / §4.3，OD-29）。
 *
 * 整个 iframe 里唯一一处建 WebSocket；URL 只由 location.protocol / location.host 拼出，
 * 不接受任何外来 URL。连上后首帧 {type:'auth', csrf_token}，token 只来自父页经
 * __nekoVisitFrameSink.setAuthToken() 注入，不从任何 JSON 端点取。
 *
 * 关闭规则：socket 一关（任何 close code）就同步停掉全部媒体并离开 vendor 房间，再谈重连——
 * 后端崩溃时没有人能发 stop，对端的 35 s 重入宽限从此刻起算。4409（被同 vid 的新 iframe 顶掉）
 * 是终态：停媒体、不离房（新 iframe 入房会把这里顶下线）、不重连；4403 / 4404 终态、离房、不重连；
 * 其余在 20 s 内按 1 s 退避重连。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};

  var RECONNECT_DELAY_MS = 1000;
  var RECONNECT_WINDOW_MS = 20000;   // VISIT_LOCAL_PAGE_GRACE_S
  var CLOSE_UNAUTHORIZED = 4403;
  var CLOSE_UNKNOWN_VISIT = 4404;
  var CLOSE_SUPERSEDED = 4409;
  var DOWNLINK = { credentials: 1, media: 1, send: 1, stop: 1 };
  // 连上后稳定这么久（或收到一条合法下行）才算后端会话恢复，断线时限才清零
  var STABLE_OPEN_MS = 5000;
  // 一直停在 CONNECTING（黑洞网络 / 卡住的代理）也要有个头：首连最多等这么久，重连等到重连窗口用完
  var CONNECT_TIMEOUT_MS = 10000;
  var TERMINAL_LEAVE_WAIT_MS = 5000;  // 终止时等离房的上限

  function transportUrl(loc, visitId, side) {
    var scheme = loc.protocol === 'https:' ? 'wss:' : 'ws:';
    return scheme + '//' + loc.host + '/api/visit/transport/ws?visit_id=' +
      encodeURIComponent(visitId) + '&side=' + encodeURIComponent(side);
  }

  /**
   * hooks: onOpen() / onMessage(msg) / stopAllMedia() / leaveRoom(reason)（返回离房完成的 promise）/ onTerminal(code)。
   */
  function BackendLink(opts) {
    this.visitId = opts.visitId;
    this.side = opts.side;
    this.hooks = opts.hooks;
    this.now = opts.now || function () { return Date.now(); };
    this.token = null;
    this.ws = null;
    this.lostAt = null;
    this.stableTimer = null;
    this.connectTimer = null;
    this.reconnectTimer = null;
    this.terminal = false;
    this.stopped = false;
  }

  BackendLink.prototype.start = function (token) {
    if (typeof token !== 'string' || !token) return;
    this.token = token;
    if (!this.ws && !this.terminal && this.reconnectTimer === null) this.connect();
  };

  BackendLink.prototype.connect = function () {
    if (this.terminal || !this.token) return;
    var self = this;
    var ws = new WebSocket(transportUrl(root.location, this.visitId, this.side));
    this.ws = ws;
    var wait = this.lostAt === null ? CONNECT_TIMEOUT_MS : Math.max(0, RECONNECT_WINDOW_MS - (this.now() - this.lostAt));
    this.clearConnectTimer();
    this.connectTimer = setTimeout(function () {
      self.connectTimer = null;
      if (ws !== self.ws || ws.readyState !== 0) return;
      // 卡在连接中：主动关掉并按断开处理（浏览器的 close 事件可能迟到，之后会因 ws 已换代被忽略）
      try {
        ws.close();
      } catch (_) {
        // 已关闭
      }
      if (ws === self.ws && typeof ws.onclose === 'function') ws.onclose({ code: 1006 });
    }, wait);
    ws.onopen = function () {
      if (ws !== self.ws) return;
      self.clearConnectTimer();
      // 主线程卡住时连接可能在重连窗口过后才打开：过期的连接不接受，按连不上终止
      if (self.lostAt !== null && self.now() - self.lostAt >= RECONNECT_WINDOW_MS) {
        self.ws = null;
        try {
          ws.close();
        } catch (_) {
          // 已关闭
        }
        self.terminal = true;
        self.hooks.onTerminal(1006);
        return;
      }
      // 只是握手成功不算恢复：鉴权前就被关掉的反复连 / 断不能一直把断线时限往后推
      self.clearStable();
      self.stableTimer = setTimeout(function () {
        self.stableTimer = null;
        if (ws === self.ws) self.lostAt = null;
      }, STABLE_OPEN_MS);
      ws.send(JSON.stringify({ type: 'auth', csrf_token: self.token }));
      self.hooks.onOpen();
    };
    ws.onmessage = function (event) {
      if (ws !== self.ws || typeof event.data !== 'string') return;
      var msg;
      try {
        msg = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      if (msg && typeof msg === 'object' && DOWNLINK[msg.type]) {
        self.lostAt = null;
        self.hooks.onMessage(msg);
      }
    };
    ws.onclose = function (event) {
      if (ws !== self.ws) return;
      self.ws = null;
      self.clearStable();
      self.clearConnectTimer();
      var code = event && event.code;
      // 先同步停媒体、离房，再决定重连：都不依赖后端的任何计时器
      self.hooks.stopAllMedia();
      var leaving = code !== CLOSE_SUPERSEDED ? self.hooks.leaveRoom('backend_socket_closed') : null;
      if (self.stopped || code === CLOSE_SUPERSEDED || code === CLOSE_UNAUTHORIZED || code === CLOSE_UNKNOWN_VISIT) {
        self.terminal = true;
        self.finishAfterLeave(leaving, code);
        return;
      }
      var now = self.now();
      if (self.lostAt === null) self.lostAt = now;
      if (now - self.lostAt >= RECONNECT_WINDOW_MS) {
        self.terminal = true;
        self.finishAfterLeave(leaving, code);
        return;
      }
      // 重连至少隔 1 s，并且等上一次离房真正结束：同一个 vendor 实例在 exitRoom 未完成时再
      // enterRoom，迟到的退出可能拆掉新房间。离房最多等到 20 s 重连窗口结束
      var delayDone = new Promise(function (resolve) {
        self.reconnectTimer = setTimeout(function () {
          self.reconnectTimer = null;
          resolve();
        }, RECONNECT_DELAY_MS);
      });
      var leftOrGaveUp = Promise.race([
        Promise.resolve(leaving).then(function () { return 'left'; }, function () { return 'left'; }),
        new Promise(function (resolve) {
          setTimeout(function () { resolve('deadline'); }, Math.max(0, RECONNECT_WINDOW_MS - (now - self.lostAt)));
        }),
      ]);
      Promise.all([delayDone, leftOrGaveUp]).then(function (res) {
        if (self.terminal || self.ws) return;
        if (res[1] === 'deadline') {
          // 离房到重连窗口结束都没完成：不能在同一个 vendor 实例上重新入房，按连不上处理
          self.terminal = true;
          self.hooks.onTerminal(code);
          return;
        }
        self.connect();
      });
    };
    ws.onerror = function () {
      // 关闭由 onclose 统一处理
    };
  };

  BackendLink.prototype.send = function (msg) {
    var ws = this.ws;
    if (!ws || ws.readyState !== 1) return false;
    try {
      ws.send(JSON.stringify(msg));
      return true;
    } catch (_) {
      return false;
    }
  };

  /** 后端 stop 之后本侧主动收线：终态，不再重连。 */
  /**
   * 终止前先等离房做完（最多 TERMINAL_LEAVE_WAIT_MS）：否则控制器拆掉适配器时 exitRoom 还在路上，
   * 对端要等 vendor 超时才看到这边走了。被同 vid 顶号（4409）不离房，直接终止。
   */
  BackendLink.prototype.finishAfterLeave = function (leaving, code) {
    var self = this;
    if (!leaving) {
      self.hooks.onTerminal(code);
      return;
    }
    var done = false;
    var finish = function () {
      if (done) return;
      done = true;
      self.hooks.onTerminal(code);
    };
    setTimeout(finish, TERMINAL_LEAVE_WAIT_MS);
    Promise.resolve(leaving).then(finish, finish);
  };

  BackendLink.prototype.clearConnectTimer = function () {
    if (this.connectTimer !== null) clearTimeout(this.connectTimer);
    this.connectTimer = null;
  };

  BackendLink.prototype.clearStable = function () {
    if (this.stableTimer !== null) clearTimeout(this.stableTimer);
    this.stableTimer = null;
  };

  BackendLink.prototype.close = function () {
    this.stopped = true;
    this.terminal = true;
    this.clearStable();
    this.clearConnectTimer();
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.ws) {
      try {
        this.ws.close(1000, 'stopped');
      } catch (_) {
        // 已关闭
      }
    }
  };

  NVT.backend = {
    BackendLink: BackendLink,
    transportUrl: transportUrl,
    RECONNECT_WINDOW_MS: RECONNECT_WINDOW_MS,
  };
})(typeof window !== 'undefined' ? window : globalThis);
