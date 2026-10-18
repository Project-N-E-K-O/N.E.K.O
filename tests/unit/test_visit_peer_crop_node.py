"""Parent bridge behaviour: visitor layer follows hb corrections, guest capture and starvation."""

from __future__ import annotations

from tests.visit_js_harness import BRIDGE_FILES, run_visit_js

_BRIDGE_JS = r"""
function makeBridge(opts) {
  opts = opts || {};
  const ctx = makeContext({ pathname: '/' });
  ctx.targetFrameRate = opts.targetFrameRate === undefined ? 60 : opts.targetFrameRate;
  if (opts.compat) ctx.__NEKO_DESKTOP_RUNTIME__ = { compatibilityMode: true };
  const view = ctx.document.createElement('canvas');
  view.width = 3840; view.height = 2160; view._rect = { left: 0, top: 0, width: 1920, height: 1080 };
  const handlers = {};
  const stage = { name: 'stage' };
  const renderer = {
    view, lastObjectRendered: stage, renderTexture: { current: null },
    on(e, f) { handlers[e] = f; }, off(e) { delete handlers[e]; },
  };
  const fpsCalls = [];
  // 三种渲染器都监听这个事件（live2d-core / vrm-manager / mmd-core）；这里只记下派发的帧率
  ctx.addEventListener('neko-frame-rate-changed', (e) => fpsCalls.push(e.detail.fps));
  ctx.live2dManager = {
    pixi_app: { renderer, stage },
    getModelScreenBounds: () => ({ left: 800, top: 100, right: 1200, bottom: 1000, width: 400, height: 900 }),
    getHeadDetectionGeometryInfo: () => ({ headRect: { left: 950, top: 150, width: 100, height: 120 } }),
  };
  ctx.avatarPortrait = { normalizeModelType: () => 'live2d', capture: () => Promise.resolve('img') };
  load(ctx, FILES);
  const bridge = ctx.NekoVisitParentBridge.createParentBridge({ window: ctx, document: ctx.document, getToken: opts.getToken || (() => Promise.resolve('tok')), assetVersion: '9' });
  const child = { posted: [], onFrames: [], token: null, suspended: [] };
  child.postMessage = (m) => child.posted.push(JSON.parse(JSON.stringify(m)));
  child.__nekoVisitFrameSink = {
    onFrame: (src, rect, ts) => { child.onFrames.push({ src: src === view, rect, ts }); return true; },
    suspend: (on) => child.suspended.push(on),
    setAuthToken: (t) => { child.token = t; },
  };
  return { ctx, bridge, child, renderer, handlers, stage, fpsCalls };
}
function change(h, action, extra) {
  h.bridge.onStateChange(Object.assign({ type: 'visit_state_change', action, side: h.side, visit_id: VISIT_ID }, extra || {}));
}
async function open(h, side, publishing) {
  h.side = side;
  change(h, 'pending');
  const frame = h.ctx.document.getElementById('visit-frame');
  frame.contentWindow = h.child;
  h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'ready', v: 1, side, visit_id: VISIT_ID } });
  await settle();
  if (side === 'guest' && publishing !== false) {
    h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'publish', on: true } });
  }
  h.frame = frame;
  return frame;
}
"""


def test_hb_corrected_peer_hidden_and_crop_reach_the_visitor_layer():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const frame = await open(h, 'host');
        change(h, 'started', { peer_crop: 'upper' });
        await advance(600);
        const upper = frame.style.width.replace('px', '') / frame.style.height.replace('px', '');
        // 只有 hb 纠正推导出的两条，没有先前的 state
        change(h, 'peer_hidden');
        const dimmed = frame.style.opacity;
        const badge = h.ctx.document.getElementById('visit-peer-away-badge');
        change(h, 'peer_crop', { peer_crop: 'full' });
        await advance(5000);
        const full = frame.style.width.replace('px', '') / frame.style.height.replace('px', '');
        change(h, 'peer_visible');
        return { upper, full, opacity: dimmed === '0.6' ? 'dim' : dimmed, badge: badge && badge.textContent,
                 badgeGone: !h.ctx.document.getElementById('visit-peer-away-badge'), afterVisible: frame.style.opacity,
                 style: frame.style.cssText, cls: frame.className, src: frame.src, token: h.child.token,
                 places: h.child.posted.filter((m) => m.t === 'place').length };
        """,
        files=BRIDGE_FILES,
    )
    assert abs(out["upper"] - 320 / 448) < 0.01
    assert abs(out["full"] - 256 / 560) < 0.01
    assert out["opacity"] == "dim"
    assert out["badge"] == "Stepped away"
    assert out["badgeGone"] and out["afterVisible"] == ""
    assert "pointer-events:none" in out["style"] and "z-index:4" in out["style"]
    assert out["cls"] == "transparent-overlay"
    assert out["src"] == "/visit/transport?v=9&side=host&visit_id=AbCdEfGhIjKlMnOpQrStUv"
    assert out["token"] == "tok"
    assert out["places"] >= 2


def test_guest_capture_guards_rate_and_starvation():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge({ targetFrameRate: 20 });
        await open(h, 'guest');
        change(h, 'started');
        change(h, 'departed');
        await advance(700);
        const post = h.handlers.postrender;
        // 守卫：临时舞台 / RenderTexture 渲染不抓
        h.renderer.lastObjectRendered = { name: 'temp' };
        for (let k = 0; k < 10; k++) { clock.now += 1000 / 60; post(); }
        h.renderer.lastObjectRendered = h.stage; h.renderer.renderTexture.current = {};
        for (let k = 0; k < 10; k++) { clock.now += 1000 / 60; post(); }
        h.renderer.renderTexture.current = null;
        const guarded = h.child.onFrames.length;
        // 60 Hz 渲染 2 s → 约 60 帧
        for (let k = 0; k < 120; k++) { clock.now += 1000 / 60; post(); }
        const captured = h.child.onFrames.length;
        const rect = h.child.onFrames[captured - 1].rect;
        await advance(1500);    // 没有渲染：帧饥饿
        const hiddenOn = h.child.posted.filter((m) => m.t === 'hidden');
        clock.now += 16; post(); clock.now += 17; post();
        const hiddenAll = h.child.posted.filter((m) => m.t === 'hidden').map((m) => m.on);
        const away = h.ctx.document.getElementById('visit-away-badge');
        const fpsDuring = h.fpsCalls.slice();
        const holdDuring = h.ctx.nekoTargetFrameRateHold && Object.assign({}, h.ctx.nekoTargetFrameRateHold);
        const fpsValueDuring = h.ctx.targetFrameRate;
        const crop = h.child.posted.filter((m) => m.t === 'crop').pop();
        change(h, 'ended');
        return { holdDuring, fpsValueDuring, holdAfter: h.ctx.nekoTargetFrameRateHold, fpsValueAfter: h.ctx.targetFrameRate, guarded, captured, rect, hiddenOn: hiddenOn.map((m) => [m.on, m.cause]), hiddenAll, fpsDuring, fpsAfter: h.fpsCalls,
                 capturing: h.ctx.live2dManager._visitCaptureActive, away: away && away.textContent, crop,
                 frameGone: !h.ctx.document.getElementById('visit-frame'), badgeGone: !h.ctx.document.getElementById('visit-away-badge'),
                 unhooked: !h.handlers.postrender };
        """,
        files=BRIDGE_FILES,
    )
    assert out["guarded"] == 0
    assert 58 <= out["captured"] <= 62
    assert out["rect"]["w"] > 0 and out["rect"]["h"] > 0 and out["rect"]["x"] >= 0
    assert out["hiddenOn"] == [[True, "frame_starvation"]]
    assert out["hiddenAll"] == [True, False]
    assert out["fpsDuring"] == [30]
    assert out["fpsAfter"] == [30, 20]
    assert out["holdDuring"] == {"userFps": 20, "heldFps": 30} and out["fpsValueDuring"] == 30
    assert out["holdAfter"] is None and out["fpsValueAfter"] == 20
    assert out["capturing"] is False
    assert out["away"] == "Out visiting"
    assert out["crop"]["fps"] == 30 and out["crop"]["mode"] == "upper"
    assert out["frameGone"] and out["badgeGone"] and out["unhooked"]


def test_compat_mode_captures_at_fifteen():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge({ compat: true, targetFrameRate: 10 });
        await open(h, 'guest');
        change(h, 'started');
        await advance(700);
        for (let k = 0; k < 120; k++) { clock.now += 1000 / 60; h.handlers.postrender(); }
        const crop = h.child.posted.filter((m) => m.t === 'crop').pop();
        return { captured: h.child.onFrames.length, fps: crop.fps, fpsCalls: h.fpsCalls };
        """,
        files=BRIDGE_FILES,
    )
    assert 28 <= out["captured"] <= 32
    assert out["fps"] == 15
    assert out["fpsCalls"] == [15]


def test_portrait_export_suspends_capture():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        await open(h, 'guest');
        change(h, 'started');
        await h.ctx.avatarPortrait.capture({});
        return h.child.suspended;
        """,
        files=BRIDGE_FILES,
    )
    assert out == [True, False]


def test_frame_messages_from_other_sources_are_ignored():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        h.side = 'host';
        change(h, 'pending');
        const frame = h.ctx.document.getElementById('visit-frame');
        frame.contentWindow = h.child;
        h.bridge._onFrameMessage({ source: {}, origin: h.ctx.location.origin, data: { t: 'ready', v: 1, side: 'host', visit_id: VISIT_ID } });
        h.bridge._onFrameMessage({ source: h.child, origin: 'http://evil.test', data: { t: 'ready', v: 1, side: 'host', visit_id: VISIT_ID } });
        await settle();
        const before = h.child.token;
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 4404 } });
        return { before, gone: !h.ctx.document.getElementById('visit-frame') };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"before": None, "gone": True}


def test_offscreen_half_of_the_model_is_not_framed():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        h.ctx.live2dManager.getModelScreenBounds = () => ({ left: 1500, top: 600, right: 2300, bottom: 1900, width: 800, height: 1300 });
        h.ctx.live2dManager.getHeadDetectionGeometryInfo = () => null;
        await open(h, 'guest');
        change(h, 'started');
        await advance(700);
        return h.bridge._state.rect;
        """,
        files=BRIDGE_FILES,
    )
    # Frame only the visible 420x480 viewport area, keeping its bottom from falling hundreds of pixels below the screen.
    assert out["y"] + out["height"] <= 1080 + 400
    assert abs(out["x"] + out["width"] / 2 - (1500 + 210)) < 1e-6


def test_user_frame_rate_change_during_a_visit_wins():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge({ targetFrameRate: 20 });
        await open(h, 'guest');
        change(h, 'started');
        await advance(700);
        // 用户在串门中把帧率改成 45（设置弹窗：先写 targetFrameRate，再派发事件）
        h.ctx.targetFrameRate = 45;
        h.ctx.dispatchEvent(new h.ctx.CustomEvent('neko-frame-rate-changed', { detail: { fps: 45 } }));
        const hold = h.ctx.nekoTargetFrameRateHold;
        change(h, 'ended');
        return { hold, after: h.ctx.targetFrameRate, calls: h.fpsCalls };
        """,
        files=BRIDGE_FILES,
    )
    assert out["hold"] is None
    assert out["after"] == 45
    assert out["calls"] == [30, 45]


def test_visitor_layer_fits_a_narrow_viewport():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        h.ctx.innerWidth = 360; h.ctx.innerHeight = 640;
        h.ctx.live2dManager.getModelScreenBounds = () => ({ left: 40, top: 0, right: 320, bottom: 640, width: 280, height: 640 });
        const frame = await open(h, 'host');
        change(h, 'started', { peer_crop: 'upper' });
        await advance(600);
        return { left: parseFloat(frame.style.left), width: parseFloat(frame.style.width), height: parseFloat(frame.style.height) };
        """,
        files=BRIDGE_FILES,
    )
    assert out["left"] >= 0
    assert out["left"] + out["width"] <= 360 + 1
    assert abs(out["width"] / out["height"] - 320 / 448) < 0.01


def test_text_only_visit_does_not_capture_or_report_hidden():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge({ targetFrameRate: 20 });
        await open(h, 'guest', false);           // 传输页没在发布（视频不可用）
        change(h, 'started');
        await advance(3000);
        const hooked = !!h.handlers.postrender;
        return { hooked, fps: h.fpsCalls, hidden: h.child.posted.filter((m) => m.t === 'hidden').length,
                 capturing: h.ctx.live2dManager._visitCaptureActive };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"hooked": False, "fps": [], "hidden": 0, "capturing": False}


def test_minimized_model_stops_capture_and_reports_hidden():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const box = h.ctx.document.createElement('div');
        box.id = 'live2d-container';
        h.ctx.document.body.appendChild(box);
        await open(h, 'guest');
        change(h, 'started');
        await advance(700);
        for (let k = 0; k < 30; k++) { clock.now += 1000 / 60; h.handlers.postrender(); }
        const visible = h.child.onFrames.length;
        box.classList.add('minimized');
        await advance(700);
        const mark = h.child.onFrames.length;
        for (let k = 0; k < 120; k++) { clock.now += 1000 / 60; h.handlers.postrender(); }
        await advance(1500);
        const hidden = h.child.posted.filter((m) => m.t === 'hidden').map((m) => m.on);
        return { visible: visible > 0, minimizedFrames: h.child.onFrames.length - mark, hidden };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"visible": True, "minimizedFrames": 0, "hidden": [True]}


def test_mirrored_css_rect_maps_from_the_right_edge():
    out = run_visit_js(
        """
        const ctx = makeContext({ pathname: '/' });
        load(ctx, FILES);
        const g = ctx.NekoVisitCropGeometry;
        const m = { originX: 100, originY: 0, pixelRatioX: 2, pixelRatioY: 2, srcW: 800, srcH: 800, cssWidth: 400 };
        const rect = { x: 120, y: 10, width: 100, height: 50 };
        return { plain: g.cssRectToPixelRect(rect, m), mirrored: g.cssRectToPixelRect(rect, Object.assign({ mirrorX: true }, m)) };
        """,
        files=BRIDGE_FILES,
    )
    assert out["plain"] == {"x": 40, "y": 20, "w": 200, "h": 100}
    # Screen 120..220 (container 100..500) mirrors to source (500-220)*2 .. (500-120)*2.
    assert out["mirrored"] == {"x": 560, "y": 20, "w": 200, "h": 100, "flipX": True}


_PNG_BRIDGE_JS = r"""
function makePngBridge(opts) {
  const ctx = makeContext({ pathname: '/' });
  ctx.targetFrameRate = 60;
  const img = ctx.document.createElement('img');
  img.naturalWidth = 1000; img.naturalHeight = 2000;
  img._rect = Object.assign({ left: 800, top: 100, width: 400, height: 800 }, opts.rect || {});
  ctx.pngtuberManager = { image: img };
  ctx.getComputedStyle = (el) => ({ visibility: 'visible', display: 'block', opacity: '1',
    transform: el === img && opts.mirror ? 'matrix(-1, 0, 0, 1, 0, 0)' : 'none' });
  ctx.nekoFramePacing = { requestPacedFrame: (cb) => { const id = setTimeoutF(() => cb(clock.now), 16); return () => clearTimerF(id); } };
  ctx.avatarPortrait = { normalizeModelType: () => 'pngtuber', capture: () => Promise.resolve('img') };
  load(ctx, FILES);
  const bridge = ctx.NekoVisitParentBridge.createParentBridge({ window: ctx, document: ctx.document, getToken: () => Promise.resolve('tok'), assetVersion: '9' });
  const child = { posted: [], onFrames: [] };
  child.postMessage = (m) => child.posted.push(JSON.parse(JSON.stringify(m)));
  child.__nekoVisitFrameSink = { onFrame: (src, rect) => { child.onFrames.push(rect); return true; }, suspend() {}, setAuthToken() {} };
  return { ctx, bridge, child, img };
}
"""


def test_png_capture_follows_mirror_and_viewport():
    out = run_visit_js(
        _BRIDGE_JS + _PNG_BRIDGE_JS + """
        const runs = {};
        for (const [name, opts] of [['mirrored', { mirror: true }], ['offscreen', { rect: { left: 2500 } }], ['edge', { rect: { left: 1700 } }]]) {
          const h = makePngBridge(opts);
          await open(h, 'guest');
          change(h, 'started');
          await advance(1600);
          const last = h.child.onFrames[h.child.onFrames.length - 1] || null;
          runs[name] = { frames: h.child.onFrames.length, last, hidden: h.child.posted.filter((m) => m.t === 'hidden').map((m) => m.on) };
          change(h, 'ended');
        }
        return runs;
        """,
        files=BRIDGE_FILES,
    )
    assert out["mirrored"]["frames"] > 0 and out["mirrored"]["last"]["flipX"] is True
    assert out["offscreen"]["frames"] == 0 and out["offscreen"]["hidden"] == [True]
    # Half offscreen (viewport width 1920): use visible 1700..1920, centered near 1810 (1900 without clipping).
    edge = out["edge"]["last"]
    center_css = 1700 + (edge["x"] + edge["w"] / 2) / 2.5
    assert out["edge"]["frames"] > 0 and abs(center_css - 1810) < 15
    # The frame may extend offscreen, but the packer's visible source region ends at the viewport's right edge: (1920-1700)*2.5 = 550.
    assert edge["visible"] == {"x": 0, "y": 0, "w": 550, "h": 2000}


def test_token_lookup_is_retried_until_the_iframe_can_authenticate():
    out = run_visit_js(
        _BRIDGE_JS + """
        const answers = [() => new Promise(() => {}), () => Promise.resolve(''), () => Promise.reject(new Error('net')), () => Promise.resolve('tok')];
        let calls = 0;
        const h = makeBridge({ getToken: () => answers[Math.min(calls++, answers.length - 1)]() });
        await open(h, 'guest', false);
        const early = h.child.token;
        await advance(5100);      // 第一次卡住：5 s 超时
        await advance(1100);      // 空 token → 1 s 后重取
        await advance(2100);      // 拒绝 → 2 s 后重取
        await advance(4100);      // 第四次拿到
        const got = h.child.token;
        const n = calls;
        await advance(60000);
        change(h, 'ended');
        return { early, got, calls: n, later: calls };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"early": None, "got": "tok", "calls": 4, "later": 4}


def test_reload_recovery_waits_for_a_token_before_asking_the_backend():
    out = run_visit_js(
        _BRIDGE_JS + """
        const runs = {};
        for (const [name, seq] of [['late', ['', '', 'tok']], ['never', ['']]]) {
          let calls = 0;
          const h = makeBridge({ getToken: () => Promise.resolve(seq[Math.min(calls++, seq.length - 1)]) });
          const fetched = [];
          h.ctx.fetch = (url, opts) => { fetched.push(opts.headers['X-CSRF-Token'] || null); return Promise.resolve({ ok: true, json: () => Promise.resolve({ active: false }) }); };
          let result;
          h.bridge.recoverAfterReload('Neko').then((r) => { result = r; });
          for (let i = 0; i < 20; i++) await advance(1000);
          runs[name] = { fetched, result, lookups: calls };
        }
        return runs;
        """,
        files=BRIDGE_FILES,
    )
    assert out["late"] == {"fetched": ["tok"], "result": False, "lookups": 3}
    assert out["never"]["fetched"] == [] and out["never"]["result"] is False
    assert 3 <= out["never"]["lookups"] <= 5


def test_overlapping_portrait_exports_resume_capture_after_the_last_one():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const releases = [];
        h.ctx.avatarPortrait.capture = () => new Promise((r) => releases.push(r));
        await open(h, 'guest');
        change(h, 'started');
        const p1 = h.ctx.avatarPortrait.capture({});
        const p2 = h.ctx.avatarPortrait.capture({});
        releases[0]('a');
        await p1;
        const afterFirst = h.child.suspended.slice();
        releases[1]('b');
        await p2;
        const afterBoth = h.child.suspended.slice();
        change(h, 'ended');
        return { afterFirst, afterBoth };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"afterFirst": [True], "afterBoth": [True, False]}


def test_live2d_capture_flag_follows_a_replaced_manager():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        await open(h, 'guest');
        change(h, 'started');
        await advance(700);
        const first = h.ctx.live2dManager;
        const before = first._visitCaptureActive;
        const renderer2 = Object.assign({}, h.renderer, { on() {}, off() {} });
        const second = Object.assign({}, first, { pixi_app: { renderer: renderer2, stage: h.stage } });
        delete second._visitCaptureActive;
        h.ctx.live2dManager = second;
        await advance(1500);
        const after = { old: first._visitCaptureActive, current: second._visitCaptureActive };
        change(h, 'ended');
        return { before, after, ended: second._visitCaptureActive };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"before": True, "after": {"old": False, "current": True}, "ended": False}


def test_inactive_live2d_manager_is_not_flagged_or_hooked():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        let type = 'live2d';
        h.ctx.avatarPortrait.normalizeModelType = () => type;
        await open(h, 'guest');
        change(h, 'started');
        await advance(700);
        const live = { flag: h.ctx.live2dManager._visitCaptureActive, hooked: !!h.handlers.postrender };
        type = 'vrm';
        await advance(1500);
        const vrm = { flag: h.ctx.live2dManager._visitCaptureActive, hooked: !!h.handlers.postrender };
        change(h, 'ended');
        return { live, vrm };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"live": {"flag": True, "hooked": True}, "vrm": {"flag": False, "hooked": False}}


def test_reload_snapshot_is_ignored_once_live_state_moved_on():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        let respond;
        h.ctx.fetch = () => new Promise((r) => { respond = r; });
        let result;
        h.bridge.recoverAfterReload('Neko').then((r) => { result = r; });
        await settle();
        h.side = 'guest';
        change(h, 'ended');                       // 实时通道先到：已经结束
        respond({ ok: true, json: () => Promise.resolve({ active: true, visit_id: VISIT_ID, side: 'guest', phase: 'active' }) });
        await settle();
        return { result, frame: !!h.ctx.document.getElementById('visit-frame') };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"result": False, "frame": False}


def test_reload_recovery_never_fetches_page_config_itself():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        let reloads = 0;
        h.ctx.lanlan_config = { lanlan_name: '' };
        // 首屏页面配置没给出角色名：不自己重拉（首页启动闸都由 startPageConfigLoad 负责），直接放弃
        h.ctx.reloadPageConfig = () => { reloads += 1; return Promise.resolve(true); };
        const asked = [];
        h.ctx.fetch = (url) => { asked.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve({ active: false }) }); };
        let result = 'pending';
        h.bridge.recoverFromPageConfig().then((r) => { result = r; });
        for (let i = 0; i < 12; i++) await advance(1000);
        return { reloads, asked: asked.length, result };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"reloads": 0, "asked": 0, "result": False}


def test_auxiliary_state_events_do_not_void_the_reload_snapshot():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        let respond;
        h.ctx.fetch = () => new Promise((r) => { respond = r; });
        let result;
        h.bridge.recoverAfterReload('Neko').then((r) => { result = r; });
        await settle();
        h.side = 'guest';
        change(h, 'peer_hidden');
        change(h, 'peer_crop', { peer_crop: 'full' });
        respond({ ok: true, json: () => Promise.resolve({ active: true, visit_id: VISIT_ID, side: 'guest', phase: 'active' }) });
        await settle();
        const frame = !!h.ctx.document.getElementById('visit-frame');
        change(h, 'ended');
        return { result, frame };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"result": True, "frame": True}


def test_recovery_waits_for_page_config_within_the_overall_deadline():
    out = run_visit_js(
        _BRIDGE_JS + """
        const runs = {};
        const cases = [
          ['hang', () => new Promise(() => {})],
          ['fail', () => Promise.reject(new Error('x'))],
          ['ok', () => Promise.resolve(true)],
          ['late', () => new Promise((r) => setTimeoutF(() => r(true), 6000))],     // 启动闸放行得晚，仍在截止时间内
          ['tooLate', () => new Promise((r) => setTimeoutF(() => r(true), 10500))], // 过了总截止时间才落定
        ];
        for (const [label, makeReady] of cases) {
          const ready = makeReady();
          ready.catch(() => {});
          const h = makeBridge();
          const navStart = clock.now;
          h.ctx.performance = { now: () => clock.now - navStart };   // performance.now() 从导航开始计时
          let starts = 0;
          h.bridge.recoverFromPageConfig = () => { starts += 1; return Promise.resolve(false); };
          h.ctx.pageConfigReady = ready;
          h.ctx.NekoVisitParentBridge.startRecoveryWhenReady(h.bridge, h.ctx, (fn, ms) => setTimeoutF(fn, ms));
          await settle();
          const early = starts;
          await advance(12000);
          runs[label] = { early, total: starts };
        }
        return runs;
        """,
        files=BRIDGE_FILES,
    )
    # Wait only for the initial screen result; do not fetch independently. Abandon if unresolved or resolved after the deadline.
    assert out == {"hang": {"early": 0, "total": 0}, "fail": {"early": 1, "total": 1}, "ok": {"early": 1, "total": 1},
                   "late": {"early": 0, "total": 1}, "tooLate": {"early": 0, "total": 0}}


def test_visit_state_request_is_bounded_and_retried():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        let calls = 0;
        h.ctx.fetch = () => {
          calls += 1;
          if (calls === 1) return new Promise(() => {});                    // 第一次挂住
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ active: true, visit_id: VISIT_ID, side: 'guest', phase: 'active' }) });
        };
        let result = 'pending';
        h.bridge.recoverAfterReload('Neko').then((r) => { result = r; });
        for (let i = 0; i < 8; i++) await advance(1000);
        const frame = !!h.ctx.document.getElementById('visit-frame');
        h.side = 'guest';
        change(h, 'ended');
        // 有回应但不是 2xx：不重试
        const h2 = makeBridge();
        let calls2 = 0;
        h2.ctx.fetch = () => { calls2 += 1; return Promise.resolve({ ok: false, status: 404 }); };
        let result2 = 'pending';
        h2.bridge.recoverAfterReload('Neko').then((r) => { result2 = r; });
        for (let i = 0; i < 15; i++) await advance(1000);
        return { calls, result, frame, calls2, result2 };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"calls": 2, "result": True, "frame": True, "calls2": 1, "result2": False}


def test_iframe_that_never_becomes_ready_is_reloaded():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        h.side = 'guest';
        change(h, 'pending');
        const frame = h.ctx.document.getElementById('visit-frame');
        let loads = 0;
        let src = frame.src;
        Object.defineProperty(frame, 'src', { get: () => src, set: (v) => { loads += 1; src = v; } });
        await advance(8100);
        const first = loads;
        await advance(8100);
        await advance(8100);
        await advance(8100);
        const total = loads;
        change(h, 'ended');
        return { first, total, sameUrl: src.indexOf('/visit/transport?') === 0 };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"first": 1, "total": 2, "sameUrl": True}


def test_reload_recovery_shares_one_overall_deadline():
    out = run_visit_js(
        _BRIDGE_JS + """
        let calls = 0;
        const h = makeBridge({ getToken: () => { calls += 1; return new Promise(() => {}); } });   // token 每次都挂住
        const navStart = clock.now;
        h.ctx.performance = { now: () => clock.now - navStart };     // performance.now() 从导航开始计时
        h.ctx.lanlan_config = { lanlan_name: '' };
        // 首屏页面配置 3 s 后才给出角色名（启动闸放行得晚）：等页面配置也从同一个总预算里扣
        h.ctx.pageConfigReady = new Promise((r) => setTimeoutF(() => { h.ctx.lanlan_config.lanlan_name = 'Neko'; r(true); }, 3000));
        h.ctx.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ active: false }) });
        const start = clock.now;
        let doneAt = null;
        const orig = h.bridge.recoverFromPageConfig;
        h.bridge.recoverFromPageConfig = (o) => orig(o).then((r) => { doneAt = clock.now; return r; });
        h.ctx.NekoVisitParentBridge.startRecoveryWhenReady(h.bridge, h.ctx, (fn, ms) => setTimeoutF(fn, ms));
        for (let i = 0; i < 40 && doneAt === null; i++) await advance(1000);
        return { elapsed: doneAt === null ? null : doneAt - start, tokenTries: calls > 0 };
        """,
        files=BRIDGE_FILES,
    )
    # Separate per-step windows take about 22 s, beyond the page's 20 s grace; a shared deadline finishes within 10 s, leaving half for iframe reconstruction and room entry.
    assert out["tokenTries"] is True
    assert out["elapsed"] is not None and out["elapsed"] <= 10000


def test_closed_transport_is_not_rebuilt_and_keeps_away_until_ended():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const box = h.ctx.document.createElement('div');
        box.id = 'live2d-container';
        h.ctx.document.body.appendChild(box);
        await open(h, 'guest');
        change(h, 'started');
        change(h, 'departed');
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 4409 } });
        const afterClosed = { frame: !!h.ctx.document.getElementById('visit-frame'), away: box.classList.contains('visiting-away') };
        change(h, 'wrap_up');
        change(h, 'ending');
        const afterWrap = { frame: !!h.ctx.document.getElementById('visit-frame'), away: box.classList.contains('visiting-away') };
        change(h, 'ended');
        const afterEnded = { away: box.classList.contains('visiting-away') };
        change(h, 'pending');                        // 新场次照常建
        const next = !!h.ctx.document.getElementById('visit-frame');
        change(h, 'ended');
        await advance(6100);
        return { afterClosed, afterWrap, afterEnded, next };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"afterClosed": {"frame": False, "away": True}, "afterWrap": {"frame": False, "away": True},
                   "afterEnded": {"away": False}, "next": True}


def test_ended_lets_the_transport_leave_before_removing_it():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        await open(h, 'host');
        change(h, 'started');
        const frame = h.frame;
        h.child.posted.length = 0;
        change(h, 'ended');
        const toIframe = h.child.posted.map((m) => m.t === 'visit_state' ? m.phase : m.t);
        const right = { removed: frame.removed, hidden: frame.style.visibility, lookup: !!h.ctx.document.getElementById('visit-frame') };
        // 报 closed 还不移除（离房等超时后也会报 closed，退房可能还在路上）；报 cleanup_done 才移除
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 0 } });
        const onClosed = frame.removed;
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'cleanup_done' } });
        const onDone = frame.removed;
        // 传输页一直不报 cleanup_done：最多 15 s（离房 5 s + 退房 / 断开 8 s 再留余量）后照样移除
        const h2 = makeBridge();
        await open(h2, 'host');
        change(h2, 'started');
        const f2 = h2.frame;
        change(h2, 'ended');
        await advance(14900);
        const early = f2.removed;
        await advance(200);
        return { toIframe, right, onClosed, onDone, early, late: f2.removed };
        """,
        files=BRIDGE_FILES,
    )
    assert out["toIframe"] == ["ended"]
    assert out["right"] == {"removed": False, "hidden": "hidden", "lookup": False}
    assert out["onClosed"] is False and out["onDone"] is True
    assert out["early"] is False and out["late"] is True


def test_ready_iframe_is_not_reloaded_while_the_token_is_slow():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge({ getToken: () => new Promise(() => {}) });   // token 一直取不到
        h.side = 'guest';
        change(h, 'pending');
        const frame = h.ctx.document.getElementById('visit-frame');
        frame.contentWindow = h.child;
        let loads = 0;
        let src = frame.src;
        Object.defineProperty(frame, 'src', { get: () => src, set: (v) => { loads += 1; src = v; } });
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'ready', v: 1, side: 'guest', visit_id: VISIT_ID } });
        await advance(20000);
        change(h, 'ended');
        await advance(6100);
        return loads;
        """,
        files=BRIDGE_FILES,
    )
    assert out == 0


def test_closed_4404_clears_away_but_still_does_not_rebuild():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const box = h.ctx.document.createElement('div');
        box.id = 'live2d-container';
        h.ctx.document.body.appendChild(box);
        await open(h, 'guest');
        change(h, 'started');
        change(h, 'departed');
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 4404 } });
        const away = box.classList.contains('visiting-away');
        change(h, 'wrap_up');
        return { away, frame: !!h.ctx.document.getElementById('visit-frame') };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"away": False, "frame": False}


def test_late_event_of_another_visit_after_closed_is_ignored():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const box = h.ctx.document.createElement('div');
        box.id = 'live2d-container';
        h.ctx.document.body.appendChild(box);
        await open(h, 'guest');
        change(h, 'started');
        change(h, 'departed');
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 4409 } });
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'ended', side: 'guest', visit_id: 'OtherVisit000000000000' });
        const away = box.classList.contains('visiting-away');
        change(h, 'wrap_up');
        return { away, frame: !!h.ctx.document.getElementById('visit-frame'), closedKept: h.bridge._state.closedVisitId === VISIT_ID };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"away": True, "frame": False, "closedKept": True}


def test_logs_from_a_retiring_frame_are_kept():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const logs = [];
        h.ctx.console = { warn: (m) => logs.push(m), error: (m) => logs.push(m), info: (m) => logs.push(m), log: (m) => logs.push(m) };
        await open(h, 'host');
        change(h, 'started');
        change(h, 'ended');
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'log', level: 'warn', msg: 'exit failed', code: 'leave_failed' } });
        await advance(6100);
        return logs.some((m) => String(m).indexOf('exit failed') >= 0);
        """,
        files=BRIDGE_FILES,
    )
    assert out is True


def test_superseded_frame_is_kept_until_its_fallback_cleanup_ran():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        await open(h, 'guest');
        change(h, 'started');
        const frame = h.frame;
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 4409 } });
        const right = { removed: frame.removed, lookup: !!h.ctx.document.getElementById('visit-frame') };
        // 它再报一次 closed 也不提前移除（兜底退房还没跑）
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 4409 } });
        await advance(20000);
        const at20 = frame.removed;
        // 传输页报 cleanup_done（兜底退房 / 断开做完）就移除
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'cleanup_done' } });
        const onDone = frame.removed;
        // 一直不报 cleanup_done：最多 27 s 后照样移除
        const h3 = makeBridge();
        await open(h3, 'guest');
        change(h3, 'started');
        const f3 = h3.frame;
        h3.bridge._onFrameMessage({ source: h3.child, origin: h3.ctx.location.origin, data: { t: 'closed', code: 4409 } });
        await advance(26900);
        const before25 = f3.removed;
        await advance(200);
        // 已经离房的终态（4403）照旧立即移除
        const h2 = makeBridge();
        await open(h2, 'guest');
        change(h2, 'started');
        const f2 = h2.frame;
        // 其它终态（4404 / 重连超时等）同样等清理做完：报 cleanup_done 就移除，最多 12 s（4403 第一次会先重取 token 重建，另有测试）
        h2.bridge._onFrameMessage({ source: h2.child, origin: h2.ctx.location.origin, data: { t: 'closed', code: 4404 } });
        const other = { removed: f2.removed, lookup: !!h2.ctx.document.getElementById('visit-frame') };
        h2.bridge._onFrameMessage({ source: h2.child, origin: h2.ctx.location.origin, data: { t: 'cleanup_done' } });
        other.onDone = f2.removed;
        const h4 = makeBridge();
        await open(h4, 'guest');
        change(h4, 'started');
        const f4 = h4.frame;
        h4.bridge._onFrameMessage({ source: h4.child, origin: h4.ctx.location.origin, data: { t: 'closed', code: 1006 } });
        await advance(11900);
        other.before12 = f4.removed;
        await advance(200);
        other.at12 = f4.removed;
        return { right, at20, onDone, before25, at25: f3.removed, other };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"right": {"removed": False, "lookup": False}, "at20": False, "onDone": True,
                   "before25": False, "at25": True,
                   "other": {"removed": False, "lookup": False, "onDone": True, "before12": False, "at12": True}}

def test_stale_visit_crop_does_not_touch_the_current_visit():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        await open(h, 'host');
        change(h, 'started', { peer_crop: 'upper' });
        const gen = h.bridge._state.liveGen;
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'peer_crop', side: 'host', visit_id: 'OtherVisit000000000000', peer_crop: 'full' });
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'ended', side: 'host', visit_id: 'OtherVisit000000000000' });
        const crop = h.bridge._state.peerCrop;
        const frame = !!h.ctx.document.getElementById('visit-frame');
        const sameGen = h.bridge._state.liveGen === gen;
        change(h, 'ended');
        await advance(6100);
        return { crop, frame, sameGen };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"crop": "upper", "frame": True, "sameGen": True}


def test_late_lifecycle_event_of_an_ended_visit_does_not_rebuild():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        await open(h, 'guest');
        change(h, 'started');
        change(h, 'ended');
        change(h, 'wrap_up');                    // 已结束场次迟到的事件
        change(h, 'ending');
        const rebuilt = !!h.ctx.document.getElementById('visit-frame');
        // 同一页面里新场次的事件（即使没先收到 pending）照常补建
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'started', side: 'guest', visit_id: 'NewVisit00000000000000' });
        const fresh = !!h.ctx.document.getElementById('visit-frame');
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'ended', side: 'guest', visit_id: 'NewVisit00000000000000' });
        await advance(6100);
        return { rebuilt, fresh };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"rebuilt": False, "fresh": True}


def test_reload_snapshot_does_not_overwrite_newer_live_auxiliary_state():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        let respond;
        h.ctx.fetch = () => new Promise((r) => { respond = r; });
        let result;
        h.bridge.recoverAfterReload('Neko').then((r) => { result = r; });
        await settle();
        // 查询期间实时通道推来更新的对端构图与显隐
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'peer_crop', side: 'host', visit_id: VISIT_ID, peer_crop: 'full' });
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'peer_visible', side: 'host', visit_id: VISIT_ID });
        respond({ ok: true, json: () => Promise.resolve({ active: true, visit_id: VISIT_ID, side: 'host', phase: 'active',
                                                           room: { peer_crop: 'upper' }, peer: { hidden: true } }) });
        await settle();
        const crop = h.bridge._state.peerCrop;
        const badge = h.bridge._state.peerHidden === true;
        h.side = 'host';
        change(h, 'ended');
        await advance(6100);
        return { result, crop, badge };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"result": True, "crop": "full", "badge": False}


def test_reload_snapshot_keeps_fields_that_were_not_updated_live():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        let respond;
        h.ctx.fetch = () => new Promise((r) => { respond = r; });
        h.bridge.recoverAfterReload('Neko');
        await settle();
        // 查询期间只来了 peer_visible：快照里的 peer_crop 照常采用
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'peer_visible', side: 'host', visit_id: VISIT_ID });
        respond({ ok: true, json: () => Promise.resolve({ active: true, visit_id: VISIT_ID, side: 'host', phase: 'active',
                                                           room: { peer_crop: 'full' }, peer: { hidden: true } }) });
        await settle();
        const out = { crop: h.bridge._state.peerCrop, hidden: h.bridge._state.peerHidden === true };
        h.side = 'host';
        change(h, 'ended');
        await advance(6100);
        return out;
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"crop": "full", "hidden": False}


def test_live_hidden_and_away_survive_frame_creation_during_recovery():
    out = run_visit_js(
        _BRIDGE_JS + """
        const runs = {};
        // host：查询期间来了 peer_hidden，快照说没隐藏 → 建完 iframe 仍是隐藏
        {
          const h = makeBridge();
          let respond;
          h.ctx.fetch = () => new Promise((r) => { respond = r; });
          h.bridge.recoverAfterReload('Neko');
          await settle();
          h.bridge.onStateChange({ type: 'visit_state_change', action: 'peer_hidden', side: 'host', visit_id: VISIT_ID });
          respond({ ok: true, json: () => Promise.resolve({ active: true, visit_id: VISIT_ID, side: 'host', phase: 'active', room: {}, peer: { hidden: false } }) });
          await settle();
          runs.host = { hidden: h.bridge._state.peerHidden === true, dim: h.ctx.document.getElementById('visit-frame').style.opacity };
          h.side = 'host'; change(h, 'ended'); await advance(6100);
        }
        // guest：查询期间来了 departed，快照阶段不是 active → 建完 iframe 仍打着离家样式
        {
          const h = makeBridge();
          const box = h.ctx.document.createElement('div');
          box.id = 'live2d-container';
          h.ctx.document.body.appendChild(box);
          let respond;
          h.ctx.fetch = () => new Promise((r) => { respond = r; });
          h.bridge.recoverAfterReload('Neko');
          await settle();
          h.bridge.onStateChange({ type: 'visit_state_change', action: 'departed', side: 'guest', visit_id: VISIT_ID });
          respond({ ok: true, json: () => Promise.resolve({ active: true, visit_id: VISIT_ID, side: 'guest', phase: 'joining' }) });
          await settle();
          runs.guest = { away: box.classList.contains('visiting-away') };
          h.side = 'guest'; change(h, 'ended'); await advance(6100);
        }
        return runs;
        """,
        files=BRIDGE_FILES,
    )
    assert out["host"]["hidden"] is True and out["host"]["dim"] not in ("", None)
    assert out["guest"] == {"away": True}


def test_late_auxiliary_events_of_an_ended_visit_are_ignored():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const box = h.ctx.document.createElement('div');
        box.id = 'live2d-container';
        h.ctx.document.body.appendChild(box);
        await open(h, 'guest');
        change(h, 'started');
        change(h, 'ended');
        const cropBefore = h.bridge._state.peerCrop;
        // 已结束场次迟到的出门 / 构图
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'departed', side: 'guest', visit_id: VISIT_ID });
        h.bridge.onStateChange({ type: 'visit_state_change', action: 'peer_crop', side: 'guest', visit_id: VISIT_ID,
                                 peer_crop: cropBefore === 'full' ? 'upper' : 'full' });
        const out = { away: box.classList.contains('visiting-away'), cropKept: h.bridge._state.peerCrop === cropBefore };
        await advance(15100);
        return out;
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"away": False, "cropKept": True}


def test_lowering_fps_during_capture_reapplies_the_hold():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        h.ctx.targetFrameRate = 60;                     // 串门开始时用户目标不低于取帧帧率：不用拉高
        await open(h, 'guest');
        change(h, 'started');
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'publish', on: true } });
        await settle();
        const before = { fps: h.ctx.targetFrameRate, hold: h.ctx.nekoTargetFrameRateHold || null };
        // 串门中途用户把帧率调到 15：照样拉到取帧帧率，用户的值记进 hold（保存设置时存它）
        h.ctx.targetFrameRate = 15;
        h.ctx.dispatchEvent(new h.ctx.CustomEvent('neko-frame-rate-changed', { detail: { fps: 15 } }));
        await settle();
        const after = { fps: h.ctx.targetFrameRate, hold: h.ctx.nekoTargetFrameRateHold };
        change(h, 'ended');
        await advance(15100);
        return { before, after, restored: h.ctx.targetFrameRate };
        """,
        files=BRIDGE_FILES,
    )
    assert out["before"] == {"fps": 60, "hold": None}
    assert out["after"]["fps"] >= 15 and out["after"]["hold"] == {"userFps": 15, "heldFps": out["after"]["fps"]}
    assert out["after"]["fps"] > 15 and out["restored"] == 15


def test_recovery_deadline_counts_from_navigation_start():
    out = run_visit_js(
        _BRIDGE_JS + """
        const runs = {};
        // [导航开始到本脚本执行已过的毫秒, 首屏页面配置落定的时刻]
        const cases = [['slow6s_ready5s', 6000, 5000], ['slow6s_ready3s', 6000, 3000], ['slow20s_ready2s', 20000, 2000], ['slow20s_ready4s', 20000, 4000]];
        for (const [label, elapsed, readyAt] of cases) {
          const h = makeBridge();
          const navStart = clock.now - elapsed;
          h.ctx.performance = { now: () => clock.now - navStart };   // performance.now() 从导航开始计时
          let starts = 0;
          h.bridge.recoverFromPageConfig = () => { starts += 1; return Promise.resolve(false); };
          h.ctx.pageConfigReady = new Promise((r) => setTimeoutF(() => r(true), readyAt));
          h.ctx.NekoVisitParentBridge.startRecoveryWhenReady(h.bridge, h.ctx, (fn, ms) => setTimeoutF(fn, ms));
          await advance(12000);
          runs[label] = starts;
        }
        return runs;
        """,
        files=BRIDGE_FILES,
    )
    # After 6 s, 4 s remain; after 20 s, use the 3 s lower bound.
    assert out == {"slow6s_ready5s": 0, "slow6s_ready3s": 1, "slow20s_ready2s": 1, "slow20s_ready4s": 0}


def test_rejected_cached_token_is_refreshed_once_before_giving_up():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        // 走真实的取 token 路径：缓存里是后端重启前的旧 token，重取才拿到新的
        h.ctx.nekoLocalMutationSecurity = {
          getMutationHeaders: () => Promise.resolve({ 'X-CSRF-Token': 'stale' }),
          refreshToken: () => Promise.resolve('fresh'),
        };
        h.bridge = h.ctx.NekoVisitParentBridge.createParentBridge({ window: h.ctx, document: h.ctx.document, assetVersion: '9' });
        await open(h, 'host');
        const first = h.child.token;
        change(h, 'started');
        const origin = h.ctx.location.origin;
        h.bridge._onFrameMessage({ source: h.child, origin, data: { t: 'closed', code: 4403 } });
        const rebuilt = h.ctx.document.getElementById('visit-frame');
        const child2 = { posted: [], token: null };
        child2.postMessage = (m) => child2.posted.push(m);
        child2.__nekoVisitFrameSink = { onFrame: () => true, suspend: () => {}, setAuthToken: (t) => { child2.token = t; } };
        if (rebuilt) rebuilt.contentWindow = child2;
        h.bridge._onFrameMessage({ source: child2, origin, data: { t: 'ready', v: 1, side: 'host', visit_id: VISIT_ID } });
        await settle();
        const second = child2.token;
        // 重取之后还被 4403 拒绝：当终态，不再重建
        h.bridge._onFrameMessage({ source: child2, origin, data: { t: 'closed', code: 4403 } });
        const again = !!h.ctx.document.getElementById('visit-frame');
        const closed = h.bridge._state.closedVisitId === VISIT_ID;
        change(h, 'ended');
        await advance(15100);
        return { first, rebuilt: !!rebuilt && rebuilt !== h.frame, second, again, closed };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"first": "stale", "rebuilt": True, "second": "fresh", "again": False, "closed": True}


def test_4403_retry_keeps_hidden_and_away_state():
    out = run_visit_js(
        _BRIDGE_JS + """
        const runs = {};
        // guest：出门后 4403 重建，离家样式照旧
        {
          const h = makeBridge();
          const box = h.ctx.document.createElement('div');
          box.id = 'live2d-container';
          h.ctx.document.body.appendChild(box);
          await open(h, 'guest');
          change(h, 'started');
          change(h, 'departed');
          h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 4403 } });
          runs.guest = { rebuilt: !!h.ctx.document.getElementById('visit-frame'), away: box.classList.contains('visiting-away') };
          change(h, 'ended'); await advance(15100);
        }
        // host：对端隐藏后 4403 重建，新 iframe 照样压暗
        {
          const h = makeBridge();
          await open(h, 'host');
          change(h, 'started');
          h.bridge.onStateChange({ type: 'visit_state_change', action: 'peer_hidden', side: 'host', visit_id: VISIT_ID });
          h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 4403 } });
          const f = h.ctx.document.getElementById('visit-frame');
          runs.host = { rebuilt: !!f, hidden: h.bridge._state.peerHidden === true, dim: f ? f.style.opacity : null };
          change(h, 'ended'); await advance(15100);
        }
        return runs;
        """,
        files=BRIDGE_FILES,
    )
    assert out["guest"] == {"rebuilt": True, "away": True}
    assert out["host"]["rebuilt"] is True and out["host"]["hidden"] is True and out["host"]["dim"] not in ("", None)


def test_normal_transport_closure_ends_the_visit_ui():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        const box = h.ctx.document.createElement('div');
        box.id = 'live2d-container';
        h.ctx.document.body.appendChild(box);
        await open(h, 'guest');
        change(h, 'started');
        change(h, 'departed');
        // 后端收线后传输 WS 以 1000 关闭；显示 WS 断着，ended 事件丢了
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 1000 } });
        const after = { away: box.classList.contains('visiting-away'), frame: !!h.ctx.document.getElementById('visit-frame'),
                        phase: h.bridge._state.phase, ended: h.bridge._state.endedVisitId === VISIT_ID };
        change(h, 'wrap_up');                                   // 迟到的事件不再补建
        after.rebuilt = !!h.ctx.document.getElementById('visit-frame');
        await advance(15100);
        return after;
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"away": False, "frame": False, "phase": None, "ended": True, "rebuilt": False}


def test_normal_closure_voids_a_pending_reload_snapshot():
    out = run_visit_js(
        _BRIDGE_JS + """
        const h = makeBridge();
        await open(h, 'guest');                                 // 实时通道先建好了 iframe
        change(h, 'started');
        let respond;
        h.ctx.fetch = () => new Promise((r) => { respond = r; });
        h.bridge.recoverAfterReload('Neko');                    // 重载恢复的快照请求还在路上
        await settle();
        h.bridge._onFrameMessage({ source: h.child, origin: h.ctx.location.origin, data: { t: 'closed', code: 1000 } });
        respond({ ok: true, json: () => Promise.resolve({ active: true, visit_id: VISIT_ID, side: 'guest', phase: 'active' }) });
        await settle();
        await settle();
        const rebuilt = !!h.ctx.document.getElementById('visit-frame');
        await advance(15100);
        return { rebuilt };
        """,
        files=BRIDGE_FILES,
    )
    assert out == {"rebuilt": False}
