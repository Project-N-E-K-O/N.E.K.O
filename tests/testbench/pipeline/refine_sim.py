"""refine_sim.py — thin testbench adapter over the memory-refine bookkeeping layer.

Scope (UPSTREAM_SYNC_2026-06 Phase 3, RAG item #4 = O)
-----------------------------------------------------
Re-exposes the **semantic-contract** half of ``memory.refine`` — the
deterministic cluster-bookkeeping pure helpers (annotate / strip / hash /
freshness / starvation ordering / render) — without the **runtime-mechanism**
half (cosine clustering needs the ONNX ``EmbeddingService``; ``refine_pass`` /
``_resolve_cluster`` need the correction-tier LLM call). Both runtime layers are
out-of-scope per Phase 3.0.

Wraps production pure helpers:
  - ``memory.refine.annotate_entry`` / ``strip_refine_metadata``
  - ``memory.refine.VALID_REFINE_ACTIONS`` (constant)
  - ``MemoryRefineEngine._cluster_hash``        (sha1 of sorted non-fact ids)
  - ``MemoryRefineEngine._all_stamped_fresh``   (revisit-window freshness gate)
  - ``MemoryRefineEngine._cluster_starvation_key`` (min last_refine_at, '' first)
  - ``MemoryRefineEngine._render_cluster``      (prompt line rendering)

The staticmethods are taken off the class directly — we never construct
``MemoryRefineEngine`` (its ``__init__`` grabs the embedding-service singleton,
which is runtime mechanism). Lazy import: ``memory/__init__`` pulls heavy deps
at package-import time. Same wanted-coupling rationale as ``evidence_sim`` /
``recall_fusion``: a formula change upstream should break the paired smoke.
"""
from __future__ import annotations

from typing import Any


def _refine():
    from memory import refine as _mod  # noqa: WPS433 — intentional lazy import
    return _mod


def _engine_cls():
    from memory.refine import MemoryRefineEngine  # noqa: WPS433
    return MemoryRefineEngine


def valid_actions() -> frozenset:
    """The four legal refine actions — production's ``VALID_REFINE_ACTIONS``."""
    return _refine().VALID_REFINE_ACTIONS


def annotate(entry: dict, *, type_: str, entity: str) -> dict:
    """Tag a candidate row with refine type/entity — ``annotate_entry``."""
    return _refine().annotate_entry(entry, type_=type_, entity=entity)


def strip(entry: dict) -> dict:
    """Remove refine internal markers — ``strip_refine_metadata``."""
    return _refine().strip_refine_metadata(entry)


def cluster_hash(cluster: list[dict]) -> str:
    """sha1 of sorted non-fact member ids — ``_cluster_hash``."""
    return _engine_cls()._cluster_hash(cluster)  # noqa: SLF001


def all_stamped_fresh(cluster: list[dict], cluster_hash_value: str) -> bool:
    """Whether every non-fact member is stamped fresh on this hash within the
    revisit window — ``_all_stamped_fresh``."""
    return _engine_cls()._all_stamped_fresh(cluster, cluster_hash_value)  # noqa: SLF001


def starvation_key(cluster: list[dict]) -> str:
    """Cluster sort key (smallest non-fact last_refine_at, '' first) —
    ``_cluster_starvation_key``."""
    return _engine_cls()._cluster_starvation_key(cluster)  # noqa: SLF001


def render_cluster(cluster: list[dict]) -> str:
    """Render cluster members as numbered prompt lines — ``_render_cluster``."""
    return _engine_cls()._render_cluster(cluster)  # noqa: SLF001


__all__ = [
    "all_stamped_fresh",
    "annotate",
    "cluster_hash",
    "render_cluster",
    "starvation_key",
    "strip",
    "valid_actions",
]
