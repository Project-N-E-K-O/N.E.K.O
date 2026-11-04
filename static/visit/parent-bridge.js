/*
 * 串门父页桥接（设计稿 §3.3 / §3.4 / §4.4，PR-11）。只在 index.html（Pet 窗 / 网页单窗口）加载。
 *
 * - 懒建同源传输 iframe：visit_state_change{pending} 建、ended 移除，同一时刻最多一个。
 *   host 侧 iframe 就是访客图层：position:fixed; z-index:4（低于模型容器：Pet / 桌面 10、非 Pet 移动端 5）;
 *   pointer-events:none——命中测试永远落在本家画布上，Pet 窗穿透不变（T3 实测：只靠
 *   transparent-overlay 类会让直通模式整窗可点）。guest 侧 1×1 放在视口外，只当传输。
 * - 同任务取帧：Live2D 在 renderer 的 postrender 里、VRM / MMD 在 render 之后（各一行钩子）、
 *   PNGTuber 在取帧期间用 nekoFramePacing.requestPacedFrame 跑节拍（定时器驱动时是定时器帧率，否则是
 *   显示器刷新率的 rAF），再由下面的累加器抽成目标帧率；同步调 iframe 的
 *   __nekoVisitFrameSink.onFrame——不能走 postMessage（异步任务里后备缓冲已被合成清空）。
 * - 分数累加器把任意源帧率抽成目标帧率：直通 30 fps、兼容模式（--disable-gpu-compositing，
 *   WebGL 源快照回读每帧约 8 ms）15 fps。
 * - 可见性 = 帧饥饿检测：>1 s 没有成功的 onFrame 就告诉 iframe hidden，不看页面可见性事件。
 * - 猫娘语音（OD-15 v4）：传输页报 voice / remote_audio，交给 voice-relay.js 开关自家 TTS 旁路、
 *   把对端语音接进自家扬声器；换 iframe / 移除 iframe 时一并收掉。
 * 除 PNGTuber 取帧期间的节拍外，本文件没有每帧循环、不自起动画帧、不建 WebSocket。
 */
(function (root) {
  'use strict';

  var CAPTURE_FPS_DIRECT = 30;          // VISIT_CAPTURE_FPS
  var CAPTURE_FPS_COMPAT = 15;          // 兼容模式取帧帧率（owner 拍板）
  var TICK_MS = 300;                    // 摆位 / 裁剪刷新节拍
  var CROP_REFRESH_MS = 600;            // VISIT_CROP_REFRESH_MS 区间内
  var STARVATION_MS = 1000;             // VISIT_FRAME_STARVATION_S
  var READY_TIMEOUT_MS = 8000;          // iframe 这么久没报 ready 就重新加载一次（本机页面正常 <1 s）
  var READY_RETRIES = 2;
  var RECOVER_FETCH_TIMEOUT_MS = 4000;  // 重载恢复查 /api/visit/state 单次的上限
  var RECOVER_FETCH_WINDOW_MS = 12000;  // 查询失败（超时 / 网络错）时重试的总时限
  // 重载恢复（等首屏页面配置、取 token、查快照）的总时限：从页面脚本加载起算。后端的 20 s 页面宽限还要容下
  // 之后重建 iframe、加载脚本、鉴权、领凭证、加载 SDK、入房并报 joined，这里只用一半，给后面留够时间
  var RECOVER_TOTAL_MS = 10000;
  var RECOVER_MIN_MS = 3000;            // 页面加载已经吃掉大半预算时仍留给重载恢复的下限（查快照本身很快）
  var TOKEN_LOOKUP_TIMEOUT_MS = 5000;   // 取 CSRF token 卡住时按失败处理
  var TOKEN_RETRY_BASE_MS = 1000;       // 取不到 token 时的重试间隔，之后翻倍
  var TOKEN_RETRY_MAX_MS = 15000;
  var RECOVER_TOKEN_WINDOW_MS = 12000;  // 重载恢复取 token 的时限（另受 RECOVER_TOTAL_MS 约束）
  var CLOSE_NORMAL = 1000;             // 传输 WS 关闭码：后端收线（stop）之后本侧主动关，这一场已经结束
  var CLOSE_UNAUTHORIZED = 4403;       // 传输 WS 关闭码：鉴权没过（与 backend-ws.js 同值）
  var CLOSE_UNKNOWN_VISIT = 4404;      // 传输 WS 关闭码：后端不认这个场次（与 backend-ws.js 同值）
  var CLOSE_SUPERSEDED = 4409;         // 传输 WS 关闭码：被同 vid 的新传输连接顶掉（与 backend-ws.js 同值）
  // 移除 iframe 会把它里面还在跑的清理一起掐掉：父页至少等到子页的清理时限
  var ENDED_LEAVE_WAIT_MS = 15000;      // ended 后等传输页清理做完（报 cleanup_done）的上限：离房最多等 5 s、
                                        // 超时后拆适配器时再等在路上的退房 / 断开最多 8 s
  var CLOSED_KEEP_MS = 12000;           // 传输页报 closed（终态）后等它清理做完的上限：拆适配器前最多 2 s、退房 / 断开最多 8 s
  var SUPERSEDED_KEEP_MS = 27000;       // 4409 被顶号后最多保留 iframe 的时长：传输页拆适配器前最多等 2 s 在途操作，
                                        // 适配器 15 s 后兜底退房 / 断开、本身再最多 8 s，
                                        // 正常由传输页清理做完后报 cleanup_done 提前移除
  var PEER_AWAY_OPACITY = '0.6';
  var HOST_SAFE_MARGIN_PX = 12;
  var CROP_ASPECT = { upper: 320 / 448, full: 256 / 560 };   // 与传输页 CROP_GEOMETRY 同一构图
  var HOST_FRAME_STYLE = 'position:fixed;z-index:4;border:0;background:transparent;pointer-events:none;' +
    'left:0;top:0;width:1px;height:1px;';
  var GUEST_FRAME_STYLE = 'position:fixed;z-index:4;border:0;background:transparent;pointer-events:none;' +
    'left:-10px;top:-10px;width:1px;height:1px;';
  var MODEL_CONTAINER_IDS = ['live2d-container', 'vrm-container', 'mmd-container', 'pngtuber-container'];
  var ACTIVE_PHASES = { active: 1, wrap_up: 1 };
  var VISIT_ID_RE = /^[A-Za-z0-9_-]{22}$/;
  var VISIT_UNAVAILABLE = {};

  /**
   * 分数累加器（纯函数）：acc += target / renderFps；acc ≥ 1 时抓一帧并减 1；
   * 减完仍 ≥ 1（源帧率低于目标）就把多余额度清零，免得帧率恢复时突发连抓。
   * renderFps 取相邻两次渲染的时间跨度；第一帧用显示器刷新率作初值（冷启动第一秒不多抓）。
   */
  function createSampler(opts) {
    var target = (opts && opts.targetFps) || CAPTURE_FPS_DIRECT;
    var initialHz = (opts && opts.initialHz) || 60;
    var acc = 0;
    var last = null;
    return {
      onRender: function (nowMs) {
        var fps;
        if (last === null || !(nowMs > last)) fps = initialHz;
        else fps = 1000 / (nowMs - last);
        last = nowMs;
        if (!(fps > 0) || !isFinite(fps)) return false;
        acc += target / fps;
        if (acc >= 1) {
          acc -= 1;
          if (acc >= 1) acc = 0;
          return true;
        }
        return false;
      },
      setTarget: function (fps) {
        target = fps;
      },
      setInitialHz: function (hz) {
        if (hz > 0) initialHz = hz;
      },
      reset: function () {
        acc = 0;
        last = null;
      },
      target: function () {
        return target;
      },
    };
  }

  function createParentBridge(env) {
    var win = env.window;
    var doc = env.document || win.document;
    var now = env.now || function () { return win.performance.now(); };
    var setIntervalFn = env.setInterval || function (fn, ms) { return win.setInterval(fn, ms); };
    var clearIntervalFn = env.clearInterval || function (h) { win.clearInterval(h); };
    var setTimeoutFn = env.setTimeout || function (fn, ms) { return win.setTimeout(fn, ms); };
    var clearTimeoutFn = env.clearTimeout || function (h) { win.clearTimeout(h); };
    var geo = env.geometry || win.NekoVisitCropGeometry;
    var assetVersion = env.assetVersion || '';
    var relayFactory = win.NekoVisitVoiceRelay;
    var voice = env.voice || (relayFactory ? relayFactory.createVoiceRelay({ window: win, log: function (level, msg) { logFrame(level, msg); } }) : null);
    var recoverySeq = 0; // 并行恢复只采纳最后发起的请求，和实时帧代数分别判定。

    var state = {
      frame: null,
      side: null,
      visitId: null,
      phase: null,
      sink: null,
      ready: false,
      readyTimer: null,
      readyRetries: 0,
      portraitExports: 0,
      liveGen: 0,   // 每收到一条实时 visit_state_change 加一：重载恢复的快照若早于它就作废
      authTimer: null,
      authGen: 0,
      tickTimer: null,
      lastCropAt: -Infinity,
      cropMode: 'upper',
      cropAspect: CROP_ASPECT.upper,
      peerCrop: 'upper',
      peerHidden: false,
      rect: null,               // 当前裁剪框（CSS 视口坐标）
      rectFrom: null,
      rectAt: 0,
      lastOkAt: null,
      hiddenSent: false,
      suspended: false,
      savedFps: null,
      captureFps: CAPTURE_FPS_DIRECT,
      sampler: createSampler({ targetFps: CAPTURE_FPS_DIRECT }),
      hookedRenderer: null,
      flaggedLive2d: null,
      pngCancel: null,
      place: null,
      awayBadge: null,
      peerBadge: null,
      firstFrame: null,
      publishing: false,
      voiceClosed: false,    // 这一场的语音已被收尾关掉（voice_off / ending）：之后的开启请求一律不理
      closedVisitId: null,   // 传输页已报 closed 的场次：ended / 新 pending 之前不再为它补建 iframe
      authRetryVisitId: null,  // 已经因 4403 重取 token 重建过一次的场次（同一场只重来一次）
      forceTokenRefresh: false,  // 下一次鉴权跳过缓存的 token、直接重取
      endedVisitId: null,    // 最近结束的场次：它迟到的 wrap_up / ending 不再补建 iframe（只拦补建，不参与场次过滤）
      retiring: [],          // 已脱离父页、等它把清理做完（报 cleanup_done）的旧 iframe [{frame, timer}]
      auxGen: { crop: 0, hidden: 0, away: 0 },   // 实时辅助状态各自的更新计数：重载快照只不覆盖查询期间更新过的那几项
      away: false,           // guest 离家样式当前是否打着
      portraitWrapped: false,
    };

    function isCompatMode() {
      var rt = win.__NEKO_DESKTOP_RUNTIME__;
      return !!(rt && rt.compatibilityMode === true);
    }

    function t(key, fallback) {
      try {
        if (typeof win.t === 'function') {
          var s = win.t(key);
          if (typeof s === 'string' && s && s !== key) return s;
        }
      } catch (_) {
        // i18n 未就绪
      }
      return fallback;
    }

    function modelType() {
      var portrait = win.avatarPortrait;
      if (portrait && typeof portrait.normalizeModelType === 'function') return portrait.normalizeModelType();
      return 'live2d';
    }

    function activeManager(type) {
      if (type === 'vrm') return win.vrmManager || null;
      if (type === 'mmd') return win.mmdManager || null;
      if (type === 'pngtuber') return win.pngtuberManager || null;
      return win.live2dManager || null;
    }

    function pngtuberDrawable() {
      var m = win.pngtuberManager;
      if (m && m.image && (m.image.naturalWidth || m.image.width)) return m.image;
      var c = doc.getElementById('pngtuber-container');
      return c ? c.querySelector('canvas.pngtuber-layered-canvas, img.pngtuber-image') : null;
    }

    /** 当前模型的取帧源（只能是四个模型容器里的画布 / 图片）。 */
    function captureSource(type) {
      if (type === 'live2d') {
        var lm = win.live2dManager;
        return lm && lm.pixi_app && lm.pixi_app.renderer ? lm.pixi_app.renderer.view : null;
      }
      if (type === 'vrm' || type === 'mmd') {
        var m = activeManager(type);
        return m && m.renderer ? m.renderer.domElement : null;
      }
      return pngtuberDrawable();
    }

    function modelBounds(type) {
      if (type === 'pngtuber') {
        var d = pngtuberDrawable();
        if (!d || typeof d.getBoundingClientRect !== 'function') return null;
        var r = d.getBoundingClientRect();
        if (!(r && r.width > 0 && r.height > 0)) return null;
        // 与其它模型一样只用视口内可见部分构框：挂在屏幕边缘 / 完全移出时不把看不见的部分发出去
        return geo.intersectRect({ x: r.left, y: r.top, width: r.width, height: r.height },
          { x: 0, y: 0, width: win.innerWidth || 0, height: win.innerHeight || 0 });
      }
      var m = activeManager(type);
      if (!m || typeof m.getModelScreenBounds !== 'function') return null;
      try {
        // 包围盒可能一半挂在屏幕外（T1~T5 实测）：只用视口内可见部分构框
        return geo.intersectRect(m.getModelScreenBounds(), { x: 0, y: 0, width: win.innerWidth || 0, height: win.innerHeight || 0 });
      } catch (_) {
        return null;
      }
    }

    function horizontallyMirrored(el) {
      try {
        var t = typeof win.getComputedStyle === 'function' ? win.getComputedStyle(el).transform : '';
        var m = /^matrix(?:3d)?\(\s*([-+0-9.eE]+)/.exec(t || '');
        return !!m && parseFloat(m[1]) < 0;
      } catch (_) {
        return false;
      }
    }

    function sourceMetrics(source, type) {
      if (!source) return null;
      if (type === 'pngtuber') {
        // 图片源：裁剪坐标换算到图片自身像素
        var rect = source.getBoundingClientRect();
        var pw = source.naturalWidth || source.width;
        var ph = source.naturalHeight || source.height;
        if (!(rect.width > 0 && rect.height > 0 && pw > 0 && ph > 0)) return null;
        // mirror 选项用负的水平 CSS 缩放显示；drawImage 读的是未变换的原图，坐标要反过来换算并在打包时翻转
        return { originX: rect.left, originY: rect.top, pixelRatioX: pw / rect.width, pixelRatioY: ph / rect.height, srcW: pw, srcH: ph,
          mirrorX: horizontallyMirrored(source), cssWidth: rect.width };
      }
      var r = source.getBoundingClientRect();
      var cssW = r.width || source.clientWidth || source.width || 1;
      var cssH = r.height || source.clientHeight || source.height || 1;
      return {
        originX: r.left || 0,
        originY: r.top || 0,
        pixelRatioX: (source.width || cssW) / cssW,
        pixelRatioY: (source.height || cssH) / cssH,
        srcW: source.width,
        srcH: source.height,
      };
    }

    function visibleSourcePixels(source, metrics) {
      var r = source.getBoundingClientRect();
      var v = geo.intersectRect({ x: r.left, y: r.top, width: r.width, height: r.height },
        { x: 0, y: 0, width: win.innerWidth || 0, height: win.innerHeight || 0 });
      var px = v && geo.cssRectToPixelRect(v, metrics);
      return px ? { x: px.x, y: px.y, w: px.w, h: px.h } : null;
    }

    function containerHidden(type) {
      var id = type === 'pngtuber' ? 'pngtuber-container' : type === 'vrm' ? 'vrm-container' : type === 'mmd' ? 'mmd-container' : 'live2d-container';
      var el = doc.getElementById(id);
      if (!el || typeof win.getComputedStyle !== 'function') return false;
      // 用户最小化模型：容器只是透明（PNG 自己的取帧循环不会因此停），同样算看不见
      if (el.classList && el.classList.contains('minimized')) return true;
      var cs = win.getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') return true;
      var canvas = type === 'live2d' ? doc.getElementById('live2d-canvas') : null;
      return !!(canvas && win.getComputedStyle(canvas).visibility === 'hidden');
    }

    // ── iframe ─────────────────────────────────────────────────────────

    function post(msg) {
      var f = state.frame;
      if (!f || !f.contentWindow) return;
      try {
        f.contentWindow.postMessage(msg, win.location.origin);
      } catch (_) {
        // iframe 正在卸载
      }
    }

    function ensureFrame(side, visitId) {
      if (side !== 'host' && side !== 'guest') return;
      if (!VISIT_ID_RE.test(visitId || '')) return;
      if (state.frame && state.visitId === visitId && state.side === side) return;
      removeFrame();
      var f = doc.createElement('iframe');
      f.id = 'visit-frame';
      f.className = 'transparent-overlay';
      f.setAttribute('aria-hidden', 'true');
      f.setAttribute('tabindex', '-1');
      f.setAttribute('allowtransparency', 'true');
      f.style.cssText = side === 'host' ? HOST_FRAME_STYLE : GUEST_FRAME_STYLE;
      f.src = '/visit/transport?v=' + encodeURIComponent(assetVersion) + '&side=' + side + '&visit_id=' + encodeURIComponent(visitId);
      state.frame = f;
      state.side = side;
      state.visitId = visitId;
      state.ready = false;
      state.sink = null;
      state.place = null;
      state.firstFrame = null;
      state.publishing = false;
      state.voiceClosed = false;
      state.captureFps = isCompatMode() ? CAPTURE_FPS_COMPAT : CAPTURE_FPS_DIRECT;
      state.sampler.setTarget(state.captureFps);
      state.sampler.reset();
      state.readyRetries = 0;
      doc.body.appendChild(f);
      armReadyTimer(f);
      startTick();
    }

    /** iframe 一直不报 ready（页面或本地脚本一时没加载出来）：同一个 iframe 重新加载，最多 READY_RETRIES 次。 */
    function armReadyTimer(f) {
      if (state.readyTimer !== null) clearTimeoutFn(state.readyTimer);
      state.readyTimer = setTimeoutFn(function () {
        state.readyTimer = null;
        if (state.frame !== f || state.ready) return;
        logFrame('warn', 'transport iframe never became ready');
        if (state.readyRetries >= READY_RETRIES) return;
        state.readyRetries += 1;
        var src = f.src;
        f.src = src;
        armReadyTimer(f);
      }, READY_TIMEOUT_MS);
    }

    /**
     * ended 时不立刻移除：先告诉传输页 ended（它据此主动离房、断开传输 WS——后端断着收不到 stop
     * 时对端也能马上看到离开），iframe 立即隐藏、脱离父页状态，等它报 cleanup_done（离房与拆适配器做完）、
     * 最多 ENDED_LEAVE_WAIT_MS 再移除。不按 closed 提前移除：离房等超时后它照样报 closed，退房 / 断开却可能还在路上。
     */
    function retireFrame() {
      var f = state.frame;
      if (!f) {
        removeFrame();
        return;
      }
      post({ t: 'visit_state', phase: 'ended' });
      removeFrame({ keepElement: true });
      retireElement(f, ENDED_LEAVE_WAIT_MS);
    }

    /**
     * 已脱离父页状态的旧 iframe 先隐藏、留在 DOM 里，让它把离房 / 兜底断开做完再移除：
     * 它报 cleanup_done（离房 / 兜底退房与拆适配器做完）就提前移除，否则到点移除。
     */
    function retireElement(f, waitMs) {
      try {
        f.style.visibility = 'hidden';
        f.id = '';   // 让新场次的 iframe 用回 #visit-frame
      } catch (_) {
        // 已卸载
      }
      var r = { frame: f, timer: null };
      r.timer = setTimeoutFn(function () { finishRetire(r); }, waitMs);
      state.retiring.push(r);
    }

    function findRetiring(source) {
      for (var i = 0; i < state.retiring.length; i++) {
        if (state.retiring[i].frame.contentWindow === source) return state.retiring[i];
      }
      return null;
    }

    function finishRetire(r) {
      if (r.timer !== null) clearTimeoutFn(r.timer);
      r.timer = null;
      try {
        r.frame.remove();
      } catch (_) {
        // 已移除
      }
      var i = state.retiring.indexOf(r);
      if (i >= 0) state.retiring.splice(i, 1);
    }

    /** opts.keepAway：保留 guest 的离家样式（场次没结束）；opts.keepElement：不从 DOM 移除（ended 退场）。 */
    function removeFrame(opts) {
      opts = opts || {};
      stopCapture();
      stopTick();
      if (voice) voice.reset();
      if (state.readyTimer !== null) clearTimeoutFn(state.readyTimer);
      state.readyTimer = null;
      if (state.authTimer !== null) clearTimeoutFn(state.authTimer);
      state.authTimer = null;
      if (state.frame && !opts.keepElement) {
        try {
          state.frame.remove();
        } catch (_) {
          // 已移除
        }
      }
      state.frame = null;
      state.sink = null;
      state.ready = false;
      state.side = null;
      state.visitId = null;
      state.rect = null;
      state.rectFrom = null;
      state.hiddenSent = false;
      state.lastOkAt = null;
      setPeerHidden(false);
      removePeerBadge();
      if (!opts.keepAway) setAway(false);
    }

    function logFrame(level, msg) {
      var c = win.console;
      if (!c) return;
      var fn = level === 'error' ? c.error : level === 'warn' ? c.warn : c.log;
      try {
        fn.call(c, '[visit-frame] ' + msg);
      } catch (_) {
        // console 不可用
      }
    }

    function getToken() {
      if (typeof env.getToken === 'function') return env.getToken();
      var sec = win.nekoLocalMutationSecurity;
      if (!sec || typeof sec.getMutationHeaders !== 'function') return Promise.resolve('');
      return sec.getMutationHeaders().then(function (h) { return (h && h['X-CSRF-Token']) || ''; });
    }

    /** 重取 token：页面配置一时失败时 getToken 会一直给缓存的空结果，要走 refreshToken。 */
    function refreshTokenLookup() {
      if (typeof env.getToken === 'function') return env.getToken();
      var sec = win.nekoLocalMutationSecurity;
      if (sec && typeof sec.refreshToken === 'function') return Promise.resolve(sec.refreshToken()).then(function (t) { return t || ''; });
      return getToken();
    }

    /** 等 p 最多 ms：超时或失败都给 fallback。 */
    function withTimeout(p, ms, fallback) {
      return new Promise(function (resolve) {
        var done = false;
        var timer = setTimeoutFn(function () {
          if (done) return;
          done = true;
          resolve(fallback);
        }, ms);
        Promise.resolve(p).then(function (v) {
          if (done) return;
          done = true;
          clearTimeoutFn(timer);
          resolve(v);
        }, function () {
          if (done) return;
          done = true;
          clearTimeoutFn(timer);
          resolve(fallback);
        });
      });
    }

    /** 重试退避：1 s 起翻倍，上限 TOKEN_RETRY_MAX_MS（取 token / 查快照共用）。 */
    function backoffMs(attempt) {
      return Math.min(TOKEN_RETRY_MAX_MS, TOKEN_RETRY_BASE_MS * Math.pow(2, attempt));
    }

    /** 单次取 token：最多 TOKEN_LOOKUP_TIMEOUT_MS（也不超过 maxMs）；卡住 / 失败 / 非字符串都给空串。 */
    function boundedToken(lookup, maxMs) {
      var wait = typeof maxMs === 'number' ? Math.max(0, Math.min(TOKEN_LOOKUP_TIMEOUT_MS, maxMs)) : TOKEN_LOOKUP_TIMEOUT_MS;
      return withTimeout(lookup, wait, '').then(function (t) { return typeof t === 'string' ? t : ''; });
    }

    /**
     * iframe 报 ready 后交 token 让它连传输 WS。iframe 只报一次 ready：取不到（卡住 / 空）
     * 就按退避重取，直到拿到或这个 iframe 被换掉。
     */
    function authenticate(f, sink, attempt, gen) {
      boundedToken(attempt === 0 && !state.forceTokenRefresh ? getToken() : refreshTokenLookup()).then(function (token) {
        if (state.frame !== f || gen !== state.authGen) return;
        if (!token) {
          var delay = backoffMs(attempt);
          if (state.authTimer !== null) clearTimeoutFn(state.authTimer);
          state.authTimer = setTimeoutFn(function () {
            state.authTimer = null;
            if (state.frame === f && gen === state.authGen) authenticate(f, sink, attempt + 1, gen);
          }, delay);
          return;
        }
        state.forceTokenRefresh = false;
        // 先同步交 token，iframe 拿到 token 才连传输 WS（首帧 auth）
        sink.setAuthToken(token);
        state.sink = sink;
        state.ready = true;
        // 就绪前发出的 place / crop 落在 iframe 的监听器装好之前、已经丢了：清缓存让下一拍重发
        state.place = null;
        state.rect = null;
        state.rectFrom = null;
        state.lastCropAt = -Infinity;
        if (state.phase) post({ t: 'visit_state', phase: state.phase });
        syncCapture();
        tick(true);
      });
    }

    function onFrameMessage(event) {
      var r = findRetiring(event.source);
      if (r && event.origin === win.location.origin) {
        var d = event.data;
        if (d && d.t === 'cleanup_done') finishRetire(r);
        // 退场期间的离房日志照常记下（离房出错时要能排查）
        else if (d && d.t === 'log') logFrame(d.level, String(d.msg || '').slice(0, 500) + (d.code ? ' (' + d.code + ')' : ''));
        return;
      }
      var f = state.frame;
      if (!f || event.source !== f.contentWindow || event.origin !== win.location.origin) return;
      var data = event.data;
      if (!data || typeof data !== 'object' || typeof data.t !== 'string') return;
      if (data.t === 'ready') {
        if (data.visit_id !== state.visitId || data.side !== state.side) return;
        // 页面已经加载出来：之后取 token 慢也不算「没报 ready」，不再重新加载它
        if (state.readyTimer !== null) clearTimeoutFn(state.readyTimer);
        state.readyTimer = null;
        var sink = f.contentWindow && f.contentWindow.__nekoVisitFrameSink;
        if (!sink || typeof sink.setAuthToken !== 'function') return;
        // 同一个 iframe 重新加载过：旧页面的语音旁路 / 对端语音都作废，新页面要时再报
        if (voice) voice.reset();
        // iframe 每报一次 ready 就重新交一次 token（它重载过就要新的）；旧的重试作废
        state.authGen += 1;
        if (state.authTimer !== null) clearTimeoutFn(state.authTimer);
        state.authTimer = null;
        authenticate(f, sink, 0, state.authGen);
      } else if (data.t === 'publish') {
        state.publishing = data.on === true;
        syncCapture();
        tick(true);
      } else if (data.t === 'voice' || data.t === 'remote_audio') {
        var voiceSink = f.contentWindow && f.contentWindow.__nekoVisitFrameSink;
        if (!voice || !voiceSink) return;
        // 收尾后迟到的 on:true 一律不处理：不再开旁路（私聊已恢复，共享的 TTS 链不能再接进房间），也不再播对端；关的照常处理
        var ending = state.voiceClosed || state.phase === 'ending' || state.phase === 'ended';
        var wantOn = data.on === true && !ending;
        if (data.t === 'voice') {
          voice.setLocalWanted(wantOn, voiceSink);
        } else {
          var peerTrack = wantOn && typeof voiceSink.remoteAudioTrack === 'function' ? voiceSink.remoteAudioTrack() : null;
          voice.setRemoteTrack(peerTrack);
        }
      } else if (data.t === 'geometry') {
        if (data.crop === 'upper' || data.crop === 'full') state.cropMode = data.crop;
        if (data.cropW > 0 && data.cropH > 0) state.cropAspect = data.cropW / data.cropH;
        state.rect = null;
        state.lastCropAt = -Infinity;
      } else if (data.t === 'first_frame') {
        if (typeof data.dataURL === 'string' && data.dataURL.indexOf('data:image/png;base64,') === 0) {
          state.firstFrame = data.dataURL;
          dispatch('neko-visit-first-frame', { visitId: state.visitId, dataURL: data.dataURL });
        }
      } else if (data.t === 'log') {
        logFrame(data.level, String(data.msg || '').slice(0, 500) + (data.code ? ' (' + data.code + ')' : ''));
      } else if (data.t === 'closed') {
        // 传输页进入终态（4403 / 4404 / 4409 / 重连超时）：脱离父页状态、隐藏，等它清理做完再移除。记住这个 visit_id，之后的
        // wrap_up / ending 不再补建 iframe。场次还在、只是这个传输页没了（4409 被顶掉、4403、重连超时）时
        // guest 的离家样式留到 ended；4404 是后端已经不认这个场次了，可能再也等不到 ended，照旧清掉
        // 离房等超时后才报 closed 时退房 / 断开可能还在路上；被同 vid 顶掉（4409）时没离房，适配器 15 s 后才兜底退房 / 断开
        var closedId = state.visitId;
        var closedSide = state.side;
        // 显隐 / 离家只在变化时推一次：4403 重建会把它们清掉，先记下来，重建后套回去
        var live = { hidden: !!state.peerHidden, away: !!state.away };
        var normalEnd = data.code === CLOSE_NORMAL;
        removeFrame({ keepAway: !normalEnd && data.code !== CLOSE_UNKNOWN_VISIT, keepElement: true });
        retireElement(f, data.code === CLOSE_SUPERSEDED ? SUPERSEDED_KEEP_MS : CLOSED_KEEP_MS);
        // 4403：缓存的 token 可能已经失效（页面开着时后端重启过），重取就能过。同一场只重来一次：
        // 跳过缓存重取 token、重建 iframe；再被 4403 拒绝才当终态
        if (data.code === CLOSE_UNAUTHORIZED && closedId && state.authRetryVisitId !== closedId) {
          state.authRetryVisitId = closedId;
          state.forceTokenRefresh = true;
          ensureFrame(closedSide, closedId);
          if (live.away) setAway(true);
          if (live.hidden) setPeerHidden(true);
          return;
        }
        if (normalEnd) {
          // 正常收线就是这一场结束了。显示 WS 断着时 ended 事件可能丢、之后也补发不了（后端已经结束这一场）：
          // 和收到 ended 一样处理，离家样式已经在上面清掉，迟到的 ended / 辅助事件按已结束场次丢掉
          state.liveGen += 1;   // 和 ended 一样：在途的重载恢复快照作废，不再给已结束的场次补建 iframe
          state.endedVisitId = closedId;
          state.closedVisitId = null;
          state.phase = null;
          return;
        }
        state.closedVisitId = closedId;
      }
    }

    function dispatch(name, detail) {
      try {
        win.dispatchEvent(new win.CustomEvent(name, { detail: detail }));
      } catch (_) {
        // 老环境没有 CustomEvent
      }
    }

    // ── 取帧 ───────────────────────────────────────────────────────────

    function captureWanted() {
      return state.side === 'guest' && state.ready && !!state.sink && !!ACTIVE_PHASES[state.phase] && state.publishing;
    }

    function syncCapture() {
      if (captureWanted()) startCapture();
      else stopCapture();
    }

    /**
     * 只在 0 < 用户目标帧率 < 取帧帧率时临时拉到取帧帧率，结束时恢复。走既有的
     * neko-frame-rate-changed 事件：Live2D / VRM / MMD 三种渲染器都按它重新应用帧率
     * （只调 live2dManager.setTargetFPS 时，VRM / MMD 停在空闲定时器里不换周期）。
     * 期间 window.nekoTargetFrameRateHold 记着用户自己的值，设置保存读它，
     * 临时值不会被写进本地设置。
     */
    function applyFrameRate(fps) {
      win.targetFrameRate = fps;
      dispatch('neko-frame-rate-changed', { fps: fps, source: 'visit' });
    }

    function holdFrameRate() {
      var target = win.targetFrameRate;
      if (state.savedFps !== null || typeof target !== 'number' || !(target > 0) || target >= state.captureFps) return;
      state.savedFps = target;
      win.nekoTargetFrameRateHold = { userFps: target, heldFps: state.captureFps };
      applyFrameRate(state.captureFps);
    }

    function releaseFrameRate() {
      if (state.savedFps === null) return;
      var saved = state.savedFps;
      state.savedFps = null;
      win.nekoTargetFrameRateHold = null;
      applyFrameRate(saved);
    }

    /** 串门期间用户自己改了帧率：以新值为准（恢复时不再回到旧值），低于取帧帧率就重新拉高。 */
    function onFrameRateChanged(event) {
      var detail = event && event.detail;
      if (detail && detail.source === 'visit') return;
      // 原来没拉高（用户目标不低于取帧帧率）时也要看：用户改低之后同样要拉到取帧帧率
      state.savedFps = null;
      win.nekoTargetFrameRateHold = null;
      if (captureWanted()) holdFrameRate();
    }

    function startCapture() {
      holdFrameRate();
      wrapPortrait();
      if (state.lastOkAt === null) {
        state.lastOkAt = now();
        // 冷启动初值：定时器驱动时取定时器帧率，否则取显示器刷新率
        var pacing = win.nekoFramePacing;
        var hz = pacing && ((typeof pacing.activeTimerTickFps === 'function' && pacing.activeTimerTickFps()) ||
          (typeof pacing.getDisplayRefreshHz === 'function' && pacing.getDisplayRefreshHz()));
        state.sampler.reset();
        if (hz > 0) state.sampler.setInitialHz(hz);
      }
      hookRenderer();
      ensurePngLoop();
    }

    function stopCapture() {
      flagLive2d(null);
      var lm = win.live2dManager;
      if (lm) lm._visitCaptureActive = false;
      releaseFrameRate();
      unhookRenderer();
      if (state.pngCancel) state.pngCancel();
      state.pngCancel = null;
      state.lastOkAt = null;
      if (state.hiddenSent) {
        state.hiddenSent = false;
        post({ t: 'hidden', on: false, cause: 'frame_starvation' });
      }
    }

    function onPostrender() {
      var lm = win.live2dManager;
      var app = lm && lm.pixi_app;
      var renderer = app && app.renderer;
      if (!renderer) return;
      // avatar-portrait 的临时舞台与 generateTexture 也会触发 postrender：两道守卫
      if (renderer.renderTexture && renderer.renderTexture.current) return;
      if (renderer.lastObjectRendered !== app.stage) return;
      if (modelType() !== 'live2d') return;
      captureFrom('live2d');
    }

    /** 取帧期间让当前 Live2D 管理器保持活跃渲染（不进空闲降帧）；管理器被换掉时旧的清掉、新的补上。 */
    function flagLive2d(lm) {
      var prev = state.flaggedLive2d;
      if (prev === lm) {
        if (lm) lm._visitCaptureActive = true;
        return;
      }
      if (prev) prev._visitCaptureActive = false;
      if (lm) lm._visitCaptureActive = true;
      state.flaggedLive2d = lm || null;
    }

    function hookRenderer() {
      // 只有当前模型是 Live2D 时才挂它的渲染钩子、打活跃标记：切到 VRM / MMD / PNG 后留着的
      // Live2D 管理器不能被拉回满速渲染
      var lm = modelType() === 'live2d' ? win.live2dManager : null;
      flagLive2d(lm);
      if (!lm) {
        unhookRenderer();
        return;
      }
      var renderer = lm.pixi_app && lm.pixi_app.renderer;
      if (renderer === state.hookedRenderer) return;
      unhookRenderer();
      if (renderer && typeof renderer.on === 'function') {
        renderer.on('postrender', onPostrender);
        state.hookedRenderer = renderer;
      }
    }

    function unhookRenderer() {
      var r = state.hookedRenderer;
      state.hookedRenderer = null;
      if (r && typeof r.off === 'function') r.off('postrender', onPostrender);
    }

    function ensurePngLoop() {
      if (state.pngCancel || modelType() !== 'pngtuber') return;
      var pacing = win.nekoFramePacing;
      if (!pacing || typeof pacing.requestPacedFrame !== 'function') return;
      var loop = function () {
        state.pngCancel = null;
        if (!captureWanted() || modelType() !== 'pngtuber') return;
        captureFrom('pngtuber');
        state.pngCancel = pacing.requestPacedFrame(loop);
      };
      state.pngCancel = pacing.requestPacedFrame(loop);
    }

    /** 包住头像导出：导出期间暂停取帧（守卫之外的第二道保险；avatar-portrait.js 不改）。 */
    function wrapPortrait() {
      if (state.portraitWrapped) return;
      var api = win.avatarPortrait;
      if (!api) return;
      ['capture', 'captureToBlob', 'captureToDataURL'].forEach(function (name) {
        var original = api[name];
        if (typeof original !== 'function') return;
        api[name] = function () {
          // 几处调用方（聊天头像、卡面）可能同时导出：按计数暂停，最后一个结束才恢复取帧
          state.portraitExports += 1;
          if (state.portraitExports === 1) bridge.suspendCapture(true);
          var settled = false;
          var done = function () {
            if (settled) return;
            settled = true;
            state.portraitExports = Math.max(0, state.portraitExports - 1);
            if (state.portraitExports === 0) bridge.suspendCapture(false);
          };
          var result;
          try {
            result = original.apply(this, arguments);
          } catch (err) {
            done();
            throw err;
          }
          return Promise.resolve(result).then(function (v) {
            done();
            return v;
          }, function (err) {
            done();
            throw err;
          });
        };
      });
      state.portraitWrapped = true;
    }

    function currentRect(nowMs) {
      if (!state.rect) return null;
      if (!state.rectFrom) return state.rect;
      var k = (nowMs - state.rectAt) / geo.HYSTERESIS.transitionMs;
      if (k >= 1) {
        state.rectFrom = null;
        return state.rect;
      }
      return geo.lerpRect(state.rectFrom, state.rect, k);
    }

    function captureFrom(type) {
      if (!captureWanted() || state.suspended) return false;
      var nowMs = now();
      if (!state.sampler.onRender(nowMs)) return false;
      var source = captureSource(type);
      var rectCss = currentRect(nowMs);
      if (!source || !rectCss) return false;
      var metrics = sourceMetrics(source, type);
      var rectPx = metrics && geo.cssRectToPixelRect(rectCss, metrics);
      if (!rectPx) return false;
      if (type === 'pngtuber') {
        // PNG 取的是原图：取景框（全身外扩 4%、补宽高比）可能伸到屏幕外，那部分原图不能发——
        // 只把屏幕上可见的源图区域交给打包器，其余保持透明
        var visible = visibleSourcePixels(source, metrics);
        if (!visible) return false;
        rectPx.visible = visible;
      }
      var ok = false;
      try {
        ok = state.sink.onFrame(source, rectPx, nowMs) === true;
      } catch (_) {
        ok = false;
      }
      if (ok) {
        state.lastOkAt = nowMs;
        if (state.hiddenSent) {
          state.hiddenSent = false;
          post({ t: 'hidden', on: false, cause: 'frame_starvation' });
        }
      }
      return ok;
    }

    // ── 节拍：裁剪框、摆位、帧饥饿 ─────────────────────────────────────

    function refreshCrop(nowMs) {
      var type = modelType();
      var bounds = modelBounds(type);
      if (!bounds || containerHidden(type)) {
        // 包围盒为 null（edge-peek 收起 / 模型管理器覆盖）：视同 hidden，不再取帧
        state.rect = null;
        state.rectFrom = null;
        return;
      }
      var aspect = state.cropAspect;
      var next;
      if (state.cropMode === 'full') {
        next = geo.fullBodyRect(bounds, aspect);
      } else {
        var head = null;
        var m = activeManager(type);
        if (m && typeof m.getHeadDetectionGeometryInfo === 'function') {
          try {
            var info = m.getHeadDetectionGeometryInfo();
            head = info && info.headRect;
          } catch (_) {
            head = null;
          }
        }
        next = geo.upperBodyRect(geo.upperBodySubject(bounds, head), aspect, 0.32);
      }
      if (!next) return;
      if (!geo.shouldMove(state.rect, next)) return;
      state.rectFrom = state.rect ? currentRect(nowMs) : null;
      state.rect = next;
      state.rectAt = nowMs;
      var source = captureSource(type);
      var metrics = sourceMetrics(source, type);
      var rectPx = metrics && geo.cssRectToPixelRect(next, metrics);
      if (rectPx) post({ t: 'crop', rectPx: rectPx, srcW: metrics.srcW, srcH: metrics.srcH, mode: state.cropMode, fps: state.captureFps });
    }

    function placeHostFrame() {
      var f = state.frame;
      if (!f) return;
      var type = modelType();
      var b = modelBounds(type);
      if (!b) return;
      var viewport = win.visualViewport;
      var viewLeft = viewport && isFinite(viewport.offsetLeft) ? viewport.offsetLeft : 0;
      var viewTop = viewport && isFinite(viewport.offsetTop) ? viewport.offsetTop : 0;
      var vw = viewport && viewport.width > 0 ? viewport.width : (win.innerWidth || 0);
      var vh = viewport && viewport.height > 0 ? viewport.height : (win.innerHeight || 0);
      var viewRight = viewLeft + vw;
      var viewBottom = viewTop + vh;
      var desktopRuntime = win.__NEKO_DESKTOP_RUNTIME__;
      var screen = win.screen;
      if (desktopRuntime && screen) {
        // macOS may move the frameless Pet window below the menu bar without reducing its
        // height (for example y=1 -> y=30 while keeping 1439 px). innerHeight / visualViewport
        // still include the pixels now below the physical work area, so clamp the host layer
        // to the part of the client area that is actually visible on screen.
        var screenX = Number(win.screenX);
        var screenY = Number(win.screenY);
        var availLeft = Number(screen.availLeft);
        var availTop = Number(screen.availTop);
        var availWidth = Number(screen.availWidth);
        var availHeight = Number(screen.availHeight);
        if ([screenX, screenY, availLeft, availTop, availWidth, availHeight].every(isFinite) &&
            availWidth > 0 && availHeight > 0) {
          viewLeft = Math.max(viewLeft, availLeft - screenX);
          viewTop = Math.max(viewTop, availTop - screenY);
          viewRight = Math.min(viewRight, availLeft + availWidth - screenX);
          viewBottom = Math.min(viewBottom, availTop + availHeight - screenY);
        }
      }
      var aspect = CROP_ASPECT[state.peerCrop] || CROP_ASPECT.upper;
      // 浏览器缩放 / Electron 可见视口可能小于 layout viewport。所有边都留安全距并最终
      // clamp 到 visualViewport 与桌面工作区的交集，避免头饰、鞋底或右侧身体落到窗口外。
      var visibleW = Math.max(1, viewRight - viewLeft);
      var visibleH = Math.max(1, viewBottom - viewTop);
      var margin = Math.min(HOST_SAFE_MARGIN_PX, Math.max(0, visibleW / 4), Math.max(0, visibleH / 4));
      var safeLeft = viewLeft + margin;
      var safeTop = viewTop + margin;
      var safeRight = viewRight - margin;
      var safeBottom = viewBottom - margin;
      var availableW = Math.max(1, safeRight - safeLeft);
      var availableH = Math.max(1, safeBottom - safeTop);
      var desiredH = Math.max(200, Math.min(900, b.height * 0.9));
      var h = Math.min(desiredH, availableW / aspect, availableH);
      var w = h * aspect;
      var spaceLeft = b.x - safeLeft;
      var spaceRight = safeRight - (b.x + b.width);
      var left = spaceRight >= spaceLeft
        ? Math.min(safeRight - w, b.x + b.width)
        : Math.max(safeLeft, b.x - w);
      left = Math.max(safeLeft, Math.min(safeRight - w, left));
      var top = Math.max(safeTop, Math.min(safeBottom - h, b.y + b.height - h));
      var p = { left: Math.round(left), top: Math.round(top), width: Math.round(w), height: Math.round(h) };
      var old = state.place;
      if (old && old.left === p.left && old.top === p.top && old.width === p.width && old.height === p.height) return;
      state.place = p;
      f.style.left = p.left + 'px';
      f.style.top = p.top + 'px';
      f.style.width = p.width + 'px';
      f.style.height = p.height + 'px';
      if (state.peerBadge) positionBadge(state.peerBadge, p.left + p.width / 2, p.top);
      post({ t: 'place', left: p.left, top: p.top, width: p.width, height: p.height });
    }

    function checkStarvation(nowMs) {
      if (!captureWanted()) return;
      var hidden = state.suspended || state.rect === null || state.lastOkAt === null || nowMs - state.lastOkAt > STARVATION_MS;
      if (hidden && !state.hiddenSent) {
        state.hiddenSent = true;
        post({ t: 'hidden', on: true, cause: state.suspended ? 'forced' : 'frame_starvation' });
      }
    }

    function tick(force) {
      if (!state.frame) return;
      var nowMs = now();
      if (state.side === 'host') {
        placeHostFrame();
        return;
      }
      if (captureWanted()) {
        hookRenderer();
        ensurePngLoop();
      }
      if (force || nowMs - state.lastCropAt >= CROP_REFRESH_MS) {
        state.lastCropAt = nowMs;
        refreshCrop(nowMs);
      }
      checkStarvation(nowMs);
      if (state.awayBadge) positionAwayBadge();
    }

    function startTick() {
      if (state.tickTimer !== null) return;
      state.tickTimer = setIntervalFn(function () { tick(false); }, TICK_MS);
    }

    function stopTick() {
      if (state.tickTimer !== null) clearIntervalFn(state.tickTimer);
      state.tickTimer = null;
    }

    // ── 徽标 ───────────────────────────────────────────────────────────

    function makeBadge(id, text) {
      var el = doc.getElementById(id);
      if (!el) {
        el = doc.createElement('div');
        el.id = id;
        el.className = 'visit-badge';
        el.setAttribute('aria-live', 'polite');
        doc.body.appendChild(el);
      }
      el.textContent = text;
      return el;
    }

    function positionBadge(el, centerX, topY) {
      el.style.left = Math.round(centerX) + 'px';
      el.style.top = Math.max(0, Math.round(topY - 28)) + 'px';
    }

    function removePeerBadge() {
      if (state.peerBadge) state.peerBadge.remove();
      state.peerBadge = null;
    }

    function setPeerHidden(on) {
      state.peerHidden = !!on;
      if (state.frame && state.side === 'host') state.frame.style.opacity = on ? PEER_AWAY_OPACITY : '';
      if (on && state.side === 'host' && state.frame) {
        state.peerBadge = makeBadge('visit-peer-away-badge', t('visitFrame.peerAway', 'Stepped away'));
        if (state.place) positionBadge(state.peerBadge, state.place.left + state.place.width / 2, state.place.top);
      } else {
        removePeerBadge();
      }
    }

    function positionAwayBadge() {
      var b = modelBounds(modelType());
      if (b && state.awayBadge) positionBadge(state.awayBadge, b.x + b.width / 2, b.y);
    }

    /** A 侧：她出门了——模型容器 .visiting-away（半透明）+ 徽标；不用 .minimized（会断水印坐标链）。 */
    function setAway(on) {
      state.away = !!on;
      MODEL_CONTAINER_IDS.forEach(function (id) {
        var el = doc.getElementById(id);
        if (el && el.classList) el.classList.toggle('visiting-away', !!on);
      });
      if (on) {
        state.awayBadge = makeBadge('visit-away-badge', t('visitFrame.visitingAway', 'Out visiting'));
        positionAwayBadge();
      } else if (state.awayBadge) {
        state.awayBadge.remove();
        state.awayBadge = null;
      }
    }

    // ── visit_state_change ─────────────────────────────────────────────

    var PHASE_OF_ACTION = {
      pending: 'pending',
      invite_ready: 'invite_ready',
      joining: 'joining',
      awaiting_accept: 'awaiting_accept',
      started: 'active',
      wrap_up: 'wrap_up',
      ending: 'ending',
      ended: 'ended',
    };

    function onStateChange(msg) {
      applyStateChange(msg, false);
    }

    function applyStateChange(msg, fromRecovery) {
      if (!msg || msg.type !== 'visit_state_change') return;
      var action = msg.action;
      // 别的场次迟到的事件在改任何状态之前就丢掉（closed 之后 visitId 已空，按记下的 closedVisitId 过滤）；
      // pending 开的是新场次，照常处理
      var current = state.visitId || state.closedVisitId;
      if (action !== 'pending' && current && msg.visit_id && msg.visit_id !== current) return;
      // 已结束场次迟到的事件同样丢掉：结束时 visitId / closedVisitId 都已清空，只剩 endedVisitId 记着它
      // （迟到的 departed 会把本机模型一直打成离家样式、peer_crop 会带进下一场的构图）
      if (action !== 'pending' && msg.visit_id && msg.visit_id === state.endedVisitId) return;
      // 只有能建 / 拆 iframe 的生命周期事件才让在途的重载恢复快照作废；peer_hidden / peer_crop /
      // departed 这类辅助事件不建 iframe，不能让快照白白丢掉
      if (!fromRecovery && (action === 'ended' || PHASE_OF_ACTION[action])) state.liveGen += 1;
      if (msg.peer_crop === 'upper' || msg.peer_crop === 'full') state.auxGen.crop += 1;
      if (action === 'peer_hidden' || action === 'peer_visible') state.auxGen.hidden += 1;
      if (action === 'departed') state.auxGen.away += 1;
      if (msg.peer_crop === 'upper' || msg.peer_crop === 'full') state.peerCrop = msg.peer_crop;
      if (action === 'pending') {
        state.closedVisitId = null;
        if (msg.visit_id === state.endedVisitId) state.endedVisitId = null;
        ensureFrame(msg.side, msg.visit_id);
      } else if (!state.frame && action !== 'ended' && PHASE_OF_ACTION[action] && msg.side && msg.visit_id &&
          msg.visit_id !== state.closedVisitId && msg.visit_id !== state.endedVisitId) {
        // 页面重载后没收到 pending：按在飞场次补建
        ensureFrame(msg.side, msg.visit_id);
      }
      var phase = PHASE_OF_ACTION[action];
      if (phase && action !== 'ended') {
        state.phase = phase;
        post({ t: 'visit_state', phase: phase });
      }
      // 收掉语音旁路与对端语音并锁住，本场不再开：voice_off 是后端交还私聊之前直接写在主连接上的
      // （私聊的 TTS 音频随后走同一条连接，这一帧一定先到），ending 再兜一道。旁路接的是共享的 TTS 链，
      // 私聊恢复后她的台词不能再进串门房间
      if (action === 'voice_off' || action === 'ending') {
        state.voiceClosed = true;
        if (voice) voice.reset();
      }
      if (action === 'departed') setAway(true);
      else if (action === 'peer_hidden') setPeerHidden(true);
      else if (action === 'peer_visible') setPeerHidden(false);
      else if (action === 'peer_crop') state.place = null;
      if (action === 'ended') {
        // 记下结束的场次（不放进 closedVisitId：那个还参与场次过滤，会把同页新场次不经 pending 的事件挡掉）
        state.endedVisitId = msg.visit_id || state.visitId || state.closedVisitId || null;
        state.closedVisitId = null;
        retireFrame();
        state.phase = null;
        return;
      }
      syncCapture();
      tick(true);
    }

    /**
     * 有限次重取 token：页面配置一时没就绪时先给空结果，按退避经 refreshToken 再取，
     * 截止时间内仍取不到就给空（调用方按失败处理）。
     */
    function acquireToken(attempt, deadline) {
      return boundedToken(attempt === 0 ? getToken() : refreshTokenLookup(), deadline - now()).then(function (token) {
        if (token) return token;
        var delay = backoffMs(attempt);
        if (now() + delay >= deadline) return '';
        return new Promise(function (resolve) { setTimeoutFn(resolve, delay); }).then(function () {
          return acquireToken(attempt + 1, deadline);
        });
      });
    }

    /**
     * 查 /api/visit/state：单次最多 RECOVER_FETCH_TIMEOUT_MS；没回应（超时 / 网络错）就按退避重试到截止时间。
     * 有回应但不是 2xx（比如串门开关关着的 404）不重试。
     */
    function fetchSnapshot(lanlanName, token, attempt, deadline) {
      var NO_RESPONSE = {};
      var request = Promise.resolve().then(function () {
        return win.fetch('/api/visit/state?catgirl=' + encodeURIComponent(lanlanName), { headers: { 'X-CSRF-Token': token }, cache: 'no-store', credentials: 'same-origin' });
      }).then(function (res) {
        if (!res) return NO_RESPONSE;
        if (res.ok) return res.json();
        if (res.status === 404) return VISIT_UNAVAILABLE;
        if (res.status === 403 && typeof res.json === 'function') {
          return Promise.resolve(res.json()).then(function (body) {
            return body && body.code === 'VISIT_E_UNAUTHORIZED' ? VISIT_UNAVAILABLE : null;
          });
        }
        return null;
      });
      var wait = Math.max(0, Math.min(RECOVER_FETCH_TIMEOUT_MS, deadline - now()));
      return withTimeout(request, wait, NO_RESPONSE).then(function (snap) {
        if (snap !== NO_RESPONSE) return snap;
        var delay = backoffMs(attempt);
        if (now() + delay >= deadline) return null;
        return new Promise(function (resolve) { setTimeoutFn(resolve, delay); }).then(function () {
          return fetchSnapshot(lanlanName, token, attempt + 1, deadline);
        });
      });
    }

    /**
     * 页面重载后：GET /api/visit/state 得知在飞场次就重建 iframe（20 s 页面重载宽限内重入房）。
     * overall：整个恢复的绝对截止时刻（不传就不额外限制）；每一步的窗口都不超过它。
     * 返回 true 表示已应用权威快照或确认串门接口不可用；临时失败或过时响应返回 false。
     */
    function recoverAfterReload(lanlanName, overall) {
      if (!lanlanName || typeof win.fetch !== 'function') return Promise.resolve(false);
      var cap = typeof overall === 'number' ? overall : Infinity;
      var requestSeq = ++recoverySeq;
      var gen = state.liveGen;
      var frameAtSend = state.frame;
      var auxGen = { crop: state.auxGen.crop, hidden: state.auxGen.hidden, away: state.auxGen.away };
      // /api/visit/state 要 CSRF：重载后页面配置一时取不到 token 时不能直接空着头去请求（403 后就再也不恢复）
      return acquireToken(0, Math.min(now() + RECOVER_TOKEN_WINDOW_MS, cap)).then(function (token) {
        if (!token) return null;
        return fetchSnapshot(lanlanName, token, 0, Math.min(now() + RECOVER_FETCH_WINDOW_MS, cap));
      }).then(function (snap) {
        // 新恢复请求、实时生命周期帧或传输 iframe 变化都会让在途快照作废。
        if (!snap || requestSeq !== recoverySeq || gen !== state.liveGen || state.frame !== frameAtSend) return false;
        var cfg = win.lanlan_config;
        if (cfg && cfg.lanlan_name && cfg.lanlan_name !== lanlanName) return false;
        // 本机访问限制或未提供串门接口时结束临时核对；拒绝访问不能证明已有场次结束。
        if (snap === VISIT_UNAVAILABLE) return true;
        if (snap.active === false) {
          var endedVisit = state.visitId || state.closedVisitId;
          if (!endedVisit) return true;
          applyStateChange({ type: 'visit_state_change', action: 'ended', visit_id: endedVisit, side: state.side }, true);
          return true;
        }
        if (!snap.active || !snap.visit_id || !snap.side || snap.phase === 'ended' || snap.phase === 'ending') return false;
        if (state.frame && (state.visitId !== snap.visit_id || state.side !== snap.side)) return false;
        var actionByPhase = { pending: 'pending', invite_ready: 'invite_ready', joining: 'joining', awaiting_accept: 'awaiting_accept', active: 'started', wrap_up: 'wrap_up' };
        var action = actionByPhase[snap.phase];
        if (!action) return false;
        // 建 iframe 会清掉显隐 / 离家样式：先记下查询期间实时推来的值，建完再套回去
        var live = { hidden: !!state.peerHidden, away: !!state.away };
        if (state.frame) {
          // 缓存期间可能错过阶段帧；同一场保留传输 iframe，并走实时路径同步采集和传输状态。
          applyStateChange({ type: 'visit_state_change', action: action, visit_id: snap.visit_id, side: snap.side }, true);
        } else {
          ensureFrame(snap.side, snap.visit_id);
          state.phase = snap.phase;
        }
        // 查询期间实时通道推过的对端构图 / 显隐 / 出门比快照新：更新过的用实时值，没更新过的才用快照
        var room = snap.room || {};
        if (auxGen.crop === state.auxGen.crop && (room.peer_crop === 'upper' || room.peer_crop === 'full')) state.peerCrop = room.peer_crop;
        if (auxGen.away !== state.auxGen.away) setAway(live.away);
        else if (snap.side === 'guest' && ACTIVE_PHASES[snap.phase]) setAway(true);
        if (auxGen.hidden !== state.auxGen.hidden) setPeerHidden(live.hidden);
        else if (snap.peer && snap.peer.hidden) setPeerHidden(true);
        syncCapture();
        tick(true);
        return true;
      }).catch(function () {
        return false;
      });
    }

    /**
     * 重载恢复的入口：读首屏页面配置给出的角色名，有就走 recoverAfterReload，没有就放弃。
     * 不自己重拉页面配置：首页启动流程（startPageConfigLoad）前面有存储位置、七日引导、悬浮头像等好几道闸，
     * 重载恢复在每次打开主页时都会跑（与串门开关无关），绕开任何一道都会影响所有用户。
     */
    function recoverFromPageConfig(overall) {
      var cfg = win.lanlan_config;
      var name = cfg && cfg.lanlan_name;
      if (!name) return Promise.resolve(false);
      return recoverAfterReload(name, overall);
    }

    var bridge = {
      onStateChange: onStateChange,
      blocksOrdinaryVoice: function () {
        return !!((state.visitId || state.closedVisitId) && state.phase
          && state.phase !== 'ending' && state.phase !== 'ended');
      },
      getPhase: function () { return state.phase; },
      getVisitId: function () { return state.visitId || state.closedVisitId || ''; },
      /** VRM / MMD 在 render 之后调用（同一任务）。 */
      onExternalRender: function () {
        if (!captureWanted()) return;
        var type = modelType();
        if (type === 'vrm' || type === 'mmd') captureFrom(type);
      },
      suspendCapture: function (on) {
        state.suspended = !!on;
        if (state.sink && typeof state.sink.suspend === 'function') state.sink.suspend(state.suspended);
      },
      recoverAfterReload: recoverAfterReload,
      /** overall：重载恢复的绝对截止时刻（startRecoveryWhenReady 在脚本加载时定下）。 */
      recoverFromPageConfig: recoverFromPageConfig,
      now: now,
      firstFrameDataURL: function () { return state.firstFrame; },
      _state: state,
      _tick: tick,
      _onFrameMessage: onFrameMessage,
    };

    win.addEventListener('message', onFrameMessage);
    win.addEventListener('neko-frame-rate-changed', onFrameRateChanged);
    return bridge;
  }

  function assetVersionFromScript(doc) {
    var s = doc && doc.currentScript;
    var src = s && s.src;
    if (!src) return '';
    try {
      return new URL(src).searchParams.get('v') || '';
    } catch (_) {
      return '';
    }
  }

  /** 这次导航开始到现在的毫秒数：performance.now() 从导航开始计时（单调时钟，不受系统时间调整影响）。 */
  function sinceNavigation(win) {
    var perf = win.performance;
    var t = perf && typeof perf.now === 'function' ? perf.now() : 0;
    return typeof t === 'number' && isFinite(t) ? Math.max(0, t) : 0;
  }

  /**
   * 首屏页面配置落定（成功或失败）后启动重载恢复。到总截止时间还没落定（启动闸一直没放行、page_config 请求挂住）
   * 就放弃：这一场按页面没开处理（后端 20 s 页面宽限后以 local_page_lost 结束）。
   */
  function startRecoveryWhenReady(bridge, win, setTimeoutFn) {
    var started = false;
    // 总截止时间在脚本加载时就定下：等首屏页面配置、取 token、查快照都从同一个预算里扣。
    // 后端的 20 s 页面宽限从旧页面断开（约等于这次导航开始）算起：首页前面几十个同步脚本可能已经吃掉好几秒，要扣掉
    var budget = Math.max(RECOVER_MIN_MS, RECOVER_TOTAL_MS - sinceNavigation(win));
    var overall = bridge.now() + budget;
    var go = function () {
      if (started) return;
      started = true;
      bridge.recoverFromPageConfig(overall);
    };
    var ready = win.pageConfigReady;
    if (ready && typeof ready.then === 'function') {
      ready.then(go, go);
      setTimeoutFn(function () { started = true; }, budget);
    } else {
      win.addEventListener('load', go);
    }
  }

  root.NekoVisitParentBridge = { createSampler: createSampler, createParentBridge: createParentBridge, startRecoveryWhenReady: startRecoveryWhenReady };

  if (root.document && root.addEventListener && !root.nekoVisitParentBridge) {
    var bridge = createParentBridge({ window: root, document: root.document, assetVersion: assetVersionFromScript(root.document) });
    root.nekoVisitParentBridge = bridge;
    startRecoveryWhenReady(bridge, root, function (fn, ms) { return root.setTimeout(fn, ms); });
  }
})(typeof window !== 'undefined' ? window : globalThis);
