"""Reflection IDs, schema defaults, and archive preparation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta


REFLECTION_ARCHIVE_DAYS = 30


def reflection_id_from_facts(source_fact_ids: list[str]) -> str:
    """Return the deterministic ID for a set of source fact IDs."""
    digest = hashlib.sha256()
    for fact_id in sorted(source_fact_ids):
        digest.update(fact_id.encode("utf-8"))
        digest.update(b"\x00")
    return f"ref_{digest.hexdigest()[:16]}"


def refine_reflection_id(text: str, *, now: datetime | None = None) -> str:
    """Return the salted ID used for a newly refined reflection."""
    salt = (now or datetime.now()).isoformat()
    return f"ref_{hashlib.sha1(f'{text}|{salt}'.encode('utf-8')).hexdigest()[:16]}"


def normalize_reflection(entry: dict) -> dict:
    """Fill current reflection schema defaults in-place."""
    defaults = {
        "reinforcement": 0.0,
        "disputation": 0.0,
        "rein_last_signal_at": None,
        "disp_last_signal_at": None,
        "sub_zero_days": 0,
        "sub_zero_last_increment_date": None,
        "user_fact_reinforce_count": 0,
        "absorbed_into": None,
        "last_promote_attempt_at": None,
        "promote_attempt_count": 0,
        "promote_blocked_reason": None,
        "recent_mentions": [],
        "suppress": False,
        "suppressed_at": None,
        "relation_type": None,
        "subject": None,
        "temporal_scope": None,
        "event_when_raw": None,
        "event_start_at": None,
        "event_end_at": None,
        "schema_version": 1,
        "embedding": None,
        "embedding_text_sha256": None,
        "embedding_model_id": None,
        "last_refine_cluster_hash": None,
        "last_refine_at": None,
    }
    for key, value in defaults.items():
        entry.setdefault(key, value)
    return entry


def prepare_save_reflections(
    reflections: list[dict],
    all_on_disk: list[dict],
    *,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Compute ``(merged_main, to_archive, keep_in_main)`` without I/O."""
    active_ids = {item["id"] for item in reflections if "id" in item}
    cutoff = (now or datetime.now()) - timedelta(days=REFLECTION_ARCHIVE_DAYS)
    keep_in_main: list[dict] = []
    to_archive: list[dict] = []
    for item in all_on_disk:
        if item.get("id") in active_ids:
            continue
        status = item.get("status")
        if status in ("promoted", "denied"):
            timestamp = (
                item.get("promoted_at")
                or item.get("denied_at")
                or item.get("created_at", "")
            )
            try:
                if datetime.fromisoformat(timestamp) < cutoff:
                    to_archive.append(item)
                    continue
            except (ValueError, TypeError):
                pass
            keep_in_main.append(item)
        elif status in ("merged", "promote_blocked"):
            keep_in_main.append(item)
    return reflections + keep_in_main, to_archive, keep_in_main


def make_archive_stamper(now_iso: str) -> Callable[[list[dict], str], None]:
    """Build the per-shard callback that stamps archived entries."""

    def stamp(chunk: list[dict], shard_basename: str) -> None:
        for entry in chunk:
            entry["archived_at"] = now_iso
            entry["archive_shard_path"] = shard_basename

    return stamp
