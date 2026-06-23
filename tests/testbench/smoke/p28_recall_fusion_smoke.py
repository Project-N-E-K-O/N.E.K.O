"""P28 recall-fusion contract smoke (UPSTREAM_SYNC_2026-06 Phase 3, RAG #2=M / #3=O).

Guards the **semantic-contract** half of the ``recall_memory`` backend that the
testbench now reuses via ``tests/testbench/pipeline/recall_fusion.py`` (a thin
adapter over ``memory.hybrid_recall`` + ``memory.recall`` + ``memory.temporal``).
Like the evidence-math smoke (p27), the fusion primitives are **imported
directly**, so this asserts the *behavioural invariants* of the imported pure
functions: BM25 tokenization / ranking, RRF math, the hard filter, and the
event-time window. If upstream changes a tokenizer rule, the BM25 formula, the
RRF constant semantics, the drop list, or the time-window parse, the relevant
check below fails — flagging that the testbench's retrieval assumptions drifted.

Out of scope (Phase 3.0 design gate): the **runtime-mechanism** cosine path
(ONNX ``EmbeddingService`` / sqlite ``timeindex`` / ``recall_memory`` HTTP+TTS
plumbing). The ``recall_bm25_fuse`` harness reproduces ``hybrid_recall``'s
pipeline with the cosine ranking fixed empty — "torque minus the embedding
delivery mechanism". Production unit tests own the cosine/infra layers.

Checks (any failure -> exit 1):

- C1  imports reachable: production pure helpers + the testbench adapter.
- C2  tokenize: CJK 2/3-gram multiplicity preserved (vs set-dedup), Latin runs
      kept whole, stop_names stripped before tokenizing.
- C3  BM25: a doc sharing query terms outranks one that doesn't; zero-overlap
      docs are dropped; empty query / empty pool -> [].
- C4  RRF: ``Σ 1/(k+rank)`` accumulates across both lists, sorts DESC, caps at
      budget_total, and skips id-less docs.
- C5  hard_filter: drops score<0 / suppressed / protected / terminal-reflection
      / empty-text, keeps promoted reflections + normal facts, skips non-dict.
- C6  parse_time_window: day / range / month tokens map to the right half-open
      [start, end), and garbage -> None.
- C7  entry_in_window: event interval intersect with [start, end); timeless
      entries are treated as outside.
- C8  recall_bm25_fuse end-to-end: hard_filter -> time-window filter -> BM25 ->
      RRF returns only the in-window, query-matching entry.

Usage:

    .venv/Scripts/python.exe tests/testbench/smoke/p28_recall_fusion_smoke.py

Exits 0 on all-clean, 1 on any violation.
"""
from __future__ import annotations

import io
import sys
from datetime import datetime
from pathlib import Path

# Force utf-8 stdout so CJK / arrows survive Windows GBK consoles.
if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_SUCCESS_BANNER = "P28 RECALL FUSION SMOKE OK"
_EPS = 1e-9


def check_c1_import() -> tuple[bool, str]:
    try:
        from memory.hybrid_recall import (  # noqa: F401
            _bm25_rank,
            _overlaps_window,
            _rrf_fuse,
            _tag_tier,
            _tokenize,
        )
        from memory.recall import MemoryRecallReranker  # noqa: F401
        from memory.temporal import parse_time_window  # noqa: F401
        from tests.testbench.pipeline import recall_fusion  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"C1 FAIL: import error: {exc!r}"
    return True, "C1 OK: production pure helpers + adapter import"


def check_c2_tokenize() -> tuple[bool, str]:
    from tests.testbench.pipeline import recall_fusion

    # CJK multiplicity: '猫猫猫' -> 2-grams ['猫猫','猫猫'] + 3-gram ['猫猫猫'].
    # The whole point of _tokenize (vs persona._extract_keywords' set) is that
    # TF survives — '猫猫' must appear twice.
    toks = recall_fusion.tokenize("猫猫猫")
    if toks.count("猫猫") != 2 or "猫猫猫" not in toks:
        return False, f"C2 FAIL: CJK multiplicity lost: {toks}"

    # Latin run kept whole, not n-grammed.
    latin = recall_fusion.tokenize("hello world")
    if sorted(latin) != ["hello", "world"]:
        return False, f"C2 FAIL: Latin split wrong: {latin}"

    # stop_names stripped before tokenizing: '主人' gone, '披萨' survives.
    stripped = recall_fusion.tokenize("主人喜欢披萨", stop_names=["主人"])
    if any("主" in t or "人" in t for t in stripped):
        return False, f"C2 FAIL: stop_name not stripped: {stripped}"
    if "披萨" not in stripped:
        return False, f"C2 FAIL: real content dropped after strip: {stripped}"
    return True, "C2 OK: CJK TF preserved, Latin whole, stop_names stripped"


def check_c3_bm25() -> tuple[bool, str]:
    from tests.testbench.pipeline import recall_fusion

    pool = [
        {"id": "d1", "text": "我爱披萨披萨"},   # has query bigram (twice)
        {"id": "d2", "text": "今天天气很好"},   # no overlap
    ]
    ranked = recall_fusion.bm25_rank("披萨", pool)
    ids = [d["id"] for d, _ in ranked]
    if ids != ["d1"]:
        return False, f"C3 FAIL: bm25 ranked ids = {ids}, expected ['d1'] (d2 dropped)"
    if not all(s > 0 for _, s in ranked):
        return False, "C3 FAIL: bm25 emitted a non-positive score"

    if recall_fusion.bm25_rank("", pool):
        return False, "C3 FAIL: empty query should rank []"
    if recall_fusion.bm25_rank("披萨", []):
        return False, "C3 FAIL: empty pool should rank []"
    return True, "C3 OK: BM25 ranks overlap-only docs, drops zero-overlap"


def check_c4_rrf() -> tuple[bool, str]:
    from config import HYBRID_RECALL_RRF_K
    from tests.testbench.pipeline import recall_fusion

    a = {"id": "A", "text": "a"}
    b = {"id": "B", "text": "b"}
    c = {"id": "C", "text": "c"}
    noid = {"text": "no id"}
    k = HYBRID_RECALL_RRF_K

    bm25 = [(a, 9.0), (b, 8.0), (noid, 7.0)]
    cosine = [(b, 0.9), (c, 0.8)]
    fused = recall_fusion.rrf_fuse(bm25, cosine, k=k, budget_total=10)
    ids = [d["id"] for d in fused]
    # B = 1/(k+2)+1/(k+1); A = 1/(k+1); C = 1/(k+2). So order B > A > C.
    if ids != ["B", "A", "C"]:
        return False, f"C4 FAIL: RRF order = {ids}, expected ['B','A','C']"
    b_expected = 1.0 / (k + 2) + 1.0 / (k + 1)
    b_got = next(d["_rrf_score"] for d in fused if d["id"] == "B")
    if abs(b_got - b_expected) > _EPS:
        return False, f"C4 FAIL: RRF(B) = {b_got}, expected {b_expected}"

    capped = recall_fusion.rrf_fuse(bm25, cosine, k=k, budget_total=2)
    if [d["id"] for d in capped] != ["B", "A"]:
        return False, f"C4 FAIL: budget_total cap = {[d['id'] for d in capped]}"
    return True, "C4 OK: RRF accumulates, sorts DESC, caps, skips id-less"


def check_c5_hard_filter() -> tuple[bool, str]:
    from tests.testbench.pipeline import recall_fusion

    pool = [
        {"id": "keep_fact", "text": "normal fact"},
        {"id": "neg", "text": "net negative", "score": -0.5},
        {"id": "supp", "text": "over-mentioned", "suppress": True},
        {"id": "supp2", "text": "over-mentioned 2", "suppressed": True},
        {"id": "prot", "text": "character card", "protected": True},
        {"id": "dead_refl", "text": "dead", "target_type": "reflection",
         "status": "denied"},
        {"id": "live_refl", "text": "alive", "target_type": "reflection",
         "status": "promoted"},
        {"id": "blank", "text": "   "},
        "i am not a dict",  # type: ignore[list-item]
    ]
    survivors = {d["id"] for d in recall_fusion.hard_filter(pool)}
    expected = {"keep_fact", "live_refl"}
    if survivors != expected:
        return False, (
            f"C5 FAIL: survivors = {sorted(survivors)}, expected {sorted(expected)}"
        )
    return True, "C5 OK: hard_filter drops neg/suppressed/protected/terminal/blank"


def check_c6_parse_window() -> tuple[bool, str]:
    from tests.testbench.pipeline import recall_fusion

    cases = [
        ("2026-05-01", (datetime(2026, 5, 1), datetime(2026, 5, 2))),
        ("2026-05-01/2026-05-07", (datetime(2026, 5, 1), datetime(2026, 5, 8))),
        ("2026-05", (datetime(2026, 5, 1), datetime(2026, 6, 1))),
    ]
    for spec, want in cases:
        got = recall_fusion.parse_time_window(spec)
        if got != want:
            return False, f"C6 FAIL: parse({spec!r}) = {got}, expected {want}"
    if recall_fusion.parse_time_window("not-a-date") is not None:
        return False, "C6 FAIL: garbage spec should parse to None"
    return True, "C6 OK: day/range/month windows + None on garbage"


def check_c7_in_window() -> tuple[bool, str]:
    from tests.testbench.pipeline import recall_fusion

    ws, we = recall_fusion.parse_time_window("2026-05")
    inside = {"id": "i", "text": "x", "event_start_at": "2026-05-10T12:00:00"}
    outside = {"id": "o", "text": "x", "event_start_at": "2026-07-10T12:00:00"}
    timeless = {"id": "t", "text": "x"}
    if not recall_fusion.entry_in_window(inside, ws, we):
        return False, "C7 FAIL: in-window entry judged outside"
    if recall_fusion.entry_in_window(outside, ws, we):
        return False, "C7 FAIL: out-of-window entry judged inside"
    if recall_fusion.entry_in_window(timeless, ws, we):
        return False, "C7 FAIL: timeless entry should be treated as outside"
    return True, "C7 OK: window membership + timeless-as-outside"


def check_c8_fuse_end_to_end() -> tuple[bool, str]:
    from tests.testbench.pipeline import recall_fusion

    window = recall_fusion.parse_time_window("2026-05")
    pool = [
        {"id": "f1", "text": "披萨很好吃", "event_start_at": "2026-05-03T12:00:00"},
        {"id": "f2", "text": "披萨派对", "event_start_at": "2026-07-03T12:00:00"},
        {"id": "f3", "text": "今天天气", "event_start_at": "2026-05-04T12:00:00"},
    ]
    fused = recall_fusion.recall_bm25_fuse("披萨", pool, time_window=window)
    ids = [d["id"] for d in fused]
    # f2 filtered out by window; f3 in-window but no query overlap -> dropped.
    if ids != ["f1"]:
        return False, f"C8 FAIL: fused ids = {ids}, expected ['f1']"
    if "_rrf_score" not in fused[0]:
        return False, "C8 FAIL: fused result missing _rrf_score"
    return True, "C8 OK: end-to-end window + BM25 + RRF returns the right hit"


_CHECKS = [
    check_c1_import,
    check_c2_tokenize,
    check_c3_bm25,
    check_c4_rrf,
    check_c5_hard_filter,
    check_c6_parse_window,
    check_c7_in_window,
    check_c8_fuse_end_to_end,
]


def main() -> int:
    print("=" * 66)
    print(" P28 Recall-Fusion Contract Smoke (Phase 3 #2=M / #3=O)")
    print("=" * 66)
    print(" adapter:  tests/testbench/pipeline/recall_fusion.py")
    print(" upstream: memory/hybrid_recall.py + recall.py + temporal.py")
    print("           (imported directly — wanted coupling; cosine path OOS)")
    print("")

    failures: list[str] = []
    for check in _CHECKS:
        try:
            ok, msg = check()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"{check.__name__} FAIL: raised {type(exc).__name__}: {exc}"
        print(msg)
        if not ok:
            failures.append(msg)

    print("")
    print("=" * 66)
    if failures:
        print(f" [FAIL] {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f.splitlines()[0]}")
        return 1
    print(f" [PASS] all {len(_CHECKS)} checks clean.")
    print(f" {_SUCCESS_BANNER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
