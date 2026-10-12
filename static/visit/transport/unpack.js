/*
 * 串门传输页：host 侧解包（设计稿 §3.4.5）。iframe 本身就是访客图层。
 *
 * 隐藏的 <video>（不能 display:none）接对端轨 → requestVideoFrameCallback 每帧一次
 * texImage2D → 解包 shader：上半取 rgb（预乘色）、下半取 r 作 alpha，画到透明 WebGL 画布
 * （premultipliedAlpha:true）。分界与显示比例从 videoWidth / videoHeight 推导，不写死尺寸。
 * rVFC 1 s 不触发 → setTimeout 30 Hz 回落。首帧缩成 96×96 PNG 经 postMessage 交父页作头像。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};

  var RVFC_WATCHDOG_MS = 1000;
  var FALLBACK_INTERVAL_MS = 33;
  var THUMB_SIZE = 96;

  var VS = 'attribute vec2 p; varying vec2 uv; void main(){ uv = p*0.5+0.5; gl_Position = vec4(p,0.0,1.0); }';
  // UNPACK_FLIP_Y 后纹理 v=0 是画面底边：下半（alpha）在 v∈[0,0.5]，上半（颜色）在 v∈[0.5,1]
  var FS = 'precision mediump float; varying vec2 uv; uniform sampler2D t;' +
    'void main(){ float a = texture2D(t, vec2(uv.x, uv.y*0.5)).r;' +
    ' vec3 c = texture2D(t, vec2(uv.x, 0.5+uv.y*0.5)).rgb;' +
    ' gl_FragColor = vec4(min(c, vec3(a)), a); }';

  function compile(gl) {
    var prog = gl.createProgram();
    [[gl.VERTEX_SHADER, VS], [gl.FRAGMENT_SHADER, FS]].forEach(function (pair) {
      var s = gl.createShader(pair[0]);
      gl.shaderSource(s, pair[1]);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error('unpack shader: ' + gl.getShaderInfoLog(s));
      gl.attachShader(prog, s);
    });
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error('unpack link: ' + gl.getProgramInfoLog(prog));
    gl.useProgram(prog);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    var tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    return { prog: prog, tex: tex };
  }

  /** 能力门 ③（host）：透明 WebGL 解包画布能不能建起来。 */
  function webglUnpackSupported(doc) {
    try {
      var c = (doc || root.document).createElement('canvas');
      var gl = c.getContext('webgl', { alpha: true, premultipliedAlpha: true });
      if (!gl) return false;
      compile(gl);
      var ext = gl.getExtension('WEBGL_lose_context');
      if (ext) ext.loseContext();
      return true;
    } catch (_) {
      return false;
    }
  }

  /** 把 CSS 尺寸换算成画布像素：乘 DPR、取整到偶数、至少 2。 */
  function canvasPixels(cssW, cssH, dpr) {
    function even(v) {
      var n = Math.max(2, Math.round(v));
      return n % 2 === 0 ? n : n + 1;
    }
    var r = dpr > 0 ? dpr : 1;
    return { width: even(cssW * r), height: even(cssH * r) };
  }

  function Unpacker(opts) {
    this.video = opts.video;
    this.canvas = opts.canvas;
    this.post = opts.post || function () {};
    this.dpr = opts.dpr || function () { return root.devicePixelRatio || 1; };
    this.gl = null;
    this.glState = null;
    this.track = null;
    this.pendingTrack = null;   // WebGL 上下文丢失期间收到的远端轨：恢复后再接上
    this.firstFrameSent = false;
    this.lastRvfcAt = 0;
    this.presented = 0;
    this.draws = 0;
    this.statsMark = null;
    this.fallbackTimer = null;
    this.watchdogTimer = null;
    this.cssSize = { width: 0, height: 0 };
    this.generation = 0;
    var self = this;
    var canvas = this.canvas;
    if (canvas && typeof canvas.addEventListener === 'function') {
      // GPU 重置 / 上下文被回收：旧的 program 与纹理都作废；恢复后按同一画布重建，访客图层接着画
      canvas.addEventListener('webglcontextlost', function (e) {
        if (e && typeof e.preventDefault === 'function') e.preventDefault();
        self.gl = null;
        self.glState = null;
      }, false);
      canvas.addEventListener('webglcontextrestored', function () {
        if (!self.track && !self.pendingTrack) return;
        try {
          self._ensureGl();
        } catch (_) {
          self.gl = null;
          self.glState = null;
        }
        if (self.gl && self.pendingTrack) {
          var pending = self.pendingTrack;
          self.pendingTrack = null;
          self.attach(pending);
        }
      }, false);
    }
  }

  /** 建 / 取解包用的 WebGL 状态；上下文丢着（含浏览器仍返回那个已丢失的上下文对象）或初始化失败都返回 false。 */
  Unpacker.prototype._ensureGl = function () {
    if (this.gl) return true;
    var gl = this.canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: false, preserveDrawingBuffer: false });
    if (!gl) return false;
    if (typeof gl.isContextLost === 'function' && gl.isContextLost() === true) return false;
    try {
      this.glState = compile(gl);
    } catch (_) {
      this.glState = null;
      return false;
    }
    this.gl = gl;
    return true;
  };

  /** 父页 place：只用来设解包画布的像素尺寸。 */
  Unpacker.prototype.setCssSize = function (width, height) {
    this.cssSize = { width: width, height: height };
  };

  Unpacker.prototype.attach = function (track) {
    if (this.track === track || (track && this.pendingTrack === track)) return;
    this.detach();
    if (!track) return;
    if (!this._ensureGl()) {
      // 上下文正丢着：先记下这条轨，webglcontextrestored 时接上（之后不保证还有新的订阅事件）
      this.pendingTrack = track;
      return;
    }
    this.track = track;
    var gen = ++this.generation;
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.srcObject = new MediaStream([track]);
    var playing = this.video.play();
    if (playing && typeof playing.catch === 'function') playing.catch(function () {});
    this._armRvfc(gen);
    this._armWatchdog(gen);
  };

  Unpacker.prototype._armRvfc = function (gen) {
    var self = this;
    if (typeof this.video.requestVideoFrameCallback !== 'function') return;
    this.video.requestVideoFrameCallback(function (now, meta) {
      if (gen !== self.generation) return;
      self.lastRvfcAt = Date.now();
      if (meta && typeof meta.presentedFrames === 'number') self.presented = meta.presentedFrames;
      self._stopFallback();
      self.draw();
      self._armRvfc(gen);
    });
  };

  Unpacker.prototype._armWatchdog = function (gen) {
    var self = this;
    this.watchdogTimer = setTimeout(function tick() {
      if (gen !== self.generation) return;
      if (Date.now() - self.lastRvfcAt > RVFC_WATCHDOG_MS) self._startFallback(gen);
      self.watchdogTimer = setTimeout(tick, RVFC_WATCHDOG_MS);
    }, RVFC_WATCHDOG_MS);
  };

  Unpacker.prototype._startFallback = function (gen) {
    if (this.fallbackTimer !== null) return;
    var self = this;
    this.fallbackTimer = setTimeout(function loop() {
      if (gen !== self.generation) return;
      self.draw();
      self.fallbackTimer = setTimeout(loop, FALLBACK_INTERVAL_MS);
    }, FALLBACK_INTERVAL_MS);
  };

  Unpacker.prototype._stopFallback = function () {
    if (this.fallbackTimer !== null) clearTimeout(this.fallbackTimer);
    this.fallbackTimer = null;
  };

  Unpacker.prototype.draw = function () {
    var gl = this.gl;
    var video = this.video;
    if (!gl || !video || video.readyState < 2) return false;
    var geo = NVT.pack.unpackGeometry(video.videoWidth, video.videoHeight);
    if (!geo) return false;
    var cssW = this.cssSize.width || geo.cropW;
    var cssH = this.cssSize.height || geo.cropH;
    var px = canvasPixels(cssW, cssH, this.dpr());
    var c = this.canvas;
    if (c.width !== px.width || c.height !== px.height) {
      c.width = px.width;
      c.height = px.height;
    }
    gl.viewport(0, 0, c.width, c.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.bindTexture(gl.TEXTURE_2D, this.glState.tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    this.draws += 1;
    if (!this.firstFrameSent) {
      this.firstFrameSent = true;
      this._sendFirstFrame(geo);
    }
    return true;
  };

  /** 首帧 96×96 缩略：与画完解包画布同一任务里拷出，回调里 FileReader 转 dataURL。 */
  Unpacker.prototype._sendFirstFrame = function (geo) {
    var post = this.post;
    try {
      var thumb = this.canvas.ownerDocument.createElement('canvas');
      thumb.width = THUMB_SIZE;
      thumb.height = THUMB_SIZE;
      var ctx = thumb.getContext('2d');
      var scale = Math.min(THUMB_SIZE / geo.cropW, THUMB_SIZE / geo.cropH);
      var w = geo.cropW * scale;
      var h = geo.cropH * scale;
      ctx.drawImage(this.canvas, (THUMB_SIZE - w) / 2, (THUMB_SIZE - h) / 2, w, h);
      thumb.toBlob(function (blob) {
        if (!blob) return;
        var reader = new FileReader();
        reader.onload = function () {
          if (typeof reader.result === 'string') post({ t: 'first_frame', dataURL: reader.result });
        };
        reader.readAsDataURL(blob);
      }, 'image/png');
    } catch (_) {
      // 缩略图失败只影响头像，访客画面照常
    }
  };

  /**
   * 5 s 统计：rx_fps 用解码帧计数差分——rVFC 在跑时用 presentedFrames，否则用
   * getVideoPlaybackQuality() 的已解码帧数。不能用回落定时器的重绘次数：流卡住后它照样
   * 每 33 ms 重画同一帧，会把停住的流报成约 30 fps，拥塞阶梯就看不到卡顿。
   */
  Unpacker.prototype.frameCounter = function () {
    var video = this.video;
    if (this.lastRvfcAt && Date.now() - this.lastRvfcAt <= RVFC_WATCHDOG_MS && this.presented) {
      return { kind: 'presented', count: this.presented };
    }
    if (video && typeof video.getVideoPlaybackQuality === 'function') {
      var q = video.getVideoPlaybackQuality();
      if (q && typeof q.totalVideoFrames === 'number') {
        return { kind: 'decoded', count: q.totalVideoFrames - (q.droppedVideoFrames || 0) };
      }
    }
    return null;
  };

  Unpacker.prototype.stats = function (nowMs) {
    var out = { rx_w: this.video ? this.video.videoWidth || 0 : 0, rx_h: this.video ? this.video.videoHeight || 0 : 0 };
    var c = this.frameCounter();
    var prev = this.statsMark;
    if (c && prev && prev.kind === c.kind && nowMs > prev.at) {
      out.rx_fps = Math.max(0, (c.count - prev.count) * 1000 / (nowMs - prev.at));
    }
    this.statsMark = c ? { at: nowMs, kind: c.kind, count: c.count } : null;
    return out;
  };

  /** 断线 / 重连：丢掉帧计数基准，下一拍重新取样（不拿含断线期的间隔算帧率）。 */
  Unpacker.prototype.resetStats = function () {
    this.statsMark = null;
  };

  Unpacker.prototype.detach = function () {
    this.generation += 1;
    this._stopFallback();
    if (this.watchdogTimer !== null) clearTimeout(this.watchdogTimer);
    this.watchdogTimer = null;
    this.track = null;
    this.pendingTrack = null;
    this.statsMark = null;
    if (this.video) {
      try {
        this.video.srcObject = null;
      } catch (_) {
        // 已卸载的 video
      }
    }
    if (this.gl) {
      this.gl.clearColor(0, 0, 0, 0);
      this.gl.clear(this.gl.COLOR_BUFFER_BIT);
    }
  };

  NVT.unpack = {
    Unpacker: Unpacker,
    canvasPixels: canvasPixels,
    webglUnpackSupported: webglUnpackSupported,
  };
})(typeof window !== 'undefined' ? window : globalThis);
