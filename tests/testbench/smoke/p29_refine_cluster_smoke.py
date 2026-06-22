"""P29 refine-bookkeeping contract smoke (UPSTREAM_SYNC_2026-06 Phase 3, RAG #4=O).

Guards the **semantic-contract** half of ``memory.refine`` that the testbench
reuses via ``tests/testbench/pipeline/refine_sim.py``: the deterministic cluster
bookkeeping (annotate/strip, ``_cluster_hash``, ``_all_stamped_fresh``,
``_cluster_starvation_key``, ``_render_cluster``). Imported directly, so this
asserts the *behavioural invariants* — fact rows are excluded from the hash,
any stale/None/mismatched stamp forces a revisit, unstamped members starve to
the front of the queue. Cluster computation (cosine) + LLM resolve are OOS.

Checks (any failure -> exit 1):

- C1  imports reachable + VALID_REFINE_ACTIONS is the 4-action set.
- C2  cluster_hash: order-independent, membership-sensitive, fact rows excluded.
- C3  all_stamped_fresh: True only when every non-fact member is stamped on the
      exact hash within the revisit window; None / stale / hash-mismatch -> False;
      fact members are ignored.
- C4  starvation_key: an unstamped member yields '' (sorts before any ISO
      timestamp), fully-stamped clusters yield the min timestamp.
- C5  render_cluster: per-type line formats; rows missing id/text are skipped.
- C6  annotate/strip round-trip leaves the original entry untouched (copy-on-write).

Usage:

    .venv/Scripts/python.exe tests/testbench/smoke/p29_refine_cluster_smoke.py

Exits 0 on all-clean, 1 on any violation.
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_SUCCESS_BANNER = "P29 REFINE CLUSTER SMOKE OK"


def _persona(eid: str, text: str = "x") -> dict:
    from tests.testbench.pipeline import refine_sim
    return refine_sim.annotate({"id": eid, "text": text}, type_="persona", entity="master")


def _fact(eid: str, text: str = "x") -> dict:
    from tests.testbench.pipeline import refine_sim
    return refine_sim.annotate({"id": eid, "text": text}, type_="fact", entity="master")


def check_c1_import() -> tuple[bool, str]:
    try:
        from memory.refine import (  # noqa: F401
            MemoryRefineEngine,
            VALID_REFINE_ACTIONS,
            annotate_entry,
            strip_refine_metadata,
        )
        from tests.testbench.pipeline import refine_sim
    except Exception as exc:  # noqa: BLE001
        return False, f"C1 FAIL: import error: {exc!r}"
    if refine_sim.valid_actions() != frozenset({"split", "merge", "modify", "discard"}):
        return False, f"C1 FAIL: VALID_REFINE_ACTIONS drift: {refine_sim.valid_actions()}"
    return True, "C1 OK: imports reachable, action set = split/merge/modify/discard"


def check_c2_cluster_hash() -> tuple[bool, str]:
    from tests.testbench.pipeline import refine_sim

    a, b = _persona("p1"), _persona("p2")
    h_ab = refine_sim.cluster_hash([a, b])
    h_ba = refine_sim.cluster_hash([b, a])
    if h_ab != h_ba:
        return False, "C2 FAIL: cluster_hash is order-dependent"
    if h_ab == refine_sim.cluster_hash([a]):
        return False, "C2 FAIL: cluster_hash not sensitive to membership"
    # fact rows excluded -> adding a fact must not change the hash.
    if refine_sim.cluster_hash([a, b, _fact("f1")]) != h_ab:
        return False, "C2 FAIL: fact row changed the cluster_hash"
    return True, "C2 OK: hash order-independent, membership-sensitive, fact-excluded"


def check_c3_stamped_fresh() -> tuple[bool, str]:
    from tests.testbench.pipeline import refine_sim

    a, b = _persona("p1"), _persona("p2")
    h = refine_sim.cluster_hash([a, b])
    now_iso = datetime.now().isoformat()

    def _stamp(entry: dict, *, hash_value: str, at: str) -> dict:
        e = dict(entry)
        e["last_refine_cluster_hash"] = hash_value
        e["last_refine_at"] = at
        return e

    fresh = [_stamp(a, hash_value=h, at=now_iso), _stamp(b, hash_value=h, at=now_iso)]
    if not refine_sim.all_stamped_fresh(fresh, h):
        return False, "C3 FAIL: fully-stamped fresh cluster judged stale"

    # One member never stamped -> revisit.
    if refine_sim.all_stamped_fresh([fresh[0], b], h):
        return False, "C3 FAIL: unstamped member should force revisit"

    # Stale stamp (far past) -> revisit.
    stale_at = (datetime.now() - timedelta(days=3650)).isoformat()
    stale = [_stamp(a, hash_value=h, at=now_iso), _stamp(b, hash_value=h, at=stale_at)]
    if refine_sim.all_stamped_fresh(stale, h):
        return False, "C3 FAIL: stale stamp should force revisit"

    # Hash mismatch -> revisit.
    if refine_sim.all_stamped_fresh(fresh, "deadbeefdeadbeef"):
        return False, "C3 FAIL: hash mismatch should force revisit"

    # Fact members are ignored: unstamped fact must not break freshness.
    with_fact = fresh + [_fact("f1")]
    if not refine_sim.all_stamped_fresh(with_fact, h):
        return False, "C3 FAIL: unstamped fact member wrongly blocked freshness"
    return True, "C3 OK: freshness honours hash+window, None/stale/mismatch revisit, fact ignored"


def check_c4_starvation() -> tuple[bool, str]:
    from tests.testbench.pipeline import refine_sim

    a, b = _persona("p1"), _persona("p2")
    older = dict(a); older["last_refine_at"] = "2026-01-01T00:00:00"
    newer = dict(b); newer["last_refine_at"] = "2026-06-01T00:00:00"

    stamped_key = refine_sim.starvation_key([older, newer])
    if stamped_key != "2026-01-01T00:00:00":
        return False, f"C4 FAIL: starvation_key = {stamped_key!r}, expected min stamp"

    # Unstamped member -> '' which sorts before any ISO timestamp.
    unstamped_key = refine_sim.starvation_key([newer, _persona("p3")])
    if unstamped_key != "":
        return False, f"C4 FAIL: unstamped starvation_key = {unstamped_key!r}, expected ''"
    if not (unstamped_key < stamped_key):
        return False, "C4 FAIL: unstamped cluster should starve before stamped"
    return True, "C4 OK: starvation key min-timestamp, unstamped '' sorts first"


def check_c5_render() -> tuple[bool, str]:
    from tests.testbench.pipeline import refine_sim

    cluster = [
        refine_sim.annotate({"id": "p1", "text": "persona line"},
                            type_="persona", entity="master"),
        refine_sim.annotate({"id": "r1", "text": "refl line",
                             "relation_type": "likes", "temporal_scope": "now"},
                            type_="reflection", entity="master"),
        refine_sim.annotate({"id": "f1", "text": "fact line", "importance": 9},
                            type_="fact", entity="master"),
        refine_sim.annotate({"id": "", "text": "no id, skipped"},
                            type_="persona", entity="master"),
    ]
    out = refine_sim.render_cluster(cluster)
    lines = out.splitlines()
    if len(lines) != 3:
        return False, f"C5 FAIL: expected 3 rendered lines (id-less skipped), got {len(lines)}"
    if "persona id=p1" not in out:
        return False, "C5 FAIL: persona line format wrong"
    if "reflection id=r1" not in out or "relation_type=likes" not in out:
        return False, "C5 FAIL: reflection line format wrong"
    if "fact id=f1" not in out or "importance=9" not in out:
        return False, "C5 FAIL: fact line format wrong"
    return True, "C5 OK: render per-type formats, id-less rows skipped"


def check_c6_annotate_strip() -> tuple[bool, str]:
    from tests.testbench.pipeline import refine_sim

    original = {"id": "p1", "text": "x"}
    tagged = refine_sim.annotate(original, type_="persona", entity="master")
    if "_refine_type" in original:
        return False, "C6 FAIL: annotate mutated the original entry"
    if tagged.get("_refine_type") != "persona" or tagged.get("_refine_entity") != "master":
        return False, "C6 FAIL: annotate did not stamp type/entity"
    stripped = refine_sim.strip(tagged)
    if "_refine_type" in stripped or "_refine_entity" in stripped:
        return False, "C6 FAIL: strip left refine markers"
    if "_refine_type" not in tagged:
        return False, "C6 FAIL: strip mutated the tagged entry"
    return True, "C6 OK: annotate/strip copy-on-write round-trip"


_CHECKS = [
    check_c1_import,
    check_c2_cluster_hash,
    check_c3_stamped_fresh,
    check_c4_starvation,
    check_c5_render,
    check_c6_annotate_strip,
]


def main() -> int:
    print("=" * 66)
    print(" P29 Refine-Bookkeeping Contract Smoke (Phase 3 #4 = O)")
    print("=" * 66)
    print(" adapter:  tests/testbench/pipeline/refine_sim.py")
    print(" upstream: memory/refine.py (imported directly; cosine+LLM OOS)")
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
