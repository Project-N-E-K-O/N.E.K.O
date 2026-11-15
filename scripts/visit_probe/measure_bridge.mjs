// 串门实现复验：在真实 Pet 窗里量 parent-bridge → 传输页 iframe 的取帧帧率与每帧打包耗时。
// 不连后端、不入房（iframe 的传输 WS 被替换成空操作），不动鼠标、不截图。
// 用法：node measure_bridge.mjs <cdpPort> <label> [check]
//   check：每帧打包后在同一任务里读回 alpha 半，统计空帧（会额外占一点主线程，只用于诊断）。
const port = process.argv[2] || '9333';
const label = process.argv[3] || 'run';
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const pet = targets.find((t) => t.type === 'page' && /127\.0\.0\.1:48911\/(\?|$|[A-Za-z0-9_%-]+$)/.test(t.url) && !/chat/.test(t.url))
  || targets.find((t) => t.type === 'page' && /48911/.test(t.url) && !/chat|toast|subtitle/.test(t.url));
if (!pet) { console.log(JSON.stringify(targets.map((t) => [t.type, t.url]))); process.exit(2); }
const ws = new WebSocket(pet.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let id = 0;
const pending = new Map();
ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
const call = (method, params) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const evaluate = async (expr) => {
  const res = await call('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (res.result.exceptionDetails) throw new Error(JSON.stringify(res.result.exceptionDetails).slice(0, 800));
  return res.result.result.value;
};

const out = await evaluate(`(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const env = {
    href: location.href, compat: !!(window.__NEKO_DESKTOP_RUNTIME__ && window.__NEKO_DESKTOP_RUNTIME__.compatibilityMode),
    modelType: window.avatarPortrait && window.avatarPortrait.normalizeModelType(),
    targetFrameRate: window.targetFrameRate, refreshHz: window.nekoFramePacing && window.nekoFramePacing.getDisplayRefreshHz && window.nekoFramePacing.getDisplayRefreshHz(),
    bridgeLoaded: !!window.nekoVisitParentBridge, dpr: devicePixelRatio, inner: [innerWidth, innerHeight],
    bounds: window.live2dManager && window.live2dManager.getModelScreenBounds && window.live2dManager.getModelScreenBounds(),
  };
  let release;
  const gate = new Promise((r) => { release = r; });
  const bridge = window.NekoVisitParentBridge.createParentBridge({ window, document, getToken: () => gate.then(() => 'probe-token'), assetVersion: 'probe' });
  const VID = 'ProbeVisit0000000000AB';
  window.__probeCheckEach = ${process.argv[4] === 'check' ? 'true' : 'false'};
  bridge.onStateChange({ type: 'visit_state_change', action: 'pending', side: 'guest', visit_id: VID });
  let result;
  // 任何一步失败都要收掉探针 iframe 与取帧状态，否则会留在 Pet 页上影响下一次测量
  try {
  const frame = bridge._state.frame;
  await new Promise((r) => { frame.addEventListener('load', r, { once: true }); setTimeout(r, 8000); });
  const cw = frame.contentWindow;
  const ctl = cw.NekoVisitTransport.controller;
  ctl.link.start = () => {};             // 不连后端：只量取帧与打包
  ctl.packer.active = true;
  // 父页只在传输页报「在发布」时取帧：走与真实 media{publish:true} 相同的通知
  ctl.media.publish = true;
  ctl.announcePublish(true);
  const diag = { frames: 0, blank: 0, blanks: [] };
  const origOnFrame = ctl.packer.onFrame.bind(ctl.packer);
  const pctx0 = ctl.packer.pack.getContext('2d');
  ctl.packer.onFrame = function (src, rect, ts) {
    const ok = origOnFrame(src, rect, ts);
    if (ok && window.__probeCheckEach) {
      const d = ctl.packer.dims;
      const img = pctx0.getImageData(0, d.cropH, d.cropW, d.cropH).data;
      let s = 0; for (let k = 0; k < img.length; k += 4) s += img[k];
      diag.frames++;
      if (s === 0) { diag.blank++; if (diag.blanks.length < 5) diag.blanks.push({ rect, srcW: src.width, srcH: src.height, t: Math.round(ts) }); }
    }
    return ok;
  };
  const track = ctl.packer.ensureTrack();
  release();
  await sleep(300);
  const t0 = performance.now();
  bridge.onStateChange({ type: 'visit_state_change', action: 'started', side: 'guest', visit_id: VID });
  const samples = [];
  let blank = 0, checked = 0;
  const pctx = ctl.packer.pack.getContext('2d');
  for (let i = 0; i <= 10; i++) {
    const d = ctl.packer.dims;
    if (ctl.packer.frames > 0) {
      const img = pctx.getImageData(0, d.cropH, d.cropW, d.cropH).data;
      let s = 0; for (let k = 0; k < img.length; k += 16) s += img[k];
      checked++; if (s === 0) blank++;
    }
    samples.push([Math.round(performance.now() - t0), ctl.packer.requestFrames, +ctl.packer.packMsTotal.toFixed(2), track.frames]);
    await sleep(1000);
  }
  const st = bridge._state;
  result = { diag, env, captureFps: st.captureFps, rect: st.rect, crop: st.cropMode, savedFps: st.savedFps,
    hiddenSent: st.hiddenSent, trackState: track.readyState, samples, blank, checked,
    iframeStyle: frame.style.cssText, liveActive: window.live2dManager && window.live2dManager._visitCaptureActive };
  } finally {
    try {
      bridge.onStateChange({ type: 'visit_state_change', action: 'ended', side: 'guest', visit_id: VID });
    } catch (err) {
      console.error('[measure_bridge] cleanup failed, reload the Pet page before the next run', err);
    }
  }
  await sleep(200);
  result.afterEnd = { frameGone: !document.getElementById('visit-frame'), targetFrameRate: window.targetFrameRate,
    liveActive: window.live2dManager && window.live2dManager._visitCaptureActive };
  return result;
})()`);
const s = out.samples;
const perSecond = s.slice(1).map((x, i) => x[1] - s[i][1]);
const total = s[s.length - 1][1] - s[0][1];
const elapsed = (s[s.length - 1][0] - s[0][0]) / 1000;
out.summary = { label, firstSecond: s[1][1] - s[0][1], perSecond, avgFps: +(total / elapsed).toFixed(2),
  packMsAvg: +((s[s.length - 1][2] - s[0][2]) / Math.max(1, total)).toFixed(3) };
console.log(JSON.stringify(out, null, 1));
ws.close();
