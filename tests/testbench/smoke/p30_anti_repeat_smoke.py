"""P30 anti-repeat BM25 contract smoke (UPSTREAM_SYNC_2026-06 Phase 3, RAG #5=O).

Guards the **semantic-contract** half of ``memory.anti_repeat`` that the
testbench reuses via ``tests/testbench/pipeline/anti_repeat_sim.py``: the
repetitiveness BM25 scorer. Imported directly, so this asserts the *behavioural
invariants* — IDF over the background window, TF accumulated over the
foreground window, "rare overall + frequent lately" scores high while a draft of
only-unseen terms scores zero, per_term sorted DESC, edge cases return (0, {}).
The disk-backed ``AntiRepeatCorpus`` class (JSON/locks/rolling window) is OOS.

Checks (any failure -> exit 1):

- C1  imports reachable (production bm25_score + adapter).
- C2  edge cases: empty draft / empty fg -> (0.0, {}); a draft of terms absent
      from the background contributes nothing.
- C3  a low-DF term repeated across the foreground scores positive and shows up
      in per_term; per_term is sorted descending.
- C4  TF accumulation: a term appearing in more foreground docs scores higher
      than the same term appearing in fewer (DF held equal-ish via background).
- C5  end-to-end ``score_texts``: a draft re-treading a recent topic scores
      strictly higher than a draft on a fresh topic.

Usage:

    .venv/Scripts/python.exe tests/testbench/smoke/p30_anti_repeat_smoke.py

Exits 0 on all-clean, 1 on any violation.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_SUCCESS_BANNER = "P30 ANTI REPEAT SMOKE OK"


def check_c1_import() -> tuple[bool, str]:
    try:
        from memory.anti_repeat import bm25_score  # noqa: F401
        from tests.testbench.pipeline import anti_repeat_sim  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"C1 FAIL: import error: {exc!r}"
    return True, "C1 OK: production bm25_score + adapter import"


def check_c2_edges() -> tuple[bool, str]:
    from tests.testbench.pipeline import anti_repeat_sim

    fg = [["老虎", "今天"], ["老虎", "吃饭"]]
    if anti_repeat_sim.bm25_score([], fg) != (0.0, {}):
        return False, "C2 FAIL: empty draft should be (0.0, {})"
    if anti_repeat_sim.bm25_score(["老虎"], []) != (0.0, {}):
        return False, "C2 FAIL: empty foreground should be (0.0, {})"
    total, per_term = anti_repeat_sim.bm25_score(["不存在词"], fg)
    if total != 0.0 or per_term != {}:
        return False, f"C2 FAIL: out-of-corpus draft scored {total}/{per_term}, expected 0/{{}}"
    return True, "C2 OK: empty/out-of-corpus edge cases score (0.0, {})"


def check_c3_positive_sorted() -> tuple[bool, str]:
    from tests.testbench.pipeline import anti_repeat_sim

    fg = [
        ["老虎", "今天"],
        ["老虎", "吃饭"],
        ["老虎", "睡觉"],
        ["天气", "今天"],
        ["工作", "累"],
    ]
    total, per_term = anti_repeat_sim.bm25_score(["老虎", "天气"], fg, fg)
    if total <= 0:
        return False, f"C3 FAIL: repeated low-DF terms scored {total}, expected > 0"
    if "老虎" not in per_term:
        return False, "C3 FAIL: repeated term '老虎' missing from per_term"
    # '老虎' (3 fg docs) should outscore '天气' (1 fg doc).
    if per_term.get("老虎", 0) <= per_term.get("天气", 0):
        return False, "C3 FAIL: more-frequent term did not outscore less-frequent"
    vals = list(per_term.values())
    if vals != sorted(vals, reverse=True):
        return False, f"C3 FAIL: per_term not sorted descending: {vals}"
    return True, "C3 OK: low-DF repeated term positive, per_term sorted DESC"


def check_c4_tf_accumulation() -> tuple[bool, str]:
    from tests.testbench.pipeline import anti_repeat_sim

    # IDF is computed over the BACKGROUND, TF over the FOREGROUND. To isolate TF
    # accumulation we hold the background fixed (so '主题' keeps the same DF/IDF)
    # and vary only how many foreground docs contain it. All docs are len-2 so
    # avgdl + length-norm stay constant across both runs.
    bg = [["主题", "a"], ["主题", "b"], ["x", "c"], ["y", "d"], ["z", "e"]]
    fg_more = [["主题", "p"], ["主题", "q"], ["主题", "r"]]  # term in 3 fg docs
    fg_less = [["主题", "p"], ["s", "q"], ["t", "r"]]        # term in 1 fg doc
    more, _ = anti_repeat_sim.bm25_score(["主题"], fg_more, bg)
    less, _ = anti_repeat_sim.bm25_score(["主题"], fg_less, bg)
    if not (more > less > 0):
        return False, f"C4 FAIL: TF accumulation broken (more={more}, less={less})"
    return True, "C4 OK: more foreground occurrences -> higher repetition score"


def check_c5_score_texts() -> tuple[bool, str]:
    from tests.testbench.pipeline import anti_repeat_sim

    recent = [
        "我们刚才聊了好久关于老虎的故事",
        "老虎真的是非常厉害的猛兽",
        "说到老虎我还想起动物园那只老虎",
    ]
    repeat_total, _ = anti_repeat_sim.score_texts("老虎又出现了我们继续聊老虎", recent)
    fresh_total, _ = anti_repeat_sim.score_texts("今天的晚饭打算吃点清淡的蔬菜沙拉", recent)
    if not (repeat_total > fresh_total):
        return False, (
            f"C5 FAIL: repeat draft ({repeat_total}) did not outscore "
            f"fresh draft ({fresh_total})"
        )
    return True, "C5 OK: end-to-end repeat draft outscores fresh draft"


_CHECKS = [
    check_c1_import,
    check_c2_edges,
    check_c3_positive_sorted,
    check_c4_tf_accumulation,
    check_c5_score_texts,
]


def main() -> int:
    print("=" * 66)
    print(" P30 Anti-Repeat BM25 Contract Smoke (Phase 3 #5 = O)")
    print("=" * 66)
    print(" adapter:  tests/testbench/pipeline/anti_repeat_sim.py")
    print(" upstream: memory/anti_repeat.py (imported directly; corpus disk/lock OOS)")
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
