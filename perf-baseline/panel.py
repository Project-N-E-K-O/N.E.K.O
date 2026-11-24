"""Plugin manager panel (/ui/) benchmarks with Playwright + Chrome DevTools Protocol.

open    first open -> interactive (fresh browser context), resources, long tasks; rep 0 also records a trace
switch  every sidebar page + plugin-detail tabs; rep 0 = first visit (traced), reps 1..N = steady state
fps     rAF frame log for home idle / page switching / plugin-list scroll; rep 0 traced
mem     (called from bench.py mem) JS heap / DOM nodes / listeners over phases + 3 heap snapshots

Browser mode opens http://127.0.0.1:48916/ui/ directly. Desktop-shell mode (cdp=...) attaches to N.E.K.O.-PC and
opens the panel through the same entry the hammer button uses: window.AgentHUD.openPowerWindow() on the Pet page.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

BASE = "http://127.0.0.1:48916/ui/"
TRACE_CATEGORIES = ",".join([
    "toplevel", "devtools.timeline", "disabled-by-default-devtools.timeline", "disabled-by-default-devtools.timeline.frame",
    "v8.execute", "blink.user_timing", "loading", "disabled-by-default-v8.cpu_profiler",
])
NAV = ".sidebar-nav .nav-item"

# In-page instrumentation: in-flight requests, DOM mutation clock, long tasks, LCP, rAF frame log.
INSTR = r"""
(() => {
  if (window.__perf) return;
  const S = (window.__perf = { inflight: 0, lastNet: 0, lastMut: 0, lastStruct: 0, longtasks: [], lcp: 0, frames: null, reqs: [] });
  const _fetch = window.fetch;
  window.fetch = function (...a) {
    S.inflight++; const t0 = performance.now(); const u = String((a[0] && a[0].url) || a[0]);
    return _fetch.apply(this, a).finally(() => { S.inflight--; S.lastNet = performance.now(); S.reqs.push({ u, t0, t1: S.lastNet }); });
  };
  const _open = XMLHttpRequest.prototype.open, _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u, ...r) { this.__u = m + ' ' + u; return _open.call(this, m, u, ...r); };
  XMLHttpRequest.prototype.send = function (...a) {
    S.inflight++; const t0 = performance.now(); const u = this.__u;
    this.addEventListener('loadend', () => { S.inflight--; S.lastNet = performance.now(); S.reqs.push({ u, t0, t1: S.lastNet }); });
    return _send.apply(this, a);
  };
  new MutationObserver((recs) => { const t = performance.now(); S.lastMut = t; for (const r of recs) { if (r.type !== 'attributes') { S.lastStruct = t; break; } } })
    .observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) S.longtasks.push({ s: e.startTime, d: e.duration }); }).observe({ type: 'longtask', buffered: true }); } catch (e) {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) S.lcp = e.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true }); } catch (e) {}
  S.startFrames = () => { S.frames = []; const f = (t) => { if (S.frames) { S.frames.push(t); requestAnimationFrame(f); } }; requestAnimationFrame(f); };
  S.stopFrames = () => { const fr = S.frames; S.frames = null; return fr; };
})();
"""

WAIT_QUIET = r"""
async ({ quiet, timeout }) => {
  const S = window.__perf; const t0 = performance.now();
  while (performance.now() - t0 < timeout) {
    await new Promise((r) => setTimeout(r, 50));
    const lt = S.longtasks.length ? S.longtasks[S.longtasks.length - 1] : null;
    const lastLt = lt ? lt.s + lt.d : 0;
    const last = Math.max(S.lastNet, S.lastMut, lastLt);
    if (S.inflight === 0 && performance.now() - last >= quiet) return { ready: last, lastNet: S.lastNet, lastMut: S.lastMut, lastStruct: S.lastStruct, lastLt, timedOut: false };
  }
  return { ready: performance.now(), timedOut: true, inflight: S.inflight };
}
"""


def wjson(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def wait_quiet(page, quiet=400, timeout=15000):
    return page.evaluate(WAIT_QUIET, {"quiet": quiet, "timeout": timeout})


class Trace:
    def __init__(self, page, cdp):
        self.page, self.cdp, self.events, self.done = page, cdp, [], False
        cdp.on("Tracing.dataCollected", lambda e: self.events.extend(e["value"]))
        cdp.on("Tracing.tracingComplete", lambda e: setattr(self, "done", True))
        cdp.send("Tracing.start", {"categories": TRACE_CATEGORIES, "transferMode": "ReportEvents"})

    def stop(self, path: Path) -> None:
        self.cdp.send("Tracing.end")
        end = time.time() + 120
        while not self.done and time.time() < end:
            self.page.wait_for_timeout(100)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"traceEvents": self.events}, fh)


class Net:
    """CDP Network collector: per-request type, bytes and server timing."""

    def __init__(self, cdp):
        self.pending, self.done = {}, []
        cdp.on("Network.requestWillBeSent", lambda e: self.pending.__setitem__(e["requestId"], {"url": e["request"]["url"], "method": e["request"]["method"], "type": e.get("type"), "ts": e["timestamp"]}))
        cdp.on("Network.responseReceived", self._resp)
        cdp.on("Network.loadingFinished", lambda e: self._fin(e, None))
        cdp.on("Network.loadingFailed", lambda e: self._fin(e, e.get("errorText")))

    def _resp(self, e):
        r = self.pending.get(e["requestId"])
        if r:
            resp = e["response"]
            r.update(status=resp.get("status"), timing=resp.get("timing"), fromCache=bool(resp.get("fromDiskCache") or resp.get("fromMemoryCache")))

    def _fin(self, e, failed):
        r = self.pending.pop(e["requestId"], None)
        if r:
            r.update(end=e["timestamp"], encoded=e.get("encodedDataLength", 0), failed=failed)
            self.done.append(r)

    def reset(self):
        self.done.clear()


class Session:
    """A browser (or desktop shell) plus helpers to obtain an instrumented panel page."""

    def __init__(self, pw, args, cdp_url):
        self.args, self.cdp_url = args, cdp_url
        if cdp_url:
            self.browser = pw.chromium.connect_over_cdp(cdp_url)
            self.context = self.browser.contexts[0]
            self.context.add_init_script(INSTR)
        else:
            kw = {"args": ["--enable-precise-memory-info", "--js-flags=--expose-gc"], "headless": not args.headed}
            if args.browser in ("chrome", "msedge"):
                kw["channel"] = args.browser
            self.browser = pw.chromium.launch(**kw)
            self.context = None

    def new_context(self):
        if self.cdp_url:
            return self.context
        ctx = self.browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
        ctx.add_init_script(INSTR)
        return ctx

    def open_panel(self, ctx):
        """Returns (page, cdp, trigger_wall_ms). In browser mode the page is blank until goto()."""
        if self.cdp_url:
            for p in list(ctx.pages):
                if "48916/ui" in p.url:
                    p.close()
            pet = next(p for p in ctx.pages if ":48911" in p.url)
            trigger = time.time() * 1000
            with ctx.expect_page(timeout=30000) as info:
                pet.evaluate("() => window.AgentHUD.openPowerWindow()")
            page = info.value
            page.wait_for_selector(NAV, timeout=30000)
        else:
            page = ctx.new_page()
            trigger = None
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Performance.enable")
        return page, cdp, trigger

    def close_panel(self, ctx, page):
        if self.cdp_url:
            page.close()
        else:
            ctx.close()

    def close(self):
        if self.cdp_url:
            for p in list(self.context.pages):
                if "48916/ui" in p.url:
                    p.close()
        self.browser.close()


def metrics(cdp) -> dict:
    return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}


# ----------------------------------------------------------------------------- open
def bench_open(sess: Session, out: Path, reps: int) -> None:
    results = []
    for i in range(reps):
        ctx = sess.new_context()
        page, cdp, trigger = sess.open_panel(ctx)
        net = Net(cdp)
        trace = Trace(page, cdp) if (i == 0 and not sess.cdp_url) else None
        t_nav = trigger if trigger is not None else time.time() * 1000
        if not sess.cdp_url:
            page.goto(BASE, wait_until="commit")
        page.wait_for_selector(NAV, timeout=30000)
        q = wait_quiet(page, 500)
        m = page.evaluate("""() => {
          const nav = performance.getEntriesByType('navigation')[0];
          const paints = Object.fromEntries(performance.getEntriesByType('paint').map((p) => [p.name, p.startTime]));
          const res = performance.getEntriesByType('resource'); const S = window.__perf;
          return { timeOrigin: performance.timeOrigin, dcl: nav.domContentLoadedEventEnd, load: nav.loadEventEnd, ttfb: nav.responseStart,
            fcp: paints['first-contentful-paint'] || 0, lcp: S.lcp, longtasks: S.longtasks, resCount: res.length + 1,
            resDecodedBytes: res.reduce((a, r) => a + (r.decodedBodySize || 0), 0), nodes: document.getElementsByTagName('*').length };
        }""")
        perf = metrics(cdp)
        if trace:
            trace.stop(out / "open-cold.trace.json")
        interactive = max(q["ready"], m["fcp"] or 0)
        by_type = {}
        for r in net.done:
            k = r.get("type") or "Other"
            by_type.setdefault(k, {"n": 0, "bytes": 0})
            by_type[k]["n"] += 1
            by_type[k]["bytes"] += r.get("encoded") or 0
        detail = [{"url": r["url"].split("48916", 1)[-1], "type": r.get("type"), "bytes": r.get("encoded"), "ms": round((r["end"] - r["ts"]) * 1000, 1)} for r in net.done] if i == 0 else None
        net.reset()
        t_w = time.time()
        page.reload(wait_until="commit")
        page.wait_for_selector(NAV, timeout=30000)
        qw = wait_quiet(page, 500)
        fcp_w = page.evaluate("() => { const p = performance.getEntriesByType('paint').find((x) => x.name === 'first-contentful-paint'); return p ? p.startTime : 0; }")
        results.append({
            "rep": i, "interactive_ms": interactive, "wall_interactive_ms": m["timeOrigin"] + interactive - t_nav, **{k: v for k, v in m.items() if k != "timeOrigin"},
            "scriptDuration_s": perf.get("ScriptDuration"), "layoutDuration_s": perf.get("LayoutDuration"), "recalcStyle_s": perf.get("RecalcStyleDuration"),
            "taskDuration_s": perf.get("TaskDuration"),
            "byType": by_type, "requestsCDP": sum(v["n"] for v in by_type.values()), "netDetail": detail,
            "warm": {"interactive_ms": max(qw["ready"], fcp_w), "wall_ms": (time.time() - t_w) * 1000},
        })
        print(f"open rep {i}: interactive={interactive:.0f}ms fcp={m['fcp']:.0f} longtasks={len(m['longtasks'])}", flush=True)
        sess.close_panel(ctx, page)
        time.sleep(0.5)
    wjson(out / "open.json", results)


# ----------------------------------------------------------------------------- switch
def measure_step(page, net, label, action):
    net.reset()
    page.evaluate("(l) => { const S = window.__perf; S.reqs = []; S.longtasks = []; S.mark = performance.now(); console.timeStamp('step:' + l); }", label)
    before = page.url
    action()
    t0 = page.evaluate("() => window.__perf.mark")
    q = wait_quiet(page, 400)
    d = page.evaluate("() => ({ reqs: window.__perf.reqs, longtasks: window.__perf.longtasks })")
    apis = [{"u": r["u"], "dur": r["t1"] - r["t0"], "start": r["t0"] - t0, "end": r["t1"] - t0} for r in d["reqs"]]
    last_api = max([a["end"] for a in apis] or [0])
    total = max(0.0, q["ready"] - t0)
    content = max(0.0, max(q.get("lastStruct") or 0, last_api + t0) - t0)
    xhr = [{"url": r["url"].split("48916", 1)[-1], "method": r["method"], "status": r.get("status"),
            "server_ms": (r["timing"]["receiveHeadersEnd"] - r["timing"]["sendEnd"]) if r.get("timing") else None}
           for r in net.done if r.get("type") in ("XHR", "Fetch")]
    return {"label": label, "url": page.url, "changed": page.url != before, "total_ms": total, "content_ready_ms": content,
            "lastApiEnd_ms": last_api, "render_after_api_ms": max(0.0, total - last_api),
            "longtasks": [{"s": l["s"] - t0, "d": l["d"]} for l in d["longtasks"]], "longtask_ms": sum(l["d"] for l in d["longtasks"]),
            "apis": apis, "net": xhr, "timedOut": q.get("timedOut", False)}


def nav_labels(page):
    return page.eval_on_selector_all(NAV, "els => els.map((e) => (e.innerText || '').trim())")


def nav_index(page, label):
    labels = nav_labels(page)
    return labels.index(label) if label in labels else 0


def switch_pass(page, net, rep):
    steps = []
    for i, label in enumerate(nav_labels(page)):
        steps.append(measure_step(page, net, "nav:" + label, lambda i=i: page.locator(NAV).nth(i).click()))
    steps.append(measure_step(page, net, "nav:PluginDetail(web_search)", lambda: page.evaluate(
        "() => { const u = new URL(location.href); u.pathname = '/ui/plugins/web_search'; history.pushState({}, '', u.toString()); dispatchEvent(new PopStateEvent('popstate')); }")))
    tabs = page.eval_on_selector_all(".el-tabs__item", "els => els.map((e) => (e.innerText || '').trim())")
    for i, label in enumerate(tabs):
        steps.append(measure_step(page, net, "tab:" + label, lambda i=i: page.locator(".el-tabs__item").nth(i).click()))
    steps.append(measure_step(page, net, "nav:Home(back)", lambda: page.locator(NAV).nth(0).click()))
    for s in steps:
        s["rep"] = rep
    return steps


def bench_switch(sess: Session, out: Path, reps: int) -> None:
    ctx = sess.new_context()
    page, cdp, _ = sess.open_panel(ctx)
    net = Net(cdp)
    if not sess.cdp_url:
        page.goto(BASE)
    page.wait_for_selector(NAV)
    wait_quiet(page, 800)
    trace = Trace(page, cdp)
    steps = switch_pass(page, net, 0)
    trace.stop(out / "switch-first.trace.json")
    for r in range(1, reps + 1):
        steps += switch_pass(page, net, r)
        print(f"switch rep {r} done", flush=True)
    wjson(out / "switch.json", steps)
    sess.close_panel(ctx, page)


# ----------------------------------------------------------------------------- fps
def fps_scenario(page, name, fn):
    page.evaluate("(n) => { console.timeStamp('fps-begin:' + n); window.__perf.longtasks = []; window.__perf.startFrames(); window.__perf.fpsT0 = performance.now(); }", name)
    extra = fn() or {}
    r = page.evaluate("(n) => { const fr = window.__perf.stopFrames(); console.timeStamp('fps-end:' + n); return { name: n, t0: window.__perf.fpsT0, frames: fr, longtasks: window.__perf.longtasks }; }", name)
    r.update(extra)
    return r


def bench_fps(sess: Session, out: Path, reps: int) -> None:
    ctx = sess.new_context()
    page, cdp, _ = sess.open_panel(ctx)
    if not sess.cdp_url:
        page.goto(BASE)
    page.wait_for_selector(NAV)
    wait_quiet(page, 800)
    n = page.locator(NAV).count()
    for i in range(n):  # load every lazy chunk first; FPS measures the steady state
        page.locator(NAV).nth(i).click()
        wait_quiet(page, 300)
    page.locator(NAV).nth(0).click()
    wait_quiet(page, 500)
    runs = []
    for r in range(reps):
        trace = Trace(page, cdp) if r == 0 else None
        runs.append({"rep": r, **fps_scenario(page, "home-idle", lambda: page.wait_for_timeout(5000))})

        def switching():
            for _ in range(2):
                for i in range(n):
                    page.locator(NAV).nth(i).click()
                    page.wait_for_timeout(350)
        runs.append({"rep": r, **fps_scenario(page, "page-switch", switching)})
        page.locator(NAV).nth(nav_index(page, "Plugins")).click()
        wait_quiet(page, 400)
        page.mouse.move(800, 500)

        def scrolling():
            reached = []
            for _ in range(3):
                for _ in range(20):
                    page.mouse.wheel(0, 120)
                    page.wait_for_timeout(30)
                reached.append(page.evaluate("() => { const e = document.querySelector('.plugin-list-shell__body'); return e ? e.scrollTop : -1; }"))
                for _ in range(20):
                    page.mouse.wheel(0, -120)
                    page.wait_for_timeout(30)
            return {"scrollTopReached": reached}
        runs.append({"rep": r, **fps_scenario(page, "list-scroll", scrolling)})
        page.locator(NAV).nth(0).click()
        wait_quiet(page, 400)
        if trace:
            trace.stop(out / "fps-rep0.trace.json")
        print(f"fps rep {r}", flush=True)
    wjson(out / "fps.json", runs)
    sess.close_panel(ctx, page)


# ----------------------------------------------------------------------------- mem (driven by bench.py)
def heap_snapshot(page, cdp, path: Path) -> None:
    chunks = []
    cdp.send("HeapProfiler.enable")
    cdp.send("HeapProfiler.collectGarbage")
    handler = lambda e: chunks.append(e["chunk"])  # noqa: E731
    cdp.on("HeapProfiler.addHeapSnapshotChunk", handler)
    cdp.send("HeapProfiler.takeHeapSnapshot", {"reportProgress": False})
    page.wait_for_timeout(500)
    cdp.remove_listener("HeapProfiler.addHeapSnapshotChunk", handler)
    path.write_text("".join(chunks), encoding="utf-8")


def run_mem(args, out: Path, event, install_cycle) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        sess = Session(pw, args, None)
        ctx = sess.new_context()
        page, cdp, _ = sess.open_panel(ctx)
        samples = []
        state = {"phase": "open"}

        def sample():
            m = metrics(cdp)
            samples.append({"t": time.time(), "phase": state["phase"], "jsHeapUsed": m.get("JSHeapUsedSize"), "nodes": m.get("Nodes"),
                            "listeners": m.get("JSEventListeners"), "documents": m.get("Documents")})

        def idle(seconds):
            end = time.time() + seconds
            while time.time() < end:
                page.wait_for_timeout(500)
                sample()

        def mark(p):
            state["phase"] = p
            event("panel:" + p)
            print("phase", p, flush=True)

        mark("open")
        page.goto(BASE)
        page.wait_for_selector(NAV)
        wait_quiet(page, 800)
        sample()
        mark("idle")
        idle(args.idle)
        heap_snapshot(page, cdp, out / "heap-after-idle.heapsnapshot")
        mark("switch")
        n = page.locator(NAV).count()
        for _ in range(args.switch_rounds):
            for i in range(n):
                page.locator(NAV).nth(i).click()
                page.wait_for_timeout(250)
                sample()
        page.locator(NAV).nth(0).click()
        wait_quiet(page, 500)
        mark("idle2")
        idle(15)
        heap_snapshot(page, cdp, out / "heap-after-switch.heapsnapshot")
        page.locator(NAV).nth(nav_index(page, "Plugins")).click()
        wait_quiet(page, 500)
        mark("install-cycles")
        for c in range(1, args.mem_cycles + 1):
            install_cycle(c)
            sample()
            idle(4)
        page.locator(NAV).nth(0).click()
        wait_quiet(page, 500)
        mark("idle3")
        idle(15)
        heap_snapshot(page, cdp, out / "heap-final.heapsnapshot")
        mark("end")
        wjson(out / "panel-mem.json", samples)
        sess.close_panel(ctx, page)
        sess.close()


# ----------------------------------------------------------------------------- entry used by bench.py panel
def run(args, out: Path, cdp=None) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        sess = Session(pw, args, cdp)
        try:
            if "open" in args.modes:
                bench_open(sess, out, args.reps)
            if "switch" in args.modes:
                bench_switch(sess, out, args.reps)
            if "fps" in args.modes:
                bench_fps(sess, out, args.reps)
        finally:
            sess.close()
