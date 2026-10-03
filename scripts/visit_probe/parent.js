/*
 * Visit T1~T5 probe: parent side. Injected into the Electron Pet window (index.html) over CDP.
 * Not product code. Mirrors design §3.3.5 (postrender hook, two guards, fractional accumulator,
 * synchronous cross-realm sink.onFrame) so the measured behaviour is the designed one.
 */
(function () {
  'use strict';
  if (window.__visitProbe && window.__visitProbe.version === 16) return;
  const P = (window.__visitProbe = { version: 16 });
  const BASE = '/static/_visit_probe/transport.html';

  P.env = function () {
    const lm = window.live2dManager;
    const app = lm && lm.pixi_app;
    const pacing = window.nekoFramePacing;
    return {
      href: location.href,
      multiWindow: !!window.__NEKO_MULTI_WINDOW__,
      wsName: window.WebSocket && window.WebSocket.name,
      dpr: window.devicePixelRatio,
      inner: [window.innerWidth, window.innerHeight],
      screenXY: [window.screenX, window.screenY],
      screen: [screen.width, screen.height],
      hasLive2d: !!(app && lm.currentModel),
      canvasWH: app ? [app.view.width, app.view.height] : null,
      refreshHz: pacing && typeof pacing.getDisplayRefreshHz === 'function' ? pacing.getDisplayRefreshHz() : null,
      targetFrameRate: window.targetFrameRate,
      idleTickMode: lm ? !!lm._idleTickMode : null,
      idleTickFps: lm ? lm._idleTickFps : null,
      tickerStarted: app ? app.ticker.started : null,
      tickerMaxFPS: app ? app.ticker.maxFPS : null,
      pixiVersion: window.PIXI && PIXI.VERSION,
      modelBounds: lm && typeof lm.getModelScreenBounds === 'function' ? lm.getModelScreenBounds() : null,
      chatSocketState: window.appState && window.appState.socket ? window.appState.socket.readyState : null,
    };
  };

  // ---------------------------------------------------------------- iframe management
  P.frames = {};
  P.makeFrame = function (name, mode, style) {
    return new Promise((resolve, reject) => {
      P.removeFrame(name);
      const f = document.createElement('iframe');
      f.src = BASE + '?mode=' + encodeURIComponent(mode) + '&v=' + Date.now();
      f.setAttribute('data-visit-probe', name);
      if (style.className) f.className = style.className;
      f.style.cssText = style.css;
      const timer = setTimeout(() => reject(new Error('iframe load timeout')), 8000);
      f.onload = () => {
        clearTimeout(timer);
        const cw = f.contentWindow;
        // keep a deadline: if transport.js throws before setting ready, fail instead of hanging forever
        const deadline = performance.now() + 5000;
        const wait = () => {
          if (cw.__probe && cw.__probe.ready) resolve(true);
          else if (performance.now() > deadline) reject(new Error('iframe loaded but transport.js never became ready'));
          else setTimeout(wait, 20);
        };
        wait();
      };
      document.body.appendChild(f);
      P.frames[name] = f;
    });
  };
  P.removeFrame = function (name) {
    const f = P.frames[name];
    if (f) {
      try { f.contentWindow.__probe && f.contentWindow.__probe.stopLoopback(); } catch (_) {}
      f.remove();
    }
    delete P.frames[name];
    return true;
  };
  P.fp = function (name) { return P.frames[name].contentWindow.__probe; };
  P.frameRect = function (name) {
    const r = P.frames[name].getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  };
  P.GUEST_STYLE = { css: 'position:fixed;left:-10px;top:-10px;width:1px;height:1px;border:0;background:transparent;pointer-events:none;' };
  P.hostStyle = function (rect, opts) {
    opts = opts || {};
    // explicit auto: the Pet body is pointer-events:none, so omitting the declaration would inherit none
    const pe = opts.peNone === false ? 'pointer-events:auto;' : 'pointer-events:none;';
    return {
      className: opts.noClass ? '' : 'transparent-overlay',
      css: `position:fixed;left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;z-index:${opts.z || 9};border:0;background:transparent;${pe}`,
    };
  };

  // Free area beside the model (the larger side), sized like §3.4.5.
  P.sideRect = function (aspect) {
    const b = window.live2dManager && window.live2dManager.getModelScreenBounds && window.live2dManager.getModelScreenBounds();
    const W = window.innerWidth, H = window.innerHeight;
    const h = Math.round(Math.max(200, Math.min(b ? b.height * 0.9 : H * 0.5, 900, H * 0.8)));
    const w = Math.round(h * (aspect || 320 / 448));
    let left;
    if (b) {
      const leftSpace = b.left, rightSpace = W - (b.left + b.width);
      left = rightSpace >= leftSpace ? Math.min(W - w - 8, b.left + b.width + 40) : Math.max(8, b.left - w - 40);
    } else {
      left = 40;
    }
    const top = Math.round(Math.max(8, Math.min(H - h - 8, b ? b.top + (b.height - h) / 2 : 40)));
    return { left: Math.round(left), top, width: w, height: h };
  };

  // ---------------------------------------------------------------- crop rect (cached, §3.3.5 step 3)
  P.rectPx = null;
  P.computeCrop = function (cropW, cropH) {
    const lm = window.live2dManager;
    const canvas = lm.pixi_app.view;
    const css = canvas.getBoundingClientRect();
    const sx = canvas.width / css.width, sy = canvas.height / css.height;
    const raw = lm.getModelScreenBounds();
    if (!raw) return null;
    // getModelScreenBounds() is not clipped to the viewport: a model hanging off-screen would put most of the
    // crop outside the canvas (transparent padding). Frame from the on-screen part only.
    const vl = Math.max(0, raw.left), vt = Math.max(0, raw.top);
    const vr = Math.min(window.innerWidth, raw.left + raw.width), vb = Math.min(window.innerHeight, raw.top + raw.height);
    if (vr <= vl || vb <= vt) return null;
    const b = { left: vl, top: vt, width: vr - vl, height: vb - vt };
    // upper-body box per §3.4.1 ratios (approx: top 64% of bounds, widened to crop aspect)
    const aspect = cropW / cropH;
    let h = b.height * 0.64;
    let w = Math.max(b.width * 1.04, h * aspect);
    h = Math.max(h, w / aspect);
    w = h * aspect;
    const cx = b.left + b.width / 2;
    const top = b.top;
    let rect = [(cx - w / 2 - css.left) * sx, (top - css.top) * sy, w * sx, h * sy];
    // keep the source rect inside the backing canvas: shrink (aspect kept) if larger, then shift in
    const k = Math.min(1, canvas.width / rect[2], canvas.height / rect[3]);
    if (k < 1) { const ncx = rect[0] + rect[2] / 2; rect[2] *= k; rect[3] *= k; rect[0] = ncx - rect[2] / 2; }
    rect[0] = Math.min(Math.max(0, rect[0]), canvas.width - rect[2]);
    rect[1] = Math.min(Math.max(0, rect[1]), canvas.height - rect[3]);
    rect = rect.map(Math.round);
    P.rectPx = rect;
    return { rect, bounds: b, rawBounds: raw, scale: [sx, sy] };
  };

  // ---------------------------------------------------------------- postrender hook (§3.3.5)
  P.stat = null;
  P.hook = function (frameName, opts) {
    opts = opts || {};
    P.unhook();
    const lm = window.live2dManager;
    const app = lm.pixi_app;
    const r = app.renderer;
    const sink = P.frames[frameName].contentWindow.__nekoVisitFrameSink;
    const stat = (P.stat = { postrender: 0, guardedRT: 0, guardedStage: 0, captures: 0, asyncCaptures: 0, renderFpsLast: 0, accMax: 0, startedAt: performance.now(), captureTimes: [] });
    const times = [];
    let acc = 0;
    const fn = function () {
      if (r.renderTexture && r.renderTexture.current) { stat.guardedRT++; return; }
      if (r.lastObjectRendered !== app.stage) { stat.guardedStage++; return; }
      const now = performance.now();
      stat.postrender++;
      times.push(now);
      while (times.length && times[0] < now - 1000) times.shift();
      const renderFps = Math.max(1, times.length);
      stat.renderFpsLast = renderFps;
      acc += 30 / renderFps;
      if (acc >= 1) {
        acc -= 1;
        if (acc >= 1) acc = 0;
        if (opts.async) {
          // control: deferred to a later task (what postMessage would do)
          const rect = P.rectPx;
          setTimeout(() => { stat.asyncCaptures++; sink.onFrame(app.view, rect, performance.now()); }, 0);
        } else {
          stat.captures++;
          if (stat.captureTimes.length < 400) stat.captureTimes.push(Math.round(now - stat.startedAt));
          const res = sink.onFrame(app.view, P.rectPx, now);
          if (res && res.blank) {
            // Blank pack: was the model really not drawn this frame (app-side), or did the cross-realm
            // drawImage read an empty buffer (probe/iframe-side)? Read the WebGL drawing buffer directly,
            // in the same task, over the same rect, and record the model's scene-graph state.
            const diag = { t: Math.round(now - stat.startedAt), captureIndex: stat.captures };
            try {
              const gl = r.gl;
              const [sx, sy, sw, sh] = P.rectPx;
              const x0 = Math.max(0, sx), y0 = Math.max(0, sy);
              const w = Math.max(0, Math.min(gl.drawingBufferWidth, sx + sw) - x0), h = Math.max(0, Math.min(gl.drawingBufferHeight, sy + sh) - y0);
              const buf = new Uint8Array(w * h * 4);
              gl.readPixels(x0, gl.drawingBufferHeight - y0 - h, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
              let a = 0;
              for (let i = 3; i < buf.length; i += 16) a += buf[i];
              diag.glReadAlphaSum = a;
              const m = lm.currentModel;
              diag.model = m ? { parentIsStage: m.parent === app.stage, visible: m.visible, renderable: m.renderable, worldAlpha: m.worldAlpha, destroyed: !!m.destroyed } : null;
              diag.stageChildren = app.stage.children.length;
              diag.canvasVisibility = getComputedStyle(app.view).visibility;
            } catch (e) { diag.err = String(e); }
            stat.blankDiag = stat.blankDiag || [];
            if (stat.blankDiag.length < 50) stat.blankDiag.push(diag);
          }
        }
      }
      stat.accMax = Math.max(stat.accMax, acc);
    };
    r.on('postrender', fn);
    P._unhook = () => r.off('postrender', fn);
    return true;
  };
  P.unhook = function () { if (P._unhook) P._unhook(); P._unhook = null; return true; };

  // ---------------------------------------------------------------- frame-rate modes
  P.saved = null;
  P.saveFps = function () {
    if (!P.saved) P.saved = { targetFrameRate: window.targetFrameRate, lipSync: window.appState && window.appState.lipSyncActive, ls: localStorage.getItem('project_neko_settings') };
    return P.saved;
  };
  P.restoreFps = function () {
    const s = P.saved;
    if (!s) return false;
    if (window.appState) window.appState.lipSyncActive = s.lipSync || false;
    if (P._origBoostX11) { window.live2dManager.boostLinuxX11InteractiveFPS = P._origBoostX11; P._origBoostX11 = null; }
    window.live2dManager._startIdleFpsGovernor();
    window.live2dManager.setTargetFPS(s.targetFrameRate);
    if (s.ls !== null && localStorage.getItem('project_neko_settings') !== s.ls) localStorage.setItem('project_neko_settings', s.ls);
    P.saved = null;
    return true;
  };
  // mode: {kind:'raf', fps} (activity + setTargetFPS) | {kind:'timer', fps} (forced setInterval tick)
  P.setMode = function (mode) {
    P.saveFps();
    const lm = window.live2dManager;
    // Hold the source rate steady for the measurement window: the idle governor would otherwise
    // flip between rAF and timer drive whenever a motion starts/ends. restoreFps() restarts it.
    lm._stopIdleFpsGovernor();
    // live2d-interaction boosts on every (synthetic) pointermove from the preload; mute it while measuring
    if (!P._origBoostX11) {
      P._origBoostX11 = lm.boostLinuxX11InteractiveFPS;
      lm.boostLinuxX11InteractiveFPS = function () {};
    }
    if (lm._idleFpsRestoreTimer) { clearTimeout(lm._idleFpsRestoreTimer); lm._idleFpsRestoreTimer = null; }
    // record who restarts the ticker behind our back (mode stability diagnostics)
    const t = lm.pixi_app.ticker;
    if (!t.__probeWrapped7) {
      const origStart = t.start;
      window.__probeTrace = P.tickerStarts = [];
      t.start = function () {
        if (P.tickerStarts.length < 20) P.tickerStarts.push({ at: Math.round(performance.now()), stack: String(new Error().stack).split(String.fromCharCode(10)).slice(2, 7).map((l) => l.trim()).join(' | ') });
        return origStart.apply(this, arguments);
      };
      t.__probeWrapped7 = true;
      for (const name of ['_enterIdleTickMode', '_exitIdleTickMode', 'boostInteractiveFPS', '_startIdleFpsGovernor', 'setTargetFPS']) {
        const orig = lm[name];
        lm[name] = function () {
          if (P.tickerStarts.length < 40 && !P._inSetMode) P.tickerStarts.push({ fn: name, args: Array.from(arguments).slice(0, 1), at: Math.round(performance.now()), stack: String(new Error().stack).split(String.fromCharCode(10)).slice(2, 6).map((l) => l.trim()).join(' | ') });
          return orig.apply(this, arguments);
        };
      }
    }
    if (window.__probeTrace) window.__probeTrace.length = 0;
    P._inSetMode = true;
    if (mode.kind === 'raf') {
      lm._exitIdleTickMode();
      lm._applyRafMaxFps(mode.fps);
    } else {
      lm._enterIdleTickMode(mode.fps);
    }
    P._inSetMode = false;
    return P.env();
  };

  // ---------------------------------------------------------------- hit-test helpers (T3)
  P.elementAt = function (x, y) {
    const el = document.elementFromPoint(x, y);
    if (!el) return null;
    return { tag: el.tagName, id: el.id || null, cls: (el.className && String(el.className).slice(0, 80)) || null, probe: el.getAttribute && el.getAttribute('data-visit-probe') };
  };

  P.ready = true;
})();
