// Visit T1~T5 probe driver (not product code).
// Drives the real Electron Pet window over CDP (--remote-debugging-port), injects parent.js,
// creates the same-origin probe iframe and records results + screenshots.
//
// usage: node drive.mjs --label direct --out <dir> [phases...]   phases: env t1 t2 t5 t3 t4 (default: all)
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf('--' + name); if (i < 0) return def; const v = args[i + 1]; args.splice(i, 2); return v; };
const LABEL = opt('label', 'direct');
const OUT = opt('out', path.join(HERE, 'results'));
const PORT = Number(opt('cdp', '9222'));
const SHELL_DIR = opt('shell', 'C:/Users/wehos/Project/lanlan_release/lanlan_frd');
const MAIN_REPO = opt('repo', 'C:/Users/wehos/Project/lanlan_release/Xiao8');
const PHASES = args.length ? args : ['env', 't1', 't2', 't5', 't3', 't4'];
const require = createRequire(path.join(SHELL_DIR, 'package.json'));
const WebSocket = require('ws');

const outDir = path.join(OUT, LABEL);
fs.mkdirSync(outDir, { recursive: true });
const results = fs.existsSync(path.join(outDir, 'results.json')) ? JSON.parse(fs.readFileSync(path.join(outDir, 'results.json'), 'utf8')) : {};
const save = () => fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify(results, null, 2));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

// ------------------------------------------------------------------ CDP
class Cdp {
  constructor(wsUrl) { this.wsUrl = wsUrl; this.id = 0; this.pending = new Map(); this.listeners = []; }
  open() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.wsUrl, { perMessageDeflate: false });
      this.ws.on('open', resolve);
      this.ws.on('error', reject);
      this.ws.on('message', (data) => {
        const msg = JSON.parse(String(data));
        if (msg.id && this.pending.has(msg.id)) {
          const { res, rej } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
        } else if (msg.method) {
          for (const l of this.listeners) l(msg);
        }
      });
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res, rej) => this.pending.set(id, { res, rej }));
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true, userGesture: true });
    if (r.exceptionDetails) throw new Error('page exception: ' + JSON.stringify(r.exceptionDetails.exception && r.exceptionDetails.exception.description || r.exceptionDetails.text));
    return r.result.value;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

async function listTargets() {
  const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
  return (await r.json()).filter((t) => t.type === 'page');
}

async function findTargets() {
  const found = { pet: null, chat: null, others: [] };
  for (const t of await listTargets()) {
    if (!/^https?:\/\/(localhost|127\.0\.0\.1)/.test(t.url)) { found.others.push(t.url); continue; }
    const u = new URL(t.url);
    if ((u.pathname === '/' || u.pathname === '/index.html') && !found.pet) found.pet = t;
    else if (u.pathname.startsWith('/chat') && !found.chat) found.chat = t;
    else found.others.push(t.url);
  }
  return found;
}

async function connectPet() {
  for (let i = 0; i < 60; i++) {
    const t = await findTargets();
    if (t.pet) {
      const c = new Cdp(t.pet.webSocketDebuggerUrl);
      await c.open();
      await c.send('Runtime.enable');
      const ok = await c.eval('return !!(window.live2dManager && window.live2dManager.currentModel && window.live2dManager.pixi_app);');
      if (ok) return { cdp: c, targets: t };
      c.close();
    }
    await sleep(1000);
  }
  throw new Error('Pet window with a loaded Live2D model not found');
}

async function inject(c) {
  await c.eval(fs.readFileSync(path.join(HERE, 'parent.js'), 'utf8') + '\nreturn true;');
}

function os(...a) {
  const out = execFileSync('uv', ['run', '--no-sync', 'python', path.join(HERE, 'osprobe.py'), ...a.map(String)], { cwd: MAIN_REPO, encoding: 'utf8' });
  return JSON.parse(out.trim().split(/\r?\n/).pop());
}

async function petGeometry(c) {
  const env = await c.eval('return window.__visitProbe.env();');
  const pet = os('pet').pet;
  const sx = pet.rect[2] / env.inner[0], sy = pet.rect[3] / env.inner[1];
  return { env, pet, toPhys: (x, y) => [Math.round(pet.rect[0] + x * sx), Math.round(pet.rect[1] + y * sy)], scale: [sx, sy] };
}

function physRect(geo, r) {
  const [x0, y0] = geo.toPhys(r.left, r.top);
  const [x1, y1] = geo.toPhys(r.left + r.width, r.top + r.height);
  return [x0, y0, x1 - x0, y1 - y0];
}

// ------------------------------------------------------------------ phases
async function phaseEnv(c) {
  const env = await c.eval('return window.__visitProbe.env();');
  results.env = { env, monitors: os('monitors'), pet: os('pet'), at: new Date().toISOString() };
  log('env', JSON.stringify(env));
}

async function phaseT1(c, targets) {
  const echo = new (WebSocket.Server || WebSocket.WebSocketServer)({ host: '127.0.0.1', port: 48990 });
  echo.on('connection', (s) => s.on('message', (m) => s.send('echo:' + m)));
  const r = { };
  try {
    await c.eval(`await window.__visitProbe.makeFrame('guest', 'guest', window.__visitProbe.GUEST_STYLE); return true;`);
    r.probe = await c.eval(`return window.__visitProbe.fp('guest').t1Probe();`);
    // Chat window observer: count CONNECTING deliveries reaching its WSProxy (pet-websocket-bridge.js:348 -> chat-websocket-bridge.js:72).
    let chat = null;
    if (targets.chat) {
      chat = new Cdp(targets.chat.webSocketDebuggerUrl);
      await chat.open();
      r.chatObserver = await chat.eval(`
        const s = window.appState && window.appState.socket;
        if (!s) return { err: 'no appState.socket', wsName: window.WebSocket && window.WebSocket.name };
        if (!s.__probeWrapped) {
          const orig = s._handleConnecting;
          window.__probeConnecting = 0;
          s._handleConnecting = function () { window.__probeConnecting++; return orig && orig.apply(this, arguments); };
          s.__probeWrapped = true;
        }
        return { ok: true, ctor: s.constructor && s.constructor.name, readyState: s.readyState, wsName: window.WebSocket && window.WebSocket.name };`);
    }
    const before = await c.eval(`return { petSocketState: window.appState && window.appState.socket && window.appState.socket.readyState };`);
    r.iframeConnect = await c.eval(`return await window.__visitProbe.fp('guest').t1Connect('ws://127.0.0.1:48990/', 3000);`);
    await sleep(1500);
    r.afterIframe = {
      ...(await c.eval(`return { petSocketState: window.appState && window.appState.socket && window.appState.socket.readyState };`)),
      chatConnectingCount: chat ? await chat.eval('return window.__probeConnecting;') : null,
      chatSocketState: chat ? await chat.eval('return window.appState && window.appState.socket && window.appState.socket.readyState;') : null,
    };
    r.before = before;
    // Positive control: the same connect from the parent realm goes through PetWebSocket and hijacks _activeWs.
    r.parentControl = await c.eval(`
      return await new Promise((resolve) => {
        const ws = new WebSocket('ws://127.0.0.1:48990/');
        const ctorName = ws.constructor && ws.constructor.name;
        const t = setTimeout(() => resolve({ ok: false, ctorName, err: 'timeout' }), 3000);
        ws.onopen = () => ws.send('ping-from-parent');
        ws.onmessage = (e) => { clearTimeout(t); resolve({ ok: true, echo: String(e.data), ctorName, parentWsName: window.WebSocket.name }); ws.close(); };
      });`);
    await sleep(1500);
    r.afterParentControl = {
      chatConnectingCount: chat ? await chat.eval('return window.__probeConnecting;') : null,
      chatSocketState: chat ? await chat.eval('return window.appState && window.appState.socket && window.appState.socket.readyState;') : null,
    };
    if (chat) chat.close();
    await c.eval(`window.__visitProbe.removeFrame('guest'); return true;`);
  } finally {
    echo.close();
  }
  results.t1 = r;
  save();
  log('t1', JSON.stringify(r, null, 1));
  // The positive control clobbered the Pet's _activeWs; reload Pet + Chat to restore the app.
  log('reloading Pet window to undo the positive control');
  await c.send('Page.reload', { ignoreCache: false });
  c.close();
  const t = await findTargets();
  if (t.chat) { const cc = new Cdp(t.chat.webSocketDebuggerUrl); await cc.open(); await cc.send('Page.reload', {}); cc.close(); }
}

async function measureWindow(c, ms) {
  const s0 = await c.eval(`return { rtc: await window.__visitProbe.fp('guest').rtcStats(), hook: Object.assign({}, window.__visitProbe.stat), t: performance.now() };`);
  await sleep(ms);
  const s1 = await c.eval(`return { rtc: await window.__visitProbe.fp('guest').rtcStats(), hook: Object.assign({}, window.__visitProbe.stat), t: performance.now(), env: window.__visitProbe.env(), trace: (window.__probeTrace || []).map((c) => c.fn + '(' + c.args.join(',') + ') <- ' + c.stack.split(' | ')[0]) };`);
  const dt = (s1.t - s0.t) / 1000;
  const o0 = s0.rtc.outbound || {}, o1 = s1.rtc.outbound || {};
  const i0 = s0.rtc.inbound || {}, i1 = s1.rtc.inbound || {};
  return {
    seconds: +dt.toFixed(2),
    postrenderPerSec: +((s1.hook.postrender - s0.hook.postrender) / dt).toFixed(2),
    capturesPerSec: +((s1.hook.captures - s0.hook.captures) / dt).toFixed(2),
    requestFramePerSec: +((s1.rtc.stat.requestFrame - s0.rtc.stat.requestFrame) / dt).toFixed(2),
    framesEncodedPerSec: +((o1.framesEncoded - o0.framesEncoded) / dt).toFixed(2),
    framesSentPerSec: +((o1.framesSent - o0.framesSent) / dt).toFixed(2),
    outboundFramesPerSecond: o1.framesPerSecond,
    framesDecodedPerSec: +((i1.framesDecoded - i0.framesDecoded) / dt).toFixed(2),
    inboundFramesPerSecond: i1.framesPerSecond,
    rvfcPerSec: +((s1.rtc.stat.rvfc - s0.rtc.stat.rvfc) / dt).toFixed(2),
    frame: [o1.frameWidth, o1.frameHeight],
    codec: o1.codec, encoderImplementation: o1.encoderImplementation, qualityLimitationReason: o1.qualityLimitationReason,
    kbps: +(((o1.bytesSent - o0.bytesSent) * 8) / dt / 1000).toFixed(1),
    packMsAvg: s1.rtc.stat.packMsAvg, packMsMax: +s1.rtc.stat.packMsMax.toFixed(2),
    guardedStage: s1.hook.guardedStage - s0.hook.guardedStage, guardedRT: s1.hook.guardedRT - s0.hook.guardedRT,
    modeChanges: s1.trace,
    mode: { idleTickMode: s1.env.idleTickMode, idleTickFps: s1.env.idleTickFps, tickerStarted: s1.env.tickerStarted, tickerMaxFPS: s1.env.tickerMaxFPS, refreshHz: s1.env.refreshHz },
  };
}

async function blankRun(c, label, frames, hookOpts) {
  await c.eval(`const fp = window.__visitProbe.fp('guest'); fp.resetStats(); fp.configure({ verifyEvery: 1 }); window.__visitProbe.hook('guest', ${JSON.stringify(hookOpts || {})}); return true;`);
  const t0 = Date.now();
  let st;
  for (;;) {
    await sleep(500);
    st = await c.eval(`return window.__visitProbe.fp('guest').getStat();`);
    if (st.verified >= frames || Date.now() - t0 > 30000) break;
  }
  const hook = await c.eval(`window.__visitProbe.unhook(); return Object.assign({}, window.__visitProbe.stat);`);
  return { label, framesChecked: st.verified, blankFrames: st.blank, minAlphaSum: st.minAlphaSum, maxAlphaSum: st.maxAlphaSum, packMsAvg: st.packMsAvg, packMsMax: +st.packMsMax.toFixed(2), hook };
}

async function phaseT2(c) {
  const r = { blank: [], fps: [] };
  await c.eval(`await window.__visitProbe.makeFrame('guest', 'guest', window.__visitProbe.GUEST_STYLE);
    window.__visitProbe.fp('guest').configure({ crop: [320, 448], packer: '2d', verifyEvery: 0 });
    return window.__visitProbe.computeCrop(320, 448);`).then((v) => { r.crop = v; });
  const env = await c.eval('return window.__visitProbe.env();');
  r.refreshHz = env.refreshHz;
  try {
    // 300-frame blank checks: timer-driven and rAF-driven, plus the async (postMessage-like) control.
    await c.eval(`return window.__visitProbe.setMode({ kind: 'timer', fps: 30 });`);
    await sleep(800);
    r.blank.push(await blankRun(c, 'timer30-sync', 300));
    await c.eval(`return window.__visitProbe.setMode({ kind: 'raf', fps: 0 });`);
    await sleep(800);
    r.blank.push(await blankRun(c, 'raf-vsync-sync', 300));
    await c.eval(`return window.__visitProbe.setMode({ kind: 'timer', fps: 60 });`);
    await sleep(800);
    r.blank.push(await blankRun(c, 'timer60-sync', 300));
    r.blank.push(await blankRun(c, 'timer60-async-control', 300, { async: true }));
    save();

    // 30 fps acceptance via loopback RTCPeerConnection getStats (no vendor needed for the pacing question).
    // Fresh iframe: the blank checks above used getImageData, which makes Chromium demote those canvases
    // to software; the product path never reads back, so cost must be measured on never-read canvases.
    await c.eval(`await window.__visitProbe.makeFrame('guest', 'guest', window.__visitProbe.GUEST_STYLE);
      const fp = window.__visitProbe.fp('guest'); fp.configure({ crop: [320, 448], packer: '2d', verifyEvery: 0 }); return await fp.startLoopback({ maxBitrate: 560000 });`);
    await c.eval(`window.__visitProbe.hook('guest'); return true;`);
    const modes = [
      { kind: 'raf', fps: 0, name: `raf-vsync(${env.refreshHz}Hz)` },
      { kind: 'raf', fps: 60, name: 'raf-cap60' },
      { kind: 'timer', fps: 30, name: 'timer30' },
      { kind: 'timer', fps: 45, name: 'timer45' },
      { kind: 'timer', fps: 60, name: 'timer60' },
      { kind: 'timer', fps: 75, name: 'timer75(sim 75Hz)' },
      { kind: 'timer', fps: 144, name: 'timer144(sim 144Hz)' },
      { kind: 'timer', fps: 24, name: 'timer24(<30 source)' },
    ];
    for (const m of modes) {
      const applied = await c.eval(`return window.__visitProbe.setMode(${JSON.stringify(m)});`);
      await sleep(3000);
      const w = await measureWindow(c, 10000);
      w.name = m.name;
      w.appliedMode = { idleTickMode: applied.idleTickMode, idleTickFps: applied.idleTickFps, tickerMaxFPS: applied.tickerMaxFPS };
      r.fps.push(w);
      log('t2 fps', m.name, JSON.stringify(w));
      save();
    }
    // H.264 once (TRTC primary codec) at the timer-60 source.
    await c.eval(`window.__visitProbe.unhook(); return await window.__visitProbe.fp('guest').startLoopback({ codec: 'h264', maxBitrate: 560000 });`);
    await c.eval(`window.__visitProbe.hook('guest'); return window.__visitProbe.setMode({ kind: 'timer', fps: 60 });`);
    await sleep(3000);
    const h = await measureWindow(c, 10000);
    h.name = 'timer60-h264';
    r.fps.push(h);
    log('t2 fps', JSON.stringify(h));
    // WebGL pack shader (T5 fallback) cost/pacing, fresh frame again
    await c.eval(`window.__visitProbe.unhook(); await window.__visitProbe.makeFrame('guest', 'guest', window.__visitProbe.GUEST_STYLE);
      const fp = window.__visitProbe.fp('guest'); fp.configure({ crop: [320, 448], packer: 'webgl', verifyEvery: 0 }); return await fp.startLoopback({ source: 'packgl', maxBitrate: 560000 });`);
    await c.eval(`window.__visitProbe.hook('guest'); return window.__visitProbe.setMode({ kind: 'timer', fps: 60 });`);
    await sleep(3000);
    const g = await measureWindow(c, 10000);
    g.name = 'timer60-webgl-packer';
    r.fps.push(g);
    log('t2 fps', JSON.stringify(g));
  } finally {
    await c.eval(`window.__visitProbe.unhook(); window.__visitProbe.restoreFps(); window.__visitProbe.removeFrame('guest'); return true;`);
  }
  results.t2 = r;
  save();
}

async function phaseT5(c) {
  const r = {};
  await c.eval(`await window.__visitProbe.makeFrame('guest', 'guest', window.__visitProbe.GUEST_STYLE);
    window.__visitProbe.fp('guest').configure({ crop: [320, 448], packer: '2d', verifyEvery: 0 }); return true;`);
  try {
    r.synthetic2dSource = await c.eval(`return window.__visitProbe.fp('guest').t5Synthetic('2d');`);
    r.syntheticWebglSource = await c.eval(`return window.__visitProbe.fp('guest').t5Synthetic('gl');`);
    // Real #live2d-canvas inside postrender (premultiplied WebGL back buffer, cross-realm drawImage).
    r.crop = await c.eval(`return window.__visitProbe.computeCrop(320, 448);`);
    await c.eval(`window.__visitProbe.hook('guest'); return true;`);
    r.live2dFrames = [];
    for (let i = 0; i < 5; i++) {
      r.live2dFrames.push(await c.eval(`return await window.__visitProbe.fp('guest').armT5(3000);`));
      await sleep(300);
    }
    // full-body crop geometry (256x560) once
    await c.eval(`window.__visitProbe.fp('guest').configure({ crop: [256, 560] }); window.__visitProbe.computeCrop(256, 560); return true;`);
    r.live2dFullBody = await c.eval(`return await window.__visitProbe.fp('guest').armT5(3000);`);
  } finally {
    await c.eval(`window.__visitProbe.unhook(); window.__visitProbe.removeFrame('guest'); return true;`);
  }
  results.t5 = r;
  save();
  log('t5', JSON.stringify(r, null, 1));
}

async function shotTriple(geo, name, rectPhys, showFn, hideFn) {
  // bg / fg / bg2 so a changing desktop behind the Pet window is detectable; retry up to 3x.
  let last;
  for (let attempt = 0; attempt < 3; attempt++) {
    await hideFn(); await sleep(400);
    const bg = path.join(outDir, `${name}-bg.png`); os('shot', bg, ...rectPhys);
    await showFn(); await sleep(700);
    const fg = path.join(outDir, `${name}-fg.png`); os('shot', fg, ...rectPhys);
    await hideFn(); await sleep(400);
    const bg2 = path.join(outDir, `${name}-bg2.png`); os('shot', bg2, ...rectPhys);
    last = { bg, fg, bg2, cmp: os('compare', bg, fg, bg2, rectPhys[2], rectPhys[3]) };
    if (last.cmp.bgStable_pixelsOver8 < last.cmp.pixels * 0.002) break;
    log(name, 'background changed between shots, retrying');
  }
  return last;
}

import { spawn } from 'node:child_process';
async function withBackdrop(rp, fn) {
  const m = 60;
  const p = spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(HERE, 'backdrop.ps1'), rp[0] - m, rp[1] - m, rp[2] + 2 * m, rp[3] + 2 * m].map(String), { stdio: 'ignore' });
  await sleep(4000);
  try { return await fn(); } finally { try { execFileSync('taskkill', ['/PID', String(p.pid), '/T', '/F'], { stdio: 'ignore' }); } catch (_) {} }
}

async function phaseT3(c) {
  const r = {};
  const geo = await petGeometry(c);
  r.geometry = { pet: geo.pet, scale: geo.scale, env: geo.env };
  const rect = await c.eval(`return window.__visitProbe.sideRect(320 / 448);`);
  r.rectCss = rect;
  const rp = physRect(geo, rect);
  r.rectPhys = rp;
  const rectJs = JSON.stringify(rect);
  await withBackdrop(rp, async () => {
  // 1) visual transparency of an empty child document in the host overlay style
  const show = () => c.eval(`await window.__visitProbe.makeFrame('host', 'empty', window.__visitProbe.hostStyle(${rectJs})); return true;`);
  const hide = () => c.eval(`window.__visitProbe.removeFrame('host'); return true;`);
  r.emptyFrameShots = await shotTriple(geo, 't3-empty', rp, show, hide);
  // same with the transport document (hidden video + transparent canvas present but unused)
  const show2 = () => c.eval(`await window.__visitProbe.makeFrame('host', 'host', window.__visitProbe.hostStyle(${rectJs})); window.__visitProbe.fp('host').startDisplay('pack'); window.__visitProbe.fp('host').stopDisplay(); return true;`);
  r.idleTransportShots = await shotTriple(geo, 't3-idle-canvas', rp, show2, hide);

  // 2) hit-testing: elementFromPoint + real click-through state (WS_EX_TRANSPARENT) at the frame centre
  const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
  const [px, py] = geo.toPhys(cx, cy);
  // positive control point: centre of the on-screen part of the model (the model may hang off-screen)
  const b = geo.env.modelBounds;
  // face: horizontal centre of the model, ~17% down from its top (an opaque pixel, unlike the bounds centre)
  const fx = Math.min(geo.env.inner[0] - 5, Math.max(5, b.left + b.width / 2));
  const fy = Math.min(geo.env.inner[1] - 5, Math.max(5, b.top + b.height * 0.17));
  const [mx, my] = geo.toPhys(fx, fy);
  r.modelPointCss = [fx, fy];
  const cursor0 = os('cursor');
  // #live2d-container is position:fixed;z-index:10 full-screen, so the designed z-index:9 sits *under* it.
  // z:11 variants put the frame above the canvas, where the two safeties (pointer-events / class) matter.
  const variants = [
    { name: 'no-iframe', make: null },
    { name: 'z9 pe-none+class (design as written)', make: { z: 9, pointerEvents: true, noClass: false } },
    { name: 'z9 neither', make: { z: 9, pointerEvents: false, noClass: true } },
    { name: 'z11 pe-none+class', make: { z: 11, pointerEvents: true, noClass: false } },
    { name: 'z11 pe-none only', make: { z: 11, pointerEvents: true, noClass: true } },
    { name: 'z11 class only (pe auto)', make: { z: 11, pointerEvents: false, noClass: false } },
    { name: 'z11 neither (negative control)', make: { z: 11, pointerEvents: false, noClass: true } },
  ];
  r.hit = [];
  try {
    r.modelPositiveControl = [];
    for (const v of variants) {
      await hide();
      if (v.make) await c.eval(`await window.__visitProbe.makeFrame('host', 'empty', window.__visitProbe.hostStyle(${rectJs}, ${JSON.stringify(v.make)})); return true;`);
      const samples = [];
      for (let k = 0; k < 3; k++) {
        // come from the model each time so a stale "not click-through" state has to be undone
        const onModel = os('hit', mx, my, 1.0);
        r.modelPositiveControl.push({ clickThrough: onModel.clickThrough, wsExTransparent: onModel.wsExTransparent });
        const at = os('hit', px, py, 1.2);
        samples.push({ clickThrough: at.clickThrough, wsExTransparent: at.wsExTransparent });
      }
      const el = await c.eval(`return window.__visitProbe.elementAt(${cx}, ${cy});`);
      r.hit.push({ variant: v.name, elementFromPoint: el, clickThroughSamples: samples });
      log('t3 hit', v.name, JSON.stringify(el), JSON.stringify(samples));
    }
  } finally {
    await hide();
    os('setcursor', ...cursor0);
  }
  });
  results.t3 = results.t3 || {};
  results.t3[LABEL] = r;
  save();
  log('t3', JSON.stringify({ empty: r.emptyFrameShots.cmp, idle: r.idleTransportShots.cmp }, null, 1));
}

async function phaseT4(c) {
  const r = {};
  const geo = await petGeometry(c);
  const rect = await c.eval(`return window.__visitProbe.sideRect(320 / 448);`);
  const rp = physRect(geo, rect);
  r.rectCss = rect; r.rectPhys = rp;
  const rectJs = JSON.stringify(rect);
  const hide = () => c.eval(`window.__visitProbe.removeFrame('host'); return true;`);
  await withBackdrop(rp, async () => {
  try {
    for (const kind of ['2d', 'gl']) {
      // direct unpack from the packed canvas (no codec): isolates iframe + transparent WebGL compositing
      const show = () => c.eval(`await window.__visitProbe.makeFrame('host', 'host', window.__visitProbe.hostStyle(${rectJs}));
        const fp = window.__visitProbe.fp('host'); fp.configure({ crop: [320, 448], packer: '2d' }); fp.startDisplay('pack'); fp.startPatternFeed(30, '${kind}'); return true;`);
      const shots = await shotTriple(geo, `t4-pattern-${kind}-direct`, rp, show, hide);
      // pack PNG for the expected composite
      await show(); await sleep(300);
      const dataUrl = await c.eval(`return window.__visitProbe.fp('host').packDataURL();`);
      const packPng = path.join(outDir, `t4-pack-${kind}.png`);
      fs.writeFileSync(packPng, Buffer.from(dataUrl.split(',')[1], 'base64'));
      await hide();
      r[`direct_${kind}`] = { shots, composite: os('composite', shots.bg, shots.fg, packPng, rp[2], rp[3]) };
      log('t4 direct', kind, JSON.stringify(r[`direct_${kind}`].composite));
    }
    // through the codec: pack -> captureStream -> loopback RTCPeerConnection -> hidden <video> -> rVFC -> WebGL unpack
    const showV = () => c.eval(`await window.__visitProbe.makeFrame('host', 'host', window.__visitProbe.hostStyle(${rectJs}));
      const fp = window.__visitProbe.fp('host'); fp.configure({ crop: [320, 448], packer: '2d' }); await fp.startLoopback({ maxBitrate: 560000 });
      fp.startDisplay('video'); fp.startPatternFeed(30, '2d'); await new Promise((r) => setTimeout(r, 2500)); return true;`);
    const shotsV = await shotTriple(geo, 't4-pattern-2d-video', rp, showV, hide);
    await showV();
    const stats = await c.eval(`return await window.__visitProbe.fp('host').rtcStats();`);
    await hide();
    r.video_2d = { shots: shotsV, composite: os('composite', shotsV.bg, shotsV.fg, path.join(outDir, 't4-pack-2d.png'), rp[2], rp[3]), rtc: stats };
    log('t4 video', JSON.stringify(r.video_2d.composite), JSON.stringify(stats && stats.stat));
    // Live model through the full chain, for a visual record only
    const showL = () => c.eval(`await window.__visitProbe.makeFrame('host', 'host', window.__visitProbe.hostStyle(${rectJs}));
      const fp = window.__visitProbe.fp('host'); fp.configure({ crop: [320, 448], packer: '2d' }); await fp.startLoopback({ maxBitrate: 560000 });
      fp.startDisplay('video'); window.__visitProbe.computeCrop(320, 448); window.__visitProbe.hook('host'); await new Promise((r) => setTimeout(r, 2500)); return true;`);
    const hideL = async () => { await c.eval(`window.__visitProbe.unhook(); return true;`); await hide(); };
    r.live_video = await shotTriple(geo, 't4-live2d-video', rp, showL, hideL);
  } finally {
    await c.eval(`window.__visitProbe.unhook(); window.__visitProbe.removeFrame('host'); return true;`);
  }
  });
  results.t4 = results.t4 || {};
  results.t4[LABEL] = r;
  save();
}

// ------------------------------------------------------------------ main
(async () => {
  let { cdp, targets } = await connectPet();
  await inject(cdp);
  for (const ph of PHASES) {
    log('== phase', ph, 'label', LABEL);
    if (ph === 'env') await phaseEnv(cdp);
    else if (ph === 't1') {
      await phaseT1(cdp, targets);
      await sleep(3000);
      ({ cdp, targets } = await connectPet());
      await sleep(4000);
      await inject(cdp);
    } else if (ph === 't2') await phaseT2(cdp);
    else if (ph === 't5') await phaseT5(cdp);
    else if (ph === 't3') await phaseT3(cdp);
    else if (ph === 't4') await phaseT4(cdp);
    else if (ph === 'trace') {
      // who moves the frame-rate mode while a forced timer-60 tick is held
      const out = await cdp.eval(`window.__visitProbe.setMode({ kind: 'timer', fps: 60 }); await new Promise((r) => setTimeout(r, 8000));
        const e = window.__visitProbe.env(); const o = { idle: e.idleTickMode, fps: e.idleTickFps, calls: window.__probeTrace };
        window.__visitProbe.restoreFps(); return o;`);
      results.modeTrace = out;
      log('trace', JSON.stringify(out, null, 1));
    }
    save();
  }
  cdp.close();
  log('done ->', path.join(outDir, 'results.json'));
  process.exit(0);
})().catch((e) => { console.error(e); save(); process.exit(1); });
