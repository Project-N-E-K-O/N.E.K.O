/*
 * 串门传输页：guest 侧取帧打包（设计稿 §3.4.2 / §3.4.6）。
 *
 * scratch（cropW×cropH，透明）与 pack（cropW×2·cropH，alpha:false）两块画布：
 * pack 填黑 → 上半画裁剪区（颜色 over 黑 = 预乘色）→ scratch 填白再 destination-in
 * 画同一裁剪区（白×alpha）→ pack 下半画 scratch（亮度 = alpha）→ packTrack.requestFrame()。
 * 尺寸只从构图几何推导：切构图 / 拥塞降档都就地改同一块画布的 width/height，不重建——
 * captureStream 出的轨道随之继续出帧，重建会让已发布的轨冻结。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};

  // 与 config/visit_settings.py VISIT_TIERS.sd600 的 crop_upper / crop_full 对偶
  var CROP_GEOMETRY = {
    upper: { w: 320, h: 448 },
    full: { w: 256, h: 560 },
  };

  // 与 VISIT_CONGESTION_LADDER 对偶：每级 [w, h, kbps]；只缩裁剪不动 fps
  var CONGESTION_LADDER = {
    upper: [[320, 448, 528], [256, 352, 400], [192, 272, 300]],
    full: [[256, 560, 528], [208, 448, 400], [160, 352, 300]],
  };

  var LADDER_TRIGGER_MS = 10000;      // VISIT_CONGESTION_TRIGGER_S
  var LADDER_RECOVER_MS = 30000;      // VISIT_CONGESTION_RECOVER_S
  var LADDER_FPS_RATIO = 0.8;         // rx_fps < 0.8 × 发送目标帧率（30 fps 时即 24）算拥塞
  var LADDER_LOSS_PCT = 15;           // 上行丢包 >15 % 算拥塞

  function normalizeCrop(crop) {
    return crop === 'full' ? 'full' : 'upper';
  }

  function clampLevel(crop, level) {
    var steps = CONGESTION_LADDER[normalizeCrop(crop)];
    var n = Number.isInteger(level) ? level : 0;
    return Math.max(0, Math.min(steps.length - 1, n));
  }

  /** 构图 + 阶梯级别 → 裁剪 / 打包尺寸与码率。 */
  function packDims(crop, level) {
    var c = normalizeCrop(crop);
    var lv = clampLevel(c, level);
    var step = CONGESTION_LADDER[c][lv];
    var base = lv === 0 ? CROP_GEOMETRY[c] : { w: step[0], h: step[1] };
    return {
      crop: c,
      level: lv,
      cropW: base.w,
      cropH: base.h,
      packW: base.w,
      packH: base.h * 2,
      kbps: step[2],
    };
  }

  /** TRTC profile / LiveKit 编码参数（尺寸 = 当前打包尺寸）。 */
  function profileFor(dims, fps) {
    return { width: dims.packW, height: dims.packH, frameRate: fps, bitrate: dims.kbps };
  }

  /**
   * 拥塞阶梯（纯函数式状态机）：坏指标连续 triggerMs 降一级，干净 recoverMs 升一级，
   * 最低档不再降。targetFps 是发送端当前目标帧率（兼容模式 15、其余 30）。
   */
  function LadderController(opts) {
    opts = opts || {};
    this.lockHighest = opts.lockHighest === true;
    this.crop = normalizeCrop(opts.crop);
    this.level = this.lockHighest ? 0 : clampLevel(this.crop, opts.level || 0);
    this.targetFps = opts.targetFps || 30;
    this.triggerMs = opts.triggerMs || LADDER_TRIGGER_MS;
    this.recoverMs = opts.recoverMs || LADDER_RECOVER_MS;
    this.badSince = null;
    this.cleanSince = null;
  }

  LadderController.prototype.reset = function (crop, level) {
    this.crop = normalizeCrop(crop);
    this.level = this.lockHighest ? 0 : clampLevel(this.crop, level || 0);
    this.badSince = null;
    this.cleanSince = null;
  };

  /** 本机 LiveKit 开发房不因双开 N.E.K.O 的渲染负载误判网络拥塞。 */
  LadderController.prototype.setLockHighest = function (on) {
    this.lockHighest = on === true;
    this.badSince = null;
    this.cleanSince = null;
    if (this.lockHighest && this.level !== 0) {
      this.level = 0;
      return 0;
    }
    return null;
  };

  LadderController.prototype.isBad = function (sample) {
    var fpsBad = typeof sample.rxFps === 'number' && sample.rxFps < this.targetFps * LADDER_FPS_RATIO;
    var lossBad = typeof sample.lossPct === 'number' && sample.lossPct > LADDER_LOSS_PCT;
    return fpsBad || lossBad;
  };

  /** 喂一次观测；级别变了返回新级别，否则 null。 */
  LadderController.prototype.feed = function (sample) {
    if (this.lockHighest) {
      this.badSince = null;
      this.cleanSince = null;
      if (this.level !== 0) {
        this.level = 0;
        return 0;
      }
      return null;
    }
    var now = sample.nowMs;
    var last = CONGESTION_LADDER[this.crop].length - 1;
    if (typeof sample.rxFps !== 'number' && typeof sample.lossPct !== 'number') {
      // 没有任何新鲜观测（对端在重连 / 暂时离开）：不能当成「干净」去升级，两个计时器都重新起算
      this.badSince = null;
      this.cleanSince = null;
      return null;
    }
    if (this.isBad(sample)) {
      this.cleanSince = null;
      if (this.badSince === null) this.badSince = now;
      if (now - this.badSince >= this.triggerMs && this.level < last) {
        this.level += 1;
        this.badSince = now;
        return this.level;
      }
      return null;
    }
    this.badSince = null;
    if (this.cleanSince === null) this.cleanSince = now;
    if (now - this.cleanSince >= this.recoverMs && this.level > 0) {
      this.level -= 1;
      this.cleanSince = now;
      return this.level;
    }
    return null;
  };

  /**
   * guest 打包器。doc 是创建画布用的 document（测试注入假实现）。
   * active=false 时 onFrame 直接返回（还没收到 publish 前不花一次 drawImage）。
   */
  function Packer(opts) {
    opts = opts || {};
    var doc = opts.document || root.document;
    this.scratch = doc.createElement('canvas');
    this.pack = doc.createElement('canvas');
    this.sctx = this.scratch.getContext('2d');
    this.pctx = this.pack.getContext('2d', { alpha: false });
    this.dims = null;
    this.track = null;
    this.active = false;
    this.frames = 0;
    this.requestFrames = 0;
    this.packMsTotal = 0;
    this.now = opts.now || function () { return (root.performance && root.performance.now) ? root.performance.now() : Date.now(); };
    this.setGeometry(opts.crop || 'upper', opts.level || 0);
  }

  /** 就地改同一块画布的尺寸（不重建）；返回新尺寸，尺寸没变返回 null。 */
  Packer.prototype.setGeometry = function (crop, level) {
    var dims = packDims(crop, level);
    var prev = this.dims;
    this.dims = dims;
    if (prev && prev.cropW === dims.cropW && prev.cropH === dims.cropH) return null;
    this.scratch.width = dims.cropW;
    this.scratch.height = dims.cropH;
    this.pack.width = dims.packW;
    this.pack.height = dims.packH;
    return dims;
  };

  /** 源矩形与源图求交，并按比例换算出目标矩形；不相交返回 null。 */
  function clipToSource(src, sx, sy, sw, sh, w, h, visible) {
    var srcW = src.naturalWidth || src.videoWidth || src.width || 0;
    var srcH = src.naturalHeight || src.videoHeight || src.height || 0;
    if (!(srcW > 0 && srcH > 0)) return null;
    var x0 = Math.max(sx, 0);
    var y0 = Math.max(sy, 0);
    var x1 = Math.min(sx + sw, srcW);
    var y1 = Math.min(sy + sh, srcH);
    if (visible) {
      // 源图里只有屏幕上可见的这一块能发（PNG 原图比视口大，取景框外扩后会落到屏幕外的像素）
      x0 = Math.max(x0, visible.x);
      y0 = Math.max(y0, visible.y);
      x1 = Math.min(x1, visible.x + visible.w);
      y1 = Math.min(y1, visible.y + visible.h);
    }
    if (!(x1 > x0 && y1 > y0)) return null;
    var kx = w / sw;
    var ky = h / sh;
    return {
      sx: x0, sy: y0, sw: x1 - x0, sh: y1 - y0,
      dx: (x0 - sx) * kx, dy: (y0 - sy) * ky, dw: (x1 - x0) * kx, dh: (y1 - y0) * ky,
    };
  }

  /** 当前可发布的轨：没有或已 ended 就从同一块 pack 画布 captureStream(0) 建新轨。 */
  Packer.prototype.ensureTrack = function () {
    if (this.track && this.track.readyState !== 'ended') return this.track;
    var stream = this.pack.captureStream(0);
    var track = stream.getVideoTracks()[0];
    try {
      track.contentHint = 'motion';
    } catch (_) {
      // 只影响编码器的降级取向，设不上不致命
    }
    this.track = track;
    return track;
  };

  /** 同步入口（父页 postrender 同一任务里调用）。返回是否真的出了一帧。 */
  Packer.prototype.onFrame = function (src, rectPx) {
    if (!this.active || !src || !rectPx) return false;
    var w = this.dims.cropW;
    var h = this.dims.cropH;
    var sx = rectPx.x;
    var sy = rectPx.y;
    var sw = rectPx.w;
    var sh = rectPx.h;
    if (!(sw > 0 && sh > 0)) return false;
    var t0 = this.now();
    var pctx = this.pctx;
    var sctx = this.sctx;
    pctx.globalCompositeOperation = 'source-over';
    pctx.fillStyle = '#000';
    pctx.fillRect(0, 0, w, h * 2);
    sctx.globalCompositeOperation = 'source-over';
    sctx.clearRect(0, 0, w, h);
    // 取景框可能越出源图（全身构图外扩 4%、模型挂在屏幕边缘）：只画与源图的交集，
    // 遮罩也只在交集里填白——越界部分在两半里都保持「黑 / 透明」，不会编成不透明黑边
    var part = clipToSource(src, sx, sy, sw, sh, w, h, rectPx.visible || null);
    if (part) {
      // flipX：源在屏幕上是水平镜像显示的（PNG mirror），两半都按镜像画，对端看到的朝向与本地一致
      var flip = rectPx.flipX === true;
      if (flip) {
        pctx.save();
        pctx.translate(w, 0);
        pctx.scale(-1, 1);
        sctx.save();
        sctx.translate(w, 0);
        sctx.scale(-1, 1);
      }
      pctx.drawImage(src, part.sx, part.sy, part.sw, part.sh, part.dx, part.dy, part.dw, part.dh);
      sctx.fillStyle = '#fff';
      sctx.fillRect(part.dx, part.dy, part.dw, part.dh);
      sctx.globalCompositeOperation = 'destination-in';
      sctx.drawImage(src, part.sx, part.sy, part.sw, part.sh, part.dx, part.dy, part.dw, part.dh);
      sctx.globalCompositeOperation = 'source-over';
      if (flip) {
        sctx.restore();
        pctx.restore();
      }
    }
    pctx.drawImage(this.scratch, 0, h);
    this.frames += 1;
    if (this.track && this.track.readyState !== 'ended' && typeof this.track.requestFrame === 'function') {
      this.track.requestFrame();
      this.requestFrames += 1;
    }
    this.packMsTotal += this.now() - t0;
    return true;
  };

  /** host 侧解包几何：裁剪高 = videoHeight/2，宽高比 = videoWidth/(videoHeight/2)。 */
  function unpackGeometry(videoW, videoH) {
    if (!(videoW > 0 && videoH > 1)) return null;
    var cropH = videoH / 2;
    return { cropW: videoW, cropH: cropH, splitY: cropH, aspect: videoW / cropH };
  }

  NVT.pack = {
    CROP_GEOMETRY: CROP_GEOMETRY,
    CONGESTION_LADDER: CONGESTION_LADDER,
    packDims: packDims,
    profileFor: profileFor,
    unpackGeometry: unpackGeometry,
    clipToSource: clipToSource,
    LadderController: LadderController,
    Packer: Packer,
  };
})(typeof window !== 'undefined' ? window : globalThis);
