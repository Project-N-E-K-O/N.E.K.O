"""P27 evidence-math contract smoke (UPSTREAM_SYNC_2026-06 Phase 3, RAG #1 = M).

Guards the evidence-RFC **semantic contract** that the testbench now reuses
via ``tests/testbench/pipeline/evidence_sim.py`` (a thin adapter over
``memory.evidence``). Unlike the avatar-dedupe drift smoke (L30 copy + byte
hash), evidence math is **imported directly** — so this smoke asserts the
*behavioural invariants* of the imported pure functions. If upstream changes a
decay formula, a status threshold, or the user_fact combo rule, the relevant
check below fails, signalling that the testbench's evidence assumptions (and
any future evidence coverage built on this adapter) went stale.

Out of scope (Phase 3.0 design gate): every runtime-mechanism layer —
event_log / outbox / embedding_worker / memory_server background loops /
sqlite timeindex. Production unit tests own those.

Checks (any failure -> exit 1):

- C1  import reachable: ``memory.evidence`` pure helpers + the testbench
      adapter both import, and the snapshot field-shape matches.
- C2  read-time decay halves at exactly one half-life, with independent
      reinforcement / disputation clocks.
- C3  ``protected=True`` -> score ``inf`` -> status ``promoted``.
- C4  derived-status tiers honour the config thresholds
      (promoted >= 2.0, confirmed >= 1.0, archive_candidate <= -2.0, else pending).
- C5  user_fact combo: bonus kicks in only after the threshold count, and
      only for ``source='user_fact'`` with a positive reinforcement delta.
- C6  disputation is clamped non-negative by ``compute_evidence_snapshot``.
- C7  importance->initial-seed table maps 10/9/8/7 -> 0.8/0.6/0.4/0.2 and
      <=6 / non-int -> 0.0.
- C8  ``maybe_mark_sub_zero`` increments at most once per calendar day,
      skips protected and non-negative entries.
- C9  adapter ``simulate_signal_sequence`` reproduces the funnel
      pending -> confirmed -> promoted across 3 user_fact reinforces.

Usage:

    .venv/Scripts/python.exe tests/testbench/smoke/p27_evidence_math_smoke.py

Exits 0 on all-clean, 1 on any violation.
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Force utf-8 stdout so box-drawing / arrows survive Windows GBK consoles.
if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_SUCCESS_BANNER = "P27 EVIDENCE MATH SMOKE OK"
_EPS = 1e-9

# Deterministic clock anchor for all decay assertions.
_T0 = datetime(2026, 1, 1, 12, 0, 0)
_T0_ISO = _T0.isoformat()


def check_c1_import() -> tuple[bool, str]:
    try:
        from memory.evidence import (  # noqa: F401
            compute_evidence_snapshot,
            derive_status,
            effective_disputation,
            effective_reinforcement,
            evidence_score,
            initial_reinforcement_from_importance,
            maybe_mark_sub_zero,
        )
        from tests.testbench.pipeline import evidence_sim
    except Exception as exc:  # noqa: BLE001
        return False, f"C1 FAIL: import error: {exc!r}"

    # Snapshot shape echo must match what compute_evidence_snapshot returns.
    snap = compute_evidence_snapshot({}, {"reinforcement": 0.1}, _T0_ISO, "x")
    missing = set(evidence_sim.EVIDENCE_SNAPSHOT_FIELDS) - set(snap.keys())
    extra = set(snap.keys()) - set(evidence_sim.EVIDENCE_SNAPSHOT_FIELDS)
    if missing or extra:
        return False, (
            "C1 FAIL: snapshot shape drift "
            f"missing={sorted(missing)} extra={sorted(extra)} "
            "— update evidence_sim.EVIDENCE_SNAPSHOT_FIELDS"
        )
    return True, "C1 OK: imports reachable, snapshot shape matches"


def check_c2_decay() -> tuple[bool, str]:
    from config import EVIDENCE_DISP_HALF_LIFE_DAYS, EVIDENCE_REIN_HALF_LIFE_DAYS
    from memory.evidence import effective_disputation, effective_reinforcement

    rein_entry = {"reinforcement": 4.0, "rein_last_signal_at": _T0_ISO}
    one_rein_hl = _T0 + timedelta(days=EVIDENCE_REIN_HALF_LIFE_DAYS)
    eff_rein = effective_reinforcement(rein_entry, one_rein_hl)
    if abs(eff_rein - 2.0) > 1e-6:
        return False, f"C2 FAIL: rein half-life decay = {eff_rein}, expected ~2.0"

    # Independent clocks: a rein-only entry has no disputation movement.
    if abs(effective_disputation(rein_entry, one_rein_hl)) > _EPS:
        return False, "C2 FAIL: rein entry shows non-zero effective disputation"

    disp_entry = {"disputation": 4.0, "disp_last_signal_at": _T0_ISO}
    one_disp_hl = _T0 + timedelta(days=EVIDENCE_DISP_HALF_LIFE_DAYS)
    eff_disp = effective_disputation(disp_entry, one_disp_hl)
    if abs(eff_disp - 2.0) > 1e-6:
        return False, f"C2 FAIL: disp half-life decay = {eff_disp}, expected ~2.0"

    # disp clock is slower: at one rein half-life the disp barely moved.
    eff_disp_early = effective_disputation(disp_entry, one_rein_hl)
    if not (eff_disp_early > 3.0):
        return False, (
            "C2 FAIL: disputation decayed too fast — clocks may not be "
            f"independent (eff_disp@30d={eff_disp_early}, expected >3.0)"
        )
    return True, "C2 OK: read-time decay halves at half-life, clocks independent"


def check_c3_protected() -> tuple[bool, str]:
    from memory.evidence import derive_status, evidence_score

    entry = {"protected": True, "reinforcement": 0.0, "disputation": 100.0,
             "disp_last_signal_at": _T0_ISO}
    score = evidence_score(entry, _T0)
    if score != float("inf"):
        return False, f"C3 FAIL: protected score = {score}, expected inf"
    status = derive_status(entry, _T0)
    if status != "promoted":
        return False, f"C3 FAIL: protected status = {status!r}, expected 'promoted'"
    return True, "C3 OK: protected -> inf -> promoted"


def check_c4_status_tiers() -> tuple[bool, str]:
    from config import (
        EVIDENCE_ARCHIVE_THRESHOLD,
        EVIDENCE_CONFIRMED_THRESHOLD,
        EVIDENCE_PROMOTED_THRESHOLD,
    )
    from memory.evidence import derive_status

    def _entry(score: float) -> dict:
        # rein_last_signal_at = now -> age 0 -> effective == raw, so
        # evidence_score == reinforcement when disputation is absent.
        return {"reinforcement": score, "rein_last_signal_at": _T0_ISO}

    cases = [
        (EVIDENCE_PROMOTED_THRESHOLD, "promoted"),
        (EVIDENCE_CONFIRMED_THRESHOLD, "confirmed"),
        (0.0, "pending"),
        (EVIDENCE_ARCHIVE_THRESHOLD, "archive_candidate"),
    ]
    for score, expected in cases:
        # archive case needs a negative score from disputation, not rein.
        if score < 0:
            entry = {"disputation": -score, "disp_last_signal_at": _T0_ISO}
        else:
            entry = _entry(score)
        got = derive_status(entry, _T0)
        if got != expected:
            return False, (
                f"C4 FAIL: score={score} -> status {got!r}, expected {expected!r}"
            )
    return True, "C4 OK: status tiers honour config thresholds"


def check_c5_combo() -> tuple[bool, str]:
    from config import (
        USER_FACT_REINFORCE_COMBO_BONUS,
        USER_FACT_REINFORCE_COMBO_THRESHOLD,
    )
    from tests.testbench.pipeline import evidence_sim

    base = 0.5  # the canonical per-signal reinforce delta
    n = USER_FACT_REINFORCE_COMBO_THRESHOLD + 1  # first signal that triggers bonus
    signals = [{"reinforcement": base, "source": evidence_sim.SOURCE_USER_FACT}
               for _ in range(n)]
    res = evidence_sim.simulate_signal_sequence({}, signals)
    final = res["final_entry"]
    # base*n raw + one bonus (only the signal beyond the threshold count).
    expected = base * n + USER_FACT_REINFORCE_COMBO_BONUS
    if abs(final["reinforcement"] - expected) > _EPS:
        return False, (
            f"C5 FAIL: user_fact combo reinforcement = {final['reinforcement']}, "
            f"expected {expected}"
        )
    if final["user_fact_reinforce_count"] != n:
        return False, (
            f"C5 FAIL: user_fact_reinforce_count = "
            f"{final['user_fact_reinforce_count']}, expected {n}"
        )

    # Non-user_fact source must NOT receive the bonus.
    other = [{"reinforcement": base, "source": "signal"} for _ in range(n)]
    res2 = evidence_sim.simulate_signal_sequence({}, other)
    if abs(res2["final_entry"]["reinforcement"] - base * n) > _EPS:
        return False, (
            "C5 FAIL: non-user_fact source got a combo bonus "
            f"(reinforcement={res2['final_entry']['reinforcement']}, expected {base * n})"
        )
    return True, "C5 OK: combo bonus only after threshold + only for user_fact"


def check_c6_disp_non_negative() -> tuple[bool, str]:
    from memory.evidence import compute_evidence_snapshot

    snap = compute_evidence_snapshot(
        {"disputation": 0.3}, {"disputation": -1.0}, _T0_ISO, "x",
    )
    if snap["disputation"] != 0.0:
        return False, (
            f"C6 FAIL: disputation clamped to {snap['disputation']}, expected 0.0"
        )
    return True, "C6 OK: disputation clamped non-negative"


def check_c7_seed() -> tuple[bool, str]:
    from tests.testbench.pipeline import evidence_sim

    expected = {10: 0.8, 9: 0.6, 8: 0.4, 7: 0.2, 6: 0.0, 5: 0.0, 0: 0.0}
    for imp, want in expected.items():
        got = evidence_sim.seed_from_importance(imp)
        if abs(got - want) > _EPS:
            return False, f"C7 FAIL: seed({imp}) = {got}, expected {want}"
    # non-int falls back to 0.0
    if abs(evidence_sim.seed_from_importance("oops") - 0.0) > _EPS:  # type: ignore[arg-type]
        return False, "C7 FAIL: non-int importance did not fall back to 0.0"
    return True, "C7 OK: importance->initial-seed table correct"


def check_c8_sub_zero() -> tuple[bool, str]:
    from memory.evidence import maybe_mark_sub_zero

    # Negative-score entry (disputation only, age 0).
    entry = {"disputation": 1.0, "disp_last_signal_at": _T0_ISO}
    if maybe_mark_sub_zero(entry, _T0) is not True:
        return False, "C8 FAIL: first sub-zero day not counted"
    if entry.get("sub_zero_days") != 1:
        return False, f"C8 FAIL: sub_zero_days = {entry.get('sub_zero_days')}, expected 1"
    # Same day -> no double count.
    if maybe_mark_sub_zero(entry, _T0) is not False:
        return False, "C8 FAIL: same-day re-count should be False"
    if entry.get("sub_zero_days") != 1:
        return False, "C8 FAIL: sub_zero_days incremented twice in one day"
    # Next day -> counts again.
    if maybe_mark_sub_zero(entry, _T0 + timedelta(days=1)) is not True:
        return False, "C8 FAIL: next-day increment not counted"
    if entry.get("sub_zero_days") != 2:
        return False, f"C8 FAIL: sub_zero_days = {entry.get('sub_zero_days')}, expected 2"

    # Protected entries never increment.
    prot = {"protected": True, "disputation": 100.0, "disp_last_signal_at": _T0_ISO}
    if maybe_mark_sub_zero(prot, _T0) is not False:
        return False, "C8 FAIL: protected entry incremented sub_zero"
    # Non-negative score entries never increment.
    pos = {"reinforcement": 5.0, "rein_last_signal_at": _T0_ISO}
    if maybe_mark_sub_zero(pos, _T0) is not False:
        return False, "C8 FAIL: non-negative entry incremented sub_zero"
    return True, "C8 OK: sub_zero counts once/day, skips protected + non-negative"


def check_c9_funnel() -> tuple[bool, str]:
    from tests.testbench.pipeline import evidence_sim

    signals = [{"reinforcement": 0.5, "source": evidence_sim.SOURCE_USER_FACT}
               for _ in range(3)]
    res = evidence_sim.simulate_signal_sequence({}, signals)
    statuses = [t["status"] for t in res["trace"]]
    # 0.5 -> pending, 1.0 -> confirmed, 2.0 (1.0 + 0.5 + 0.5 bonus) -> promoted
    if statuses != ["pending", "confirmed", "promoted"]:
        return False, (
            f"C9 FAIL: funnel statuses = {statuses}, "
            "expected ['pending','confirmed','promoted']"
        )
    if res["status"] != "promoted":
        return False, f"C9 FAIL: final status = {res['status']!r}, expected 'promoted'"
    return True, "C9 OK: simulate funnel pending->confirmed->promoted"


_CHECKS = [
    check_c1_import,
    check_c2_decay,
    check_c3_protected,
    check_c4_status_tiers,
    check_c5_combo,
    check_c6_disp_non_negative,
    check_c7_seed,
    check_c8_sub_zero,
    check_c9_funnel,
]


def main() -> int:
    print("=" * 66)
    print(" P27 Evidence-Math Contract Smoke (Phase 3 #1 = M)")
    print("=" * 66)
    print(" adapter:  tests/testbench/pipeline/evidence_sim.py")
    print(" upstream: memory/evidence.py (imported directly — wanted coupling)")
    print("")

    failures: list[str] = []
    for check in _CHECKS:
        try:
            ok, msg = check()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"{check.__name__} FAIL: raised {type(exc).__name__}: {exc}"
        print(msg if ok else msg)
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
