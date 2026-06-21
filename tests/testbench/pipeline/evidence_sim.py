"""evidence_sim.py — thin testbench adapter over ``memory.evidence``.

Scope (UPSTREAM_SYNC_2026-06 Phase 3, RAG item #1 = M)
------------------------------------------------------
Re-exposes the evidence-RFC **semantic-contract** layer (pure math:
read-time decay, derived status tiers, the signal→snapshot accumulator,
the importance→initial-seed table) so the testbench can *simulate* an
evidence signal sequence and read out the resulting score / status —
**without** pulling in any of the evidence-RFC **runtime-mechanism**
layer (``event_log`` / ``outbox`` / ``embedding_worker`` /
``memory_server`` background loops / sqlite ``timeindex``), all of which
are explicitly out-of-scope per the Phase 3.0 design gate.

Why import from ``memory.evidence`` directly
--------------------------------------------
This is the **wanted coupling** (skill ``semantic-contract-vs-runtime-
mechanism`` anti-pattern C): if upstream changes a decay formula, a
threshold, or the combo rule, the paired smoke
(``p27_evidence_math_smoke.py``) should break — that is the early signal
that the testbench's evidence assumptions went stale. We do **not** keep
a byte-copy here (contrast ``avatar_dedupe.py`` + L30): ``memory.evidence``
is a pure module that only imports ``config`` constants, and the testbench
already imports ``memory.reflection`` / ``memory.persona`` at runtime
(see ``memory_runner``), so importing ``memory.evidence`` carries no new
side-effect risk.

The import is **lazy inside functions** (mirroring ``memory_runner``)
because ``memory/__init__`` eagerly pulls heavy deps (sqlite / embeddings)
at package-import time; keeping the import lazy means merely importing this
adapter module stays cheap.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Source string that triggers the user_fact reinforce combo in
# ``compute_evidence_snapshot``. Mirrored here (not imported) because
# ``memory.evidence`` keeps it private (``_SOURCE_USER_FACT``) to dodge a
# circular import; the value is part of the public signal contract.
SOURCE_USER_FACT = "user_fact"

# The field set ``compute_evidence_snapshot`` returns — echoed for callers
# that want to assert the snapshot shape without importing the private impl.
EVIDENCE_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "reinforcement",
    "disputation",
    "rein_last_signal_at",
    "disp_last_signal_at",
    "sub_zero_days",
    "user_fact_reinforce_count",
)

# Deterministic anchor for simulations so traces are reproducible.
_DEFAULT_BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def _evidence():
    """Lazily resolve the ``memory.evidence`` module (single chokepoint)."""
    from memory import evidence as _ev  # noqa: WPS433 — intentional lazy import
    return _ev


def describe_entry(entry: dict, now: datetime | None = None) -> dict[str, Any]:
    """Read-time snapshot of an evidence entry at ``now``.

    Returns ``{effective_reinforcement, effective_disputation, score,
    status}`` — the four read-side values the production decay model
    derives. ``now`` defaults to wall-clock; pass an explicit ``datetime``
    in tests so decay is deterministic.
    """
    ev = _evidence()
    now = now or datetime.now()
    return {
        "effective_reinforcement": ev.effective_reinforcement(entry, now),
        "effective_disputation": ev.effective_disputation(entry, now),
        "score": ev.evidence_score(entry, now),
        "status": ev.derive_status(entry, now),
    }


def apply_signal(
    entry: dict,
    *,
    reinforcement: float = 0.0,
    disputation: float = 0.0,
    source: str = "",
    now_iso: str | None = None,
) -> dict:
    """Apply one signal delta and return a NEW merged entry dict.

    Delegates the math (independent rein/disp clocks, disputation
    non-negativity, user_fact combo bonus) entirely to
    ``compute_evidence_snapshot``; this wrapper only merges the snapshot
    back onto a copy of ``entry`` so callers can chain signals.
    """
    ev = _evidence()
    now_iso = now_iso or datetime.now().isoformat()
    snapshot = ev.compute_evidence_snapshot(
        entry,
        {"reinforcement": float(reinforcement), "disputation": float(disputation)},
        now_iso,
        source,
    )
    merged = dict(entry)
    merged.update(snapshot)
    return merged


def simulate_signal_sequence(
    entry: dict,
    signals: list[dict[str, Any]],
    *,
    base_time: datetime | None = None,
    step_days: float = 0.0,
) -> dict[str, Any]:
    """Apply a sequence of signals and return the funnel trace.

    Each ``signal`` is ``{reinforcement?, disputation?, source?}``.
    ``step_days`` advances the clock between signals so read-time decay can
    be exercised deterministically (0 = all signals land at ``base_time``).

    Returns ``{final_entry, score, status, trace}`` where ``trace[i]`` is
    ``{i, score, status}`` evaluated at the instant signal ``i`` landed —
    i.e. the same "how many reinforces until confirmed/promoted" funnel a
    tester would watch.
    """
    ev = _evidence()
    base_time = base_time or _DEFAULT_BASE_TIME
    current = dict(entry)
    trace: list[dict[str, Any]] = []
    for i, sig in enumerate(signals):
        at = base_time + timedelta(days=step_days * i)
        current = apply_signal(
            current,
            reinforcement=float(sig.get("reinforcement", 0.0) or 0.0),
            disputation=float(sig.get("disputation", 0.0) or 0.0),
            source=str(sig.get("source", "")),
            now_iso=at.isoformat(),
        )
        trace.append({
            "i": i,
            "score": ev.evidence_score(current, at),
            "status": ev.derive_status(current, at),
        })
    final_time = base_time + timedelta(
        days=step_days * max(0, len(signals) - 1),
    )
    return {
        "final_entry": current,
        "score": ev.evidence_score(current, final_time),
        "status": ev.derive_status(current, final_time),
        "trace": trace,
    }


def seed_from_importance(max_importance: int) -> float:
    """Initial reinforcement seed for a reflection given its max source-fact
    importance (thin pass-through to the production table)."""
    return _evidence().initial_reinforcement_from_importance(max_importance)


__all__ = [
    "SOURCE_USER_FACT",
    "EVIDENCE_SNAPSHOT_FIELDS",
    "apply_signal",
    "describe_entry",
    "seed_from_importance",
    "simulate_signal_sequence",
]
