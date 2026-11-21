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

"""Read-only 3-step single-memory lineage story builder for "Why do they remember this?".

Traces one selected memory (persona / reflection / fact) backwards into:
  1. Conversation snippets (exact time window around source facts, or honest
     lightweight bigram attribution when no direct link exists).
  2. Concrete remembered facts (``source_fact_ids`` or closely matching facts,
     allowing the user to forget the root misunderstanding right inside the drawer).
  3. Synthesized impression (reflection -> persona + version_history).
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

from .imprint import _CORE_ENTITIES, _META_ENTITY, _parse_dt, _read_json_safe, _text_bigrams

logger = get_module_logger(__name__, "Memory")

_MAX_CONV_SCAN_ROWS = 600


def _read_recent_conversations_ro(db_path: Path, limit: int = _MAX_CONV_SCAN_ROWS) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    conn = None
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='time_indexed_original'"
        )
        if cur.fetchone() is None:
            return []
        cur.execute(
            "SELECT timestamp, role, content FROM time_indexed_original ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for ts, role, content in rows:
            txt = str(content or "").strip()
            if not txt:
                continue
            out.append({
                "timestamp": str(ts) if ts else None,
                "role": str(role or "human"),
                "text": txt[:260],
            })
        return out
    except Exception as exc:
        logger.debug("[MemoryLineage] time_indexed_original read failed: %s", exc)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _read_recent_json_messages(recent_path: Path) -> list[dict[str, Any]]:
    raw = _read_json_safe(recent_path, [])
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mtype = item.get("type")
        if mtype not in ("human", "ai"):
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        content = data.get("content")
        if isinstance(content, list):
            parts = [str(p.get("text") or "") for p in content if isinstance(p, dict)]
            text = " ".join(parts).strip()
        else:
            text = str(content or "").strip()
        if text:
            out.append({
                "timestamp": None,
                "role": mtype,
                "text": text[:260],
            })
    return out


def _overlap_score(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / min(len(a), len(b))


def build_memory_lineage_story(
    character_name: str,
    *,
    kind: str,
    entry_id: str,
    memory_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Synchronously trace one memory into a clean 3-step story."""
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    char_dir = base / character_name

    persona_raw = _read_json_safe(char_dir / "persona.json", {})
    reflections_raw = _read_json_safe(char_dir / "reflections.json", [])
    facts_raw = _read_json_safe(char_dir / "facts.json", [])
    facts_archive_raw = _read_json_safe(char_dir / "facts_archive.json", [])

    if not isinstance(persona_raw, dict):
        persona_raw = {}
    if not isinstance(reflections_raw, list):
        reflections_raw = []
    if not isinstance(facts_raw, list):
        facts_raw = []
    if not isinstance(facts_archive_raw, list):
        facts_archive_raw = []

    fact_by_id: dict[str, tuple[dict[str, Any], bool]] = {}
    for f in facts_raw:
        if isinstance(f, dict) and f.get("id"):
            fact_by_id[str(f["id"])] = (f, False)
    for f in facts_archive_raw:
        if isinstance(f, dict) and f.get("id") and str(f["id"]) not in fact_by_id:
            fact_by_id[str(f["id"])] = (f, True)

    refl_by_id: dict[str, dict[str, Any]] = {}
    for r in reflections_raw:
        if isinstance(r, dict) and r.get("id"):
            refl_by_id[str(r["id"])] = r

    # 1. Locate target memory
    target: dict[str, Any] | None = None
    target_entity = "master"
    linked_reflections: list[dict[str, Any]] = []
    linked_fact_ids: list[str] = []
    version_history: list[dict[str, Any]] = []

    if kind == "persona":
        for ent, section in persona_raw.items():
            if ent == _META_ENTITY or ent not in _CORE_ENTITIES or not isinstance(section, dict):
                continue
            for entry in section.get("facts") or []:
                if isinstance(entry, dict) and str(entry.get("id") or "") == entry_id:
                    target = entry
                    target_entity = ent
                    break
            if target is not None:
                break
        if target is None:
            return None
        vh = target.get("version_history") if isinstance(target.get("version_history"), list) else []
        version_history = [
            {
                "text": str(v.get("text") or ""),
                "replaced_at": v.get("replaced_at"),
                "reason": str(v.get("reason") or ""),
            }
            for v in vh
            if isinstance(v, dict) and str(v.get("text") or "").strip()
        ]
        # Gather source reflections via source_id and merged_from_ids
        candidate_rids: list[str] = []
        if target.get("source_id"):
            candidate_rids.append(str(target["source_id"]))
        for mid in target.get("merged_from_ids") or []:
            if mid and str(mid) not in candidate_rids:
                candidate_rids.append(str(mid))
        for rid in candidate_rids:
            r = refl_by_id.get(rid)
            if r is not None:
                linked_reflections.append(r)
                for sid in r.get("source_fact_ids") or []:
                    if sid and str(sid) not in linked_fact_ids:
                        linked_fact_ids.append(str(sid))

    elif kind == "reflection":
        target = refl_by_id.get(entry_id)
        if target is None:
            return None
        target_entity = str(target.get("entity") or "master")
        linked_reflections.append(target)
        for sid in target.get("source_fact_ids") or []:
            if sid and str(sid) not in linked_fact_ids:
                linked_fact_ids.append(str(sid))

    elif kind == "fact":
        pair = fact_by_id.get(entry_id)
        if pair is None:
            return None
        target = pair[0]
        target_entity = str(target.get("entity") or "master")
        linked_fact_ids.append(entry_id)
        for r in reflections_raw:
            if isinstance(r, dict) and entry_id in (r.get("source_fact_ids") or []):
                linked_reflections.append(r)
    else:
        return None

    target_text = str(target.get("text") or "").strip()
    target_shingles = _text_bigrams(target_text)

    # 2. Step 2 — Concrete facts (exact persisted links first, then honest inferred fallback)
    facts_step: list[dict[str, Any]] = []
    facts_inferred = False
    for fid in linked_fact_ids:
        pair = fact_by_id.get(fid)
        if pair is None:
            continue
        f_obj, is_archived = pair
        facts_step.append({
            "id": fid,
            "text": str(f_obj.get("text") or "").strip(),
            "created_at": f_obj.get("created_at"),
            "importance": int(f_obj.get("importance") or 5),
            "archived": is_archived,
            "inferred": False,
        })

    if not facts_step and kind != "fact" and target_shingles:
        scored_facts: list[tuple[float, dict[str, Any], bool]] = []
        for fid, (f_obj, is_archived) in fact_by_id.items():
            ft = str(f_obj.get("text") or "").strip()
            if not ft:
                continue
            ov = _overlap_score(target_shingles, _text_bigrams(ft))
            if ov >= 0.25:
                scored_facts.append((ov, f_obj, is_archived))
        scored_facts.sort(key=lambda t: -t[0])
        for _, f_obj, is_archived in scored_facts[:3]:
            facts_step.append({
                "id": str(f_obj.get("id") or ""),
                "text": str(f_obj.get("text") or "").strip(),
                "created_at": f_obj.get("created_at"),
                "importance": int(f_obj.get("importance") or 5),
                "archived": is_archived,
                "inferred": True,
            })
        facts_inferred = bool(facts_step)

    # 3. Step 1 — Conversation snippets (window around source fact timestamps + text overlap)
    conv_pool = _read_recent_conversations_ro(char_dir / "time_indexed.db")
    if not conv_pool:
        conv_pool = _read_recent_json_messages(char_dir / "recent.json")

    anchor_dates: list[datetime] = []
    for fs in facts_step:
        dt = _parse_dt(fs.get("created_at"))
        if dt is not None:
            anchor_dates.append(dt)
    target_dt = _parse_dt(target.get("created_at") or target.get("promoted_at"))
    if target_dt is not None and not anchor_dates:
        anchor_dates.append(target_dt)

    query_shingles = set(target_shingles)
    for fs in facts_step[:2]:
        query_shingles |= _text_bigrams(fs["text"])

    conv_candidates: list[tuple[float, bool, dict[str, Any]]] = []
    for row in conv_pool:
        row_shingles = _text_bigrams(row["text"])
        ov = _overlap_score(query_shingles, row_shingles)
        row_dt = _parse_dt(row.get("timestamp"))
        near_time = False
        if row_dt is not None and anchor_dates:
            near_time = any(abs((row_dt - ad).total_seconds()) <= 7200 for ad in anchor_dates)
        bonus = 0.25 if near_time else 0.0
        total_score = ov + bonus
        if total_score >= 0.22:
            conv_candidates.append((total_score, not near_time, row))

    conv_candidates.sort(key=lambda t: -t[0])
    conversations_step = [
        {
            "role": row["role"],
            "text": row["text"],
            "timestamp": row.get("timestamp"),
            "inferred": inferred,
        }
        for _, inferred, row in conv_candidates[:4]
    ]

    # 4. Step 3 — Impression summary
    reflections_step = [
        {
            "id": str(r.get("id") or ""),
            "text": str(r.get("text") or "").strip(),
            "status": str(r.get("status") or ""),
            "created_at": r.get("created_at"),
        }
        for r in linked_reflections[:3]
    ]

    return {
        "character_name": character_name,
        "target": {
            "kind": kind,
            "id": entry_id,
            "entity": target_entity,
            "text": target_text,
            "created_at": target.get("created_at") or target.get("promoted_at"),
            "source": str(target.get("source") or ""),
            "protected": bool(target.get("protected")) or target.get("source") == "character_card",
        },
        "steps": {
            "conversations": conversations_step,
            "facts": facts_step,
            "facts_inferred": facts_inferred,
            "reflections": reflections_step,
            "version_history": version_history,
        },
    }


async def abuild_memory_lineage_story(
    character_name: str,
    *,
    kind: str,
    entry_id: str,
    memory_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Non-blocking async wrapper around ``build_memory_lineage_story``."""
    return await asyncio.to_thread(
        build_memory_lineage_story,
        character_name,
        kind=kind,
        entry_id=entry_id,
        memory_dir=memory_dir,
    )


def _read_recent_memos(recent_path: Path, limit: int = 12) -> list[dict[str, Any]]:
    raw = _read_json_safe(recent_path, [])
    if not isinstance(raw, list):
        return []
    memos: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        mtype = str(item.get("type") or "")
        if mtype != "system":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        content = str(data.get("content") or "").strip()
        if not content:
            continue
        # Only pick compact conversation memo/summary blocks, skip huge system prompts
        if len(content) > 600 and "摘要" not in content and "summary" not in content.lower():
            continue
        memos.append({
            "id": f"memo_{idx}",
            "text": content[:200],
            "created_at": None,
        })
    return memos[-limit:]


def build_full_lineage_graph(
    character_name: str,
    *,
    memory_dir: Path | None = None,
    max_nodes_per_lane: int = 24,
    include_conversations: bool = True,
) -> dict[str, Any]:
    """Synchronously assemble a 5-lane causal DAG for Developer Mode inspection.

    Lanes (left to right):
      0. ``message``       - recent conversation turns (omitted if ``include_conversations=False``)
      1. ``recent_memo``   - compressed recent conversation memos
      2. ``fact``          - extracted atomic facts
      3. ``reflection``    - synthesized reflections
      4. ``persona_entry`` - promoted long-term persona impressions
    """
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    char_dir = base / character_name

    persona_raw = _read_json_safe(char_dir / "persona.json", {})
    reflections_raw = _read_json_safe(char_dir / "reflections.json", [])
    facts_raw = _read_json_safe(char_dir / "facts.json", [])
    facts_archive_raw = _read_json_safe(char_dir / "facts_archive.json", [])

    if not isinstance(persona_raw, dict):
        persona_raw = {}
    if not isinstance(reflections_raw, list):
        reflections_raw = []
    if not isinstance(facts_raw, list):
        facts_raw = []
    if not isinstance(facts_archive_raw, list):
        facts_archive_raw = []

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def _add_node(node: dict[str, Any]) -> None:
        nid = str(node["id"])
        if nid not in node_ids:
            node_ids.add(nid)
            nodes.append(node)

    # Lane 4: persona_entry (non-protected user impressions)
    persona_entries: list[tuple[str, dict[str, Any]]] = []
    for ent, section in persona_raw.items():
        if ent == _META_ENTITY or ent not in _CORE_ENTITIES or not isinstance(section, dict):
            continue
        for entry in section.get("facts") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("protected") or entry.get("source") == "character_card":
                continue
            eid = str(entry.get("id") or "").strip()
            text = str(entry.get("text") or "").strip()
            if eid and text:
                persona_entries.append((ent, entry))
    persona_entries.sort(
        key=lambda pair: str(pair[1].get("promoted_at") or pair[1].get("created_at") or ""),
        reverse=True,
    )
    persona_entries = persona_entries[:max_nodes_per_lane]

    referenced_refl_ids: set[str] = set()
    for ent, entry in persona_entries:
        eid = str(entry["id"])
        p_nid = f"persona:{eid}"
        _add_node({
            "id": p_nid,
            "lane": "persona_entry",
            "kind": "persona",
            "raw_id": eid,
            "entity": ent,
            "label": str(entry.get("text") or "").strip()[:100],
            "status": "pinned" if entry.get("user_pinned") else "active",
            "created_at": entry.get("promoted_at") or entry.get("created_at"),
        })
        if entry.get("source_id"):
            referenced_refl_ids.add(str(entry["source_id"]))
        for mid in entry.get("merged_from_ids") or []:
            if mid:
                referenced_refl_ids.add(str(mid))

    # Lane 3: reflections (prioritize those linked to persona, then recent active)
    refl_by_id: dict[str, dict[str, Any]] = {}
    for r in reflections_raw:
        if isinstance(r, dict) and r.get("id") and str(r.get("text") or "").strip():
            refl_by_id[str(r["id"])] = r

    selected_refls: list[dict[str, Any]] = []
    seen_r: set[str] = set()
    for rid in referenced_refl_ids:
        r = refl_by_id.get(rid)
        if r is not None and rid not in seen_r:
            selected_refls.append(r)
            seen_r.add(rid)
    remaining_refls = [
        r for rid, r in refl_by_id.items()
        if rid not in seen_r and str(r.get("status") or "") not in ("archived", "denied")
    ]
    remaining_refls.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    for r in remaining_refls:
        if len(selected_refls) >= max_nodes_per_lane:
            break
        selected_refls.append(r)
        seen_r.add(str(r["id"]))

    referenced_fact_ids: set[str] = set()
    for r in selected_refls:
        rid = str(r["id"])
        r_nid = f"reflection:{rid}"
        _add_node({
            "id": r_nid,
            "lane": "reflection",
            "kind": "reflection",
            "raw_id": rid,
            "entity": str(r.get("entity") or "master"),
            "label": str(r.get("text") or "").strip()[:100],
            "status": str(r.get("status") or "pending"),
            "created_at": r.get("created_at"),
        })
        for fid in r.get("source_fact_ids") or []:
            if fid:
                referenced_fact_ids.add(str(fid))

    # Edges: reflection -> persona_entry
    for ent, entry in persona_entries:
        p_nid = f"persona:{entry['id']}"
        src_id = str(entry.get("source_id") or "")
        if src_id and f"reflection:{src_id}" in node_ids:
            edges.append({
                "source": f"reflection:{src_id}",
                "target": p_nid,
                "relation": "promoted_to",
                "inferred": False,
            })
        for mid in entry.get("merged_from_ids") or []:
            mid_s = str(mid or "")
            if mid_s and f"reflection:{mid_s}" in node_ids:
                edges.append({
                    "source": f"reflection:{mid_s}",
                    "target": p_nid,
                    "relation": "merged_into",
                    "inferred": False,
                })

    # Lane 2: facts (prioritize referenced facts, then recent active facts)
    fact_by_id: dict[str, tuple[dict[str, Any], bool]] = {}
    for f in facts_raw:
        if isinstance(f, dict) and f.get("id") and str(f.get("text") or "").strip():
            fact_by_id[str(f["id"])] = (f, False)
    for f in facts_archive_raw:
        if isinstance(f, dict) and f.get("id") and str(f.get("text") or "").strip():
            fid = str(f["id"])
            if fid not in fact_by_id:
                fact_by_id[fid] = (f, True)

    selected_facts: list[tuple[dict[str, Any], bool]] = []
    seen_f: set[str] = set()
    for fid in referenced_fact_ids:
        pair = fact_by_id.get(fid)
        if pair is not None and fid not in seen_f:
            selected_facts.append(pair)
            seen_f.add(fid)
    remaining_facts = [
        (f, archived) for fid, (f, archived) in fact_by_id.items()
        if fid not in seen_f and not archived
    ]
    remaining_facts.sort(key=lambda pair: str(pair[0].get("created_at") or ""), reverse=True)
    for pair in remaining_facts:
        if len(selected_facts) >= max_nodes_per_lane:
            break
        selected_facts.append(pair)
        seen_f.add(str(pair[0]["id"]))

    for f, archived in selected_facts:
        fid = str(f["id"])
        f_nid = f"fact:{fid}"
        _add_node({
            "id": f_nid,
            "lane": "fact",
            "kind": "fact",
            "raw_id": fid,
            "entity": str(f.get("entity") or "master"),
            "label": str(f.get("text") or "").strip()[:100],
            "status": "archived" if archived else ("absorbed" if f.get("absorbed") else "active"),
            "created_at": f.get("created_at"),
        })

    # Edges: fact -> reflection
    for r in selected_refls:
        r_nid = f"reflection:{r['id']}"
        for fid in r.get("source_fact_ids") or []:
            fid_s = str(fid or "")
            if fid_s and f"fact:{fid_s}" in node_ids:
                edges.append({
                    "source": f"fact:{fid_s}",
                    "target": r_nid,
                    "relation": "synthesized_into",
                    "inferred": False,
                })

    # Lane 1: recent_memo & Lane 0: message (optional for privacy-clean diagnostics)
    memo_nodes: list[tuple[str, set[str]]] = []
    if include_conversations:
        memos = _read_recent_memos(char_dir / "recent.json", limit=min(10, max_nodes_per_lane))
        for m in memos:
            m_nid = f"memo:{m['id']}"
            _add_node({
                "id": m_nid,
                "lane": "recent_memo",
                "kind": "recent_memo",
                "raw_id": m["id"],
                "entity": "master",
                "label": m["text"][:100],
                "status": "memo",
                "created_at": m.get("created_at"),
            })
            memo_nodes.append((m_nid, _text_bigrams(m["text"])))

        conv_pool = _read_recent_conversations_ro(char_dir / "time_indexed.db", limit=180)
        if not conv_pool:
            conv_pool = _read_recent_json_messages(char_dir / "recent.json")

        fact_shingles = [
            (f"fact:{f['id']}", _text_bigrams(str(f.get("text") or "")), _parse_dt(f.get("created_at")))
            for f, _ in selected_facts
        ]

        linked_msgs: list[tuple[float, int, dict[str, Any], str]] = []
        for idx, row in enumerate(conv_pool):
            row_sh = _text_bigrams(row["text"])
            row_dt = _parse_dt(row.get("timestamp"))
            best_score = 0.0
            best_target = ""
            for f_nid, f_sh, f_dt in fact_shingles:
                ov = _overlap_score(row_sh, f_sh)
                near = bool(row_dt and f_dt and abs((row_dt - f_dt).total_seconds()) <= 7200)
                sc = ov + (0.2 if near else 0.0)
                if sc > best_score:
                    best_score = sc
                    best_target = f_nid
            if best_score >= 0.25 and best_target:
                linked_msgs.append((best_score, idx, row, best_target))

        linked_msgs.sort(key=lambda t: -t[0])
        chosen_msgs = linked_msgs[: min(14, max_nodes_per_lane)]
        for _, idx, row, target_fact_nid in chosen_msgs:
            msg_nid = f"msg:{idx}"
            _add_node({
                "id": msg_nid,
                "lane": "message",
                "kind": "message",
                "raw_id": str(idx),
                "entity": row["role"],
                "label": row["text"][:100],
                "status": row["role"],
                "created_at": row.get("timestamp"),
            })
            edges.append({
                "source": msg_nid,
                "target": target_fact_nid,
                "relation": "extracted_from",
                "inferred": True,
            })

        # Connect recent_memo -> fact via lightweight bigram overlap
        for m_nid, m_sh in memo_nodes:
            for f_nid, f_sh, _ in fact_shingles:
                if _overlap_score(m_sh, f_sh) >= 0.28:
                    edges.append({
                        "source": m_nid,
                        "target": f_nid,
                        "relation": "summarized_for",
                        "inferred": True,
                    })

    # Deduplicate edges
    dedup_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for e in edges:
        key = (e["source"], e["target"], e["relation"])
        if key not in seen_edges:
            seen_edges.add(key)
            dedup_edges.append(e)

    lane_order = ["message", "recent_memo", "fact", "reflection", "persona_entry"]
    lane_counts = {lane: 0 for lane in lane_order}
    for n in nodes:
        lane = n.get("lane")
        if lane in lane_counts:
            lane_counts[lane] += 1

    return {
        "character_name": character_name,
        "lanes": lane_order,
        "lane_counts": lane_counts,
        "nodes": nodes,
        "edges": dedup_edges,
        "includes_conversations": include_conversations,
    }


async def abuild_full_lineage_graph(
    character_name: str,
    *,
    memory_dir: Path | None = None,
    max_nodes_per_lane: int = 24,
    include_conversations: bool = True,
) -> dict[str, Any]:
    """Non-blocking async wrapper around ``build_full_lineage_graph``."""
    return await asyncio.to_thread(
        build_full_lineage_graph,
        character_name,
        memory_dir=memory_dir,
        max_nodes_per_lane=max_nodes_per_lane,
        include_conversations=include_conversations,
    )
