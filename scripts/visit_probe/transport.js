/*
 * Visit T1~T5 probe: iframe side (served same-origin under /static/_visit_probe/).
 * Not product code. Mirrors design §3.3 / §3.4.2 / §3.4.5 closely enough to measure:
 *   T1 native WebSocket / RTCPeerConnection inside the same-origin child frame
 *   T2 synchronous cross-realm drawImage from the parent's postrender + 30 fps pacing (loopback getStats)
 *   T4 transparent WebGL unpack canvas composited over the desktop
 *   T5 2D destination-in alpha packing vs WebGL pack shader
 */
(function () {
  'use strict';

  const params = new URLSearchParams(location.search);
  const MODE = params.get('mode') || 'guest';
  const probe = (window.__probe = { mode: MODE });

  function isNative(fn) {
    try {
      return typeof fn === 'function' && /\{\s*\[native code\]\s*\}\s*$/.test(Function.prototype.toString.call(fn));
    } catch (_) {
      return false;
    }
  }

  // ---------------------------------------------------------------- T1
  probe.t1Probe = function () {
    let parentWsName = null;
    let parentWsNative = null;
    try {
      parentWsName = parent.WebSocket && parent.WebSocket.name;
      parentWsNative = isNative(parent.WebSocket);
    } catch (_) {}
    return {
      wsName: window.WebSocket && window.WebSocket.name,
      wsNative: isNative(window.WebSocket),
      wsProtoNative: Object.getPrototypeOf(window.WebSocket.prototype) === EventTarget.prototype,
      wsSendNative: isNative(window.WebSocket.prototype.send),
      sameAsParentWS: window.WebSocket === parent.WebSocket,
      parentWsName,
      parentWsNative,
      rtcName: window.RTCPeerConnection && window.RTCPeerConnection.name,
      rtcNative: isNative(window.RTCPeerConnection),
      captureStreamNative: isNative(HTMLCanvasElement.prototype.captureStream),
      rvfcNative: isNative(HTMLVideoElement.prototype.requestVideoFrameCallback),
      isSecureContext: window.isSecureContext,
      ownGlobals: {
        electronScreen: 'electronScreen' in window,
        __NEKO_MULTI_WINDOW__: '__NEKO_MULTI_WINDOW__' in window,
        nekoFramePacing: 'nekoFramePacing' in window,
        appState: 'appState' in window,
        live2dManager: 'live2dManager' in window,
        require: typeof window.require,
        process: typeof window.process,
      },
      userAgent: navigator.userAgent,
    };
  };

  probe.t1Connect = function (url, timeoutMs) {
    return new Promise((resolve) => {
      const t0 = performance.now();
      let ws;
      try {
        ws = new WebSocket(url);
      } catch (e) {
        resolve({ ok: false, err: String(e) });
        return;
      }
      const ctorName = ws.constructor && ws.constructor.name;
      const timer = setTimeout(() => {
        resolve({ ok: false, err: 'timeout', readyState: ws.readyState, ctorName });
        try { ws.close(); } catch (_) {}
      }, timeoutMs || 3000);
      ws.onopen = () => ws.send('ping-from-iframe');
      ws.onmessage = (ev) => {
        clearTimeout(timer);
        resolve({ ok: true, echo: String(ev.data), ms: Math.round(performance.now() - t0), ctorName });
        ws.close();
      };
      ws.onerror = () => {};
    });
  };

  // ---------------------------------------------------------------- packers
  const crop = { w: 320, h: 448 };

  // 2D packer (design §3.4.2): scratch (alpha) + pack (opaque, cropW x 2cropH).
  const scratch = document.createElement('canvas');
  const sctx = scratch.getContext('2d');
  const pack = document.createElement('canvas');
  const pctx = pack.getContext('2d', { alpha: false });

  // WebGL packer (T5 fallback): raw crop (2D, alpha) uploaded as texture, shader writes rgb / aaa halves.
  const raw = document.createElement('canvas');
  const rctx = raw.getContext('2d');
  const packGL = document.createElement('canvas');
  const gpack = packGL.getContext('webgl', { alpha: false, antialias: false, premultipliedAlpha: false, preserveDrawingBuffer: false });

  function sizeCanvases() {
    scratch.width = crop.w; scratch.height = crop.h;
    pack.width = crop.w; pack.height = crop.h * 2;
    raw.width = crop.w; raw.height = crop.h;
    packGL.width = crop.w; packGL.height = crop.h * 2;
  }
  sizeCanvases();

  function compile(gl, vs, fs) {
    const p = gl.createProgram();
    for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      gl.attachShader(p, s);
    }
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return p;
  }
  const QUAD_VS = 'attribute vec2 p; varying vec2 uv; void main(){ uv = p*0.5+0.5; gl_Position = vec4(p,0.0,1.0); }';
  function setupQuad(gl, prog) {
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  }
  function makeTex(gl) {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }

  const glPack = (function () {
    const gl = gpack;
    const prog = compile(gl, QUAD_VS,
      'precision mediump float; varying vec2 uv; uniform sampler2D t; uniform float m;' +
      'void main(){ vec4 c = texture2D(t, uv); gl_FragColor = m < 0.5 ? vec4(c.rgb, 1.0) : vec4(c.aaa, 1.0); }');
    gl.useProgram(prog);
    setupQuad(gl, prog);
    const tex = makeTex(gl);
    const mLoc = gl.getUniformLocation(prog, 'm');
    return function draw() {
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, raw);
      // GL origin is bottom-left: the top half of the packed image is viewport y = crop.h.
      gl.viewport(0, crop.h, crop.w, crop.h);
      gl.uniform1f(mLoc, 0);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.viewport(0, 0, crop.w, crop.h);
      gl.uniform1f(mLoc, 1);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };
  })();

  function pack2D(src, sx, sy, sw, sh) {
    pctx.globalCompositeOperation = 'source-over';
    pctx.fillStyle = '#000';
    pctx.fillRect(0, 0, crop.w, crop.h * 2);
    pctx.drawImage(src, sx, sy, sw, sh, 0, 0, crop.w, crop.h);
    sctx.globalCompositeOperation = 'source-over';
    sctx.clearRect(0, 0, crop.w, crop.h);
    sctx.fillStyle = '#fff';
    sctx.fillRect(0, 0, crop.w, crop.h);
    sctx.globalCompositeOperation = 'destination-in';
    sctx.drawImage(src, sx, sy, sw, sh, 0, 0, crop.w, crop.h);
    sctx.globalCompositeOperation = 'source-over';
    pctx.drawImage(scratch, 0, crop.h);
  }

  function packWebGL(src, sx, sy, sw, sh) {
    rctx.clearRect(0, 0, crop.w, crop.h);
    rctx.drawImage(src, sx, sy, sw, sh, 0, 0, crop.w, crop.h);
    glPack();
  }

  // Readback of the packed result as top-down RGBA (only used by verification paths).
  function readPacked(which) {
    if (which === 'webgl') {
      const w = crop.w, h = crop.h * 2;
      const buf = new Uint8Array(w * h * 4);
      gpack.readPixels(0, 0, w, h, gpack.RGBA, gpack.UNSIGNED_BYTE, buf);
      const out = new Uint8ClampedArray(w * h * 4);
      for (let y = 0; y < h; y++) out.set(buf.subarray((h - 1 - y) * w * 4, (h - y) * w * 4), y * w * 4);
      return out;
    }
    return pctx.getImageData(0, 0, crop.w, crop.h * 2).data;
  }

  // Reference crop: plain alpha 2D canvas, getImageData gives un-premultiplied RGBA.
  const ref = document.createElement('canvas');
  const refctx = ref.getContext('2d', { willReadFrequently: true });
  function readReference(src, sx, sy, sw, sh) {
    ref.width = crop.w; ref.height = crop.h;
    refctx.clearRect(0, 0, crop.w, crop.h);
    refctx.drawImage(src, sx, sy, sw, sh, 0, 0, crop.w, crop.h);
    return refctx.getImageData(0, 0, crop.w, crop.h).data;
  }

  function compareToReference(refData, packed) {
    const n = crop.w * crop.h;
    const off = n * 4;
    let maxA = 0, sumA = 0, maxC = 0, sumC = 0, overA2 = 0, overC2 = 0, opaqueTopLeak = 0, covered = 0;
    const alphaSeen = new Set();
    for (let i = 0; i < n; i++) {
      const r = refData[i * 4], g = refData[i * 4 + 1], b = refData[i * 4 + 2], a = refData[i * 4 + 3];
      if (a > 0) covered++;
      alphaSeen.add(a);
      // bottom half: luminance must equal alpha (r=g=b=a)
      const la = packed[off + i * 4], lg = packed[off + i * 4 + 1], lb = packed[off + i * 4 + 2];
      const ea = Math.max(Math.abs(la - a), Math.abs(lg - a), Math.abs(lb - a));
      maxA = Math.max(maxA, ea); sumA += ea; if (ea > 2) overA2++;
      // top half: premultiplied colour over black
      const pr = (r * a) / 255, pg = (g * a) / 255, pb = (b * a) / 255;
      const ec = Math.max(Math.abs(packed[i * 4] - pr), Math.abs(packed[i * 4 + 1] - pg), Math.abs(packed[i * 4 + 2] - pb));
      maxC = Math.max(maxC, ec); sumC += ec; if (ec > 2.5) overC2++;
      if (a === 0 && (packed[i * 4] | packed[i * 4 + 1] | packed[i * 4 + 2]) !== 0) opaqueTopLeak++;
    }
    return {
      pixels: n,
      coveredPixels: covered,
      distinctAlphaValues: alphaSeen.size,
      alphaMaxErr: maxA,
      alphaMeanErr: +(sumA / n).toFixed(4),
      alphaPixelsErrOver2: overA2,
      colorMaxErr: +maxC.toFixed(2),
      colorMeanErr: +(sumC / n).toFixed(4),
      colorPixelsErrOver2_5: overC2,
      transparentPixelsWithNonBlackTop: opaqueTopLeak,
    };
  }

  // ---------------------------------------------------------------- frame sink
  const stat = (probe.stat = {
    onFrame: 0, requestFrame: 0, blank: 0, verified: 0, minAlphaSum: Infinity, maxAlphaSum: 0,
    packMsSum: 0, packMsMax: 0, rvfc: 0, lastRvfcPresented: 0, unpackDraws: 0,
  });
  let packer = '2d';
  let verifyEvery = 0; // 0 = no readback; N = readback bottom-half every N frames (blank-frame check)
  let t5Pending = null; // resolve fn for one-shot T5 verification on the next real frame
  let track = null;
  let displaySource = null; // 'pack' | 'packgl' | 'video'

  function bottomAlphaSum(which) {
    const d = readPacked(which);
    const off = crop.w * crop.h * 4;
    let s = 0;
    for (let i = off; i < d.length; i += 16) s += d[i]; // every 4th pixel of the alpha half
    return s;
  }

  const sink = {
    onFrame(src, rectPx /* [sx, sy, sw, sh] */, tsMs) {
      const t0 = performance.now();
      const [sx, sy, sw, sh] = rectPx;
      if (t5Pending) {
        const refData = readReference(src, sx, sy, sw, sh);
        pack2D(src, sx, sy, sw, sh);
        const r2d = compareToReference(refData, readPacked('2d'));
        packWebGL(src, sx, sy, sw, sh);
        const rgl = compareToReference(refData, readPacked('webgl'));
        const done = t5Pending;
        t5Pending = null;
        done({ '2d': r2d, webgl: rgl, rect: rectPx, srcW: src.width, srcH: src.height });
      }
      if (packer === 'webgl') packWebGL(src, sx, sy, sw, sh);
      else pack2D(src, sx, sy, sw, sh);
      stat.onFrame++;
      if (verifyEvery > 0 && stat.onFrame % verifyEvery === 0) {
        const s = bottomAlphaSum(packer);
        stat.verified++;
        if (s === 0) stat.blank++;
        stat.minAlphaSum = Math.min(stat.minAlphaSum, s);
        stat.maxAlphaSum = Math.max(stat.maxAlphaSum, s);
      }
      if (track) { track.requestFrame(); stat.requestFrame++; }
      if (displaySource === 'pack' || displaySource === 'packgl') drawUnpack(displaySource === 'pack' ? pack : packGL);
      const dt = performance.now() - t0;
      stat.packMsSum += dt;
      stat.packMsMax = Math.max(stat.packMsMax, dt);
    },
    suspend() {},
    setAuthToken() {},
  };
  window.__nekoVisitFrameSink = sink;

  probe.configure = function (opts) {
    if (opts.crop) { crop.w = opts.crop[0]; crop.h = opts.crop[1]; sizeCanvases(); }
    if (opts.packer) packer = opts.packer;
    if ('verifyEvery' in opts) verifyEvery = opts.verifyEvery;
    return { crop: [crop.w, crop.h], packer, verifyEvery };
  };
  probe.resetStats = function () {
    Object.assign(stat, { onFrame: 0, requestFrame: 0, blank: 0, verified: 0, minAlphaSum: Infinity, maxAlphaSum: 0, packMsSum: 0, packMsMax: 0, rvfc: 0, unpackDraws: 0 });
  };
  probe.getStat = function () {
    return Object.assign({}, stat, { minAlphaSum: stat.minAlphaSum === Infinity ? null : stat.minAlphaSum, packMsAvg: stat.onFrame ? +(stat.packMsSum / stat.onFrame).toFixed(3) : null });
  };
  probe.packDataURL = function () { return pack.toDataURL('image/png'); };
  probe.armT5 = function (timeoutMs) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => { t5Pending = null; resolve({ err: 'timeout' }); }, timeoutMs || 3000);
      t5Pending = (r) => { clearTimeout(timer); resolve(r); };
    });
  };

  // ---------------------------------------------------------------- synthetic pattern (T5 exhaustive alpha, T4 composite)
  const BANDS = [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255], [128, 128, 128], [255, 200, 0], [255, 0, 255], [0, 200, 255]];
  probe.BANDS = BANDS;
  probe.makePattern = function (w, h) {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const x2 = c.getContext('2d');
    const img = x2.createImageData(w, h);
    for (let y = 0; y < h; y++) {
      const band = BANDS[Math.min(BANDS.length - 1, Math.floor((y / h) * BANDS.length))];
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        img.data[i] = band[0]; img.data[i + 1] = band[1]; img.data[i + 2] = band[2];
        img.data[i + 3] = Math.round((x / (w - 1)) * 255);
      }
    }
    x2.putImageData(img, 0, 0);
    return c;
  };
  // Same pattern drawn by a premultipliedAlpha:true WebGL context (closer to how PIXI hands us #live2d-canvas).
  probe.makePatternGL = function (w, h) {
    const src = probe.makePattern(w, h);
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const gl = c.getContext('webgl', { alpha: true, premultipliedAlpha: true, preserveDrawingBuffer: true, antialias: false });
    const prog = compile(gl, QUAD_VS, 'precision mediump float; varying vec2 uv; uniform sampler2D t; void main(){ gl_FragColor = texture2D(t, uv); }');
    gl.useProgram(prog);
    setupQuad(gl, prog);
    makeTex(gl);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    return c;
  };
  probe.t5Synthetic = function (kind) {
    const src = kind === 'gl' ? probe.makePatternGL(crop.w, crop.h) : probe.makePattern(crop.w, crop.h);
    const rect = [0, 0, crop.w, crop.h];
    const refData = readReference(src, ...rect);
    pack2D(src, ...rect);
    const r2d = compareToReference(refData, readPacked('2d'));
    packWebGL(src, ...rect);
    const rgl = compareToReference(refData, readPacked('webgl'));
    // a few spot values on the white band: expected bottom luminance == x/(w-1)*255
    const whiteRow = Math.floor((3.5 / BANDS.length) * crop.h);
    const p2 = readPacked('2d');
    const spots = [0, 1, 2, 64, 128, 191, 254, crop.w - 1].map((x) => {
      const exp = Math.round((x / (crop.w - 1)) * 255);
      const i = ((crop.h + whiteRow) * crop.w + x) * 4;
      const it = (whiteRow * crop.w + x) * 4;
      return { x, expectedAlpha: exp, packBottom: p2[i], packTop: p2[it] };
    });
    return { kind, '2d': r2d, webgl: rgl, whiteBandSpots2d: spots };
  };

  // Feed the synthetic pattern through the sink as if it came from the parent (used by T4 overlay).
  let patternTimer = null;
  probe.startPatternFeed = function (fps, kind) {
    probe.stopPatternFeed();
    const src = kind === 'gl' ? probe.makePatternGL(crop.w, crop.h) : probe.makePattern(crop.w, crop.h);
    patternTimer = setInterval(() => sink.onFrame(src, [0, 0, crop.w, crop.h], performance.now()), Math.round(1000 / (fps || 30)));
    return true;
  };
  probe.stopPatternFeed = function () { if (patternTimer) clearInterval(patternTimer); patternTimer = null; };

  // ---------------------------------------------------------------- loopback WebRTC (T2 getStats acceptance)
  let pc1 = null, pc2 = null, sender = null, dcRecv = 0, rv = null;
  probe.startLoopback = async function (opts) {
    opts = opts || {};
    await probe.stopLoopback();
    const srcCanvas = opts.source === 'packgl' ? packGL : pack;
    const stream = srcCanvas.captureStream(0);
    track = stream.getVideoTracks()[0];
    track.contentHint = 'motion';
    pc1 = new RTCPeerConnection();
    pc2 = new RTCPeerConnection();
    pc1.onicecandidate = (e) => e.candidate && pc2.addIceCandidate(e.candidate);
    pc2.onicecandidate = (e) => e.candidate && pc1.addIceCandidate(e.candidate);
    const dc = pc1.createDataChannel('ctl', { ordered: true });
    pc2.ondatachannel = (e) => { e.channel.onmessage = () => { dcRecv++; }; };
    const tx = pc1.addTransceiver(track, { direction: 'sendonly', streams: [stream] });
    sender = tx.sender;
    if (opts.codec) {
      const caps = RTCRtpReceiver.getCapabilities('video').codecs;
      const want = caps.filter((c) => c.mimeType.toLowerCase() === 'video/' + opts.codec);
      const rest = caps.filter((c) => c.mimeType.toLowerCase() !== 'video/' + opts.codec);
      if (want.length) tx.setCodecPreferences(want.concat(rest));
    }
    rv = document.createElement('video');
    rv.id = 'rv';
    rv.muted = true; rv.playsInline = true; rv.autoplay = true;
    document.body.appendChild(rv);
    pc2.ontrack = (e) => { rv.srcObject = e.streams[0] || new MediaStream([e.track]); rv.play().catch(() => {}); };
    const offer = await pc1.createOffer();
    await pc1.setLocalDescription(offer);
    await pc2.setRemoteDescription(offer);
    const answer = await pc2.createAnswer();
    await pc2.setLocalDescription(answer);
    await pc1.setRemoteDescription(answer);
    const p = sender.getParameters();
    if (!p.encodings || !p.encodings.length) p.encodings = [{}];
    p.encodings[0].maxBitrate = opts.maxBitrate || 560000;
    p.encodings[0].maxFramerate = 30;
    p.degradationPreference = 'maintain-framerate';
    try { await sender.setParameters(p); } catch (e) { probe.setParamsErr = String(e); }
    await new Promise((res) => {
      if (dc.readyState === 'open') res();
      else dc.onopen = res;
      setTimeout(res, 3000);
    });
    dc.send('hello');
    const rvfcLoop = (now, meta) => {
      stat.rvfc++;
      stat.lastRvfcPresented = meta && meta.presentedFrames;
      if (displaySource === 'video') drawUnpack(rv);
      if (rv) rv.requestVideoFrameCallback(rvfcLoop);
    };
    rv.requestVideoFrameCallback(rvfcLoop);
    return { ok: true, dcState: dc.readyState };
  };
  probe.stopLoopback = async function () {
    try { if (pc1) pc1.close(); } catch (_) {}
    try { if (pc2) pc2.close(); } catch (_) {}
    if (track) try { track.stop(); } catch (_) {}
    if (rv) rv.remove();
    pc1 = pc2 = sender = track = rv = null;
    return true;
  };
  probe.rtcStats = async function () {
    if (!pc1) return null;
    const out = {};
    const s1 = await pc1.getStats();
    s1.forEach((r) => {
      if (r.type === 'outbound-rtp' && r.kind === 'video') {
        out.outbound = {
          framesEncoded: r.framesEncoded, framesSent: r.framesSent, framesPerSecond: r.framesPerSecond,
          frameWidth: r.frameWidth, frameHeight: r.frameHeight, encoderImplementation: r.encoderImplementation,
          qualityLimitationReason: r.qualityLimitationReason, bytesSent: r.bytesSent, targetBitrate: r.targetBitrate,
          scalabilityMode: r.scalabilityMode, powerEfficientEncoder: r.powerEfficientEncoder, codecId: r.codecId,
          totalEncodeTime: r.totalEncodeTime,
        };
      }
    });
    if (out.outbound && out.outbound.codecId) {
      const c = s1.get(out.outbound.codecId);
      out.outbound.codec = c && c.mimeType;
    }
    const s2 = await pc2.getStats();
    s2.forEach((r) => {
      if (r.type === 'inbound-rtp' && r.kind === 'video') {
        out.inbound = { framesDecoded: r.framesDecoded, framesPerSecond: r.framesPerSecond, frameWidth: r.frameWidth, frameHeight: r.frameHeight, framesDropped: r.framesDropped };
      }
    });
    out.dcRecv = dcRecv;
    out.videoWH = rv ? [rv.videoWidth, rv.videoHeight] : null;
    out.stat = probe.getStat();
    out.t = performance.now();
    return out;
  };

  // ---------------------------------------------------------------- unpack display (host overlay, design §3.4.5)
  let ugl = null, utex = null;
  probe.startDisplay = function (source) {
    displaySource = source;
    if (!ugl) {
      const c = document.createElement('canvas');
      c.id = 'gl';
      document.body.appendChild(c);
      ugl = c.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: false, preserveDrawingBuffer: false });
      const prog = compile(ugl, QUAD_VS,
        'precision mediump float; varying vec2 uv; uniform sampler2D t;' +
        'void main(){ float a = texture2D(t, vec2(uv.x, uv.y*0.5)).r; vec3 c = texture2D(t, vec2(uv.x, 0.5+uv.y*0.5)).rgb;' +
        ' gl_FragColor = vec4(min(c, vec3(a)), a); }');
      ugl.useProgram(prog);
      setupQuad(ugl, prog);
      utex = makeTex(ugl);
    }
    return true;
  };
  probe.stopDisplay = function () {
    displaySource = null;
    if (ugl) { ugl.clearColor(0, 0, 0, 0); ugl.clear(ugl.COLOR_BUFFER_BIT); }
    return true;
  };
  function drawUnpack(src) {
    if (!ugl) return;
    const c = ugl.canvas;
    const w = Math.max(1, Math.round(c.clientWidth * devicePixelRatio));
    const h = Math.max(1, Math.round(c.clientHeight * devicePixelRatio));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    ugl.viewport(0, 0, w, h);
    ugl.clearColor(0, 0, 0, 0);
    ugl.clear(ugl.COLOR_BUFFER_BIT);
    ugl.bindTexture(ugl.TEXTURE_2D, utex);
    ugl.pixelStorei(ugl.UNPACK_FLIP_Y_WEBGL, true);
    ugl.pixelStorei(ugl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    ugl.texImage2D(ugl.TEXTURE_2D, 0, ugl.RGBA, ugl.RGBA, ugl.UNSIGNED_BYTE, src);
    ugl.drawArrays(ugl.TRIANGLE_STRIP, 0, 4);
    stat.unpackDraws++;
  }

  probe.ready = true;
})();
