"""P27.1 — read-only memory lineage aggregator (single chokepoint).

Builds the ground-truth ``{nodes, edges, meta}`` snapshot that the P27 memory
trace workspace renders. This is the **only** place the lineage graph shape is
assembled: the frontend consumes it verbatim and never re-derives structure
from the raw memory JSON (blueprint §3 #1 / LESSONS_LEARNED §7.25). Tier C
attribution (P27.3) and any future Tier B capture (P27.4) attach to the same
node ids produced here.

Honest tiering (blueprint §2.2)
-------------------------------
* **Tier A — structural true causality (this module).** Edges that are already
  persisted on disk: ``reflection.source_fact_ids`` -> facts, persona entry
  ``source_id`` / ``merged_from_ids`` -> reflections. Drawn as ``persisted``
  (solid). 100% reliable for any character.
* Conversation nodes (messages / recent memo) are included as available source
  material but carry **no** Tier A edge to facts (no such link exists on disk);
  they become connectable only via Tier C reverse attribution.

Discipline
----------
* Read only. No writes, no directory creation.
* Soft errors: a single unreadable file becomes a ``meta.file_warnings`` entry
  and a partial graph, never a hard failure (blueprint §3 #4).
* Node budget: conversation nodes are capped first so a heavy imported db can't
  explode the graph (blueprint §6.6 / R5).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager

from tests.testbench.pipeline.conversation_corpus import load_conversation_corpus

# Lane layout (left -> right), blueprint §2.3.
LANE_MESSAGE = 0
LANE_RECENT_MEMO = 1
LANE_FACT = 2
LANE_REFLECTION = 3
LANE_PERSONA = 4

#: Default ceiling on total nodes. Conversation nodes are trimmed first.
_DEFAULT_NODE_BUDGET = 400

#: How much memory text to keep in a node ``label`` (full text lives in meta).
_LABEL_MAX = 80

#: Locale-independent prefixes that mark a recent.json system turn as a
#: "compressed memo" rather than a verbatim message. Mirrors
#: ``config.prompts.prompts_sys.MEMORY_MEMO_WITH_SUMMARY`` (the part before the
#: ``{summary}`` placeholder). Kept as a static list so this reader has no
#: import-time dependency on the prompt module.
_MEMO_PREFIXES: tuple[str, ...] = (
    "先前对话的备忘录",
    "Memo from prior conversations",
    "以前の会話のメモ",
    "이전 대화의 메모",
    "Заметки из предыдущих разговоров",
    "Notas de conversaciones previas",
    "Notas de conversas anteriores",
)


def _truncate(text: str, limit: int = _LABEL_MAX) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def _memory_dir(character: str) -> Path:
    """``<sandbox memory_dir>/<character>`` without creating it (read only)."""
    cm = get_config_manager()
    return Path(str(cm.memory_dir)) / character


def _read_json(path: Path, *, expect: type, warnings: list[str]) -> Any:
    """Read a JSON file with soft-error semantics.

    Returns the parsed value when its top-level type matches ``expect``;
    otherwise appends a warning and returns ``expect()`` (empty list/dict).
    Missing file -> empty value, no warning (a clean character simply has no
    reflections yet).
    """
    if not path.exists():
        return expect()
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{path.name} 读取失败 ({type(exc).__name__}): {exc}")
        return expect()
    if not isinstance(data, expect):
        warnings.append(
            f"{path.name} 顶层应为 {expect.__name__}, 实际 {type(data).__name__}; 跳过"
        )
        return expect()
    return data


def _is_memo(role: str, content: str) -> bool:
    if role != "system":
        return False
    head = content.lstrip()
    return any(head.startswith(p) for p in _MEMO_PREFIXES)


def _correction_id(old_text: str, new_text: str, entity: str) -> str:
    digest = hashlib.sha256(
        f"{entity}\x00{old_text}\x00{new_text}".encode("utf-8")
    ).hexdigest()
    return f"corr:{digest[:12]}"


def build_lineage_snapshot(
    character: str,
    *,
    node_budget: int = _DEFAULT_NODE_BUDGET,
    conversation_limit: int | None = None,
) -> dict[str, Any]:
    """Assemble the read-only lineage snapshot for ``character``.

    Returns ``{nodes, edges, meta}`` (see module docstring / blueprint §2.3).
    Never raises for absent or malformed memory data.
    """
    character = str(character or "").strip()
    file_warnings: list[str] = []
    mem = _memory_dir(character)

    facts = _read_json(mem / "facts.json", expect=list, warnings=file_warnings)
    # A fact absorbed into a reflection is *moved* out of facts.json into
    # facts_archive.json (main-program memory lifecycle), yet the reflection
    # keeps citing its original ``source_fact_ids``. Without the archive the
    # graph can't draw the reflection<-fact edge, and the overview's D4 check
    # mislabels every such citation as "referencing a deleted fact" (a P29
    # false positive). Loaded here; only the referenced subset is materialised
    # (lane 2.5) — never the whole archive.
    facts_archive = _read_json(
        mem / "facts_archive.json", expect=list, warnings=file_warnings)
    reflections = _read_json(
        mem / "reflections.json", expect=list, warnings=file_warnings)
    persona = _read_json(mem / "persona.json", expect=dict, warnings=file_warnings)
    corrections = _read_json(
        mem / "persona_corrections.json", expect=list, warnings=file_warnings)

    # When no explicit conversation_limit is given, fall back to
    # load_conversation_corpus's built-in 5000-row default rather than passing
    # None (which would disable the cap and read the entire table into memory).
    if conversation_limit:
        corpus = load_conversation_corpus(
            character, limit_rows=conversation_limit)
    else:
        corpus = load_conversation_corpus(character)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def _add_node(node: dict[str, Any]) -> None:
        if node["id"] in node_ids:
            return
        node_ids.add(node["id"])
        nodes.append(node)

    # ── lane 2: facts ──
    active_fact_ids: set[str] = set()
    for f in facts:
        if not isinstance(f, dict):
            continue
        fid = f.get("id")
        if not fid:
            continue
        active_fact_ids.add(str(fid))
        _add_node({
            "id": str(fid),
            "type": "fact",
            "lane": LANE_FACT,
            "label": _truncate(f.get("text", "")),
            "status": "absorbed" if f.get("absorbed") else "active",
            "entity": f.get("entity"),
            "created_at": f.get("created_at"),
            "meta": {
                "text": f.get("text", ""),
                "importance": f.get("importance"),
                "tags": f.get("tags") or [],
                "absorbed": bool(f.get("absorbed")),
            },
            "warnings": [],
        })

    # ── lane 2.5: archived facts a reflection still references ──
    # Materialise ONLY the archived facts an existing reflection cites (not the
    # whole archive: a heavy character archives hundreds, and an un-referenced
    # archived fact adds no lineage while burning the node budget). These nodes
    # carry a distinct ``fact_archived`` type + ``archived`` status so the
    # reflection<-fact edge below resolves and D4 sees the fact as present,
    # while every fact-quality/count metric keyed on ``type == "fact"``
    # automatically excludes them. Placed before lane 3 so the edge pass finds
    # them in ``node_ids``.
    referenced_fact_ids: set[str] = set()
    for r in reflections:
        if not isinstance(r, dict):
            continue
        for x in (r.get("source_fact_ids") or []):
            if x:
                referenced_fact_ids.add(str(x))
    need_archived = referenced_fact_ids - active_fact_ids
    archived_fact_count = 0
    if need_archived:
        for f in facts_archive:
            if not isinstance(f, dict):
                continue
            fid = f.get("id")
            if not fid:
                continue
            fid = str(fid)
            if fid not in need_archived or fid in node_ids:
                continue
            archived_fact_count += 1
            _add_node({
                "id": fid,
                "type": "fact_archived",
                "lane": LANE_FACT,
                "label": _truncate(f.get("text", "")),
                "status": "archived",
                "entity": f.get("entity"),
                "created_at": f.get("created_at"),
                "meta": {
                    "text": f.get("text", ""),
                    "importance": f.get("importance"),
                    "tags": f.get("tags") or [],
                    "absorbed": True,
                    "archived": True,
                },
                "warnings": [],
            })

    # ── lane 3: reflections (+ fact -> reflection edges) ──
    for r in reflections:
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        if not rid:
            continue
        rid = str(rid)
        source_fact_ids = [str(x) for x in (r.get("source_fact_ids") or []) if x]
        _add_node({
            "id": rid,
            "type": "reflection",
            "lane": LANE_REFLECTION,
            "label": _truncate(r.get("text", "")),
            "status": r.get("status"),
            "entity": r.get("entity"),
            "created_at": r.get("created_at"),
            "meta": {
                "text": r.get("text", ""),
                "source_fact_ids": source_fact_ids,
                "feedback": r.get("feedback"),
            },
            "warnings": [],
        })
        for fid in source_fact_ids:
            if fid in node_ids:
                edges.append({
                    "source": fid,
                    "target": rid,
                    "relation": "source_fact",
                    "confidence": "persisted",
                    "score": None,
                    "note": None,
                })

    # ── lane 4: persona entries (+ reflection -> persona edges) ──
    persona_count = 0
    for entity_key, section in persona.items():
        if not isinstance(section, dict):
            continue
        section_facts = section.get("facts")
        if not isinstance(section_facts, list):
            continue
        for pf in section_facts:
            if not isinstance(pf, dict):
                continue
            pid = pf.get("id")
            if not pid:
                continue
            pid = str(pid)
            persona_count += 1
            source = pf.get("source")
            source_id = pf.get("source_id")
            merged_from_ids = [
                str(x) for x in (pf.get("merged_from_ids") or []) if x
            ]
            _add_node({
                "id": pid,
                "type": "persona_entry",
                "lane": LANE_PERSONA,
                "label": _truncate(pf.get("text", "")),
                "status": "suppressed" if pf.get("suppress") else "active",
                "entity": entity_key,
                "created_at": pf.get("created_at"),
                "meta": {
                    "text": pf.get("text", ""),
                    "source": source,
                    "source_id": source_id,
                    "merged_from_ids": merged_from_ids,
                    "protected": bool(pf.get("protected")),
                },
                "warnings": [],
            })
            if source == "reflection" and source_id and str(source_id) in node_ids:
                edges.append({
                    "source": str(source_id),
                    "target": pid,
                    "relation": "promoted_from",
                    "confidence": "persisted",
                    "score": None,
                    "note": None,
                })
            for mid in merged_from_ids:
                if mid in node_ids:
                    edges.append({
                        "source": mid,
                        "target": pid,
                        "relation": "merged_from",
                        "confidence": "persisted",
                        "score": None,
                        "note": None,
                    })

    # ── lane 4: corrections (pending contradictions) ──
    # An exact old_text match links the correction to the persona entry it
    # disputes; that link IS persisted (the conflict was recorded on disk).
    persona_text_to_id: dict[str, str] = {}
    for n in nodes:
        if n["type"] == "persona_entry":
            persona_text_to_id.setdefault(n["meta"].get("text", ""), n["id"])
    correction_count = 0
    for c in corrections:
        if not isinstance(c, dict):
            continue
        old_text = str(c.get("old_text") or "")
        new_text = str(c.get("new_text") or "")
        if not old_text and not new_text:
            continue
        cid = _correction_id(old_text, new_text, str(c.get("entity") or ""))
        correction_count += 1
        _add_node({
            "id": cid,
            "type": "correction",
            "lane": LANE_PERSONA,
            "label": _truncate(new_text or old_text),
            "status": "pending",
            "entity": c.get("entity"),
            "created_at": c.get("created_at"),
            "meta": {"old_text": old_text, "new_text": new_text},
            "warnings": [],
        })
        target_pid = persona_text_to_id.get(old_text)
        if target_pid:
            edges.append({
                "source": cid,
                "target": target_pid,
                "relation": "corrects",
                "confidence": "persisted",
                "score": None,
                "note": None,
            })

    # ── lane 0/1: conversation nodes (budgeted) ──
    structural_count = len(nodes)
    remaining_budget = max(0, node_budget - structural_count)
    turns = corpus.get("turns") or []
    convo_total = len(turns)
    convo_shown = 0
    truncated = False
    for turn in turns:
        if convo_shown >= remaining_budget:
            truncated = True
            break
        role = turn.get("role", "other")
        content = turn.get("content", "")
        is_memo = _is_memo(role, content)
        _add_node({
            "id": turn["id"],
            "type": "recent_memo" if is_memo else "message",
            "lane": LANE_RECENT_MEMO if is_memo else LANE_MESSAGE,
            "label": _truncate(content),
            "status": role,
            "entity": None,
            "created_at": turn.get("ts"),
            "meta": {
                "content": content,
                "role": role,
                "session_id": turn.get("session_id"),
                "origin": turn.get("origin"),
            },
            "warnings": [],
        })
        convo_shown += 1

    message_count = sum(1 for n in nodes if n["type"] == "message")
    recent_memo_count = sum(1 for n in nodes if n["type"] == "recent_memo")

    meta = {
        "character": character,
        "counts": {
            "messages": message_count,
            "recent_memos": recent_memo_count,
            "facts": sum(1 for n in nodes if n["type"] == "fact"),
            "facts_archived": archived_fact_count,
            "reflections": sum(1 for n in nodes if n["type"] == "reflection"),
            "persona": persona_count,
            "corrections": correction_count,
        },
        "sources_present": {
            "events_ndjson": (mem / "events.ndjson").is_file(),
            "time_indexed_db": bool(corpus.get("sources", {}).get("time_indexed_db")),
            "trace_provenance": (mem / "trace_provenance.json").is_file(),
        },
        "file_warnings": file_warnings,
        "corpus_warnings": corpus.get("warnings") or [],
        "node_budget": {
            "total": structural_count + convo_total,
            "shown": len(nodes),
            "truncated": truncated,
        },
        "edge_count": len(edges),
    }

    return {"nodes": nodes, "edges": edges, "meta": meta}
