"""Compare tracemalloc snapshots tm-1/2/3.pickle written by perf_boot.py.

Run with the project interpreter: uv run --no-sync python perf-baseline/tm_compare.py <dir>
"""
import json, os, pickle, re, sys, tracemalloc


def _short(p):
    p = p.replace(chr(92), '/')
    p = re.sub(r'^.*?/site-packages/', '', p)
    return re.sub(r'^.*?/(plugin|utils|app|main_logic|main_routers|memory|config)/', r'\1/', p)

d = sys.argv[1]
snaps = [pickle.load(open(os.path.join(d, f"tm-{i}.pickle"), "rb")) for i in (1, 2, 3) if os.path.exists(os.path.join(d, f"tm-{i}.pickle"))]
flt = [tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, "<frozen importlib._bootstrap*>")]
snaps = [s.filter_traces(flt) for s in snaps]
out = {}
for (a, b, name) in [(0, 1, "A->B (install/uninstall cycles)"), (1, 2, "B->C (panel API GET rounds)")]:
    if b >= len(snaps):
        continue
    res = {}
    for key in ("lineno", "traceback"):
        st = snaps[b].compare_to(snaps[a], key)
        rows = []
        for x in st[:15]:
            fr = [f"{_short(f.filename)}:{f.lineno}" for f in x.traceback]
            rows.append({"size_diff_kb": round(x.size_diff / 1024, 1), "count_diff": x.count_diff, "size_kb": round(x.size / 1024, 1),
                         "where": fr[0] if key == "lineno" else fr[-12:]})
        res[key] = rows
    res["total_diff_kb"] = round((sum(t.size for t in snaps[b].traces) - sum(t.size for t in snaps[a].traces)) / 1024, 1)
    out[name] = res
json.dump(out, open(os.path.join(d, "tm-compare.json"), "w"), indent=1)
for k, v in out.items():
    print("==", k, "total", v["total_diff_kb"], "KB")
    for r in v["lineno"][:12]:
        print(f"  {r['size_diff_kb']:9.1f} KB {r['count_diff']:7d}  {r['where']}")
