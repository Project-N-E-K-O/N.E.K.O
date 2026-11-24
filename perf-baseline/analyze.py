"""Aggregate a results directory into analysis/summary.json, analysis/results.md and charts.

Pure Python (+ matplotlib): includes a sourcemap decoder for mapping Chrome trace samples back to
frontend/plugin-manager/src files, and a minimal flame-graph SVG writer for py-spy raw stacks.
"""
from __future__ import annotations

import collections
import csv
import html
import json
import re
import statistics as st
from pathlib import Path


# ----------------------------------------------------------------------------- helpers
def q(xs):
    xs = sorted(x for x in xs if isinstance(x, (int, float)))
    if not xs:
        return None
    p90 = xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]
    return {"n": len(xs), "median": round(st.median(xs), 1), "p90": round(p90, 1), "min": round(xs[0], 1), "max": round(xs[-1], 1)}


def fmt(s, key="median"):
    return "—" if not s else f"{s['median']:.0f} / {s['p90']:.0f} [{s['min']:.0f}–{s['max']:.0f}]"


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------------------- boot
PHASES = [
    ("spawn_to_python_ms", "process launch (uv + interpreter)"),
    ("imports_and_app_ms", "imports + app build"),
    ("import_http_app_ms", "  of which import plugin.server.http_app"),
    ("lifespan_ms", "lifespan total"),
    ("startup.migrate_layout_reconcile", "  layout migration / reconcile"),
    ("startup.delivery_path(message_plane)", "  message plane (ZeroMQ)"),
    ("registry.refresh(discover+load manifests)", "  registry refresh (discover + manifests)"),
    ("registry.autostart_groups", "  autostart grouping"),
    ("autostart.start_plugins_batch", "  start plugins (batch)"),
    ("per_plugin_start_median", "  one plugin start (median)"),
    ("lifespan.StartupReconciler.run", "  StartupReconciler"),
    ("lifespan.ensure_default_slots", "  default model slots"),
    ("analysis_wall", "capability analysis (first start -> last end)"),
    ("analysis_end_ms", "capability analysis finished at"),
]


def boot_run(d: Path) -> dict | None:
    s = load(d / "summary.json")
    if not s or not (d / "phases.jsonl").exists():
        return None
    by = collections.defaultdict(list)
    for line in (d / "phases.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        by[r["name"]].append(r)

    def dur(n):
        return by[n][0]["t1"] - by[n][0]["t0"] if n in by else None

    out = {"health_ms": s.get("health_ms"), "all_running_ms": s.get("all_running_ms")}
    pm = by.get("python_main_entered")
    out["spawn_to_python_ms"] = pm[0]["t0"] if pm else None
    us = by.get("uvicorn.Server.startup(lifespan total+bind)")
    if us and pm:
        out["imports_and_app_ms"] = us[0]["t0"] - pm[0]["t0"]
        out["lifespan_ms"] = us[0]["t1"] - us[0]["t0"]
    out["import_http_app_ms"] = dur("import:plugin.server.http_app")
    for key, _ in PHASES:
        if key not in out and "." in key:
            out[key] = dur(key)
    starts = [r["t1"] - r["t0"] for r in by.get("plugin.start_under_lock", [])]
    out["per_plugin_start_median"] = st.median(starts) if starts else None
    an = by.get("analysis.capability_run(static analysis)", [])
    if an:
        out["analysis_wall"] = max(r["t1"] for r in an) - min(r["t0"] for r in an)
        out["analysis_end_ms"] = max(r["t1"] for r in an)
        out["analysis_per_plugin"] = {r.get("arg"): r["t1"] - r["t0"] for r in an}
    mem = s.get("tree_memory") or []
    out["tree_procs"] = len(mem)
    out["tree_rss_mb"] = sum(r["rss_mb"] for r in mem)
    out["tree_uss_mb"] = sum(r["uss_mb"] for r in mem)
    out["tree_pss_mb"] = sum(r.get("pss_mb") or 0 for r in mem) or None
    out["cold_note"] = s.get("cold")
    return out


def analyze_boot(root: Path) -> dict:
    groups = collections.defaultdict(list)
    for sub in sorted(root.glob("boot*")):
        tag = sub.name[len("boot"):]
        for d in sorted(sub.glob("t*-*-*")):
            m = re.match(r"t(\d)-(cold|warm)-\d+$", d.name)
            r = boot_run(d) if m else None
            if r:
                groups[f"tier{m.group(1)}-{m.group(2)}{tag}"].append(r)
    res = {}
    for k, runs in groups.items():
        keys = [x for x in runs[0] if x not in ("analysis_per_plugin", "cold_note")]
        res[k] = {x: q([r.get(x) for r in runs]) for x in keys}
        res[k]["cold_note"] = runs[0].get("cold_note")
    return res


# ----------------------------------------------------------------------------- py-spy
def parse_pyspy(raw: Path) -> list[dict]:
    procs = collections.defaultdict(lambda: {"total": 0, "incl": collections.Counter(), "self": collections.Counter(), "files": collections.Counter(), "cmd": ""})
    stacks = []
    for line in raw.read_text(encoding="utf-8", errors="replace").splitlines():
        stack, _, cnt = line.rpartition(" ")
        if not stack or not cnt.isdigit():
            continue
        cnt = int(cnt)
        heads = list(re.finditer(r'process (\d+):"', stack))
        pid = heads[-1].group(1) if heads else "?"
        rest = stack[heads[-1].end():] if heads else stack
        end = rest.find('";')
        cmd = rest[:end] if end >= 0 else rest
        frames = [f for f in (rest[end + 2:] if end >= 0 else "").split(";") if f and not f.startswith("thread ")]
        P = procs[pid]
        P["cmd"] = cmd[:160]
        P["total"] += cnt
        seen, seenf = set(), set()
        for f in frames:
            fn = clean_frame(re.sub(r":\d+\)$", ")", f))
            if fn not in seen:
                P["incl"][fn] += cnt
                seen.add(fn)
            fm = re.search(r"\(([^)]*?)(?::\d+)?\)$", f)
            if fm:
                fl = clean_path(fm.group(1))
                if fl not in seenf:
                    P["files"][fl] += cnt
                    seenf.add(fl)
        if frames:
            P["self"][clean_frame(re.sub(r":\d+\)$", ")", frames[-1]))] += cnt
        stacks.append((["process " + pid] + frames, cnt))
    res = [{"pid": pid, "cmd": clean_path(P["cmd"]), "samples": P["total"], "top_inclusive": P["incl"].most_common(45),
            "top_self": P["self"].most_common(25), "top_files_inclusive": P["files"].most_common(30)}
           for pid, P in sorted(procs.items(), key=lambda kv: -kv[1]["total"])]
    return res, stacks


def clean_path(p: str) -> str:
    p = p.replace("\\", "/")
    p = re.sub(r"^.*?/site-packages/", "site-packages/", p)
    p = re.sub(r"^.*?/(plugin|utils|app|main_logic|main_routers|memory|config|perf-baseline)/", r"\1/", p)
    return p


def clean_frame(f: str) -> str:
    return re.sub(r"\(([^)]*)\)$", lambda m: "(" + clean_path(m.group(1)) + ")", f)


def flamegraph_svg(stacks, title: str, path: Path, width: int = 1600) -> None:
    """Minimal icicle-style flame graph (root at the bottom), good enough to eyeball where time goes."""
    tree = {"n": "all", "v": 0, "c": {}}
    for frames, cnt in stacks:
        node = tree
        node["v"] += cnt
        for f in frames:
            node = node["c"].setdefault(f, {"n": f, "v": 0, "c": {}})
            node["v"] += cnt
    total = tree["v"] or 1
    rows = []

    def walk(node, x, depth):
        rows.append((x, depth, node))
        cx = x
        for ch in sorted(node["c"].values(), key=lambda c: c["n"]):
            walk(ch, cx, depth + 1)
            cx += ch["v"]

    walk(tree, 0, 0)
    depth_max = max(r[1] for r in rows) + 1
    h = 16
    height = depth_max * h + 60
    scale = (width - 20) / total
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="Verdana" font-size="11">',
             f'<rect width="100%" height="100%" fill="#fcfcfb"/><text x="{width / 2}" y="20" text-anchor="middle" font-size="15">{html.escape(title)}</text>']
    for x, depth, node in rows:
        w = node["v"] * scale
        if w < 0.5:
            continue
        y = height - 30 - (depth + 1) * h
        hue = (hash(node["n"].split("(")[0]) % 50) + 5
        name = html.escape(node["n"])
        label = name[: int(w / 7)] if w > 30 else ""
        parts.append(f'<g><title>{name} ({node["v"]} samples, {100 * node["v"] / total:.1f}%)</title><rect x="{10 + x * scale:.1f}" y="{y}" width="{max(w - 0.5, 0.5):.1f}" height="{h - 1}" fill="hsl({hue},80%,62%)"/>'
                     f'<text x="{12 + x * scale:.1f}" y="{y + 12}">{label}</text></g>')
    parts.append("</svg>")
    path.write_text("".join(parts), encoding="utf-8")


def analyze_pyspy(root: Path, adir: Path) -> dict:
    res = {}
    for raw in sorted(root.glob("pyspy/*/pyspy.raw")):
        name = raw.parent.name
        procs, stacks = parse_pyspy(raw)
        server = [p for p in procs if "perf_boot" in p["cmd"]]
        scanners = [p for p in procs if "_worker_main" in p["cmd"] or "import os,sys" in p["cmd"]]
        res[name] = {"rate_hz": 200, "processes": [(p["samples"], p["cmd"][:80]) for p in procs],
                     "server": max(server, key=lambda p: p["samples"]) if server else None,
                     "scanner_workers": len(scanners), "scanner_samples_total": sum(p["samples"] for p in scanners),
                     "scanner_example": scanners[0] if scanners else None}
        flamegraph_svg(stacks, f"plugin server start-up {name} (py-spy 200 Hz, CPU-active samples)", adir / f"flame-{name}.svg")
    return res


def analyze_importtime(root: Path) -> dict | None:
    p = root / "importtime" / "importtime.txt"
    if not p.exists():
        return None
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"import time:\s+(\d+) \|\s+(\d+) \|( *)(\S+)", line)
        if m:
            rows.append({"self_ms": int(m.group(1)) / 1000, "cum_ms": int(m.group(2)) / 1000, "depth": len(m.group(3)) // 2, "mod": m.group(4)})
    return {"modules": len(rows), "top_cumulative": sorted(rows, key=lambda x: -x["cum_ms"])[:30], "top_self": sorted(rows, key=lambda x: -x["self_ms"])[:20]}


# ----------------------------------------------------------------------------- browser results
def open_stats(d):
    out = {k: q([r.get(k) for r in d]) for k in ["interactive_ms", "wall_interactive_ms", "fcp", "dcl", "lcp", "requestsCDP", "resCount", "nodes"]}
    out["longtask_count"] = q([len(r["longtasks"]) for r in d])
    out["longtask_max_ms"] = q([max([x["d"] for x in r["longtasks"]] or [0]) for r in d])
    for k, src in [("script_ms", "scriptDuration_s"), ("layout_ms", "layoutDuration_s"), ("recalc_style_ms", "recalcStyle_s"), ("task_ms", "taskDuration_s")]:
        out[k] = q([(r.get(src) or 0) * 1000 for r in d])
    out["warm_interactive_ms"] = q([r["warm"]["interactive_ms"] for r in d])
    out["byType_rep0"] = d[0].get("byType")
    out["largest_rep0"] = sorted(d[0].get("netDetail") or [], key=lambda x: -(x.get("bytes") or 0))[:12]
    return out


def switch_stats(d):
    labels = list(dict.fromkeys(s["label"] for s in d))
    rows = []
    for L in labels:
        steady = [s for s in d if s["label"] == L and s["rep"] >= 1]
        first = [s for s in d if s["label"] == L and s["rep"] == 0]
        rows.append({"label": L, "first_visit_ms": round(first[0]["total_ms"], 1) if first else None, "total": q([s["total_ms"] for s in steady]),
                     "content_ready": q([s.get("content_ready_ms") for s in steady]), "api_wait": q([s["lastApiEnd_ms"] for s in steady]),
                     "render_after_api": q([s["render_after_api_ms"] for s in steady]), "longtask_ms": q([s["longtask_ms"] for s in steady]),
                     "n_api": round(st.mean([len(s["apis"]) for s in steady]), 1) if steady else None, "timeouts": sum(1 for s in d if s["label"] == L and s["timedOut"])})
    ep = collections.defaultdict(list)
    undefined = 0
    for s in d:
        for n in s["net"]:
            if "/undefined" in n["url"]:
                undefined += 1
            if s["rep"] >= 1 and n.get("server_ms") is not None:
                ep[n["method"] + " " + re.sub(r"\?.*$", "", n["url"])].append(n["server_ms"])
    return {"pages": rows, "endpoints": sorted([{"endpoint": k, **q(v)} for k, v in ep.items()], key=lambda x: -x["median"]), "undefined_requests": undefined}


def fps_stats(d):
    by = collections.defaultdict(list)
    for r in d:
        fr = sorted(set(r.get("frames") or []))
        iv = [b - a for a, b in zip(fr, fr[1:])]
        if len(iv) < 5:
            continue
        by[r["name"]].append({"fps": len(iv) * 1000 / (fr[-1] - fr[0]), "iv": iv, "lt": r["longtasks"], "scroll": r.get("scrollTopReached")})
    out = {}
    for name, runs in by.items():
        iv = [x for r in runs for x in r["iv"]]
        out[name] = {"reps": len(runs), "fps": q([r["fps"] for r in runs]), "frame_ms": q(iv), "frames": len(iv),
                     "pct_over_25ms": round(100 * sum(x > 25 for x in iv) / len(iv), 2), "pct_over_50ms": round(100 * sum(x > 50 for x in iv) / len(iv), 2),
                     "max_frame_ms": round(max(iv), 1), "longtasks_per_rep": q([len(r["lt"]) for r in runs]), "scrollTopReached": runs[0]["scroll"],
                     "hist": {b: sum(lo < x <= hi for x in iv) for b, lo, hi in [("<=17", 0, 17.5), ("17-25", 17.5, 25), ("25-33", 25, 33.5), ("33-50", 33.5, 50), ("50-100", 50, 100), (">100", 100, 1e9)]}}
    return out


# ----------------------------------------------------------------------------- sourcemaps + Chrome trace
_B64 = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}


def _vlq(seg):
    vals, shift, value = [], 0, 0
    for ch in seg:
        d = _B64[ch]
        value += (d & 31) << shift
        if d & 32:
            shift += 5
        else:
            vals.append(-(value >> 1) if value & 1 else value >> 1)
            shift = value = 0
    return vals


class SourceMap:
    def __init__(self, data):
        self.sources = data.get("sources", [])
        self.names = data.get("names", [])
        self.lines = []
        src = srcline = srccol = name = 0
        for line in data.get("mappings", "").split(";"):
            col, segs = 0, []
            if line:
                for seg in line.split(","):
                    v = _vlq(seg)
                    col += v[0]
                    if len(v) >= 4:
                        src += v[1]; srcline += v[2]; srccol += v[3]
                        nm = None
                        if len(v) >= 5:
                            name += v[4]
                            nm = name
                        segs.append((col, src, srcline, nm))
            self.lines.append(segs)

    def lookup(self, line0, col):
        if line0 >= len(self.lines):
            return None
        best = None
        for seg in self.lines[line0]:
            if seg[0] <= col:
                best = seg
            else:
                break
        if not best:
            return None
        return self.sources[best[1]], best[2] + 1, self.names[best[3]] if best[3] is not None else None


class Mapper:
    def __init__(self, dist_assets: Path):
        self.dist, self.cache = dist_assets, {}

    def norm(self, src):
        if not src:
            return "?"
        src = re.sub(r"^(\.\./)+", "", src)
        nm = re.search(r"node_modules/((?:@[^/]+/)?[^/]+)", src)
        if nm:
            return "npm:" + nm.group(1)
        return re.sub(r"^.*?/?src/", "src/", src)

    def frame(self, cf):
        url = cf.get("url") or ""
        m = re.search(r"/ui/assets/([^?#]+\.js)", url)
        if not url:
            return {"src": "(" + (cf.get("functionName") or "native") + ")", "fn": cf.get("functionName") or "(native)", "line": None, "js": False}
        if not m:
            return {"src": re.sub(r"^https?://[^/]+", "", url), "fn": cf.get("functionName") or "(anon)", "line": None, "js": True}
        name = m.group(1)
        if name not in self.cache:
            try:
                self.cache[name] = SourceMap(json.loads((self.dist / (name + ".map")).read_text(encoding="utf-8")))
            except (OSError, ValueError):
                self.cache[name] = None
        sm = self.cache[name]
        hit = sm.lookup(cf.get("lineNumber", 0), cf.get("columnNumber", 0)) if sm else None
        if not hit:
            return {"src": "assets/" + name, "fn": cf.get("functionName") or "(anon)", "line": None, "js": True}
        return {"src": self.norm(hit[0]), "fn": hit[2] or cf.get("functionName") or "(anon)", "line": hit[1], "js": True}


def analyze_trace(path: Path, mapper: Mapper, fps: list | None = None) -> dict:
    ev = json.loads(path.read_text(encoding="utf-8"))["traceEvents"]
    tname = {(e["pid"], e["tid"]): e["args"]["name"] for e in ev if e.get("ph") == "M" and e.get("name") == "thread_name"}
    load_ = collections.Counter()
    for e in ev:
        if e.get("ph") == "X" and e.get("name") in ("RunTask", "ThreadControllerImpl::RunTask") and tname.get((e["pid"], e["tid"])) == "CrRendererMain":
            load_[(e["pid"], e["tid"])] += e.get("dur", 0)
    if not load_:
        return {"error": "no renderer main thread"}
    MP, MT = load_.most_common(1)[0][0]
    main = sorted([e for e in ev if e.get("pid") == MP and e.get("tid") == MT and e.get("ph") == "X" and isinstance(e.get("dur"), (int, float))], key=lambda e: (e["ts"], -e["dur"]))
    stack = []
    for e in main:
        while stack and stack[-1]["ts"] + stack[-1]["dur"] <= e["ts"]:
            stack.pop()
        if stack:
            stack[-1]["_c"] = stack[-1].get("_c", 0) + e["dur"]
        stack.append(e)
    selfms = collections.Counter()
    for e in main:
        selfms[e["name"]] += max(0, e["dur"] - e.get("_c", 0))

    def group(rx):
        return round(sum(v for k, v in selfms.items() if re.search(rx, k)) / 1000, 1)

    groups = {"script": group(r"^(EvaluateScript|v8\.evaluateModule|FunctionCall|TimerFire|EventDispatch|FireAnimationFrame|RunMicrotasks|v8\.run|V8\.Execute)"),
              "compile": group(r"^(v8\.compile|CompileScript|v8\.produceCache|CompileCode|ParseScript)"),
              "style": group(r"(UpdateLayoutTree|recalcStyle|RecalculateStyles|ParseAuthorStyleSheet)"),
              "layout": group(r"^(Layout|UpdateLayout|LocalFrameView::performLayout)$|ForcedStyleAndLayout"),
              "paint": group(r"^(Paint|PrePaint|Layerize|UpdateLayer|CompositeLayers|Commit)"),
              "gc": group(r"GC")}
    # CPU samples of the main thread's profile
    ids = {e.get("id") for e in ev if e.get("pid") == MP and e.get("tid") == MT and e.get("name") == "Profile"}
    nodes, samples, t = {}, [], 0
    for e in ev:
        if e.get("pid") != MP or e.get("id") not in ids:
            continue
        if e.get("name") == "Profile":
            t = e["args"]["data"]["startTime"]
        elif e.get("name") == "ProfileChunk":
            d = e["args"]["data"]
            cp = d.get("cpuProfile") or {}
            for n in cp.get("nodes") or []:
                nodes[n["id"]] = n
            for sid, dt in zip(cp.get("samples") or [], d.get("timeDeltas") or []):
                t += dt
                samples.append((t, sid))
    cache = {}

    def info(nid):
        if nid in cache:
            return cache[nid]
        n = nodes.get(nid)
        leaf = mapper.frame(n["callFrame"]) if n else {"src": "?", "fn": "?", "js": False}
        app = owner = None
        a = n
        while a:
            f = mapper.frame(a["callFrame"])
            if owner is None and f["js"] and f["src"] != "?" and (a["callFrame"].get("url") or ""):
                owner = f
            if f["src"].startswith("src/"):
                app = f
                break
            a = nodes.get(a.get("parent"))
        cache[nid] = (leaf, app, owner)
        return cache[nid]

    dt = (samples[-1][0] - samples[0][0]) / len(samples) if len(samples) > 1 else 0

    def attribute(a, b):
        app, owner, leafsrc = collections.Counter(), collections.Counter(), collections.Counter()
        n = 0
        for ts, sid in samples:
            if ts < a or ts > b:
                continue
            node = nodes.get(sid)
            if not node or node["callFrame"].get("functionName") in ("(idle)", "(root)"):
                continue
            n += 1
            leaf, ap, ow = info(sid)
            leafsrc[leaf["src"]] += 1
            app[ap["src"] if ap else "(no app frame)"] += 1
            owner[f"{ow['fn']} @ {ow['src']}:{ow['line']}" if ow else f"(no JS: {node['callFrame'].get('functionName') or 'native'})"] += 1
        top = lambda c: [{"key": k, "ms": round(v * dt / 1000, 1)} for k, v in c.most_common(8)]  # noqa: E731
        return {"samples": n, "appComponent": top(app), "jsOwner": top(owner), "leafSource": top(leafsrc)}

    marks = sorted([(e["ts"], e["args"]["data"]["message"]) for e in ev if e.get("name") == "TimeStamp" and e.get("pid") == MP and e.get("args", {}).get("data", {}).get("message")], key=lambda x: x[0])

    def step_at(ts):
        cur = None
        for mts, m in marks:
            if mts <= ts:
                cur = m
            else:
                break
        return cur

    t0 = main[0]["ts"] if main else 0
    longtasks = []
    for e in main:
        if e["name"] in ("RunTask", "ThreadControllerImpl::RunTask") and e["dur"] >= 50000:
            inside = [x for x in main if x is not e and x["ts"] >= e["ts"] and x["ts"] + x["dur"] <= e["ts"] + e["dur"]]
            sb = collections.Counter()
            for x in inside:
                sb[x["name"]] += max(0, x["dur"] - x.get("_c", 0))
            longtasks.append({"t_ms": round((e["ts"] - t0) / 1000, 1), "dur_ms": round(e["dur"] / 1000, 1), "step": step_at(e["ts"]),
                              "events_self_ms": {k: round(v / 1000, 1) for k, v in sb.most_common(6)}, **attribute(e["ts"], e["ts"] + e["dur"])})
    out = {"trace": path.name, "groups_self_ms": groups, "top_events_self_ms": {k: round(v / 1000, 1) for k, v in selfms.most_common(15) if "RunTask" not in k},
           "longtasks": longtasks, "whole": attribute(samples[0][0], samples[-1][0]) if samples else None}
    if fps:
        out["fps_drops"] = []
        for r in [x for x in fps if x.get("rep") == 0]:
            mk = next((ts for ts, m in marks if m == "fps-begin:" + r["name"]), None)
            if mk is None:
                continue
            off = mk - r["t0"] * 1000
            fr = sorted(set(r["frames"]))
            drops = []
            for a, b in zip(fr, fr[1:]):
                if b - a > 25:
                    ta, tb = a * 1000 + off, b * 1000 + off
                    tasks = [round(x["dur"] / 1000, 1) for x in main if x["name"] in ("RunTask", "ThreadControllerImpl::RunTask") and x["ts"] < tb and x["ts"] + x["dur"] > ta and x["dur"] > 8000]
                    drops.append({"at_ms": round(a - r["t0"], 1), "frame_ms": round(b - a, 1), "tasks_ms": tasks, **attribute(ta, tb)})
            out["fps_drops"].append({"scenario": r["name"], "drops": drops})
    return out


# ----------------------------------------------------------------------------- memory
def analyze_mem(root: Path, adir: Path) -> dict | None:
    m = root / "mem"
    if not (m / "proc-mem.csv").exists():
        return None
    rows = list(csv.DictReader(open(m / "proc-mem.csv", encoding="utf-8")))
    events = [json.loads(line) for line in open(m / "events.jsonl", encoding="utf-8")]
    panel = load(m / "panel-mem.json") or []
    T0 = min(float(r["t"]) for r in rows)
    by_t = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        t = round(float(r["t"]) - T0, 1)
        if r["role"] == "server":
            by_t[t]["server_rss"] += float(r["rss_mb"]); by_t[t]["server_uss"] += float(r["uss_mb"])
        elif r["role"].startswith("plugin:"):
            by_t[t]["plug_uss"] += float(r["uss_mb"]); by_t[t]["plug_rss"] += float(r["rss_mb"]); by_t[t]["plug_n"] += 1
    ts = sorted(by_t)
    ev = sorted((round(e["t"] - T0, 1), e["name"]) for e in events)
    marks = [(t, n) for t, n in ev if n.startswith(("panel:", "server:"))]
    phases = []
    for (ta, na), (tb, nb) in zip(marks, marks[1:]):
        w = [by_t[t] for t in ts if ta <= t <= tb]
        if w:
            phases.append({"phase": f"{na} -> {nb}", "t0": ta, "t1": tb, **{k: {"start": round(w[0][k], 1), "end": round(w[-1][k], 1), "max": round(max(x[k] for x in w), 1)}
                                                                        for k in ("server_rss", "server_uss", "plug_uss", "plug_n")}})
    ptab = [{"t": round(s["t"] - T0, 1), "phase": s["phase"], "heap_mb": round((s["jsHeapUsed"] or 0) / 2**20, 2), "nodes": s["nodes"], "listeners": s["listeners"], "documents": s["documents"]} for s in panel]
    pph = collections.defaultdict(list)
    for s in ptab:
        pph[s["phase"]].append(s)
    panel_phases = {k: {"heap_start": v[0]["heap_mb"], "heap_end": v[-1]["heap_mb"], "heap_max": max(x["heap_mb"] for x in v), "nodes_end": v[-1]["nodes"],
                        "nodes_max": max(x["nodes"] or 0 for x in v), "listeners_end": v[-1]["listeners"], "listeners_max": max(x["listeners"] or 0 for x in v)} for k, v in pph.items()}
    with open(adir / "mem-timeline.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "server_rss_mb", "server_uss_mb", "plugins_n", "plugins_uss_sum_mb", "plugins_rss_sum_mb"])
        last = None
        for t in ts:
            if last is not None and int(t) == last:
                continue
            last = int(t)
            b = by_t[t]
            w.writerow([t, round(b["server_rss"], 1), round(b["server_uss"], 1), b["plug_n"], round(b["plug_uss"], 1), round(b["plug_rss"], 1)])
    with open(adir / "panel-mem-timeline.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "phase", "js_heap_used_mb", "dom_nodes", "js_event_listeners", "documents"])
        for s in ptab:
            w.writerow([s["t"], s["phase"], s["heap_mb"], s["nodes"], s["listeners"], s["documents"]])
    try:
        mem_chart(ts, by_t, ptab, marks, adir / "mem-timeline.png")
    except Exception as exc:  # noqa: BLE001
        print("chart failed:", exc)
    heap = {}
    for name in ("heap-after-idle", "heap-after-switch", "heap-final"):
        p = m / f"{name}.heapsnapshot"
        if p.exists():
            heap[name] = heap_summary(p)
    diffs = {}
    for a, b in (("heap-after-idle", "heap-after-switch"), ("heap-after-switch", "heap-final")):
        if a in heap and b in heap:
            (ta, A), (tb, B) = heap[a], heap[b]
            keys = set(A) | set(B)
            rows_ = [{"ctor": k, "count_delta": B.get(k, [0, 0])[0] - A.get(k, [0, 0])[0], "size_delta_kb": round((B.get(k, [0, 0])[1] - A.get(k, [0, 0])[1]) / 1024, 1)} for k in keys]
            diffs[f"{a} -> {b}"] = {"total_before_mb": round(ta / 2**20, 2), "total_after_mb": round(tb / 2**20, 2), "top_growth": sorted(rows_, key=lambda x: -x["size_delta_kb"])[:20]}
    return {"phases": phases, "panel_phases": panel_phases, "heap_diff": diffs, "events": ev}


def heap_summary(p: Path):
    s = json.loads(p.read_text(encoding="utf-8"))
    meta = s["snapshot"]["meta"]
    nf, nt = meta["node_fields"], meta["node_types"][0]
    W, it, iname, isz = len(nf), nf.index("type"), nf.index("name"), nf.index("self_size")
    nodes, strings = s["nodes"], s["strings"]
    agg, tot = collections.defaultdict(lambda: [0, 0]), 0
    for i in range(0, len(nodes), W):
        typ = nt[nodes[i + it]]
        nm = strings[nodes[i + iname]] if typ in ("object", "closure", "native") else f"({typ})"
        a = agg[nm.split(" ")[0][:80] if typ == "native" else nm[:80]]
        a[0] += 1
        a[1] += nodes[i + isz]
        tot += nodes[i + isz]
    return tot, agg


def mem_chart(ts, by_t, ptab, marks, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    C1, C2, C3, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#1baf7a", "#1f1f1e", "#6b6a63", "#e4e3dd"
    fig, axs = plt.subplots(3, 1, figsize=(12, 9), sharex=True, facecolor="#fcfcfb")

    def style(ax, title):
        ax.set_facecolor("#fcfcfb"); ax.set_title(title, loc="left", fontsize=11, color=INK); ax.set_ylabel("MB", color=MUTED)
        ax.grid(axis="y", color=GRID, lw=0.8); ax.tick_params(colors=MUTED)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(GRID)
    axs[0].plot(ts, [by_t[t]["server_rss"] for t in ts], color=C1, lw=2, label="RSS")
    axs[0].plot(ts, [by_t[t]["server_uss"] for t in ts], color=C2, lw=2, label="USS")
    style(axs[0], "Plugin server process"); axs[0].legend(frameon=False, loc="lower right")
    axs[1].plot(ts, [by_t[t]["plug_uss"] for t in ts], color=C1, lw=2)
    style(axs[1], "Plugin child processes (sum of USS)")
    axs[2].plot([p["t"] for p in ptab], [p["heap_mb"] for p in ptab], color=C3, lw=2)
    style(axs[2], "Panel page: JS heap used")
    axs[2].set_xlabel("seconds since server spawn", color=MUTED)
    labels = {"server:all-running": "all plugins running", "panel:open": "panel open", "panel:switch": "page switching", "panel:idle2": "idle",
              "panel:install-cycles": "install/uninstall", "panel:idle3": "idle", "server:idle-after": "panel closed"}
    for t, n in marks:
        if n in labels:
            for ax in axs:
                ax.axvline(t, color=MUTED, lw=0.8, ls="--")
            axs[0].annotate(labels[n], (t, 1.0), xycoords=("data", "axes fraction"), rotation=90, va="top", ha="right", fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(path, dpi=110)


# ----------------------------------------------------------------------------- report
def results_md(S: dict) -> str:
    L = ["# perf-baseline results", "", f"environment: `{json.dumps(S.get('env') or {}, ensure_ascii=False)[:600]}`", ""]
    B = S.get("boot") or {}
    if B:
        L += ["## Plugin server start-up (ms, median / P90 [min–max])", "", "| variant | n | /health | all plugins running | " + " | ".join(lbl.strip() for _, lbl in PHASES) + " |",
              "|" + "---|" * (4 + len(PHASES))]
        for k in sorted(B):
            v = B[k]
            n = (v.get("health_ms") or {}).get("n", 0)
            L.append(f"| {k} | {n} | {fmt(v.get('health_ms'))} | {fmt(v.get('all_running_ms'))} | " + " | ".join(
                (f"{v[key]['median']:.0f}" if v.get(key) else "—") for key, _ in PHASES) + " |")
        L += ["", "cold-start note: " + str(next((v.get("cold_note") for v in B.values() if v.get("cold_note")), "—")), ""]
    P = S.get("pyspy") or {}
    for name, v in P.items():
        if v.get("server"):
            L += [f"### py-spy {name}: server {v['server']['samples']} samples; {v['scanner_workers']} metadata-scan workers, {v['scanner_samples_total']} samples", ""]
            L += [f"- {c} {f}" for f, c in v["server"]["top_inclusive"][:25] if not re.search(r"importlib|runpy|threading\.py|concurrent/futures|perf_boot", f)]
            L.append("")
    for key, title in (("stack", "Full backend (launcher.py) start-up"),):
        if S.get(key):
            L += [f"## {title} (ms)", "", "| tier | backend ready | main ready | plugin ready |", "|---|---|---|---|"]
            for t, v in S[key].items():
                L.append(f"| {t} | {fmt(v.get('backend_ready_ms'))} | {fmt(v.get('main_ready_ms'))} | {fmt(v.get('plugin_ready_ms'))} |")
            L.append("")
    for mode, v in (S.get("panel") or {}).items():
        o = v.get("open")
        if o:
            L += [f"## Panel open — {mode} (ms)", "", "| interactive | from trigger | FCP | DCL | warm reload | long tasks | script | layout | requests |", "|---|---|---|---|---|---|---|---|---|",
                  f"| {fmt(o['interactive_ms'])} | {fmt(o['wall_interactive_ms'])} | {fmt(o['fcp'])} | {fmt(o['dcl'])} | {fmt(o['warm_interactive_ms'])} | {fmt(o['longtask_count'])} | {fmt(o['script_ms'])} | {fmt(o['layout_ms'])} | {fmt(o['requestsCDP'])} |", "",
                  f"bytes by type (rep 0): `{json.dumps(o.get('byType_rep0'))}`", ""]
        s = v.get("switch")
        if s:
            L += [f"## Page / tab switch — {mode} (ms; steady state median / P90)", "", "| page | first visit | total | API wait | after API | long tasks | APIs |", "|---|---|---|---|---|---|---|"]
            for r in s["pages"]:
                L.append(f"| {r['label']} | {r['first_visit_ms']} | {fmt(r['total'])} | {fmt(r['api_wait'])} | {fmt(r['render_after_api'])} | {fmt(r['longtask_ms'])} | {r['n_api']} |")
            L += ["", "slowest endpoints (server ms):", ""] + [f"- `{e['endpoint']}` n={e['n']} median {e['median']} P90 {e['p90']} max {e['max']}" for e in s["endpoints"][:10]]
            L += ["", f"`/plugins/undefined` requests seen: {s['undefined_requests']}", ""]
        f = v.get("fps")
        if f:
            L += [f"## FPS — {mode}", "", "| scenario | fps | frame ms | >25 ms | >50 ms | max | histogram |", "|---|---|---|---|---|---|---|"]
            for n, x in f.items():
                L.append(f"| {n} | {fmt(x['fps'])} | {fmt(x['frame_ms'])} | {x['pct_over_25ms']}% | {x['pct_over_50ms']}% | {x['max_frame_ms']} | `{json.dumps(x['hist'])}` |")
            L.append("")
    M = S.get("mem")
    if M:
        L += ["## Memory phases", "", "| phase | server RSS start→end | server USS start→end | plugin USS sum | plugins |", "|---|---|---|---|---|"]
        for p in M["phases"]:
            L.append(f"| {p['phase']} | {p['server_rss']['start']}→{p['server_rss']['end']} | {p['server_uss']['start']}→{p['server_uss']['end']} | {p['plug_uss']['start']}→{p['plug_uss']['end']} | {p['plug_n']['end']} |")
        L += ["", "panel: `" + json.dumps(M["panel_phases"]) + "`", ""]
    if S.get("soak"):
        L += ["## Soak (install/uninstall)", "", "| cycle | RSS MB | USS MB | threads | handles/fds |", "|---|---|---|---|---|"]
        for r in S["soak"]:
            if int(r["cycle"]) % 10 == 0:
                L.append(f"| {r['cycle']} | {r['rss_mb']} | {r['uss_mb']} | {r['threads']} | {r['handles_or_fds']} |")
        L.append("")
    return "\n".join(L)


def run(root: Path, repo: Path) -> None:
    root = Path(root)
    adir = root / "analysis"
    adir.mkdir(parents=True, exist_ok=True)
    mapper = Mapper(Path(repo) / "frontend" / "plugin-manager" / "dist" / "assets")
    S = {"env": load(root / "env.json"), "boot": analyze_boot(root), "pyspy": analyze_pyspy(root, adir), "importtime": analyze_importtime(root)}
    stack = {}
    for f in root.glob("stack/t*-summary.json"):
        rows = load(f) or []
        stack[f.stem.replace("-summary", "")] = {k: q([r.get(k) for r in rows]) for k in ("backend_ready_ms", "main_ready_ms", "plugin_ready_ms")}
    S["stack"] = stack
    S["panel"] = {}
    for d in sorted(list(root.glob("browser/t*")) + list(root.glob("electron/t*"))):
        key = f"{d.parent.name}-{d.name}"
        v = {}
        if (d / "open.json").exists():
            v["open"] = open_stats(load(d / "open.json"))
        if (d / "switch.json").exists():
            v["switch"] = switch_stats(load(d / "switch.json"))
        fps = load(d / "fps.json") if (d / "fps.json").exists() else None
        if fps:
            v["fps"] = fps_stats(fps)
        for tr in d.glob("*.trace.json"):
            try:
                v.setdefault("traces", {})[tr.name] = analyze_trace(tr, mapper, fps if tr.name.startswith("fps") else None)
            except (MemoryError, ValueError, KeyError) as exc:
                v.setdefault("traces", {})[tr.name] = {"error": f"{type(exc).__name__}: {exc}"}
        S["panel"][key] = v
    S["mem"] = analyze_mem(root, adir)
    soak = root / "soak" / "soak.csv"
    S["soak"] = list(csv.DictReader(open(soak, encoding="utf-8"))) if soak.exists() else None
    S["tracemalloc"] = load(root / "tracemalloc" / "tm-compare.json")
    S["tracemalloc_rss_mb"] = load(root / "tracemalloc" / "rss.json")
    text = json.dumps(S, ensure_ascii=False, indent=1)
    text = re.sub(r"token=[^&\"' ]*", "token=<redacted>", text)
    (adir / "summary.json").write_text(text, encoding="utf-8")
    (adir / "results.md").write_text(re.sub(r"token=[^&\"' ]*", "token=<redacted>", results_md(S)), encoding="utf-8")
    print("analysis written to", adir)
