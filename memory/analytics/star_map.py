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

"""Read-only Memory Star Map (2D PCA + high-dim clustering + cached theme labels)
and related-memory neighbor lookup for cascade correction.

Design invariants:
- Never loads any embedding model or installs packages at runtime.
- Pure numpy SVD PCA projection (millisecond latency, zero heavy deps).
- Caches decoded/projected star maps in a bounded thread-safe FIFO keyed by
  source file mtimes/sizes so UI tab switches stay instant.
- LLM theme labeling is strictly manual in UI or weekly-throttled in ArchiveSweep,
  persisted to ``star_map_clusters_cache.json``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config import (
    STAR_MAP_LABEL_INPUT_MAX_TOKENS,
    STAR_MAP_LABEL_MAX_TOKENS,
    STAR_MAP_LABEL_REFRESH_DAYS,
    STAR_MAP_LABEL_TIMEOUT_SECONDS,
    STAR_MAP_MIN_EMBEDDED_ENTRIES,
)
from config.prompts.prompts_memory import get_star_map_cluster_label_prompt
from memory.evidence import evidence_score
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json_async
from utils.language_utils import get_global_language_full, normalize_language_code
from utils.llm_client import create_chat_llm_async
from utils.logger_config import get_module_logger
from utils.tokenize import truncate_to_tokens
from utils.token_tracker import set_call_type

from .imprint import _CORE_ENTITIES, _META_ENTITY, _read_json_safe, _strength_level, _text_bigrams

try:
    from memory.embeddings import decode_embedding
except ImportError:
    from memory.embeddings_fallback import decode_embedding

logger = get_module_logger(__name__, "Memory")

_STAR_MAP_CACHE_FILENAME = "star_map_clusters_cache.json"
_MAX_STAR_POINTS = 500
_CLUSTER_COSINE_THRESHOLD = 0.56
_CLUSTER_TOPK_EDGES = 6
_NEIGHBOR_TOP_K = 4
_MEM_CACHE_SLOTS = 16

_STAR_CACHE_LOCK = threading.Lock()
_STAR_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _source_fingerprint(char_dir: Path) -> str:
    parts: list[str] = []
    for fname in ("persona.json", "reflections.json", "facts.json", _STAR_MAP_CACHE_FILENAME):
        p = char_dir / fname
        try:
            st = p.stat()
            parts.append(f"{fname}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{fname}:0:0")
    return "|".join(parts)


def _load_cluster_label_cache(char_dir: Path) -> dict[str, Any]:
    data = _read_json_safe(char_dir / _STAR_MAP_CACHE_FILENAME, {})
    return data if isinstance(data, dict) else {}


def _short_medoid_label(text: str, max_len: int = 12) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rstrip() + "..."


def _collect_memory_entries(char_dir: Path, now_dt: datetime) -> list[dict[str, Any]]:
    """Collect active non-protected persona, reflections, and facts with decoded vectors."""
    items: list[dict[str, Any]] = []

    persona_raw = _read_json_safe(char_dir / "persona.json", {})
    if isinstance(persona_raw, dict):
        for entity_key, section in persona_raw.items():
            if entity_key == _META_ENTITY or entity_key not in _CORE_ENTITIES:
                continue
            if not isinstance(section, dict):
                continue
            for entry in section.get("facts") or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("protected") or entry.get("source") == "character_card":
                    continue
                text = str(entry.get("text") or "").strip()
                eid = str(entry.get("id") or "").strip()
                if not text or not eid:
                    continue
                score = evidence_score(entry, now_dt)
                rein = int(entry.get("user_fact_reinforce_count") or 0)
                items.append({
                    "uid": f"persona:{entity_key}:{eid}",
                    "kind": "persona",
                    "id": eid,
                    "entity": entity_key,
                    "text": text,
                    "created_at": entry.get("created_at") or entry.get("promoted_at"),
                    "strength": _strength_level(score, rein),
                    "user_pinned": bool(entry.get("user_pinned")),
                    "embedding_raw": entry.get("embedding"),
                })

    reflections_raw = _read_json_safe(char_dir / "reflections.json", [])
    if isinstance(reflections_raw, list):
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
            rid = str(r.get("id") or "").strip()
            if not text or not rid:
                continue
            score = evidence_score(r, now_dt)
            rein = int(r.get("user_fact_reinforce_count") or 0)
            items.append({
                "uid": f"reflection:{entity}:{rid}",
                "kind": "reflection",
                "id": rid,
                "entity": entity,
                "text": text,
                "created_at": r.get("created_at"),
                "strength": _strength_level(score, rein),
                "user_pinned": False,
                "embedding_raw": r.get("embedding"),
            })

    facts_raw = _read_json_safe(char_dir / "facts.json", [])
    if isinstance(facts_raw, list):
        for f in facts_raw:
            if not isinstance(f, dict):
                continue
            entity = str(f.get("entity") or "master")
            if entity not in _CORE_ENTITIES:
                continue
            text = str(f.get("text") or "").strip()
            fid = str(f.get("id") or "").strip()
            if not text or not fid:
                continue
            imp = int(f.get("importance") or 5)
            items.append({
                "uid": f"fact:{entity}:{fid}",
                "kind": "fact",
                "id": fid,
                "entity": entity,
                "text": text,
                "created_at": f.get("created_at"),
                "strength": 3 if imp >= 9 else (2 if imp >= 7 else 1),
                "user_pinned": False,
                "embedding_raw": f.get("embedding"),
            })

    return items


def _pca_2d(unit_mat) -> list[tuple[float, float]]:
    """Fast pure-numpy 2D PCA normalized to [-0.92, 0.92]."""
    import numpy as np

    n = unit_mat.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0)]
    if n == 2:
        return [(-0.5, 0.0), (0.5, 0.0)]

    centered = unit_mat - unit_mat.mean(axis=0, keepdims=True)
    try:
        u, s, _vt = np.linalg.svd(centered, full_matrices=False)
        k = min(2, u.shape[1])
        coords = u[:, :k] * s[:k]
        if k == 1:
            coords = np.hstack([coords, np.zeros((n, 1), dtype=np.float32)])
    except Exception:
        angles = np.linspace(0, 2 * math.pi, n, endpoint=False)
        coords = np.stack([np.cos(angles), np.sin(angles)], axis=1)

    max_abs = float(np.max(np.abs(coords))) if coords.size else 1.0
    scale = 0.88 / max_abs if max_abs > 1e-6 else 1.0
    coords = coords * scale
    return [(round(float(coords[i, 0]), 4), round(float(coords[i, 1]), 4)) for i in range(n)]


def _cluster_cosine_cc(sim_mat) -> list[int]:
    """Connected-component clustering on high-dim cosine graph (no sklearn required)."""
    n = sim_mat.shape[0]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        row = sim_mat[i]
        cands = [
            (j, float(row[j]))
            for j in range(n)
            if j != i and float(row[j]) >= _CLUSTER_COSINE_THRESHOLD
        ]
        cands.sort(key=lambda t: -t[1])
        for j, _ in cands[:_CLUSTER_TOPK_EDGES]:
            union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    # Order clusters by size desc; singletons get -1
    multi = sorted(
        (members for members in groups.values() if len(members) >= 2),
        key=lambda g: (-len(g), g[0]),
    )
    assignments = [-1] * n
    for cid, members in enumerate(multi[:12]):
        for idx in members:
            assignments[idx] = cid
    return assignments


def build_star_map(
    character_name: str,
    *,
    memory_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Synchronously build the 2D Memory Star Map payload for one character."""
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    char_dir = base / character_name
    cache_key = f"{char_dir.resolve()}::{_source_fingerprint(char_dir)}"

    with _STAR_CACHE_LOCK:
        cached_val = _STAR_CACHE.get(cache_key)
        if cached_val is not None:
            _STAR_CACHE.move_to_end(cache_key)
            return cached_val

    now_dt = now if now is not None else datetime.now()
    all_items = _collect_memory_entries(char_dir, now_dt)

    # Decode vectors and pick dominant dimension
    decoded_items: list[tuple[dict[str, Any], Any]] = []
    dim_counts: dict[int, int] = {}
    for item in all_items:
        emb = item.get("embedding_raw")
        if not emb:
            continue
        vec = decode_embedding(emb)
        if vec is None or getattr(vec, "size", 0) == 0:
            continue
        d = int(vec.size)
        dim_counts[d] = dim_counts.get(d, 0) + 1
        decoded_items.append((item, vec))

    primary_dim = max(dim_counts.items(), key=lambda kv: kv[1])[0] if dim_counts else None
    usable = [
        (item, vec)
        for item, vec in decoded_items
        if primary_dim is not None and int(vec.size) == primary_dim
    ][:_MAX_STAR_POINTS]

    embedded_count = len(usable)
    min_required = STAR_MAP_MIN_EMBEDDED_ENTRIES
    label_cache = _load_cluster_label_cache(char_dir)

    if embedded_count < min_required:
        result = {
            "character_name": character_name,
            "ready": False,
            "embedded_count": embedded_count,
            "min_required": min_required,
            "total_memories": len(all_items),
            "corpus_hash": "",
            "points": [],
            "clusters": [],
            "labels_updated_at": label_cache.get("generated_at"),
            "labels_source": "none",
        }
        with _STAR_CACHE_LOCK:
            _STAR_CACHE[cache_key] = result
            while len(_STAR_CACHE) > _MEM_CACHE_SLOTS:
                _STAR_CACHE.popitem(last=False)
        return result

    import numpy as np

    mat = np.stack([vec.astype(np.float32) for _, vec in usable])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms > 1e-8, norms, 1.0)
    unit = mat / norms
    sim_mat = unit @ unit.T
    np.fill_diagonal(sim_mat, -1.0)

    coords = _pca_2d(unit)
    cluster_ids = _cluster_cosine_cc(sim_mat)

    # Include entry uid, text fingerprint, and cluster membership so editing
    # text or rebuilding vectors invalidates cached cluster labels cleanly.
    corpus_rows = sorted(
        f"{item['uid']}:{hashlib.sha256(item['text'].encode('utf-8')).hexdigest()[:12]}:c{cluster_ids[idx]}"
        for idx, (item, _) in enumerate(usable)
    )
    corpus_material = "|".join(corpus_rows)
    corpus_hash = hashlib.sha256(corpus_material.encode("utf-8")).hexdigest()[:16]

    cached_labels_map: dict[str, str] = {}
    labels_source = "medoid"
    if (
        isinstance(label_cache.get("labels"), dict)
        and label_cache.get("labels")
        and label_cache.get("corpus_hash") == corpus_hash
    ):
        cached_labels_map = {
            str(k): str(v).strip()
            for k, v in label_cache["labels"].items()
            if str(v or "").strip()
        }
        if cached_labels_map:
            labels_source = "llm_cached"

    # Assemble clusters & medoids
    cluster_members: dict[int, list[int]] = {}
    for idx, cid in enumerate(cluster_ids):
        if cid >= 0:
            cluster_members.setdefault(cid, []).append(idx)

    clusters_out: list[dict[str, Any]] = []
    for cid in sorted(cluster_members.keys()):
        members = cluster_members[cid]
        cx = sum(coords[i][0] for i in members) / len(members)
        cy = sum(coords[i][1] for i in members) / len(members)
        # Medoid = member with highest average cosine to other cluster members
        best_idx = members[0]
        best_avg = -2.0
        for i in members:
            avg_sim = sum(float(sim_mat[i, j]) for j in members if j != i) / max(1, len(members) - 1)
            if avg_sim > best_avg:
                best_avg = avg_sim
                best_idx = i
        medoid_text = usable[best_idx][0]["text"]
        fallback_label = _short_medoid_label(medoid_text, max_len=10)
        theme_label = cached_labels_map.get(str(cid)) or fallback_label
        sample_texts = [usable[i][0]["text"] for i in members[:5]]
        clusters_out.append({
            "id": cid,
            "label": theme_label,
            "fallback_label": fallback_label,
            "count": len(members),
            "cx": round(cx, 4),
            "cy": round(cy, 4),
            "medoid_uid": usable[best_idx][0]["uid"],
            "samples": sample_texts,
        })

    points_out: list[dict[str, Any]] = []
    for idx, (item, _) in enumerate(usable):
        row = sim_mat[idx]
        top_n = np.argsort(-row)[:_NEIGHBOR_TOP_K]
        neighbor_uids = [
            usable[int(j)][0]["uid"]
            for j in top_n
            if int(j) != idx and float(row[int(j)]) >= 0.45
        ]
        points_out.append({
            "uid": item["uid"],
            "kind": item["kind"],
            "id": item["id"],
            "entity": item["entity"],
            "text": item["text"],
            "created_at": item["created_at"],
            "strength": item["strength"],
            "user_pinned": item["user_pinned"],
            "x": coords[idx][0],
            "y": coords[idx][1],
            "cluster_id": cluster_ids[idx],
            "neighbors": neighbor_uids,
        })

    result = {
        "character_name": character_name,
        "ready": True,
        "embedded_count": embedded_count,
        "min_required": min_required,
        "total_memories": len(all_items),
        "corpus_hash": corpus_hash,
        "points": points_out,
        "clusters": clusters_out,
        "labels_updated_at": label_cache.get("generated_at"),
        "labels_source": labels_source,
    }

    with _STAR_CACHE_LOCK:
        _STAR_CACHE[cache_key] = result
        while len(_STAR_CACHE) > _MEM_CACHE_SLOTS:
            _STAR_CACHE.popitem(last=False)
    return result


async def abuild_star_map(
    character_name: str,
    *,
    memory_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Non-blocking async wrapper around ``build_star_map``."""
    return await asyncio.to_thread(
        build_star_map,
        character_name,
        memory_dir=memory_dir,
        now=now,
    )


def _extract_json_dict(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


async def alabel_star_map_clusters(
    character_name: str,
    *,
    language: str | None = None,
    config_manager=None,
    force: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Generate and persist warm theme labels for Star Map clusters via summary tier.

    When ``force=False`` (used by the weekly ArchiveSweep background hook), skips
    unless:
      1. Star map is ready (embedded_count >= STAR_MAP_MIN_EMBEDDED_ENTRIES) and has clusters.
      2. Either no cached labels exist, OR (age >= STAR_MAP_LABEL_REFRESH_DAYS AND corpus_hash changed).
    """
    cm = config_manager or get_config_manager()
    memory_dir = Path(cm.memory_dir)
    char_dir = memory_dir / character_name
    now_dt = now if now is not None else datetime.now()

    star_map = await abuild_star_map(character_name, memory_dir=memory_dir, now=now_dt)
    clusters = star_map.get("clusters") or []
    if not star_map.get("ready") or not clusters:
        return {
            "skipped": True,
            "reason": "not_ready",
            "labels": {},
            "star_map": star_map,
        }

    existing_cache = await asyncio.to_thread(_load_cluster_label_cache, char_dir)
    corpus_hash = str(star_map.get("corpus_hash") or "")

    if not force:
        prev_hash = str(existing_cache.get("corpus_hash") or "")
        prev_ts_raw = existing_cache.get("generated_at")
        prev_labels = existing_cache.get("labels")
        if prev_labels and prev_hash == corpus_hash:
            return {"skipped": True, "reason": "corpus_unchanged", "labels": prev_labels}
        if prev_labels and isinstance(prev_ts_raw, str):
            try:
                prev_dt = datetime.fromisoformat(prev_ts_raw)
                if (now_dt - prev_dt) < timedelta(days=STAR_MAP_LABEL_REFRESH_DAYS):
                    return {"skipped": True, "reason": "within_cooldown", "labels": prev_labels}
            except (ValueError, TypeError):
                pass

    assert_cloudsave_writable(
        cm,
        operation="save",
        target=f"memory/{character_name}/{_STAR_MAP_CACHE_FILENAME}",
    )

    lines: list[str] = []
    for c in clusters:
        cid = c["id"]
        samples = " ; ".join(str(s) for s in (c.get("samples") or [])[:4])
        lines.append(f'"{cid}": {samples}')
    clusters_block = truncate_to_tokens("\n".join(lines), STAR_MAP_LABEL_INPUT_MAX_TOKENS)

    raw_lang = language or get_global_language_full()
    full_lang = normalize_language_code(raw_lang, format="full")
    prompt_lang = "zh-TW" if full_lang == "zh-TW" else normalize_language_code(full_lang, format="short")

    template = get_star_map_cluster_label_prompt(prompt_lang)
    prompt = template.replace("{CLUSTERS}", clusters_block)
    prompt = truncate_to_tokens(prompt, STAR_MAP_LABEL_INPUT_MAX_TOKENS)

    api_config = await cm.aget_model_api_config("summary")
    set_call_type("star_map_cluster_labels")
    llm = await create_chat_llm_async(
        api_config["model"],
        api_config["base_url"],
        api_config["api_key"],
        timeout=STAR_MAP_LABEL_TIMEOUT_SECONDS,
        max_retries=0,
        max_completion_tokens=STAR_MAP_LABEL_MAX_TOKENS,
        provider_type=api_config.get("provider_type"),
    )
    try:
        resp = await llm.ainvoke(prompt)
    finally:
        await llm.aclose()

    parsed = _extract_json_dict(str(getattr(resp, "content", "") or ""))
    if not parsed:
        raise ValueError("Cluster label model returned invalid JSON")

    clean_labels: dict[str, str] = {}
    valid_ids = {str(c["id"]) for c in clusters}
    for k, v in parsed.items():
        ks = str(k).strip()
        vs = str(v or "").strip()
        if ks in valid_ids and vs:
            clean_labels[ks] = vs[:24]

    if not clean_labels:
        raise ValueError("Cluster label response contained no valid cluster titles")

    cache_payload = {
        "corpus_hash": corpus_hash,
        "generated_at": now_dt.isoformat(),
        "language": prompt_lang,
        "labels": clean_labels,
    }
    await asyncio.to_thread(char_dir.mkdir, parents=True, exist_ok=True)
    await atomic_write_json_async(
        str(char_dir / _STAR_MAP_CACHE_FILENAME),
        cache_payload,
        indent=2,
        ensure_ascii=False,
    )

    refreshed = await abuild_star_map(character_name, memory_dir=memory_dir, now=now_dt)
    return {
        "skipped": False,
        "labels": clean_labels,
        "generated_at": cache_payload["generated_at"],
        "star_map": refreshed,
    }


def find_related_memories(
    character_name: str,
    *,
    kind: str,
    entry_id: str,
    text: str,
    top_k: int = 4,
    memory_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Find closely related memories for cascade review after editing/forgetting an entry."""
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    char_dir = base / character_name
    now_dt = datetime.now()
    all_items = _collect_memory_entries(char_dir, now_dt)
    if not all_items:
        return []

    target_vec = None
    target_text = str(text or "").strip()
    candidates: list[dict[str, Any]] = []
    for item in all_items:
        if item["kind"] == kind and item["id"] == entry_id:
            if item.get("embedding_raw"):
                target_vec = decode_embedding(item["embedding_raw"])
            if not target_text:
                target_text = item["text"]
            continue
        candidates.append(item)

    if not candidates:
        return []

    scored: list[tuple[float, dict[str, Any], str]] = []

    # 1. Vector cosine similarity when target vector is available
    if target_vec is not None and getattr(target_vec, "size", 0) > 0:
        try:
            import numpy as np

            t_norm = float(np.linalg.norm(target_vec))
            if t_norm > 1e-8:
                t_unit = target_vec.astype(np.float32) / t_norm
                for item in candidates:
                    raw_emb = item.get("embedding_raw")
                    if not raw_emb:
                        continue
                    v = decode_embedding(raw_emb)
                    if v is None or int(v.size) != int(t_unit.size):
                        continue
                    v_norm = float(np.linalg.norm(v))
                    if v_norm <= 1e-8:
                        continue
                    sim = float(np.dot(t_unit, v.astype(np.float32) / v_norm))
                    if sim >= 0.72:
                        scored.append((sim, item, "embedding"))
        except Exception as exc:
            logger.debug("[MemoryAnalytics] related vector lookup skipped: %s", exc)

    # 2. Exact normalized match + text bigram Jaccard supplement / fallback
    if target_text:
        t_norm_text = " ".join(target_text.casefold().split())
        t_shingles = _text_bigrams(target_text)
        seen_uids = {item["uid"] for _, item, _ in scored}
        for item in candidates:
            if item["uid"] in seen_uids:
                continue
            c_norm_text = " ".join(str(item["text"] or "").casefold().split())
            if t_norm_text and c_norm_text == t_norm_text:
                scored.append((1.0, item, "text"))
                seen_uids.add(item["uid"])
                continue
            if len(t_shingles) < 2:
                continue
            c_shingles = _text_bigrams(item["text"])
            if len(c_shingles) < 2:
                continue
            inter = len(t_shingles & c_shingles)
            if inter == 0:
                continue
            jacc = inter / len(t_shingles | c_shingles)
            if jacc >= 0.25:
                scored.append((jacc, item, "text"))

    scored.sort(key=lambda t: -t[0])
    out: list[dict[str, Any]] = []
    for sim, item, method in scored[: max(1, min(top_k, 8))]:
        out.append({
            "kind": item["kind"],
            "id": item["id"],
            "entity": item["entity"],
            "text": item["text"],
            "created_at": item["created_at"],
            "user_pinned": item["user_pinned"],
            "strength": item["strength"],
            "similarity": round(sim, 3),
            "method": method,
        })
    return out


async def afind_related_memories(
    character_name: str,
    *,
    kind: str,
    entry_id: str,
    text: str,
    top_k: int = 4,
    memory_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Non-blocking async wrapper around ``find_related_memories``."""
    return await asyncio.to_thread(
        find_related_memories,
        character_name,
        kind=kind,
        entry_id=entry_id,
        text=text,
        top_k=top_k,
        memory_dir=memory_dir,
    )
