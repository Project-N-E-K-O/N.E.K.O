# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Read-only aggregator powering the Memory Browser Imprint, Profile/Correction,
and Multi-Dimensional Health Diagnostics views.

Design invariants:
- Pure read-only: never mutates memory stores or triggers lazy migrations.
- Non-blocking: ``abuild_memory_overview`` runs the full disk/SQLite/vector
  pass inside ``asyncio.to_thread``.
- Locale-neutral payload: emits semantic keys and structured metrics instead of
  hardcoded user-facing strings so the frontend renders all 8 locales cleanly.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from memory.evidence import evidence_score
from memory.evidence_analytics import funnel_counts, to_naive_local
from memory.user_directives import get_user_directives_manager
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

try:
    from memory.embeddings import decode_embedding
except ImportError:
    from memory.embeddings_fallback import decode_embedding

logger = get_module_logger(__name__, "Memory")

_META_ENTITY = "__meta__"
_CORE_ENTITIES = ("master", "neko", "relationship")
_IMPRINT_CACHE_FILENAME = "imprint_notes_cache.json"

# Relation-type → 6 user-facing imprint dimensions (for the SVG radar chart).
_RELATION_TO_DIMENSION: dict[str, str] = {
    "preference": "preferences",
    "habit": "habits",
    "identity": "personality",
    "trait": "personality",
    "emotional": "personality",
    "boundary": "agreements",
    "agreement": "agreements",
    "dynamic": "agreements",
    "tension": "agreements",
    "shared_memory": "shared_memories",
    "milestone": "shared_memories",
    "self_awareness": "growth",
    "learned": "growth",
    "role_note": "growth",
}

_DIMENSION_ORDER = (
    "preferences",
    "habits",
    "personality",
    "shared_memories",
    "agreements",
    "growth",
)


def _read_json_safe(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.debug("[MemoryAnalytics] failed to read %s: %s", path.name, exc)
        return default


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return to_naive_local(datetime.fromisoformat(text))
    except (ValueError, TypeError):
        return None


def _read_timeindex_stats(db_path: Path) -> dict[str, Any]:
    """Lightweight SQLite aggregate (COUNT/MIN/MAX only; never scans rows)."""
    if not db_path.is_file():
        return {
            "exists": False,
            "message_count": 0,
            "first_chat_at": None,
            "last_chat_at": None,
        }
    conn = None
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='time_indexed_original'"
        )
        if cur.fetchone() is None:
            return {
                "exists": True,
                "message_count": 0,
                "first_chat_at": None,
                "last_chat_at": None,
            }
        cur.execute(
            "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM time_indexed_original"
        )
        row = cur.fetchone() or (0, None, None)
        return {
            "exists": True,
            "message_count": int(row[0] or 0),
            "first_chat_at": str(row[1]) if row[1] else None,
            "last_chat_at": str(row[2]) if row[2] else None,
        }
    except Exception as exc:
        logger.debug("[MemoryAnalytics] timeindex stats failed: %s", exc)
        return {
            "exists": True,
            "message_count": 0,
            "first_chat_at": None,
            "last_chat_at": None,
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _count_recent_turns(recent_path: Path) -> int:
    raw = _read_json_safe(recent_path, [])
    if not isinstance(raw, list):
        return 0
    count = 0
    for item in raw:
        if isinstance(item, dict) and item.get("type") in ("human", "ai"):
            count += 1
    return count


def _strength_level(score: float, reinforce_count: int = 0) -> int:
    """Map continuous evidence_score to a calm 1..3 visual strength tier."""
    if math.isinf(score) or score >= 2.0 or reinforce_count >= 3:
        return 3
    if score >= 0.8 or reinforce_count >= 1:
        return 2
    return 1


def _text_bigrams(text: str) -> set[str]:
    compact = "".join(ch for ch in (text or "").lower() if not ch.isspace())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _inspect_embeddings(items: list[tuple[str, dict]]) -> dict[str, Any]:
    """Audit embedding coverage, staleness, corruption, and dimension splits."""
    total = len(items)
    embedded = 0
    missing = 0
    stale = 0
    corrupt = 0
    dims: dict[int, int] = {}
    decoded_vectors: list[tuple[str, str, str, str, Any]] = []

    for kind, entry in items:
        text = str(entry.get("text") or "").strip()
        eid = str(entry.get("id") or "")
        entity = str(entry.get("entity") or entry.get("_entity") or "master")
        emb = entry.get("embedding")
        if not emb:
            missing += 1
            continue
        vec = decode_embedding(emb)
        if vec is None or getattr(vec, "size", 0) == 0:
            corrupt += 1
            continue
        embedded += 1
        dim = int(vec.size)
        dims[dim] = dims.get(dim, 0) + 1
        emb_sha = entry.get("embedding_text_sha256")
        emb_legacy_hash = entry.get("embedding_text_hash")
        if text:
            full_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if emb_sha:
                if str(emb_sha).strip() != full_sha:
                    stale += 1
            elif emb_legacy_hash:
                if str(emb_legacy_hash).strip()[:16] != full_sha[:16]:
                    stale += 1
        if eid and text:
            decoded_vectors.append((kind, entity, eid, text, vec))

    primary_dim = max(dims.items(), key=lambda kv: kv[1])[0] if dims else None
    other_dim_count = sum(c for d, c in dims.items() if d != primary_dim)
    coverage = round((embedded / total), 4) if total else 1.0
    return {
        "total": total,
        "embedded": embedded,
        "missing": missing,
        "stale": stale,
        "corrupt": corrupt,
        "coverage": coverage,
        "primary_dim": primary_dim,
        "dimension_split": len(dims) > 1,
        "other_dim_count": other_dim_count,
        "_decoded": decoded_vectors,
    }


def _find_near_duplicates(
    decoded_vectors: list[tuple[str, str, str, str, Any]],
    fallback_items: list[tuple[str, str, str, str]],
    *,
    cosine_threshold: float = 0.90,
    jaccard_threshold: float = 0.78,
    max_pairs: int = 12,
) -> list[dict[str, Any]]:
    """Find high-confidence near-duplicate pairs (vector first, bigram fallback)."""
    pairs: list[dict[str, Any]] = []
    # Prefer vectors in the primary dimension (bounded to 400 items for <10ms runtime).
    if len(decoded_vectors) >= 2:
        try:
            import numpy as np

            dim_counts: dict[int, int] = {}
            for item in decoded_vectors:
                d = int(item[4].size)
                dim_counts[d] = dim_counts.get(d, 0) + 1
            primary_dim = max(dim_counts.items(), key=lambda kv: kv[1])[0]
            usable = [item for item in decoded_vectors if int(item[4].size) == primary_dim][:400]
            if len(usable) >= 2:
                mat = np.stack([item[4].astype(np.float32) for item in usable])
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms = np.where(norms > 1e-8, norms, 1.0)
                mat = mat / norms
                sim = mat @ mat.T
                n = len(usable)
                raw_hits: list[tuple[float, int, int]] = []
                for i in range(n):
                    row = sim[i]
                    for j in range(i + 1, n):
                        score = float(row[j])
                        if score >= cosine_threshold:
                            # Only propose merges within the same kind & entity with distinct IDs.
                            if (
                                usable[i][0] == usable[j][0]
                                and usable[i][1] == usable[j][1]
                                and usable[i][2] != usable[j][2]
                            ):
                                raw_hits.append((score, i, j))
                raw_hits.sort(key=lambda t: -t[0])
                for score, i, j in raw_hits[:max_pairs]:
                    pairs.append(
                        {
                            "kind": usable[i][0],
                            "entity": usable[i][1],
                            "a_id": usable[i][2],
                            "a_text": usable[i][3],
                            "b_id": usable[j][2],
                            "b_text": usable[j][3],
                            "similarity": round(score, 3),
                            "method": "embedding",
                        }
                    )
        except Exception as exc:
            logger.debug("[MemoryAnalytics] vector duplicate scan skipped: %s", exc)

    if len(pairs) >= max_pairs:
        return pairs[:max_pairs]

    seen_pair_keys = {
        (p["kind"], p["entity"], min(p["a_id"], p["b_id"]), max(p["a_id"], p["b_id"]))
        for p in pairs
    }

    # Lightweight text bigram fallback/supplement for items without comparable embeddings.
    capped = fallback_items[:200]
    shingles = [_text_bigrams(item[3]) for item in capped]
    hits: list[tuple[float, int, int]] = []
    for i in range(len(capped)):
        sa = shingles[i]
        if len(sa) < 3:
            continue
        for j in range(i + 1, len(capped)):
            if capped[i][0] != capped[j][0] or capped[i][1] != capped[j][1]:
                continue
            if capped[i][2] == capped[j][2]:
                continue
            pk = (
                capped[i][0],
                capped[i][1],
                min(capped[i][2], capped[j][2]),
                max(capped[i][2], capped[j][2]),
            )
            if pk in seen_pair_keys:
                continue
            sb = shingles[j]
            if len(sb) < 3:
                continue
            inter = len(sa & sb)
            if inter == 0:
                continue
            jacc = inter / len(sa | sb)
            if jacc >= jaccard_threshold:
                hits.append((jacc, i, j))
    hits.sort(key=lambda t: -t[0])
    for score, i, j in hits[:max_pairs]:
        pairs.append(
            {
                "kind": capped[i][0],
                "entity": capped[i][1],
                "a_id": capped[i][2],
                "a_text": capped[i][3],
                "b_id": capped[j][2],
                "b_text": capped[j][3],
                "similarity": round(score, 3),
                "method": "text",
            }
        )
    return pairs


def load_imprint_note_cache(
    character_name: str,
    *,
    memory_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Read cached warm relationship note if present."""
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    cache_path = base / character_name / _IMPRINT_CACHE_FILENAME
    data = _read_json_safe(cache_path, None)
    if not isinstance(data, dict):
        return None
    summary = str(data.get("summary") or "").strip()
    commentary = str(data.get("commentary") or "").strip()
    blessing = str(data.get("blessing") or "").strip()
    if not (summary or commentary or blessing):
        return None
    return {
        "summary": summary,
        "commentary": commentary,
        "blessing": blessing,
        "generated_at": data.get("generated_at"),
        "language": data.get("language"),
        "source": "llm_cached",
    }


def _compute_stage_key(
    companion_days: int,
    imprint_persona_count: int,
    facts_total: int,
    total_messages: int,
) -> str:
    total_signal = imprint_persona_count * 4 + facts_total + (total_messages // 10)
    if total_signal == 0 and companion_days <= 1:
        return "fresh"
    if companion_days >= 30 or imprint_persona_count >= 15 or total_signal >= 90:
        return "deep_bond"
    if companion_days >= 7 or imprint_persona_count >= 6 or total_signal >= 30:
        return "familiar"
    return "budding"


def _compute_dimension_status(issues_bad: int, issues_warn: int) -> str:
    if issues_bad > 0:
        return "bad"
    if issues_warn > 0:
        return "warn"
    return "good"


def build_memory_overview(
    character_name: str,
    *,
    memory_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Synchronously aggregate imprint, editable memories, and 6-axis health diagnostics."""
    now_dt = to_naive_local(now) if now is not None else datetime.now()
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    char_dir = base / character_name

    persona_raw = _read_json_safe(char_dir / "persona.json", {})
    if not isinstance(persona_raw, dict):
        persona_raw = {}
    corrections_raw = _read_json_safe(char_dir / "persona_corrections.json", [])
    if not isinstance(corrections_raw, list):
        corrections_raw = []
    reflections_raw = _read_json_safe(char_dir / "reflections.json", [])
    if not isinstance(reflections_raw, list):
        reflections_raw = []
    facts_raw = _read_json_safe(char_dir / "facts.json", [])
    if not isinstance(facts_raw, list):
        facts_raw = []
    facts_archive_raw = _read_json_safe(char_dir / "facts_archive.json", [])
    if not isinstance(facts_archive_raw, list):
        facts_archive_raw = []

    time_stats = _read_timeindex_stats(char_dir / "time_indexed.db")
    recent_turns = _count_recent_turns(char_dir / "recent.json")

    # Gather directives (live unexpired entries).
    try:
        directives_active = get_user_directives_manager().get_active(
            character_name, limit=50,
        )
    except Exception:
        directives_active = []

    # ── Normalize persona entries ──────────────────────────────────
    about_you: list[dict[str, Any]] = []
    relationship_items: list[dict[str, Any]] = []
    growth_items: list[dict[str, Any]] = []
    character_card_items: list[dict[str, Any]] = []
    suppressed_items: list[dict[str, Any]] = []
    evolution_items: list[dict[str, Any]] = []

    persona_all_non_protected: list[dict[str, Any]] = []
    persona_ids_seen: set[str] = set()
    duplicate_ids: list[str] = []
    unattributed_persona = 0
    negative_evidence_count = 0
    sub_zero_entries = 0
    suppressed_count = 0
    recent_30d_cutoff = now_dt - timedelta(days=30)
    recent_30d_activity = 0

    all_fact_ids: set[str] = set()
    for f in facts_raw:
        if isinstance(f, dict) and f.get("id"):
            fid = str(f["id"])
            if fid in all_fact_ids:
                duplicate_ids.append(fid)
            all_fact_ids.add(fid)
    for f in facts_archive_raw:
        if isinstance(f, dict) and f.get("id"):
            all_fact_ids.add(str(f["id"]))

    reflection_ids: set[str] = set()
    for r in reflections_raw:
        if isinstance(r, dict) and r.get("id"):
            rid = str(r["id"])
            if rid in reflection_ids:
                duplicate_ids.append(rid)
            reflection_ids.add(rid)

    all_dates: list[datetime] = []
    first_db_dt = _parse_dt(time_stats.get("first_chat_at"))
    if first_db_dt is not None:
        all_dates.append(first_db_dt)

    embed_audit_pool: list[tuple[str, dict]] = []
    dedup_fallback_pool: list[tuple[str, str, str, str]] = []

    for entity_key, section in persona_raw.items():
        if entity_key == _META_ENTITY or not isinstance(section, dict):
            continue
        if entity_key not in _CORE_ENTITIES:
            continue
        raw_facts = section.get("facts")
        if not isinstance(raw_facts, list):
            continue
        for entry in raw_facts:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            eid = str(entry.get("id") or "")
            if eid:
                if eid in persona_ids_seen:
                    duplicate_ids.append(eid)
                persona_ids_seen.add(eid)
            is_protected = bool(entry.get("protected")) or entry.get("source") == "character_card"
            perm_marker = entry.get("permanent_suppression")
            is_perm_suppressed = bool(perm_marker)
            retirement_reason = (
                str(perm_marker.get("reason") or "")
                if isinstance(perm_marker, dict)
                else ""
            )
            score = evidence_score(entry, now_dt)
            rein_count = int(entry.get("user_fact_reinforce_count") or 0)
            vh = entry.get("version_history") if isinstance(entry.get("version_history"), list) else []
            created_dt = _parse_dt(entry.get("created_at") or entry.get("promoted_at"))
            if created_dt is not None:
                all_dates.append(created_dt)
                if created_dt >= recent_30d_cutoff:
                    recent_30d_activity += 1

            card_item = {
                "kind": "persona",
                "id": eid,
                "entity": entity_key,
                "text": text,
                "source": str(entry.get("source") or "unknown"),
                "protected": is_protected,
                "user_pinned": bool(entry.get("user_pinned")),
                "suppress": bool(entry.get("suppress")),
                "permanent_suppression": is_perm_suppressed,
                "retirement_reason": retirement_reason,
                "suppressed_at": entry.get("suppressed_at"),
                "strength": _strength_level(score, rein_count),
                "reinforce_count": rein_count,
                "created_at": entry.get("created_at") or entry.get("promoted_at"),
                "version_history": [
                    {
                        "index": idx,
                        "text": str(v.get("text") or ""),
                        "replaced_at": v.get("replaced_at"),
                        "reason": str(v.get("reason") or ""),
                    }
                    for idx, v in enumerate(vh)
                    if isinstance(v, dict) and str(v.get("text") or "").strip()
                ],
            }

            if is_protected:
                character_card_items.append(card_item)
                continue

            if is_perm_suppressed:
                suppressed_count += 1
                suppressed_items.append(card_item)
                continue

            persona_all_non_protected.append(entry)
            embed_entry = dict(entry)
            embed_entry["_entity"] = entity_key
            embed_audit_pool.append(("persona", embed_entry))
            if eid:
                dedup_fallback_pool.append(("persona", entity_key, eid, text))

            if not math.isinf(score) and score < 0:
                negative_evidence_count += 1
            if int(entry.get("sub_zero_days") or 0) > 0:
                sub_zero_entries += 1
            if entry.get("source") == "reflection" and not entry.get("source_id"):
                unattributed_persona += 1
            if card_item["suppress"]:
                suppressed_count += 1
                suppressed_items.append(card_item)

            if entity_key == "master":
                about_you.append(card_item)
            elif entity_key == "relationship":
                relationship_items.append(card_item)
            elif entity_key == "neko":
                growth_items.append(card_item)

            if card_item["version_history"]:
                latest_prev = card_item["version_history"][-1]
                evolution_items.append(
                    {
                        "id": eid,
                        "entity": entity_key,
                        "previous_text": latest_prev["text"],
                        "current_text": text,
                        "changed_at": latest_prev.get("replaced_at"),
                        "reason": latest_prev.get("reason") or "",
                    }
                )

    # ── Normalize reflections ──────────────────────────────────────
    pending_reflections: list[dict[str, Any]] = []
    confirmed_reflections_count = 0
    stalled_pending_reflections = 0
    sourceless_reflections = 0
    dangling_fact_refs = 0
    dim_counts: dict[str, int] = {k: 0 for k in _DIMENSION_ORDER}
    milestones: list[dict[str, Any]] = []
    seven_days_ago = now_dt - timedelta(days=7)

    for r in reflections_raw:
        if not isinstance(r, dict):
            continue
        entity = str(r.get("entity") or "master")
        if entity not in _CORE_ENTITIES:
            continue
        status = str(r.get("status") or "pending")
        if status in ("archived", "merged", "denied"):
            continue
        text = str(r.get("text") or "").strip()
        rid = str(r.get("id") or "")
        if not text:
            continue
        created_dt = _parse_dt(r.get("created_at"))
        if created_dt is not None:
            all_dates.append(created_dt)
            if created_dt >= recent_30d_cutoff:
                recent_30d_activity += 1

        embed_audit_pool.append(("reflection", r))
        if rid:
            dedup_fallback_pool.append(("reflection", entity, rid, text))

        rel_type = str(r.get("relation_type") or "")
        dim = _RELATION_TO_DIMENSION.get(rel_type)
        if dim:
            dim_counts[dim] += 1
        elif entity == "neko":
            dim_counts["growth"] += 1
        elif entity == "relationship":
            dim_counts["shared_memories"] += 1

        src_ids = r.get("source_fact_ids") if isinstance(r.get("source_fact_ids"), list) else []
        if not src_ids:
            sourceless_reflections += 1
        else:
            for sid in src_ids:
                if str(sid) not in all_fact_ids:
                    dangling_fact_refs += 1

        score = evidence_score(r, now_dt)
        if not math.isinf(score) and score < 0:
            negative_evidence_count += 1
        if int(r.get("sub_zero_days") or 0) > 0:
            sub_zero_entries += 1

        rein_count = int(r.get("user_fact_reinforce_count") or 0)
        item = {
            "kind": "reflection",
            "id": rid,
            "entity": entity,
            "text": text,
            "status": status,
            "relation_type": rel_type,
            "temporal_scope": str(r.get("temporal_scope") or ""),
            "strength": _strength_level(score, rein_count),
            "reinforce_count": rein_count,
            "created_at": r.get("created_at"),
            "suppress": bool(r.get("suppress")),
        }
        if status == "pending":
            pending_reflections.append(item)
            if created_dt is not None and created_dt < seven_days_ago:
                stalled_pending_reflections += 1
        elif status in ("confirmed", "promoted"):
            confirmed_reflections_count += 1
            if entity == "master":
                about_you.append(item)
            elif entity == "relationship":
                relationship_items.append(item)
            elif entity == "neko":
                growth_items.append(item)

        if rel_type in ("milestone", "shared_memory", "agreement"):
            milestones.append(
                {
                    "id": rid,
                    "kind": "reflection",
                    "category": rel_type,
                    "entity": entity,
                    "text": text,
                    "date": r.get("event_start_at") or r.get("created_at"),
                }
            )

    # ── Normalize facts ────────────────────────────────────────────
    unabsorbed_facts = 0
    low_importance_facts = 0
    for f in facts_raw:
        if not isinstance(f, dict):
            continue
        entity = str(f.get("entity") or "master")
        if entity not in _CORE_ENTITIES:
            continue
        text = str(f.get("text") or "").strip()
        fid = str(f.get("id") or "")
        if not text:
            continue
        created_dt = _parse_dt(f.get("created_at"))
        if created_dt is not None:
            all_dates.append(created_dt)
            if created_dt >= recent_30d_cutoff:
                recent_30d_activity += 1

        embed_audit_pool.append(("fact", f))
        if not f.get("absorbed"):
            unabsorbed_facts += 1
        imp = int(f.get("importance") or 5)
        if imp <= 3:
            low_importance_facts += 1
        if imp >= 8:
            milestones.append(
                {
                    "id": fid,
                    "kind": "fact",
                    "category": "highlight",
                    "entity": entity,
                    "text": text,
                    "date": f.get("event_start_at") or f.get("created_at"),
                }
            )
            if not f.get("absorbed"):
                fact_card = {
                    "kind": "fact",
                    "id": fid,
                    "entity": entity,
                    "text": text,
                    "importance": imp,
                    "strength": 2 if imp >= 9 else 1,
                    "created_at": f.get("created_at"),
                }
                if entity == "master":
                    about_you.append(fact_card)
                elif entity == "relationship":
                    relationship_items.append(fact_card)
                elif entity == "neko":
                    growth_items.append(fact_card)

    # Contribute non-protected persona entries into the radar chart so users with
    # persona impressions (even before reflections carry relation_type) see a rich shape.
    master_persona_count = len([x for x in about_you if x["kind"] == "persona"])
    dim_counts["preferences"] += max(0, master_persona_count // 2)
    dim_counts["personality"] += max(0, master_persona_count - (master_persona_count // 2))
    dim_counts["shared_memories"] += len([x for x in relationship_items if x["kind"] == "persona"])
    dim_counts["growth"] += len([x for x in growth_items if x["kind"] == "persona"])

    # ── Pending corrections ────────────────────────────────────────
    pending_corrections: list[dict[str, Any]] = []
    for idx, item in enumerate(corrections_raw):
        if not isinstance(item, dict):
            continue
        old_text = str(item.get("old_text") or "").strip()
        new_text = str(item.get("new_text") or "").strip()
        if not (old_text or new_text):
            continue
        pending_corrections.append(
            {
                "index": idx,
                "correction_id": str(item.get("correction_id") or "").strip(),
                "entity": str(item.get("entity") or "master"),
                "scope": str(item.get("scope") or ""),
                "old_text": old_text,
                "new_text": new_text,
                "queued_at": item.get("queued_at") or item.get("created_at"),
                "reason": str(item.get("reason") or ""),
            }
        )

    # ── Embedding & near-duplicate audit ───────────────────────────
    emb_audit = _inspect_embeddings(embed_audit_pool)
    near_duplicates = _find_near_duplicates(
        emb_audit["_decoded"],
        dedup_fallback_pool,
    )

    # ── Funnel metrics (last 30 days) ──────────────────────────────
    try:
        funnel_30d = funnel_counts(character_name, recent_30d_cutoff, now_dt)
    except Exception:
        funnel_30d = {}

    # ── Companion timeline stats ───────────────────────────────────
    first_dt = min(all_dates) if all_dates else None
    companion_days = max(1, (now_dt - first_dt).days + 1) if first_dt is not None else 0
    total_messages = int(time_stats.get("message_count") or 0)
    if total_messages == 0 and recent_turns > 0:
        total_messages = recent_turns
    facts_total = len(facts_raw) + len(facts_archive_raw)
    imprint_persona_count = len(persona_all_non_protected)
    pending_attention_count = (
        len(pending_corrections) + len(pending_reflections) + len(near_duplicates)
    )

    # Sort milestones & evolution newest first, cap for crisp presentation.
    milestones.sort(key=lambda m: str(m.get("date") or ""), reverse=True)
    evolution_items.sort(key=lambda e: str(e.get("changed_at") or ""), reverse=True)

    # ── Warm summary (deterministic + cached LLM note if any) ──────
    stage_key = _compute_stage_key(
        companion_days, imprint_persona_count, facts_total, total_messages,
    )
    ranked_dims = sorted(
        ((k, dim_counts[k]) for k in _DIMENSION_ORDER),
        key=lambda kv: (-kv[1], _DIMENSION_ORDER.index(kv[0])),
    )
    top_dimensions = [k for k, v in ranked_dims if v > 0][:3]
    highlight_snippets = [m["text"] for m in milestones[:3]] or [
        x["text"] for x in about_you[:2]
    ]
    cached_note = load_imprint_note_cache(character_name, memory_dir=base)

    # ── 6-Dimension Technical Health Diagnostics ───────────────────
    # 1. Consistency
    cons_findings: list[dict[str, Any]] = []
    if pending_corrections:
        cons_findings.append(
            {"code": "N1", "severity": "warn", "count": len(pending_corrections)}
        )
    if negative_evidence_count:
        cons_findings.append(
            {"code": "B1", "severity": "warn", "count": negative_evidence_count}
        )
    if sub_zero_entries:
        cons_findings.append(
            {"code": "B3", "severity": "info", "count": sub_zero_entries}
        )
    cons_score = max(
        0, 100 - len(pending_corrections) * 12 - negative_evidence_count * 8
    )

    # 2. Redundancy
    red_findings: list[dict[str, Any]] = []
    if near_duplicates:
        red_findings.append(
            {
                "code": "A1",
                "severity": "warn" if len(near_duplicates) >= 3 else "info",
                "count": len(near_duplicates),
            }
        )
    red_score = max(0, 100 - len(near_duplicates) * 10)

    # 3. Embedding & Retrieval Readiness
    emb_findings: list[dict[str, Any]] = []
    if emb_audit["corrupt"] > 0:
        emb_findings.append({"code": "E3", "severity": "bad", "count": emb_audit["corrupt"]})
    if emb_audit["dimension_split"]:
        emb_findings.append({"code": "E4", "severity": "bad", "count": emb_audit["other_dim_count"]})
    if emb_audit["stale"] > 0:
        emb_findings.append({"code": "E2", "severity": "warn", "count": emb_audit["stale"]})
    if emb_audit["missing"] > 0 and emb_audit["total"] > 0:
        sev = "warn" if emb_audit["coverage"] < 0.7 else "info"
        emb_findings.append({"code": "E1", "severity": sev, "count": emb_audit["missing"]})
    emb_score = max(
        0,
        int(round(emb_audit["coverage"] * 100))
        - emb_audit["corrupt"] * 15
        - (20 if emb_audit["dimension_split"] else 0)
        - min(20, emb_audit["stale"] * 4),
    )

    # 4. Structure & Provenance Integrity
    struct_findings: list[dict[str, Any]] = []
    if duplicate_ids:
        struct_findings.append({"code": "ID-DUP", "severity": "bad", "count": len(duplicate_ids)})
    if dangling_fact_refs > 0:
        struct_findings.append({"code": "D4", "severity": "warn", "count": dangling_fact_refs})
    if sourceless_reflections > 0:
        struct_findings.append({"code": "D1", "severity": "info", "count": sourceless_reflections})
    if unattributed_persona > 0:
        struct_findings.append({"code": "D2", "severity": "info", "count": unattributed_persona})
    struct_score = max(
        0,
        100
        - len(duplicate_ids) * 25
        - min(30, dangling_fact_refs * 6)
        - min(15, sourceless_reflections * 3),
    )

    # 5. Pipeline Health
    pipe_findings: list[dict[str, Any]] = []
    if stalled_pending_reflections > 0:
        pipe_findings.append({"code": "F3", "severity": "warn", "count": stalled_pending_reflections})
    if unabsorbed_facts >= 25:
        pipe_findings.append({"code": "F2", "severity": "warn", "count": unabsorbed_facts})
    elif unabsorbed_facts >= 12:
        pipe_findings.append({"code": "F2", "severity": "info", "count": unabsorbed_facts})
    synth_30d = int(funnel_30d.get("reflections_synthesized") or 0)
    denied_30d = int(funnel_30d.get("reflections_denied") or 0)
    promoted_30d = int(funnel_30d.get("reflections_promoted") or 0)
    if synth_30d >= 5 and (denied_30d / max(1, synth_30d)) >= 0.4:
        pipe_findings.append({"code": "F4", "severity": "warn", "count": denied_30d})
    pipe_score = max(
        0,
        100
        - stalled_pending_reflections * 10
        - (15 if unabsorbed_facts >= 25 else 0),
    )

    # 6. Freshness & Boundaries
    fresh_findings: list[dict[str, Any]] = []
    if suppressed_count > 0:
        fresh_findings.append({"code": "H4", "severity": "info", "count": suppressed_count})
    if low_importance_facts >= 20:
        fresh_findings.append({"code": "H2", "severity": "info", "count": low_importance_facts})
    fresh_score = max(
        0,
        100 - min(20, suppressed_count * 4) - (10 if low_importance_facts >= 20 else 0),
    )

    health_dimensions = [
        {
            "key": "consistency",
            "score": cons_score,
            "status": _compute_dimension_status(
                sum(1 for x in cons_findings if x["severity"] == "bad"),
                sum(1 for x in cons_findings if x["severity"] == "warn"),
            ),
            "metrics": {
                "pending_corrections": len(pending_corrections),
                "negative_evidence": negative_evidence_count,
                "sub_zero_entries": sub_zero_entries,
            },
            "findings": cons_findings,
        },
        {
            "key": "redundancy",
            "score": red_score,
            "status": _compute_dimension_status(
                0,
                sum(1 for x in red_findings if x["severity"] == "warn"),
            ),
            "metrics": {
                "near_duplicate_pairs": len(near_duplicates),
            },
            "findings": red_findings,
        },
        {
            "key": "embedding",
            "score": emb_score,
            "status": _compute_dimension_status(
                sum(1 for x in emb_findings if x["severity"] == "bad"),
                sum(1 for x in emb_findings if x["severity"] == "warn"),
            ),
            "metrics": {
                "embedded": emb_audit["embedded"],
                "total": emb_audit["total"],
                "coverage_pct": round(emb_audit["coverage"] * 100, 1),
                "missing": emb_audit["missing"],
                "stale": emb_audit["stale"],
                "corrupt": emb_audit["corrupt"],
                "primary_dim": emb_audit["primary_dim"],
            },
            "findings": emb_findings,
        },
        {
            "key": "structure",
            "score": struct_score,
            "status": _compute_dimension_status(
                sum(1 for x in struct_findings if x["severity"] == "bad"),
                sum(1 for x in struct_findings if x["severity"] == "warn"),
            ),
            "metrics": {
                "duplicate_ids": len(duplicate_ids),
                "dangling_fact_refs": dangling_fact_refs,
                "sourceless_reflections": sourceless_reflections,
                "unattributed_persona": unattributed_persona,
            },
            "findings": struct_findings,
        },
        {
            "key": "pipeline",
            "score": pipe_score,
            "status": _compute_dimension_status(
                0,
                sum(1 for x in pipe_findings if x["severity"] == "warn"),
            ),
            "metrics": {
                "unabsorbed_facts": unabsorbed_facts,
                "stalled_pending_reflections": stalled_pending_reflections,
                "promoted_30d": promoted_30d,
                "denied_30d": denied_30d,
                "synthesized_30d": synth_30d,
            },
            "findings": pipe_findings,
        },
        {
            "key": "freshness",
            "score": fresh_score,
            "status": _compute_dimension_status(
                0,
                sum(1 for x in fresh_findings if x["severity"] == "warn"),
            ),
            "metrics": {
                "recent_30d_activity": recent_30d_activity,
                "suppressed_entries": suppressed_count,
                "active_directives": len(directives_active),
                "low_importance_facts": low_importance_facts,
            },
            "findings": fresh_findings,
        },
    ]

    overall_score = int(
        round(sum(d["score"] for d in health_dimensions) / len(health_dimensions))
    )
    overall_status = (
        "bad"
        if any(d["status"] == "bad" for d in health_dimensions)
        else ("warn" if any(d["status"] == "warn" for d in health_dimensions) else "good")
    )

    max_dim_val = max(dim_counts.values()) if dim_counts else 0
    radar_dimensions = [
        {
            "key": k,
            "count": dim_counts[k],
            "ratio": round(dim_counts[k] / max(1, max_dim_val), 3) if max_dim_val else 0.0,
        }
        for k in _DIMENSION_ORDER
    ]

    return {
        "character_name": character_name,
        "generated_at": now_dt.isoformat(),
        "imprint": {
            "stats": {
                "companion_days": companion_days,
                "first_chat_at": first_db_dt.isoformat() if first_db_dt else None,
                "total_messages": total_messages,
                "facts_total": facts_total,
                "imprint_persona_count": imprint_persona_count,
                "character_card_count": len(character_card_items),
                "confirmed_reflections_count": confirmed_reflections_count,
                "pending_attention_count": pending_attention_count,
            },
            "warm_summary": {
                "stage_key": stage_key,
                "top_dimensions": top_dimensions,
                "highlight_snippets": highlight_snippets,
                "cached_note": cached_note,
            },
            "dimensions": radar_dimensions,
            "milestones": milestones[:12],
            "evolution": evolution_items[:8],
        },
        "memories": {
            "pending_corrections": pending_corrections,
            "pending_reflections": pending_reflections,
            "near_duplicates": near_duplicates,
            "groups": {
                "about_you": about_you,
                "relationship": relationship_items,
                "growth": growth_items,
                "character_card": character_card_items,
            },
            "directives": directives_active,
            "suppressed": suppressed_items,
        },
        "health": {
            "overall_score": overall_score,
            "overall_status": overall_status,
            "dimensions": health_dimensions,
            "funnel_30d": funnel_30d,
            "data_sources": {
                "time_indexed_db": bool(time_stats.get("exists")),
                "persona_json": (char_dir / "persona.json").is_file(),
                "reflections_json": (char_dir / "reflections.json").is_file(),
                "facts_json": (char_dir / "facts.json").is_file(),
                "events_ndjson": (char_dir / "events.ndjson").is_file(),
            },
        },
    }


async def abuild_memory_overview(
    character_name: str,
    *,
    memory_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Non-blocking async twin of ``build_memory_overview``."""
    return await asyncio.to_thread(
        build_memory_overview,
        character_name,
        memory_dir=memory_dir,
        now=now,
    )
