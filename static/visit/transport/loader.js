/*
 * 串门传输页：能力门与控制器（设计稿 §3.3.4 / §4.3 / §4.4）。
 *
 * 能力门拆两段：
 *   预检（每条传输 WS 连上、auth 之后就跑，与 transport 无关）：① isSecureContext；
 *   ② 原生 WebSocket 且 RTCPeerConnection 在场——只查数据通道必需的能力。
 *   SDK 段（收到 credentials 之后）：按 credentials.transport 动态插一份随包分发的 SDK
 *   （SRI 校验固定版本）→ ③ TRTC.isSupported() / RTCRtpSender.getCapabilities；视频原语
 *   按侧位在这里查（guest：2D 抓帧画布 + captureStream；host：WebGL 解包画布），缺了只
 *   video_ok:false，串门照常（文字 + 字幕）。
 * 预检段不引用任何 vendor 全局；vendor SDK 只在这里、只在 iframe 内、按需加载。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};

  // 随包分发的固定版本（static/libs/THIRD_PARTY_NOTICES.md 登记版本与 sha256；静态测试钉住）
  var SDK_INTEGRITY = {
    trtc: 'sha256-mnm38g4liu1FedNS1ZrCSC1Yk35TRBemiy5h6Y9KbCU=',
    livekit: 'sha256-f6F+N69emW2KJfFaY33MBiAhW8AbOU5dIJ9yav59wE0=',
  };
  var PAGE_QUERY_KEYS = ['v', 'side', 'visit_id'];
  // TRTC SDK 的调试插件会按 URL 参数 / sessionStorage 从外部 CDN 拉脚本：载入前清掉这两个开关
  var TRTC_DEBUG_STORAGE_KEYS = ['TRTC_ENABLE_DEBUG_PLUGIN', 'TRTC_DEBUG_DIALOG_PATH'];
  var STATS_INTERVAL_MS = 5000;
  var STATS_COLLECT_TIMEOUT_MS = 4000;  // 异步统计（getStats）单拍的上限：短于取样周期，卡住时按没拿到处理
  var HIDDEN_REPORT_MIN_MS = 1000;
  var PEER_STATS_FRESH_MS = 12000;
  var SOFTENC_WINDOW_MS = 10000;      // VISIT_VP9_CPU_FALLBACK 的秒数
  var SOFTENC_FPS_RATIO = 0.9;        // 27 / 30：编码帧率低于目标帧率的 90 % 算软编跟不上
  var PUBLISH_RETRY_BASE_MS = 2000;   // SDK 拒绝发布后的首次重试间隔，之后翻倍
  var PUBLISH_RETRY_MAX_MS = 30000;
  var SDK_LOAD_TIMEOUT_MS = 10000;    // 本地 SDK 脚本加载的上限：一直不报 load / error 时按加载失败上报（后端首次入房留了 25 s）
  var JOIN_ATTEMPT_TIMEOUT_MS = 10000; // 中途断线后重进时单次入房的上限：网络切换时 SDK 的入房可能既不成功也不失败
  var REJOIN_LEAVE_WAIT_MS = 5000;    // 手动重新入房前离房的上限：操作链卡住时 leave 不会结束
  var STOP_LEAVE_WAIT_MS = 5000;      // 收到 stop 后等离房的上限（与 backend-ws 终止时同值；父页 ended 后最多留 iframe 6 s）
  var TEARDOWN_IDLE_WAIT_MS = 2000;   // 拆适配器前等在途 SDK 操作的上限（父页对被顶号的 iframe 按 2 + 15 + 8 s 再留余量）
  // 后端判自己断线是断开后 25 s：手动重新入房失败时在这个窗口内按退避再试，不让一次暂时性失败拖到判死
  var REJOIN_RETRY_WINDOW_MS = 20000;
  var REJOIN_RETRY_BASE_MS = 1000;
  var VISIT_ID_RE = /^[A-Za-z0-9_-]{22}$/;
  var IN_ROOM = { joined: 1, connected: 1 };

  function isNativeFunction(fn) {
    try {
      return typeof fn === 'function' && /\{\s*\[native code\]\s*\}\s*$/.test(Function.prototype.toString.call(fn));
    } catch (_) {
      return false;
    }
  }

  /** 预检段（①②）：只看本 frame 的原生能力，不碰 vendor SDK。 */
  function runPreflight(win) {
    if (!win.isSecureContext) return { ok: false, reason: 'insecure_context' };
    var WS = win.WebSocket;
    var wsNative = !!WS && WS.name === 'WebSocket' && isNativeFunction(WS) &&
      isNativeFunction(WS.prototype && WS.prototype.send) &&
      Object.getPrototypeOf(WS.prototype) === win.EventTarget.prototype;
    if (!wsNative) return { ok: false, reason: 'foreign_websocket' };
    if (typeof win.RTCPeerConnection !== 'function') return { ok: false, reason: 'no_webrtc' };
    return { ok: true };
  }

  /** guest 视频原语：2D 抓帧画布 + captureStream。 */
  function guestVideoPrimitives(doc) {
    try {
      var c = doc.createElement('canvas');
      if (!c.getContext('2d', { alpha: false }) || typeof c.captureStream !== 'function') return false;
      // 打包靠 captureStream(0) + requestFrame() 手动出帧：实际建一次轨确认，不只看方法在不在
      var stream = c.captureStream(0);
      var track = stream && stream.getVideoTracks && stream.getVideoTracks()[0];
      var ok = !!track && typeof track.requestFrame === 'function';
      if (track && typeof track.stop === 'function') track.stop();
      return ok;
    } catch (_) {
      return false;
    }
  }

  /** 页面查询串只允许本页自己的三个参数（vendor SDK 会读 location.search 里的调试开关）。 */
  function queryIsClean(search) {
    var params = new URLSearchParams(search);
    var ok = true;
    params.forEach(function (_value, key) {
      if (PAGE_QUERY_KEYS.indexOf(key) === -1) ok = false;
    });
    return ok;
  }

  function scrubVendorDebugSwitches(win) {
    try {
      TRTC_DEBUG_STORAGE_KEYS.forEach(function (k) { win.sessionStorage.removeItem(k); });
    } catch (_) {
      // 存储不可用时 SDK 同样读不到
    }
  }

  function loadScriptOnce(win, transport, version) {
    var vendor = NVT.vendors && NVT.vendors[transport];
    if (!vendor || !SDK_INTEGRITY[transport]) return Promise.reject(new Error('unknown transport'));
    if (win[vendor.globalName]) return Promise.resolve(win[vendor.globalName]);
    if (!queryIsClean(win.location.search)) return Promise.reject(new Error('unexpected page query'));
    scrubVendorDebugSwitches(win);
    return new Promise(function (resolve, reject) {
      var s = win.document.createElement('script');
      s.src = vendor.script + (version ? '?v=' + encodeURIComponent(version) : '');
      s.integrity = SDK_INTEGRITY[transport];
      s.crossOrigin = 'anonymous';
      s.async = true;
      // 请求卡住时 load / error 都不来：到点按加载失败处理，能力门照常上报，不让这一场等到后端的入房时限
      var timer = setTimeout(function () {
        s.onload = null;
        s.onerror = null;
        if (typeof s.remove === 'function') s.remove();
        reject(new Error('sdk load timeout'));
      }, SDK_LOAD_TIMEOUT_MS);
      s.onload = function () {
        clearTimeout(timer);
        if (win[vendor.globalName]) resolve(win[vendor.globalName]);
        else reject(new Error('sdk global missing'));
      };
      s.onerror = function () {
        clearTimeout(timer);
        reject(new Error('sdk load failed'));
      };
      win.document.head.appendChild(s);
    });
  }

  /**
   * 拥塞阶梯级别由传输页自己决定（后端不维护），按场次记进 sessionStorage：传输 WS 重连
   * 或 Pet 页重载（同一标签页）后接着用，不在已经拥塞的连接上先退回 560 kbps 再等一轮。
   */
  function ladderKey(visitId) {
    return 'neko-visit-ladder:' + visitId;
  }

  function loadLadder(win, visitId) {
    try {
      var raw = win.sessionStorage && win.sessionStorage.getItem(ladderKey(visitId));
      var v = raw ? JSON.parse(raw) : null;
      if (v && (v.crop === 'upper' || v.crop === 'full') && Number.isInteger(v.level)) return v;
    } catch (_) {
      // 存储不可用：从 0 级开始
    }
    return null;
  }

  function saveLadder(win, visitId, crop, level) {
    try {
      if (win.sessionStorage) win.sessionStorage.setItem(ladderKey(visitId), JSON.stringify({ crop: crop, level: level }));
    } catch (_) {
      // 存储不可用：只是重连后从 0 级开始
    }
  }

  /** 开发机上的 LiveKit：双开客户端可能掉解码帧，但本地链路没有网络拥塞。 */
  function isLoopbackLivekit(creds) {
    if (!creds || creds.transport !== 'livekit') return false;
    var livekit = creds.vendor && creds.vendor.livekit;
    var url = livekit && typeof livekit.url === 'string' ? livekit.url : '';
    var match = /^wss?:\/\/(\[[^\]]+\]|[^\/:?#]+)/i.exec(url);
    if (!match) return false;
    var host = match[1].toLowerCase();
    return host === 'localhost' || host === '[::1]' || /^127(?:\.\d{1,3}){3}$/.test(host);
  }

  function finiteOr(value, fallback) {
    return typeof value === 'number' && isFinite(value) && value >= 0 ? value : fallback;
  }

  /**
   * 控制器：把父页、传输 WS、vendor 适配器与打包 / 解包串起来。
   * env 注入 window / document / 计时器，测试用假实现。
   */
  function VisitTransportController(env) {
    this.win = env.window;
    this.doc = env.document;
    this.side = env.side;
    this.visitId = env.visitId;
    this.assetVersion = env.assetVersion || '';
    this.post = env.post;
    this.log = env.log || function () {};
    this.loadSdk = env.loadSdk || function (transport) { return loadScriptOnce(env.window, transport, env.assetVersion); };
    this.now = env.now || function () { return Date.now(); };
    this.creds = null;
    this.vendor = null;
    this.sdk = null;
    this.adapter = null;
    this.adapterSeq = 0;      // 每换一个适配器加一：旧适配器的事件回调据此作废
    this.everJoined = false;  // 这个传输页是否入房成功过（一开始就进不去 vs 中途断线后重进）
    this.connGen = 0;
    this.sdkCapsGen = -1;
    this.preflight = null;
    this.vendorState = null;
    this.peerPresent = false;
    this.remoteVideo = false;
    this.hidden = false;
    this.hiddenReportedAt = 0;
    this.hiddenTimer = null;
    this.stopped = false;
    this.rejoining = false;
    this.captureFps = 30;
    this.media = { publish: false, subscribe: false, crop: 'upper', ladder: 0, peerCrop: null };
    this.peerRxFps = null;
    this.loss = null;
    this.softencSince = null;
    this.statsMark = null;
    this.lastRxSample = null;   // 上一拍成功取到的接收统计（getStats 卡住时沿用，见 collectStats）
    this.statsGen = 0;
    this.statsSeq = 0;
    this.statsEmittedSeq = 0;
    this.codecOverride = null;
    this.lockHighestQuality = false;
    this.publishRetries = 0;
    this.publishRetryTimer = null;
    this.profileRetries = 0;
    this.profileRetryTimer = null;
    this.rejoinUntil = 0;
    this.rejoinAttempts = 0;
    this.rejoinTimer = null;
    var self = this;
    this.core = new NVT.TransportCore({
      visitId: this.visitId,
      onRecv: function (m) { self.onRecv(m); },
      onBackpressure: function (b) { self.sendBackend({ type: 'tx_backpressure', on: b.on, queue: b.queue, dropped: b.dropped }); },
    });
    this.packer = null;
    this.unpacker = null;
    this.ladder = null;
    if (this.side === 'guest') {
      this.packer = new NVT.pack.Packer({ document: this.doc, crop: 'upper' });
      this.ladder = new NVT.pack.LadderController({ crop: 'upper', targetFps: this.captureFps });
      this.savedLadder = loadLadder(this.win, this.visitId);
      NVT.packer = this.packer;
    } else if (env.video && env.canvas) {
      this.unpacker = new NVT.unpack.Unpacker({ video: env.video, canvas: env.canvas, post: this.post });
    }
    this.link = new NVT.backend.BackendLink({
      visitId: this.visitId,
      side: this.side,
      hooks: {
        onOpen: function () { self.onSocketOpen(); },
        onMessage: function (msg) { self.onDownlink(msg); },
        stopAllMedia: function () { self.stopAllMedia(); },
        leaveRoom: function (reason) { return self.leaveRoom(reason); },
        onTerminal: function (code) { self.onTerminal(code); },
      },
    });
  }

  var P = VisitTransportController.prototype;

  P.sendBackend = function (msg) {
    return this.link.send(msg);
  };

  P.onAuthToken = function (token) {
    if (this.stopped) return;
    this.link.start(token);
  };

  // ── 传输 WS ──────────────────────────────────────────────────────────

  P.onSocketOpen = function () {
    this.connGen += 1;
    if (!this.preflight) this.preflight = runPreflight(this.win);
    var caps = {
      type: 'caps', stage: 'preflight', visit_id: this.visitId, side: this.side,
      preflight_ok: this.preflight.ok, is_secure_context: !!this.win.isSecureContext,
      ua: String((this.win.navigator && this.win.navigator.userAgent) || '').slice(0, 200),
    };
    if (this.preflight.reason) caps.reason = this.preflight.reason;
    this.sendBackend(caps);
    // 本页的出站队列是新的：显式告诉后端没有背压（旧页面重载前报过 on:true 时，后端的标志不会自己清）
    this.sendBackend({ type: 'tx_backpressure', on: false, queue: this.core.queueLength(), dropped: false });
  };

  P.onDownlink = function (msg) {
    if (this.stopped) return;
    if (msg.type === 'credentials') this.onCredentials(msg);
    else if (msg.type === 'media') this.applyMedia(msg);
    else if (msg.type === 'send') this.core.send(msg.cmd, msg.payload);
    else if (msg.type === 'stop') this.onStop(msg);
  };

  P.onCredentials = function (msg) {
    if (msg.visit_id !== this.visitId || msg.side !== this.side) return;
    if (msg.refresh) {
      // 续期：只替换本地保存的凭证，不重新入房、不重跑能力门
      if (this.creds && msg.transport === this.creds.transport) {
        this.applyCodecOverride(msg);
        this.creds = msg;
        if (this.adapter) this.adapter.updateCredentials(msg);
      }
      return;
    }
    this.codecOverride = null;
    this.creds = msg;
    this.lockHighestQuality = isLoopbackLivekit(msg);
    if (typeof msg.peer_vid === 'string' && msg.peer_vid) this.core.setPeerVid(msg.peer_vid);
    if (this.packer) {
      var crop = msg.crop === 'full' ? 'full' : 'upper';
      // 同一构图下沿用已降的级别（传输 WS 重连 / 页面重载）；换了构图从 0 级开始
      this.ladder.setLockHighest(this.lockHighestQuality);
      var level = this.lockHighestQuality ? 0 : this.keptLevel(crop);
      this.media.crop = crop;
      this.media.ladder = level;
      this.ladder.reset(crop, level);
      this.packer.setGeometry(crop, level);
      this.announceGeometry();
    }
    var gen = this.connGen;
    var self = this;
    var transport = msg.transport;
    if (!NVT.vendors || !NVT.vendors[transport]) {
      this.sendSdkCaps(gen, { transport_ok: false, video_ok: false, reason: 'sdk_unsupported', codecs: [] });
      return;
    }
    this.vendor = NVT.vendors[transport];
    this.loadSdk(transport).then(function (sdk) {
      self.sdk = sdk;
      return self.vendor.capabilities(sdk, self.creds, self.side);
    }, function (err) {
      self.log('warn', 'sdk load failed: ' + (err && err.message), 'sdk_load_failed');
      self.sendSdkCaps(gen, { transport_ok: false, video_ok: false, reason: 'sdk_load_failed', codecs: [] });
      return null;
    }).then(function (caps) {
      if (!caps || gen !== self.connGen || self.stopped) return;
      if (caps.codec && self.creds.publish && caps.codec !== self.creds.publish.codec) {
        // 本机编不了凭证选定的 codec（如 vp9）：本侧退到能编的那个（vp8），续期凭证同样改写
        self.codecOverride = caps.codec;
        self.applyCodecOverride(self.creds);
      }
      var videoOk;
      if (self.side === 'guest') videoOk = caps.encode && guestVideoPrimitives(self.doc);
      else videoOk = caps.decode && NVT.unpack.webglUnpackSupported(self.doc);
      var report = { transport_ok: !!caps.transportOk, video_ok: !!(caps.transportOk && videoOk), codecs: caps.codecs || [] };
      if (!caps.transportOk) report.reason = 'sdk_unsupported';
      else if (!videoOk) report.reason = 'no_encoder';
      self.sendSdkCaps(gen, report);
      if (caps.transportOk) {
        // 首次入房同样在窗口内按退避重试：后端给首次入房留了 25 s，一次暂时性失败不该直接结束整场
        self.rejoinUntil = self.now() + REJOIN_RETRY_WINDOW_MS;
        self.rejoinAttempts = 0;
        self.join();
      }
    }).catch(function (err) {
      self.log('warn', 'capability gate failed: ' + (err && err.message), 'sdk_unsupported');
      self.sendSdkCaps(gen, { transport_ok: false, video_ok: false, reason: 'sdk_unsupported', codecs: [] });
    });
  };

  P.applyCodecOverride = function (creds) {
    if (this.codecOverride && creds && creds.publish) creds.publish.codec = this.codecOverride;
  };

  P.sendSdkCaps = function (gen, fields) {
    if (gen !== this.connGen || this.sdkCapsGen === gen) return;
    this.sdkCapsGen = gen;
    var msg = { type: 'caps', stage: 'sdk', visit_id: this.visitId, side: this.side };
    for (var k in fields) msg[k] = fields[k];
    this.sendBackend(msg);
  };

  P.ensureAdapter = function () {
    if (this.adapter) return this.adapter;
    var self = this;
    var seq = this.adapterSeq;
    // 被 retireAdapter 丢掉的适配器迟到的事件（进房完成、被同身份顶下线等）一概不管
    var live = function () { return self.adapterSeq === seq; };
    this.adapter = this.vendor.createAdapter({
      sdk: this.sdk,
      peerVid: this.core.peerVid,
      handlers: {
        onPiece: function (fromVid, cmd, bytes) { if (live()) self.core.onPiece(fromVid, cmd, bytes); },
        onPeer: function (kind, vid, reason, explicit) { if (live()) self.onPeer(kind, vid, reason, explicit); },
        onState: function (state, extra) { if (live()) self.onVendorState(state, extra); },
        onRemoteTrack: function (track) { if (live()) self.onRemoteTrack(track); },
        onNetwork: function (info) { if (live() && info && typeof info.lossPct === 'number') self.loss = { value: info.lossPct, at: self.now() }; },
        onNeedRejoin: function () { if (live()) self.rejoin(); },
        onLog: function (level, msg, code) { if (live()) self.log(level, msg, code); },
      },
    });
    this.core.attach(this.adapter);
    return this.adapter;
  };

  P.join = function () {
    if (this.stopped || !this.creds) return;
    var adapter = this.ensureAdapter();
    var self = this;
    var gen = this.connGen;
    var initial = !this.everJoined;
    var settled = false;
    // 只认这次入房之后的进房事件：离房期间排队的旧进房事件可能把在场又置回去，空房里重进不会再有纠正的事件
    this.peerPresent = false;
    // 入房可能既不成功也不失败（网络切换时 SDK 卡住），重试窗口只管得住失败的那种。
    // 一开始就进不去：客户端不另设上限、也不换适配器，交给后端的首次入房期限（能力门通过后 25 s）结束这一场——
    // SDK 自己的连接超时与 UDP → TCP → TURN 回退在慢网络上要十几秒，客户端提前判死会误伤本来能进的场次。
    // 中途断线后重进卡住：每次最多等 JOIN_ATTEMPT_TIMEOUT_MS，丢掉卡住的适配器，换新的在重试窗口内再试
    var timer = initial ? null : setTimeout(function () {
      if (settled || self.stopped || self.adapter !== adapter) return;
      settled = true;
      self.log('warn', 'vendor join timed out', 'join_timeout');
      self.retireAdapter();
      if (self.scheduleRejoinRetry(gen)) return;
      self.onVendorState('error', { error_code: 'join_timeout' });
    }, JOIN_ATTEMPT_TIMEOUT_MS);
    adapter.join(this.creds).then(function () {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      self.everJoined = true;
      self.rejoinUntil = 0;
      self.rejoinAttempts = 0;
      self.core.flush();
      self.reapplyMedia();
    }, function (err) {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      var code = (err && err.errorCode) || 'join_failed';
      self.log('warn', 'vendor join failed', code);
      if (self.scheduleRejoinRetry(gen)) return;
      self.onVendorState('error', { error_code: code });
    });
  };

  /**
   * 丢掉卡住的适配器（入房 / 离房一直不结束）：事件回调作废，马上退房 / 断开后销毁（不等 15 s 顶号窗口），
   * 下一次 join 由 ensureAdapter 新建一个。
   */
  P.retireAdapter = function () {
    var old = this.adapter;
    if (!old) return;
    this.adapter = null;
    this.adapterSeq += 1;
    this.core.attach(null);
    Promise.resolve().then(function () { return old.destroy({ immediate: true }); }).then(null, function () {});
  };

  /** 手动重新入房失败：还在重试窗口里、传输 WS 没换代就按退避再试一次；排上了返回 true。 */
  P.scheduleRejoinRetry = function (gen) {
    if (!this.rejoinUntil || this.stopped || gen !== this.connGen || !this.link.ws) return false;
    var delay = REJOIN_RETRY_BASE_MS * Math.pow(2, this.rejoinAttempts);
    if (this.now() + delay >= this.rejoinUntil) {
      this.rejoinUntil = 0;
      return false;
    }
    this.rejoinAttempts += 1;
    var self = this;
    this.cancelRejoinRetry(true);
    this.rejoinTimer = setTimeout(function () {
      self.rejoinTimer = null;
      if (!self.stopped && gen === self.connGen && self.link.ws) self.join();
    }, delay);
    return true;
  };

  P.cancelRejoinRetry = function (keepWindow) {
    if (this.rejoinTimer) clearTimeout(this.rejoinTimer);
    this.rejoinTimer = null;
    if (!keepWindow) {
      this.rejoinUntil = 0;
      this.rejoinAttempts = 0;
    }
  };

  /**
   * 入房完成后按当前期望的媒体状态再应用一次：入房期间到达的 media{publish/subscribe}
   * 会被适配器以「未入房」丢掉，不能等下一个快照才恢复视频。
   */
  P.reapplyMedia = function () {
    if (this.stopped || !this.adapter) return;
    if (this.side === 'guest' && this.media.publish && this.packer) {
      this.publishNow();
    } else if (this.side === 'host' && this.media.subscribe) {
      this.adapter.setSubscribe(true);
    }
  };

  /**
   * 发布当前画布轨（轨已被结束就从同一块画布建新轨）。只有真的发布成功才告诉父页开始取帧，
   * 失败就让父页停下；下一次发布（快照 / 入房后补应用）成功时再通知开始。
   */
  P.publishNow = function () {
    var self = this;
    var track = this.packer.ensureTrack();
    var sent = this.profile();
    // 发布期间这个适配器被换掉（入房 / 离房卡住）：它迟到的结果不算数，不能叫停新适配器的发布或排重试
    var seq = this.adapterSeq;
    this.adapter.publish(track, sent).then(function (ok) {
      if (seq !== self.adapterSeq) return;
      // 'profile_failed'：轨已发布，但发送端码率 / 帧率上限没设上，交给 pushProfile 重试
      var profileFailed = ok === 'profile_failed';
      if (profileFailed) ok = true;
      if (ok === true && self.media.publish && !self.stopped) {
        self.publishRetries = 0;
        // 发布挂起期间构图 / 阶梯 / 取帧帧率变了（那时 updateProfile 因未发布而无效）：补一次
        var now = self.profile();
        if (profileFailed || now.width !== sent.width || now.height !== sent.height || now.frameRate !== sent.frameRate || now.bitrate !== sent.bitrate) {
          self.pushProfile();
        }
        self.announcePublish(true);
      }
      else if (ok === false) self.announcePublish(false);
    }, function (err) {
      if (seq !== self.adapterSeq) return;
      self.log('warn', 'publish failed', String(err && err.code));
      self.announcePublish(false);
      self.schedulePublishRetry();
    });
  };

  /**
   * SDK 拒绝发布（暂时性协商失败等）：后端的 media 快照只在状态变化时发一次，不能等它；
   * 仍要发布时按退避间隔自己重试，直到成功、不再发布或结束。
   */
  P.schedulePublishRetry = function () {
    if (this.publishRetryTimer || this.stopped || !this.media.publish) return;
    var delay = Math.min(PUBLISH_RETRY_MAX_MS, PUBLISH_RETRY_BASE_MS * Math.pow(2, this.publishRetries));
    this.publishRetries += 1;
    var self = this;
    this.publishRetryTimer = setTimeout(function () {
      self.publishRetryTimer = null;
      if (!self.stopped && self.media.publish && self.adapter && self.packer) self.publishNow();
    }, delay);
  };

  /**
   * 把当前期望的 profile（构图 / 阶梯 / 取帧帧率）设到发送端。SDK 拒绝时按退避重试：
   * 几何已经记下，之后同一级别不会再触发设置，不重试就一直停在旧的码率 / 帧率上限。
   */
  P.pushProfile = function () {
    if (!this.media.publish || !this.adapter || !this.packer || this.stopped) return;
    var self = this;
    var adapter = this.adapter;
    var retry = function () {
      if (self.adapter !== adapter || self.profileRetryTimer || self.stopped || !self.media.publish) return;
      var delay = Math.min(PUBLISH_RETRY_MAX_MS, PUBLISH_RETRY_BASE_MS * Math.pow(2, self.profileRetries));
      self.profileRetries += 1;
      self.profileRetryTimer = setTimeout(function () {
        self.profileRetryTimer = null;
        self.pushProfile();
      }, delay);
    };
    adapter.updateProfile(this.profile()).then(function (applied) {
      // 解析成 false（发送端 / 编码参数还没就绪、轨还没发布上）也算没设上，同样按退避重试
      if (applied === true) self.profileRetries = 0;
      else retry();
    }, function (err) {
      self.log('warn', 'updateProfile failed', String(err && err.code));
      retry();
    });
  };

  P.cancelProfileRetry = function () {
    if (this.profileRetryTimer) clearTimeout(this.profileRetryTimer);
    this.profileRetryTimer = null;
    this.profileRetries = 0;
  };

  P.cancelPublishRetry = function () {
    if (this.publishRetryTimer) clearTimeout(this.publishRetryTimer);
    this.publishRetryTimer = null;
    this.publishRetries = 0;
  };

  /** SDK 自动重连失败（旧凭证鉴权失败）：用最新凭证、同一个 vid 手动重新入房。 */
  P.rejoin = function () {
    if (this.stopped || this.rejoining || !this.adapter || !this.creds) return;
    this.rejoining = true;
    this.rejoinUntil = this.now() + REJOIN_RETRY_WINDOW_MS;
    this.rejoinAttempts = 0;
    var self = this;
    // 离房期间传输 WS 断开重连：新连接拿到凭证后自己入房，这里迟到的续接不能再入一次（同一实例并发 enterRoom / 换掉新房间）
    var gen = this.connGen;
    // 断线期间对端可能已经走了、退出事件没送到：重新入房后只认新的进房事件，不把旧的在场带进 joined 上报
    this.peerPresent = false;
    this.stopAllMedia();
    var leaving = this.adapter.leave();
    var done = false;
    var proceed = function (timedOut) {
      if (done) return;
      done = true;
      self.rejoining = false;
      if (self.stopped || gen !== self.connGen || !self.link.ws) return;
      // 离房一直不结束（发布 / 档位更新等操作链卡住，leave 排在它后面）：丢掉这个适配器，换新的入房
      if (timedOut) self.retireAdapter();
      self.join();
    };
    setTimeout(function () { proceed(true); }, REJOIN_LEAVE_WAIT_MS);
    leaving.then(function () { proceed(false); }, function () { proceed(false); });
  };

  // ── vendor 事件 ───────────────────────────────────────────────────────

  P.onVendorState = function (state, extra) {
    this.vendorState = state;
    if (!IN_ROOM[state]) {
      // 断线 / 重连期间的观测不代表恢复后的连接：丢掉旧丢包样本与对端接收帧率，阶梯两个计时器重新起算
      this.loss = null;
      this.peerRxFps = null;
      this.softencSince = null;   // 不在房里时不编码：软编过载窗口重新起算
      if (this.ladder) this.ladder.reset(this.ladder.crop, this.ladder.level);
      // 收发统计的差分基准也作废：重连后第一拍若拿断线前的基准算，间隔里含断线期，帧率 / 码率偏低；
      // 已经在路上的那一拍统计同样丢掉
      this.resetStatsBaselines();
    }
    if (state === 'kicked') {
      // 被踢 / 被同 vid 顶号：停掉自己的媒体；是否结束整场由后端按 vendor_reason 判
      this.stopAllMedia();
    }
    this.reportState(extra || {});
    if (IN_ROOM[state]) this.core.flush();
  };

  P.onPeer = function (kind, vid, reason, explicit) {
    if (kind === 'enter') {
      if (this.peerPresent) return;
      this.peerPresent = true;
      this.reportState({});
      return;
    }
    // 超时类离开不上报（交给心跳）；只有显式离开报 peer_present:false（后端按暂定离开起 35 s 宽限）
    if (!explicit || !this.peerPresent) return;
    this.peerPresent = false;
    this.reportState({ vendor_reason: reason });
  };

  P.onRemoteTrack = function (track) {
    if (!this.unpacker) return;
    if (track && this.media.subscribe) {
      this.unpacker.attach(track);
      this.remoteVideo = true;
    } else {
      this.unpacker.detach();
      this.remoteVideo = false;
    }
    this.reportState({});
  };

  P.reportState = function (extra) {
    if (!this.vendorState) return;
    var msg = { type: 'state', state: this.vendorState, peer_present: this.peerPresent, remote_video: this.remoteVideo, hidden: this.hidden };
    if (extra && extra.vendor_reason) msg.vendor_reason = String(extra.vendor_reason).slice(0, 64);
    if (extra && extra.error_code) msg.error_code = String(extra.error_code).slice(0, 64);
    this.hiddenReportedAt = this.now();
    this.sendBackend(msg);
  };

  P.onRecv = function (m) {
    // guest 的拥塞阶梯要对端的接收帧率：只看 cmd 3 的 stats，其余语义一概不读
    if (this.side === 'guest' && m.cmd === 3 && m.payload && m.payload.t === 'stats' && typeof m.payload.rx_fps === 'number') {
      this.peerRxFps = { value: m.payload.rx_fps, at: this.now() };
    }
    this.sendBackend({ type: 'recv', from_vid: m.from_vid, cmd: m.cmd, payload: m.payload });
  };

  // ── media ─────────────────────────────────────────────────────────────

  P.profile = function () {
    return NVT.pack.profileFor(this.packer.dims, this.captureFps);
  };

  /** 父页只在真的发布视频时取帧（纯文字串门不拉帧率、不挂钩子、不报 hidden）。 */
  P.announcePublish = function (on) {
    if (!this.packer) return;
    on = !!on;
    if (this.publishAnnounced === on) return;
    this.publishAnnounced = on;
    this.post({ t: 'publish', on: on });
  };

  P.announceGeometry = function () {
    if (!this.packer) return;
    var d = this.packer.dims;
    this.post({ t: 'geometry', crop: d.crop, level: d.level, cropW: d.cropW, cropH: d.cropH });
  };

  P.keptLevel = function (crop) {
    if (crop === this.media.crop && this.media.ladder > 0) return this.media.ladder;
    var saved = this.savedLadder;
    return saved && saved.crop === crop ? saved.level : 0;
  };

  P.setGeometry = function (crop, level) {
    var changed = this.packer.setGeometry(crop, level);
    this.media.crop = this.packer.dims.crop;
    this.media.ladder = this.packer.dims.level;
    this.savedLadder = { crop: this.media.crop, level: this.media.ladder };
    saveLadder(this.win, this.visitId, this.media.crop, this.media.ladder);
    if (changed) {
      this.announceGeometry();
      this.pushProfile();
    }
  };

  P.applyMedia = function (msg) {
    if (typeof msg.peer_vid === 'string' && msg.peer_vid) {
      this.core.setPeerVid(msg.peer_vid);
      if (this.adapter) this.adapter.setPeerVid(msg.peer_vid);
    }
    if (msg.peer_crop === 'upper' || msg.peer_crop === 'full') this.media.peerCrop = msg.peer_crop;
    if (this.side === 'guest') {
      var crop = msg.crop === 'full' || msg.crop === 'upper' ? msg.crop : this.media.crop;
      // 后端快照里的 ladder 只当下限：级别由本页的拥塞阶梯决定，快照不能把已降的级别抬回去。
      // 本机 LiveKit 锁最高画质时级别固定 0（同入房时）：锁定的阶梯停在 0 级、feed() 不会再把打包级别纠正回来
      var level = this.lockHighestQuality ? 0
        : Math.max(Number.isInteger(msg.ladder) ? msg.ladder : 0, crop === this.media.crop ? this.media.ladder : 0);
      if (crop !== this.media.crop || level !== this.media.ladder) this.ladder.reset(crop, level);
      this.setGeometry(crop, level);
      if (msg.publish === true) {
        this.media.publish = true;
        this.packer.active = true;
        if (this.adapter) this.publishNow();
      } else if (msg.publish === false) {
        this.media.publish = false;
        this.cancelPublishRetry();
        this.cancelProfileRetry();
        this.announcePublish(false);
        this.packer.active = false;
        if (this.adapter) this.adapter.unpublish();
      }
    } else if (typeof msg.subscribe === 'boolean') {
      this.media.subscribe = msg.subscribe;
      if (this.adapter) this.adapter.setSubscribe(msg.subscribe);
      if (!msg.subscribe && this.unpacker) {
        this.unpacker.detach();
        this.remoteVideo = false;
      }
    }
  };

  /** 停掉全部媒体（不离房、不 stop 画布轨）：guest 取消发布，host 取消订阅。同步执行。 */
  P.stopAllMedia = function () {
    this.media.publish = false;
    this.cancelPublishRetry();
    this.cancelProfileRetry();
    this.loss = null;
    this.peerRxFps = null;
    this.softencSince = null;
    this.announcePublish(false);
    this.media.subscribe = false;
    if (this.packer) this.packer.active = false;
    if (this.adapter) {
      if (this.side === 'guest') this.adapter.unpublish();
      else this.adapter.setSubscribe(false);
    }
    if (this.unpacker) this.unpacker.detach();
    this.remoteVideo = false;
    this.core.reset();
  };

  /** 收发统计的差分基准与在途取样一起作废（离开在房状态、传输 WS 断开时）。 */
  P.resetStatsBaselines = function () {
    this.statsGen += 1;
    this.statsMark = null;
    this.lastRxSample = null;
    if (this.unpacker) this.unpacker.resetStats();
    if (this.adapter && typeof this.adapter.resetStats === 'function') this.adapter.resetStats();
  };

  /** 离开 vendor 房间（同 vid 之后可再入房）。 */
  P.leaveRoom = function () {
    // 传输 WS 断了：这一代连接上还没跑完的 SDK 加载 / 能力门不能再去入房（重连后按新凭证重来）
    this.connGen += 1;
    // vendor 不一定先报 reconnecting：断开前发出的统计取样与差分基准在这里同样作废
    this.resetStatsBaselines();
    this.cancelRejoinRetry(false);
    this.peerPresent = false;
    this.vendorState = 'left';
    return this.adapter ? this.adapter.leave() : Promise.resolve();
  };

  P.onStop = function () {
    var self = this;
    this.stopped = true;
    this.stopAllMedia();
    var leaving = this.adapter ? this.adapter.leave() : Promise.resolve();
    // 离房最多等 STOP_LEAVE_WAIT_MS（失败也算完）：网络坏着时 leave 可能一直不落定，收线与拆适配器不能跟着挂住
    var done = false;
    var finish = function (timedOut) {
      if (done) return;
      done = true;
      self.vendorState = 'left';
      self.peerPresent = false;
      self.reportState({});
      self.link.close();
      // leave 排在同一条 SDK 操作链上：它等超时了，链本身就是卡住的，不再等，直接拆（赶在父页移除 iframe 之前）
      self.teardown(timedOut ? 0 : undefined);
    };
    setTimeout(function () { finish(true); }, STOP_LEAVE_WAIT_MS);
    Promise.resolve(leaving).then(function () { finish(false); }, function () { finish(false); });
  };

  P.onTerminal = function (code) {
    this.stopped = true;
    this.teardown();
    this.post({ t: 'closed', code: typeof code === 'number' ? code : 0 });
  };

  /** 收掉计时器与采集轨、拆适配器；idleWaitMs 是拆之前等在途 SDK 操作的上限（缺省 TEARDOWN_IDLE_WAIT_MS）。 */
  P.teardown = function (idleWaitMs) {
    // 只做一次：stop 超时收线时浏览器的 onclose 是异步的，它再走离房 → onTerminal → teardown；第二次拆会对着已清空的
    // 适配器立刻报 cleanup_done，父页据此提前移除 iframe、把第一次还在等的退房 / 断开一起掐掉
    if (this.tornDown) return;
    this.tornDown = true;
    this.cancelPublishRetry();
    this.cancelProfileRetry();
    this.cancelRejoinRetry(false);
    if (this.statsTimer) clearInterval(this.statsTimer);
    this.statsTimer = null;
    if (this.hiddenTimer) clearTimeout(this.hiddenTimer);
    this.hiddenTimer = null;
    if (this.packer && this.packer.track) {
      try {
        this.packer.track.stop();
      } catch (_) {
        // 已结束
      }
    }
    var adapter = this.adapter;
    var post = this.post;
    var destroy = function () { return adapter.destroy(); };
    var cleaned = Promise.resolve();
    if (adapter) {
      // 在途的发布 / 档位更新可能一直不落定：最多等这么久就拆，否则销毁与 cleanup_done 永远不发生
      var wait = typeof idleWaitMs === 'number' ? idleWaitMs : TEARDOWN_IDLE_WAIT_MS;
      var idle = typeof adapter.idle === 'function' ? adapter.idle() : null;
      cleaned = Promise.race([
        Promise.resolve(idle),
        new Promise(function (resolve) { setTimeout(resolve, wait); }),
      ]).then(destroy, destroy);
    }
    // 适配器的清理（含 4409 被顶号后 15 s 的兜底退房 / 断开）都在这个 iframe 里跑：做完了才告诉父页可以移除
    cleaned.then(null, function () {}).then(function () { post({ t: 'cleanup_done' }); });
  };

  // ── 父页消息 ──────────────────────────────────────────────────────────

  P.onParentCrop = function (data) {
    var fps = data && data.fps;
    if (!(fps === 15 || fps === 30) || fps === this.captureFps) return;
    this.captureFps = fps;
    if (this.ladder) this.ladder.targetFps = fps;
    this.pushProfile();
  };

  P.onParentHidden = function (on) {
    var next = !!on;
    if (next === this.hidden) return;
    this.hidden = next;
    // 隐藏期间对端报的低帧率不代表恢复显示后的连接
    if (!next) this.peerRxFps = null;
    this.scheduleHiddenReport();
  };

  /** 可见性变化经 state{hidden} 上报（≤1 Hz）；只在房里时报，不在房里时随下一次状态带上。 */
  P.scheduleHiddenReport = function () {
    if (this.hiddenTimer) return;
    var self = this;
    var wait = Math.max(0, HIDDEN_REPORT_MIN_MS - (this.now() - this.hiddenReportedAt));
    this.hiddenTimer = setTimeout(function () {
      self.hiddenTimer = null;
      if (!self.stopped && IN_ROOM[self.vendorState]) self.reportState({});
    }, wait);
  };

  P.onParentPlace = function (data) {
    if (this.unpacker && data.width > 0 && data.height > 0) this.unpacker.setCssSize(data.width, data.height);
  };

  P.onParentVisitState = function (phase) {
    if (phase !== 'ended' || this.stopped) return;
    // 保险：正常由后端 stop 驱动
    this.onStop({ reason: 'ended' });
  };

  // ── 5 s 统计 ──────────────────────────────────────────────────────────

  P.collectStats = function () {
    var now = this.now();
    var self = this;
    var gen = this.statsGen;
    var seq = ++this.statsSeq;
    var base = this.adapter ? this.adapter.stats() : {};
    var out = { type: 'stats', dc_queue: this.core.queueLength() };
    ['enc_fps', 'tx_kbps', 'tx_w', 'tx_h', 'rx_kbps', 'rtt_ms', 'loss_pct'].forEach(function (k) {
      if (typeof base[k] === 'number' && isFinite(base[k])) out[k] = base[k];
    });
    if (this.packer) {
      var frames = this.packer.requestFrames;
      if (this.statsMark && now > this.statsMark.at) out.tx_fps = (frames - this.statsMark.frames) * 1000 / (now - this.statsMark.at);
      this.statsMark = { at: now, frames: frames };
      if (typeof out.enc_fps !== 'number' && typeof out.tx_fps === 'number') out.enc_fps = out.tx_fps;
      if (this.creds && this.creds.publish && this.creds.publish.codec) out.enc = this.creds.publish.codec;
    }
    var finish = function (extra) {
      if (gen !== self.statsGen) return;   // 取样期间离开了在房状态：这拍作废
      // 比这拍更新的取样已经发出：慢回来的旧拍不再发、不喂阶梯（比较的是已发出的最新序号，
      // 不是已发起的——getStats 一直慢于 5 s 时每拍按顺序照样发出）
      if (seq <= self.statsEmittedSeq) return;
      self.statsEmittedSeq = seq;
      if (extra) {
        ['enc_fps', 'tx_kbps', 'tx_w', 'tx_h'].forEach(function (key) {
          if (typeof extra[key] === 'number' && isFinite(extra[key])) out[key] = extra[key];
        });
        if (typeof extra.qlr === 'string') out.qlr = extra.qlr;
      }
      // 取样可能拖了好几秒：新鲜度与持续时长按完成的时刻算，不按发起的时刻
      var doneAt = self.now();
      if (self.unpacker) {
        // 接收帧率在异步统计完成时取样：等待期间画面停住 / 恢复，发出去的都是当下的值
        var rx = self.unpacker.stats(doneAt);
        for (var k in rx) out[k] = rx[k];
      }
      self.checkSoftenc(out, doneAt);
      self.stepLadder(doneAt);
      self.sendBackend(out);
      self.post({ t: 'stats', fps: finiteOr(out.tx_fps, finiteOr(out.rx_fps, 0)), kbps: Math.round(finiteOr(out.tx_kbps, finiteOr(out.rx_kbps, 0))), rtt_ms: Math.round(finiteOr(out.rtt_ms, 0)), hidden: self.hidden });
    };
    var adapter = this.adapter;
    var sender = adapter && typeof adapter.senderStats === 'function' && this.media.publish
      ? adapter.senderStats() : Promise.resolve(null);
    var receiver = adapter && typeof adapter.receiverStats === 'function' && this.media.subscribe
      ? adapter.receiverStats(now) : Promise.resolve(null);
    // getStats 可能一直不回（网络 / 设备切换时 WebRTC 卡住）：每拍最多等 STATS_COLLECT_TIMEOUT_MS，到点只发同步部分，
    // 否则后端统计、接收帧率与拥塞阶梯都会停住。晚回来的那份由上面的代号 / 序号检查丢掉
    var collected = Promise.all([sender, receiver]);
    new Promise(function (resolve) {
      var timer = setTimeout(function () { resolve(null); }, STATS_COLLECT_TIMEOUT_MS);
      collected.then(function (res) {
        clearTimeout(timer);
        resolve(res);
      }, function () {
        clearTimeout(timer);
        resolve(null);
      });
    }).then(function (res) {
      var keys = ['rx_kbps', 'rtt_ms', 'loss_pct'];
      var rx = res && res[1];
      if (rx) {
        keys.forEach(function (key) {
          if (typeof rx[key] === 'number' && isFinite(rx[key])) out[key] = rx[key];
        });
      }
      if (self.media.subscribe) {
        // 后端只在 rx_kbps / rtt_ms / loss_pct 都在时才把这拍转发给对端；对端只读其中新鲜的 rx_fps（接收帧率，
        // 拥塞阶梯用）。取样超时、getStats 失败给了空、或只给了一部分时，缺的项沿用上一拍成功的值（从没取到过就填 0），
        // 否则对端的阶梯会跟着失明
        if (keys.every(function (key) { return typeof out[key] === 'number'; })) {
          self.lastRxSample = { rx_kbps: out.rx_kbps, rtt_ms: out.rtt_ms, loss_pct: out.loss_pct };
        } else {
          var last = self.lastRxSample || { rx_kbps: 0, rtt_ms: 0, loss_pct: 0 };
          keys.forEach(function (key) { if (typeof out[key] !== 'number') out[key] = last[key]; });
        }
      }
      finish(res ? res[0] : null);
    });
  };

  /** VP9 软编跟不上：编码帧率持续 10 s 低于目标的 90 %（兼容模式按 15 fps 算）→ 下次串门用 vp8。 */
  P.checkSoftenc = function (out, now) {
    var codec = this.creds && this.creds.publish && this.creds.publish.codec;
    // 模型被隐藏 / 最小化时本来就不出帧（编码帧率掉到 0），不能算软编跟不上
    if (!this.media.publish || this.hidden || codec !== 'vp9' || typeof out.enc_fps !== 'number') {
      this.softencSince = null;
      return;
    }
    if (out.enc_fps < this.captureFps * SOFTENC_FPS_RATIO) {
      if (this.softencSince === null) this.softencSince = now;
      if (now - this.softencSince >= SOFTENC_WINDOW_MS) out.softenc_overloaded = true;
    } else {
      this.softencSince = null;
    }
  };

  P.stepLadder = function (now) {
    if (!this.ladder || !this.media.publish) return;
    if (this.hidden) {
      // 模型被隐藏 / 最小化时本来就不出帧，对端 rx_fps 为 0 不是拥塞：计时器重新起算，不降档
      this.ladder.reset(this.ladder.crop, this.ladder.level);
      return;
    }
    var rxFps = this.peerRxFps && now - this.peerRxFps.at <= PEER_STATS_FRESH_MS ? this.peerRxFps.value : undefined;
    // 丢包样本与对端接收帧率同样只认新鲜的（重连期间 SDK 可能不再回调质量）
    var current = this.adapter && typeof this.adapter.currentLossPct === 'function' ? this.adapter.currentLossPct() : undefined;
    var lossPct = typeof current === 'number' ? current
      : (this.loss && now - this.loss.at <= PEER_STATS_FRESH_MS ? this.loss.value : undefined);
    var level = this.ladder.feed({ nowMs: now, rxFps: rxFps, lossPct: lossPct });
    if (level !== null) this.setGeometry(this.media.crop, level);
  };

  P.startStats = function () {
    var self = this;
    this.statsTimer = setInterval(function () {
      if (!self.stopped && IN_ROOM[self.vendorState]) self.collectStats();
    }, STATS_INTERVAL_MS);
  };

  // ── 页面启动 ──────────────────────────────────────────────────────────

  function boot(win) {
    var params = new URLSearchParams(win.location.search);
    var side = params.get('side');
    var visitId = params.get('visit_id');
    var sink = NVT.frameSink;
    if ((side !== 'host' && side !== 'guest') || !VISIT_ID_RE.test(visitId || '')) {
      sink.log('error', 'bad transport page query', 'bad_query');
      return null;
    }
    var controller = new VisitTransportController({
      window: win,
      document: win.document,
      side: side,
      visitId: visitId,
      assetVersion: params.get('v') || '',
      post: sink.postToParent,
      log: sink.log,
      video: win.document.getElementById('visit-video'),
      canvas: win.document.getElementById('visit-canvas'),
    });
    NVT.controller = controller;
    sink.onParentMessage('crop', function (d) { controller.onParentCrop(d); });
    sink.onParentMessage('place', function (d) { controller.onParentPlace(d); });
    sink.onParentMessage('hidden', function (d) { controller.onParentHidden(d.on); });
    sink.onParentMessage('visit_state', function (d) { controller.onParentVisitState(d.phase); });
    controller.startStats();
    sink.postToParent({ t: 'ready', v: 1, side: side, visit_id: visitId });
    return controller;
  }

  NVT.loader = {
    runPreflight: runPreflight,
    queryIsClean: queryIsClean,
    isLoopbackLivekit: isLoopbackLivekit,
    SDK_INTEGRITY: SDK_INTEGRITY,
    VisitTransportController: VisitTransportController,
    boot: boot,
  };

  if (root.document && root.document.getElementById && root.document.getElementById('visit-transport-root')) {
    boot(root);
  }
})(typeof window !== 'undefined' ? window : globalThis);
