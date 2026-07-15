"""P39 (P29) — memory system overview aggregator smoke.

Guards the read-only overview chokepoint
(``tests/testbench/pipeline/memory_overview.py``) behind the 系统概况 sub-page:
``GET /api/memory/overview`` + ``POST /api/memory/overview/{ai_report,contradictions}``.

Contracts under test
--------------------
O1 — **payload shape**: cards (composition/coverage/space/clusters/pipeline) +
     findings[] (code/category/stage/severity/count/data/drill) + attention_count
     (== #findings with severity warn|bad) + meta.confidence.
O2 — **redundancy**: a known near-duplicate pair surfaces A1 (drill → embedding
     duplicates) and the redundancy-cost A3.
O3 — **contradiction honesty (blueprint §6)**: a recorded correction whose
     old_text still lives in persona surfaces B1 (L0) + N1 (unresolved, bad).
     A same-topic similar pair surfaces B2 with severity **info** — a candidate
     for review, NOT a contradiction verdict. The rules pass NEVER asserts a
     "contradiction" beyond the recorded L0 signals.
O4 — **embedding health**: missing / stale / off-dim entries surface E1/E2/E4.
O5 — **structure**: an orphan reflection (no sources) → D1; an orphan persona
     (source reflection missing) → D2; a merge-promoted persona (source_id gone
     but merged_from edges live) is NOT D2; a reflection citing a hard-deleted
     source fact → D4 (referential integrity, distinct from D1).
O6 — **pipeline / fidelity / retention**: high reject rate → F4; an aged pending
     reflection → F3; a promoted persona that drifted from its source → G1;
     a high-importance unused fact → H1; a too-short fact → H2; an aged unused
     fact → H3.
O7 — **gating**: a zero-vector character still returns 200 with structural
     findings; vector findings (A/B2/C/G) are absent and meta.confidence.notes
     carries NO_EMBEDDINGS.
O8 — **errors**: no session → 404; no character → 409 NoCharacterSelected.
O9 — **LLM endpoints degrade, never 500**: ai_report → method 'unavailable'
     with an actionable warning when no memory model is configured; contradictions
     → 200, method in {llm, unavailable, none}, candidates returned.
O10 — **archived source fact is present, not deleted**: a reflection citing a
     fact that was absorbed then moved to facts_archive.json must NOT trip D4 (it
     materialises as a ``fact_archived`` lineage node in the fact lane with a live
     reflection<-fact edge); only a citation to an id in neither pool stays D4.
     Active fact count / composition stay unpolluted by archived facts.

Environment isolation mirrors p37_embedding_space_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p39_memory_overview_smoke.py
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── Env setup — must run before any testbench import ────────────────────


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p39_overview_"))
    os.environ["TESTBENCH_DATA_DIR"] = str(tmp_data)
    from tests.testbench import config as tb_config
    tb_config.DATA_DIR = tmp_data
    tb_config.SAVED_SESSIONS_DIR = tmp_data / "saved_sessions"
    tb_config.AUTOSAVE_DIR = tmp_data / "saved_sessions" / "_autosave"
    tb_config.LOGS_DIR = tmp_data / "logs"
    tb_config.SANDBOXES_DIR = tmp_data / "sandboxes"
    for d in [
        tb_config.SAVED_SESSIONS_DIR,
        tb_config.AUTOSAVE_DIR,
        tb_config.LOGS_DIR,
        tb_config.SANDBOXES_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
    return tmp_data


# ── Helpers ─────────────────────────────────────────────────────────────


class _AssertFail(Exception):
    pass


def _check(cond: bool, label: str, msg: str = "") -> None:
    if not cond:
        detail = f" — {msg}" if msg else ""
        raise _AssertFail(f"[{label}]{detail}")


def _create_session(client, name: str, *, with_character: bool = True) -> None:
    r = client.post("/api/session", json={"name": name})
    assert r.status_code == 201, f"create session failed: {r.status_code} {r.text}"
    if with_character:
        r = client.put("/api/persona", json={
            "character_name": "NEKO",
            "master_name": "Master",
            "language": "zh-CN",
            "system_prompt": "You are {LANLAN_NAME}.",
        })
        assert r.status_code == 200, f"persona PUT failed: {r.text}"


def _delete_session(client) -> None:
    try:
        client.delete("/api/session")
    except Exception:
        pass


def _mem_dir() -> Path:
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    p = Path(str(cm.memory_dir)) / "NEKO"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _unit(vec: list[float]):
    import numpy as np
    a = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(a))
    return (a / n).tolist() if n > 0 else a.tolist()


_MODEL_ID = "local-text-retrieval-v1-8d-int8-mlen1024"
_RECENT = "2026-06-29T12:00:00"
_OLD = "2020-01-01T00:00:00"


def _entry(eid: str, text: str, vec: list[float] | None, *,
           entity: str = "master", created_at: str = _RECENT,
           extra: dict | None = None, stale: bool = False) -> dict:
    from memory.embeddings import stamp_embedding_fields
    e: dict[str, Any] = {"id": eid, "text": text, "entity": entity,
                         "created_at": created_at}
    if extra:
        e.update(extra)
    if vec is not None:
        stamp_embedding_fields(e, _unit(vec), text, _MODEL_ID)
        if stale:
            e["text"] = text + "（已编辑）"
    else:
        e["embedding"] = None
        e["embedding_text_sha256"] = None
        e["embedding_model_id"] = None
    return e


def _seed_rich_memory() -> None:
    """A character engineered to trip a representative spread of findings."""
    mem = _mem_dir()
    e0 = [1, 0, 0, 0, 0, 0, 0, 0]
    e1 = [0, 1, 0, 0, 0, 0, 0, 0]
    e3 = [0, 0, 1, 0, 0, 0, 0, 0]
    e4 = [0, 0, 0, 1, 0, 0, 0, 0]
    e5 = [0, 0, 0, 0, 0, 1, 0, 0]
    e6 = [0, 0, 0, 0, 0, 0, 1, 0]
    e7 = [0, 0, 0, 0, 0, 0, 0, 1]
    b2 = [0.7, 0.714, 0, 0, 0, 0, 0, 0]  # ~0.70 cosine with e0 → B2 candidate

    _write_json(mem / "facts.json", [
        _entry("fact_a", "主人喜欢深夜调试代码", e0, extra={"importance": 5}),
        _entry("fact_b", "主人爱喝美式咖啡", e1, extra={"importance": 5}),
        _entry("fact_hi", "主人对隐私极度敏感且重要", e3, extra={"importance": 9}),  # H1
        _entry("fact_zombie", "很久以前的无用事实", e4,
               created_at=_OLD, extra={"importance": 2, "absorbed": False}),     # H3
        _entry("fact_short", "嗯", e6, extra={"importance": 3}),                  # H2
        _entry("fact_b2", "主人偶尔也点拿铁咖啡", b2, extra={"importance": 4}),    # B2 partner
        _entry("fact_missing", "未嵌入的事实", None),                            # E1
        _entry("fact_stale", "改过文的事实", e7, stale=True),                     # E2
        _entry("fact_offdim", "另一个向量空间", [1, 0, 0, 0]),                    # E4 (4-d)
    ])
    _write_json(mem / "reflections.json", [
        _entry("ref_1", "主人偏好黑咖啡", [0.05, 0.99, 0, 0, 0, 0, 0, 0],
               extra={"status": "promoted", "source_fact_ids": ["fact_a"]}),     # C1 + promote
        _entry("ref_orphan", "无来源的反思", None,
               created_at=_OLD, extra={"status": "pending", "source_fact_ids": []}),  # D1 + F3
        _entry("ref_denied", "被否决的反思甲", None,
               # fact_b exists; fact_ghost_deleted was hard-deleted but is still
               # cited → D4 dangling source reference (also non-embedded, which
               # the bridges view can't surface — D4 catches it structurally).
               extra={"status": "denied",
                      "source_fact_ids": ["fact_b", "fact_ghost_deleted"]}),     # B1 + F4 + D4
        _entry("ref_denied2", "被否决的反思乙", None,
               extra={"status": "denied", "source_fact_ids": []}),               # B1 + F4 + D1
    ])
    _write_json(mem / "persona.json", {
        "master": {
            "facts": [
                _entry("p_1", "主人是夜猫子", [0.97, 0.05, 0, 0, 0, 0, 0, 0],
                       extra={"source": "manual"}),                              # A1 dup w/ fact_a
                _entry("p_drift", "主人讨厌猫", e5,
                       extra={"source": "reflection", "source_id": "ref_1"}),    # G1 drift
                _entry("p_suppress", "被抑制的人设", None,
                       extra={"source": "manual", "suppress": True}),            # B1 suppressed
                _entry("p_orphan", "孤儿人设", None,
                       extra={"source": "reflection", "source_id": "ref_nope"}),  # D2
                # Promoted-by-merge: its single source_id was consumed in the
                # merge (gone), but merged_from_ids point at live reflections —
                # the lineage graph draws merged_from edges, so this is NOT an
                # orphan. D2 must skip it (issue-2 regression).
                _entry("p_merged", "合并晋升的人设", None,
                       extra={"source": "reflection", "source_id": "ref_gone",
                              "merged_from_ids": ["ref_1", "ref_denied"]}),
                _entry("p_corrected", "主人不喝酒", None,
                       extra={"source": "manual"}),                              # N1 target
            ]
        },
    })
    _write_json(mem / "persona_corrections.json", [
        {"old_text": "主人不喝酒", "new_text": "主人其实小酌", "entity": "master",
         "created_at": _RECENT},  # → N1 unresolved (old_text still active in persona)
    ])


def _seed_archived_memory() -> None:
    """A reflection that cites an absorbed-then-archived fact + one truly gone.

    ``fact_arch`` lives in facts_archive.json (moved there when absorbed), still
    cited by ``ref_ok``; ``fact_gone_forever`` exists nowhere. D4 must flag ONLY
    the latter, and the archived one must materialise as a ``fact_archived`` node
    with a live reflection<-fact edge (P29 dangling-source false-positive fix).
    """
    mem = _mem_dir()
    _write_json(mem / "facts.json", [
        {"id": "fact_active", "text": "主人喜欢深夜写代码", "entity": "master",
         "importance": 5, "created_at": _RECENT},
    ])
    _write_json(mem / "facts_archive.json", [
        {"id": "fact_arch", "text": "主人常喝美式咖啡", "entity": "master",
         "importance": 6, "absorbed": True, "created_at": _OLD},
        # An archived fact NO reflection cites — must stay unmaterialised.
        {"id": "fact_arch_unused", "text": "无人引用的归档事实", "entity": "master",
         "importance": 4, "absorbed": True, "created_at": _OLD},
    ])
    _write_json(mem / "reflections.json", [
        {"id": "ref_ok", "text": "主人偏好黑咖啡且爱夜里工作", "entity": "master",
         "status": "promoted",
         "source_fact_ids": ["fact_active", "fact_arch"], "created_at": _RECENT},
        {"id": "ref_dangling", "text": "引用真删事实的反思", "entity": "master",
         "status": "pending",
         "source_fact_ids": ["fact_gone_forever"], "created_at": _RECENT},
    ])


def _codes(findings: list[dict]) -> dict[str, dict]:
    return {f["code"]: f for f in findings}


# ── Cases ───────────────────────────────────────────────────────────────


def check_o1_o6_rich(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "o_rich")
        _seed_rich_memory()
        r = client.get("/api/memory/overview")
        _check(r.status_code == 200, "O1.status", f"{r.status_code} {r.text[:200]}")
        body = r.json()

        # O1 — shape.
        cards = body["cards"]
        for k in ("composition", "coverage", "space", "clusters", "pipeline"):
            _check(k in cards, "O1.cards", f"missing card {k}")
        findings = body["findings"]
        _check(isinstance(findings, list) and findings, "O1.has_findings", f"{len(findings)}")
        for f in findings:
            for k in ("code", "category", "stage", "severity", "count", "data"):
                _check(k in f, "O1.finding_keys", f"{f.get('code')} missing {k}")
            _check(f["severity"] in ("bad", "warn", "info"), "O1.sev", f"{f}")
        att = body["attention_count"]
        want_att = sum(1 for f in findings if f["severity"] in ("bad", "warn"))
        _check(att == want_att, "O1.attention_count", f"{att} != {want_att}")
        _check("confidence" in body["meta"], "O1.confidence", f"{body['meta'].keys()}")
        _check(body["meta"]["generated_with_embeddings"] is True, "O1.has_emb")

        c = _codes(findings)

        # O2 — redundancy.
        _check("A1" in c, "O2.A1", f"codes={list(c)}")
        _check(c["A1"]["drill"]["page"] == "embedding"
               and c["A1"]["drill"]["opts"]["mode"] == "duplicates",
               "O2.A1_drill", f"{c['A1']['drill']}")
        _check("A3" in c, "O2.A3", f"codes={list(c)}")

        # O3 — contradiction honesty.
        _check("B1" in c and c["B1"]["category"] == "contradiction", "O3.B1", f"{list(c)}")
        _check(c["B1"]["data"]["denied"] == 2 and c["B1"]["data"]["suppressed"] == 1
               and c["B1"]["data"]["corrections"] == 1, "O3.B1_data", f"{c['B1']['data']}")
        _check("N1" in c and c["N1"]["severity"] == "bad", "O3.N1", f"{c.get('N1')}")
        _check("B2" in c and c["B2"]["severity"] == "info", "O3.B2_info",
               f"{c.get('B2')}")
        # Honesty: the rules pass must NEVER emit an LLM-style contradiction verdict;
        # the only contradiction-category codes allowed are B1/N1 (recorded) + B2 (candidate).
        contra_codes = {f["code"] for f in findings if f["category"] == "contradiction"}
        _check(contra_codes <= {"B1", "N1", "B2"}, "O3.no_verdict", f"{contra_codes}")

        # O4 — embedding health.
        for code in ("E1", "E2", "E4"):
            _check(code in c, "O4.health", f"{code} not in {list(c)}")

        # O5 — structure.
        _check("D1" in c, "O5.D1", f"{list(c)}")
        _check("D2" in c, "O5.D2", f"{list(c)}")
        # D2 derives orphan-ness from the lineage's OWN source edges, so a
        # merge-promoted persona (source_id gone, merged_from_ids live) is NOT
        # an orphan, while the truly-sourceless p_orphan still is (issue-2).
        d2_ids = {ex.get("id") for ex in c["D2"].get("examples", [])}
        _check("p_orphan" in d2_ids, "O5.D2_real_orphan", f"{d2_ids}")
        _check("p_merged" not in d2_ids, "O5.D2_skips_merged", f"{d2_ids}")
        _check(c["D2"]["count"] == 1, "O5.D2_count", f"{c['D2']['count']}")
        # D4 — a reflection citing a hard-deleted source fact (referential
        # integrity); distinct from D1 (it has a valid source too).
        _check("D4" in c, "O5.D4", f"{list(c)}")
        _check(c["D4"]["data"]["dangling_refs"] >= 1, "O5.D4_count", f"{c['D4']}")
        d4_ids = {ex.get("id") for ex in c["D4"].get("examples", [])}
        _check("ref_denied" in d4_ids, "O5.D4_ref", f"{d4_ids}")

        # O6 — pipeline / fidelity / retention.
        _check("F4" in c, "O6.F4", f"{list(c)}")
        _check("F3" in c, "O6.F3", f"{list(c)}")
        _check("G1" in c and c["G1"]["data"]["unverifiable"] >= 1, "O6.G1",
               f"{c.get('G1')}")
        for code in ("H1", "H2", "H3"):
            _check(code in c, "O6.retention", f"{code} not in {list(c)}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[O1.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_o7_gating(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "o_novec")
        mem = _mem_dir()
        # Structural-only character: facts + a denied reflection, no vectors.
        _write_json(mem / "facts.json", [
            {"id": "f1", "text": "无向量的事实一二三", "entity": "master",
             "importance": 3, "created_at": _RECENT},
        ])
        _write_json(mem / "reflections.json", [
            {"id": "r1", "text": "无来源反思", "entity": "master",
             "status": "pending", "source_fact_ids": [], "created_at": _OLD},
        ])
        r = client.get("/api/memory/overview")
        _check(r.status_code == 200, "O7.status", f"{r.status_code}")
        body = r.json()
        _check(body["meta"]["generated_with_embeddings"] is False, "O7.no_emb")
        notes = body["meta"]["confidence"]["notes"]
        _check("NO_EMBEDDINGS" in notes, "O7.note", f"{notes}")
        c = _codes(body["findings"])
        # Vector-only findings must be absent without embeddings.
        for code in ("A1", "A2", "A3", "B2", "C1", "C2", "G1"):
            _check(code not in c, "O7.vector_absent", f"{code} present without vectors")
        # Structural findings still run (D1 orphan reflection).
        _check("D1" in c, "O7.structural_present", f"{list(c)}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[O7.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_o8_errors(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        r = client.get("/api/memory/overview")
        _check(r.status_code == 404, "O8.no_session", f"{r.status_code}")
        _create_session(client, "o_nochar", with_character=False)
        r = client.get("/api/memory/overview")
        _check(r.status_code == 409, "O8.no_char", f"{r.status_code} {r.text[:160]}")
        detail = (r.json() or {}).get("detail", {})
        err_type = detail.get("error_type") if isinstance(detail, dict) else None
        _check(err_type == "NoCharacterSelected", "O8.no_char_type", f"{err_type!r}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[O8.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_o9_llm_degrade(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "o_llm")
        _seed_rich_memory()

        # ai_report: no memory model configured → graceful 'unavailable' + reason.
        r = client.post("/api/memory/overview/ai_report")
        _check(r.status_code == 200, "O9.ai_status", f"{r.status_code} {r.text[:200]}")
        ai = r.json()
        _check(ai["method"] in ("llm", "unavailable"), "O9.ai_method", f"{ai['method']}")
        if ai["method"] == "unavailable":
            _check(len(ai.get("warnings", [])) >= 1, "O9.ai_reason", f"{ai}")
        _check("overview" in ai and "cards" in ai["overview"], "O9.ai_overview", f"{list(ai)}")

        # contradictions: candidates exist (B2), LLM degrades but never 500s.
        r = client.post("/api/memory/overview/contradictions")
        _check(r.status_code == 200, "O9.contra_status", f"{r.status_code} {r.text[:200]}")
        cn = r.json()
        _check(cn["method"] in ("llm", "unavailable", "none"), "O9.contra_method",
               f"{cn['method']}")
        if cn["method"] != "none":
            _check(isinstance(cn.get("candidates"), list) and cn["candidates"],
                   "O9.contra_candidates", f"{cn}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[O9.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_o10_archived_facts(client) -> list[str]:
    """O10 — an archived (absorbed) source fact is 'present', not 'deleted'.

    Regression for the P29 dangling-source false positive: a reflection citing a
    fact that was absorbed then moved to facts_archive.json must NOT trip D4, and
    the archived fact must appear as a ``fact_archived`` lineage node (same lane
    as active facts) with a live reflection<-fact edge. Only a citation to an id
    that exists in neither pool (``fact_gone_forever``) stays a D4 dangling ref.
    """
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "o_archived")
        _seed_archived_memory()

        # ── lineage: archived fact materialised + edge, active count unpolluted.
        r = client.get("/api/memory/lineage")
        _check(r.status_code == 200, "O10.lin_status", f"{r.status_code} {r.text[:160]}")
        lin = r.json()
        nodes = {n["id"]: n for n in lin["nodes"]}
        _check("fact_arch" in nodes, "O10.arch_node", f"nodes={list(nodes)}")
        _check(nodes["fact_arch"]["type"] == "fact_archived", "O10.arch_type",
               f"{nodes['fact_arch'].get('type')}")
        _check(nodes["fact_arch"]["lane"] == nodes["fact_active"]["lane"],
               "O10.arch_lane", "archived fact must share the fact lane")
        # Only referenced archived facts are materialised (budget discipline).
        _check("fact_arch_unused" not in nodes, "O10.arch_unused_skipped",
               "un-referenced archived fact must NOT be materialised")
        edge_set = {(e["source"], e["target"]) for e in lin["edges"]}
        _check(("fact_arch", "ref_ok") in edge_set, "O10.arch_edge",
               "reflection<-archived-fact edge missing")
        counts = lin["meta"]["counts"]
        _check(counts.get("facts_archived") == 1, "O10.arch_count", f"{counts}")
        _check(counts.get("facts") == 1, "O10.active_unpolluted", f"{counts}")

        # ── overview: ref_ok not dangling; only ref_dangling trips D4.
        r = client.get("/api/memory/overview")
        _check(r.status_code == 200, "O10.ov_status", f"{r.status_code} {r.text[:160]}")
        body = r.json()
        comp = body["cards"]["composition"]
        _check(comp.get("facts") == 1, "O10.comp_facts", f"{comp}")
        _check(comp.get("facts_archived") == 1, "O10.comp_archived", f"{comp}")
        c = _codes(body["findings"])
        _check("D4" in c, "O10.D4_present", f"expected D4 for the真删 ref; codes={list(c)}")
        _check(c["D4"]["data"]["dangling_refs"] == 1, "O10.D4_only_real",
               f"only fact_gone_forever should dangle: {c['D4']['data']}")
        d4_ids = {ex.get("id") for ex in c["D4"].get("examples", [])}
        _check("ref_dangling" in d4_ids, "O10.D4_keeps_real", f"{d4_ids}")
        _check("ref_ok" not in d4_ids, "O10.D4_excludes_archived", f"{d4_ids}")
        # D1 orphan: ref_ok has valid sources (one active + one archived).
        if "D1" in c:
            d1_ids = {ex.get("id") for ex in c["D1"].get("examples", [])}
            _check("ref_ok" not in d1_ids, "O10.D1_excludes_archived_sourced",
                   f"{d1_ids}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[O10.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


# ── Orchestration ───────────────────────────────────────────────────────


def _report(title: str, errors: list[str]) -> int:
    print("")
    print(f"* {title}")
    if not errors:
        print("  [ok]")
        return 0
    print(f"  [ERR] {len(errors)} violation(s):")
    for line in errors:
        print(f"    {line}")
    return len(errors)


def main() -> int:
    print("=" * 66)
    print(" P39 (P29) — memory system overview aggregator smoke")
    print("=" * 66)

    _setup_env()

    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)

    total = 0
    total += _report("O1-O6 — rich payload: cards + all finding groups",
                     check_o1_o6_rich(client))
    total += _report("O7 — gating: zero-vector character", check_o7_gating(client))
    total += _report("O8 — error mapping (no session / no character)",
                     check_o8_errors(client))
    total += _report("O9 — LLM endpoints degrade, never 500",
                     check_o9_llm_degrade(client))
    total += _report("O10 — archived source fact is present, not deleted (D4 fix)",
                     check_o10_archived_facts(client))

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in overview smoke.")
        return 1
    print(" [PASS] overview contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
