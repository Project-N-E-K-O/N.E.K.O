"""P32 — read-only **code leads** aggregator (由记忆分析反推主程序记忆代码可疑点).

Turns the mechanical-invariant findings that P29 :func:`memory_overview.build_overview`
already computed into *navigational leads* pointing at which main-program memory
module / write path is worth inspecting, plus two extra deterministic checks
(ID-DUP / EVT-DUP) that the overview does not surface.

Design rails (blueprint P32):
  * **Read-only, no LLM, offline, deterministic.** Reuses ``build_overview``
    (one call) + sandbox-aware raw reads via ``memory_lineage._memory_dir`` /
    ``_read_json``. Never writes, never loads a model.
  * **Only mechanical invariants become code leads.** Content-quality findings
    (redundancy / contradiction / attribution / drift / ratios / retention) are
    counted but *never* turned into a code conclusion (feasibility doc §2.2).
    A code-mechanical *allowlist* decides what is a lead — any future finding
    code defaults to "excluded", so upstream additions never get mis-promoted.
  * **Honest status, never silent (LESSONS §1.1 / §7.14).** ``embedding_status``
    / ``evt_status`` distinguish "not checked" from "checked and passed" so the
    UI never implies "no lead ⇒ code is fine". ``build_overview`` warnings are
    passed through.
  * **Navigational, not a verdict.** Every lead carries ``needs_human_confirm``
    (always True), ``missing_evidence`` (what runtime evidence would be needed to
    confirm), and ``suspect_modules`` (a *direction*, verified against the real
    ``memory/`` layout, not a precise locator).
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from tests.testbench.pipeline.memory_lineage import _memory_dir, _read_json
from tests.testbench.pipeline.memory_overview import build_overview

#: Codes from ``build_overview`` that map to a *mechanical* code lead. Anything
#: NOT here is treated as a content-quality finding and only counted (excluded).
#: (blueprint §2.2 / §2.3 — verified against the code set memory_overview emits.)
MECHANICAL_LEAD_CODES: dict[str, dict[str, Any]] = {
    "D1": {"strength": "low",
           "suspect_modules": ["memory/reflection/synthesis.py"]},
    "D2": {"strength": "medium",
           "suspect_modules": ["memory/reflection/promotion.py",
                               "memory/reflection/promotion_merge.py"]},
    "D4": {"strength": "high",
           "suspect_modules": ["memory/facts.py (删除路径)",
                               "memory/reflection/persistence.py"]},
    "E2": {"strength": "medium",
           "suspect_modules": ["memory/embeddings.py",
                               "memory/embedding_worker.py"]},
    "E3": {"strength": "high",
           "suspect_modules": ["memory/embeddings.py",
                               "memory/_embeddings/schema.py"]},
    "E4": {"strength": "high",
           "suspect_modules": ["memory/embeddings.py",
                               "memory/_embeddings/profiles.py"]},
}

#: Runtime evidence a human would still need to confirm each lead is a real code
#: defect (feasibility doc §3 — disk WHAT can only *point at*, not *prove*, HOW).
_MISSING_EVIDENCE: dict[str, list[str]] = {
    "D1": ["反思合成时的来源绑定日志", "该反思是否本就没有源事实 (非代码原因)"],
    "D2": ["晋升/合并写入时的 source/merged_from 赋值轨迹"],
    "D4": ["事实删除事件与其引用反思的时序", "删除路径是否级联清理引用"],
    "E2": ["嵌入写入时的 text 与 sha256 快照", "文本更新后是否触发重嵌入"],
    "E3": ["嵌入向量写入前的维度/数值校验点"],
    "E4": ["各条向量的模型/维度来源标注", "是否混用了不同嵌入模型"],
    "ID-DUP": ["主键分配点是否保证唯一", "写入是否做过去重"],
    "EVT-DUP": ["事件 append/reconcile 路径是否重复写入同一 event_id"],
}

#: events.ndjson scan ceiling. Main program compacts around 10k lines; scanning
#: beyond this ⇒ ``evt_status="truncated"`` (LESSONS §7.24 resource-limit UX).
_EVT_SCAN_MAX_LINES = 20000


def _norm_examples(finding: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce a finding's heterogeneous examples to ``[{id, label}]`` (id-only)."""
    out: list[dict[str, Any]] = []
    for ex in finding.get("examples") or []:
        if not isinstance(ex, dict):
            continue
        eid = ex.get("id") or ex.get("reflection_id") or ex.get("a") or ""
        label = ex.get("label") or ex.get("a_label") or ""
        out.append({"id": str(eid), "label": str(label)})
    return out


def _lead_from_finding(finding: dict[str, Any]) -> dict[str, Any]:
    code = finding["code"]
    spec = MECHANICAL_LEAD_CODES[code]
    return {
        "code": code,
        "invariant": code,  # frontend renders the human invariant text by code
        "strength": spec["strength"],
        "suspect_modules": list(spec["suspect_modules"]),
        "missing_evidence": list(_MISSING_EVIDENCE.get(code, [])),
        "count": int(finding.get("count", 0)),
        "examples": _norm_examples(finding),
        "needs_human_confirm": True,
    }


def _id_dup_leads(character: str, warnings: list[str]) -> list[dict[str, Any]]:
    """Detect duplicate primary ids in the raw facts/reflections/persona files.

    A uuid/id collision inside one container ⇒ an id-allocation or write-dedupe
    bug (the lineage graph silently folds duplicate ids, hiding it). High signal.
    Missing/empty/malformed files simply produce no lead (soft, via _read_json).
    """
    mem = _memory_dir(character)
    examples: list[dict[str, Any]] = []
    modules: set[str] = set()

    def _scan_flat(filename: str, module: str) -> None:
        rows = _read_json(mem / filename, expect=list, warnings=warnings)
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = row.get("id")
            if not rid:
                continue
            rid = str(rid)
            if rid in seen:
                examples.append({"id": rid, "label": f"{filename}"})
                modules.add(module)
            else:
                seen.add(rid)

    _scan_flat("facts.json", "memory/facts.py")
    _scan_flat("reflections.json", "memory/reflection/persistence.py")

    # persona.json = {entity: {"facts": [{"id": ...}]}}; ids live nested.
    persona = _read_json(mem / "persona.json", expect=dict, warnings=warnings)
    pseen: set[str] = set()
    for section in persona.values():
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
            if pid in pseen:
                examples.append({"id": pid, "label": "persona.json"})
                modules.add("memory/persona/persistence.py")
            else:
                pseen.add(pid)

    if not examples:
        return []
    return [{
        "code": "ID-DUP",
        "invariant": "ID-DUP",
        "strength": "high",
        "suspect_modules": sorted(modules),
        "missing_evidence": list(_MISSING_EVIDENCE["ID-DUP"]),
        "count": len(examples),
        "examples": examples[:20],
        "needs_human_confirm": True,
    }]


def _evt_dup_lead(
    character: str, warnings: list[str],
) -> tuple[list[dict[str, Any]], str]:
    """Detect duplicate ``event_id`` in events.ndjson → (leads, evt_status).

    uuid4 event_ids cannot collide naturally, so a repeat is an append/reconcile
    write bug in ``memory/event_log.py``. Returns evt_status: ``unavailable`` when
    the file is absent (NOT "passed"), ``truncated`` when the scan ceiling is hit,
    else ``ran``.
    """
    path = _memory_dir(character) / "events.ndjson"
    if not path.exists():
        return [], "unavailable"

    seen: set[str] = set()
    dup_examples: list[dict[str, Any]] = []
    bad_lines = 0
    truncated = False
    try:
        with path.open("r", encoding="utf-8") as fp:
            for i, line in enumerate(fp):
                if i >= _EVT_SCAN_MAX_LINES:
                    truncated = True
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                if not isinstance(obj, dict):
                    bad_lines += 1
                    continue
                eid = obj.get("event_id")
                if not eid:
                    bad_lines += 1
                    continue
                eid = str(eid)
                if eid in seen:
                    if len(dup_examples) < 20:
                        dup_examples.append(
                            {"id": eid, "label": str(obj.get("type", ""))})
                else:
                    seen.add(eid)
    except OSError as exc:
        warnings.append(f"events.ndjson 读取失败 ({type(exc).__name__}): {exc}")
        return [], "unavailable"

    if bad_lines:
        warnings.append(f"events.ndjson 有 {bad_lines} 行无法解析或缺 event_id")

    status = "truncated" if truncated else "ran"
    if not dup_examples:
        return [], status
    return [{
        "code": "EVT-DUP",
        "invariant": "EVT-DUP",
        "strength": "high",
        "suspect_modules": ["memory/event_log.py"],
        "missing_evidence": list(_MISSING_EVIDENCE["EVT-DUP"]),
        "count": len(dup_examples),
        "examples": dup_examples,
        "needs_human_confirm": True,
    }], status


_STRENGTH_RANK = {"high": 0, "medium": 1, "low": 2}


def build_code_leads(character: str) -> dict[str, Any]:
    """Assemble the read-only code-leads payload for ``character``.

    Never raises for absent / malformed memory data — degrades to soft warnings
    and honest status flags (blueprint §2.5). See module docstring for rails.
    """
    character = str(character or "").strip()
    ov = build_overview(character)
    findings = ov.get("findings", [])
    meta = ov.get("meta", {}) or {}
    has_emb = bool(meta.get("generated_with_embeddings"))
    warnings: list[str] = list(meta.get("warnings") or [])

    leads: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for f in findings:
        code = f.get("code")
        if code in MECHANICAL_LEAD_CODES:
            leads.append(_lead_from_finding(f))
        else:
            excluded.append({
                "code": code,
                "category": f.get("category", ""),
                "count": int(f.get("count", 0)),
            })

    leads.extend(_id_dup_leads(character, warnings))
    evt_leads, evt_status = _evt_dup_lead(character, warnings)
    leads.extend(evt_leads)

    leads.sort(key=lambda l: (_STRENGTH_RANK.get(l["strength"], 9), l["code"]))

    return {
        "character": character,
        "leads": leads,
        "excluded_content_findings": excluded,
        "embedding_status": "ran" if has_emb else "unavailable",
        "evt_status": evt_status,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
