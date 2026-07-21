"""P45 (P32) — memory **code leads** aggregator smoke.

Guards the read-only code-leads chokepoint
(``tests/testbench/pipeline/memory_code_leads.py``) behind the 代码线索 (开发者)
sub-page: ``GET /api/memory/code_leads``.

Contracts under test
--------------------
Y1 — **mechanical findings map to leads**: the rich fixture trips D4/D2/D1/E2/E4;
     each surfaces as a lead with a real suspect module + missing_evidence +
     needs_human_confirm==True.
Y2 — **content-quality findings are NOT leads**: A1/B1/N1/C1/G1/H*/... land in
     ``excluded_content_findings`` (counted), never in ``leads``.
Y3 — **ID-DUP**: a duplicate primary id inside facts.json (flat) AND persona.json
     (nested persona[entity].facts[].id) → one high-strength ID-DUP lead whose
     examples cite the duplicate ids.
Y4 — **EVT-DUP**: a duplicate event_id in events.ndjson → high-strength EVT-DUP
     lead + evt_status=="ran"; all-unique → no EVT-DUP, evt_status=="ran";
     missing events.ndjson → evt_status=="unavailable" (NOT "passed").
Y5 — **endpoint happy shape**: 200 + leads / excluded_content_findings /
     embedding_status / evt_status / warnings.
Y6 — **errors**: no session → 404; no character → 409 NoCharacterSelected.
Y7 — **honest empty**: a clean character (facts only, no invariant violations)
     → leads == [].
Y8 — **honest status (LR-1)**: a zero-vector character → embedding_status ==
     "unavailable" and NO E* leads (absence of E* ≠ "vectors are fine").
Y9 — **full classification, no silent drop (LR-2)**: for the rich fixture every
     finding code the overview emits is either mapped to a lead or counted in
     excluded — no finding is silently discarded.

Environment isolation mirrors p39_memory_overview_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p45_memory_code_leads_smoke.py
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
    tmp_data = Path(tempfile.mkdtemp(prefix="p45_code_leads_"))
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


# ── Helpers (mirror p39) ────────────────────────────────────────────────


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
    """Same rich fixture as p39 — trips a representative spread of findings."""
    mem = _mem_dir()
    e0 = [1, 0, 0, 0, 0, 0, 0, 0]
    e1 = [0, 1, 0, 0, 0, 0, 0, 0]
    e3 = [0, 0, 1, 0, 0, 0, 0, 0]
    e4 = [0, 0, 0, 1, 0, 0, 0, 0]
    e5 = [0, 0, 0, 0, 0, 1, 0, 0]
    e6 = [0, 0, 0, 0, 0, 0, 1, 0]
    e7 = [0, 0, 0, 0, 0, 0, 0, 1]
    b2 = [0.7, 0.714, 0, 0, 0, 0, 0, 0]

    _write_json(mem / "facts.json", [
        _entry("fact_a", "主人喜欢深夜调试代码", e0, extra={"importance": 5}),
        _entry("fact_b", "主人爱喝美式咖啡", e1, extra={"importance": 5}),
        _entry("fact_hi", "主人对隐私极度敏感且重要", e3, extra={"importance": 9}),
        _entry("fact_zombie", "很久以前的无用事实", e4,
               created_at=_OLD, extra={"importance": 2, "absorbed": False}),
        _entry("fact_short", "嗯", e6, extra={"importance": 3}),
        _entry("fact_b2", "主人偶尔也点拿铁咖啡", b2, extra={"importance": 4}),
        _entry("fact_missing", "未嵌入的事实", None),
        _entry("fact_stale", "改过文的事实", e7, stale=True),
        _entry("fact_offdim", "另一个向量空间", [1, 0, 0, 0]),
    ])
    _write_json(mem / "reflections.json", [
        _entry("ref_1", "主人偏好黑咖啡", [0.05, 0.99, 0, 0, 0, 0, 0, 0],
               extra={"status": "promoted", "source_fact_ids": ["fact_a"]}),
        _entry("ref_orphan", "无来源的反思", None,
               created_at=_OLD, extra={"status": "pending", "source_fact_ids": []}),
        _entry("ref_denied", "被否决的反思甲", None,
               extra={"status": "denied",
                      "source_fact_ids": ["fact_b", "fact_ghost_deleted"]}),
        _entry("ref_denied2", "被否决的反思乙", None,
               extra={"status": "denied", "source_fact_ids": []}),
    ])
    _write_json(mem / "persona.json", {
        "master": {
            "facts": [
                _entry("p_1", "主人是夜猫子", [0.97, 0.05, 0, 0, 0, 0, 0, 0],
                       extra={"source": "manual"}),
                _entry("p_drift", "主人讨厌猫", e5,
                       extra={"source": "reflection", "source_id": "ref_1"}),
                _entry("p_suppress", "被抑制的人设", None,
                       extra={"source": "manual", "suppress": True}),
                _entry("p_orphan", "孤儿人设", None,
                       extra={"source": "reflection", "source_id": "ref_nope"}),
                _entry("p_merged", "合并晋升的人设", None,
                       extra={"source": "reflection", "source_id": "ref_gone",
                              "merged_from_ids": ["ref_1", "ref_denied"]}),
                _entry("p_corrected", "主人不喝酒", None,
                       extra={"source": "manual"}),
            ]
        },
    })
    _write_json(mem / "persona_corrections.json", [
        {"old_text": "主人不喝酒", "new_text": "主人其实小酌", "entity": "master",
         "created_at": _RECENT},
    ])


def _leads_by_code(payload: dict) -> dict[str, dict]:
    return {l["code"]: l for l in payload.get("leads", [])}


def _excluded_codes(payload: dict) -> set[str]:
    return {x["code"] for x in payload.get("excluded_content_findings", [])}


# ── Cases ───────────────────────────────────────────────────────────────


def check_y1_y2_y9_rich(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "cl_rich")
        _seed_rich_memory()
        r = client.get("/api/memory/code_leads")
        _check(r.status_code == 200, "Y5.status", f"{r.status_code} {r.text[:200]}")
        body = r.json()

        # Y5 — shape.
        for k in ("leads", "excluded_content_findings", "embedding_status",
                  "evt_status", "warnings"):
            _check(k in body, "Y5.keys", f"missing {k}")

        leads = _leads_by_code(body)
        # Y1 — mechanical invariants map to leads.
        for code in ("D4", "D2", "D1", "E2", "E4"):
            _check(code in leads, "Y1.mapped", f"{code} not in leads {list(leads)}")
        for code, lead in leads.items():
            _check(lead.get("needs_human_confirm") is True, "Y1.confirm", f"{code}")
            _check(lead.get("strength") in ("high", "medium", "low"),
                   "Y1.strength", f"{code} {lead.get('strength')}")
            _check(isinstance(lead.get("suspect_modules"), list)
                   and lead["suspect_modules"], "Y1.suspect", f"{code}")
            _check(isinstance(lead.get("missing_evidence"), list),
                   "Y1.missing_ev", f"{code}")
        # D4 suspect modules must point at a real main-program path.
        _check(any("memory/facts.py" in m for m in leads["D4"]["suspect_modules"]),
               "Y1.D4_suspect", f"{leads['D4']['suspect_modules']}")

        # Y2 — content-quality findings are excluded, never leads.
        excl = _excluded_codes(body)
        for code in ("A1", "B1", "N1", "C1", "H1"):
            _check(code in excl, "Y2.excluded", f"{code} not in excluded {excl}")
            _check(code not in leads, "Y2.not_lead", f"{code} wrongly a lead")

        # Y9 — full classification: every overview finding code is classified.
        ov = client.get("/api/memory/overview").json()
        all_codes = {f["code"] for f in ov.get("findings", [])}
        classified = set(leads) | excl
        missing = all_codes - classified
        _check(not missing, "Y9.no_silent_drop",
               f"unclassified finding codes: {missing}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[Y1.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_y3_id_dup(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "cl_iddup")
        mem = _mem_dir()
        # facts.json: same id twice (flat container).
        _write_json(mem / "facts.json", [
            {"id": "fact_dup", "text": "重复主键的事实一", "entity": "master",
             "created_at": _RECENT},
            {"id": "fact_dup", "text": "重复主键的事实二", "entity": "master",
             "created_at": _RECENT},
        ])
        # persona.json: nested duplicate id inside persona[entity].facts[].
        _write_json(mem / "persona.json", {
            "master": {"facts": [
                {"id": "p_dup", "text": "人设甲", "entity": "master",
                 "created_at": _RECENT, "source": "manual"},
                {"id": "p_dup", "text": "人设乙", "entity": "master",
                 "created_at": _RECENT, "source": "manual"},
            ]},
        })
        body = client.get("/api/memory/code_leads").json()
        leads = _leads_by_code(body)
        _check("ID-DUP" in leads, "Y3.present", f"{list(leads)}")
        lead = leads["ID-DUP"]
        _check(lead["strength"] == "high", "Y3.strength", f"{lead['strength']}")
        ex_ids = {e.get("id") for e in lead.get("examples", [])}
        _check("fact_dup" in ex_ids, "Y3.flat_dup", f"{ex_ids}")
        _check("p_dup" in ex_ids, "Y3.nested_dup", f"{ex_ids}")
        mods = " ".join(lead.get("suspect_modules", []))
        _check("persona" in mods and "facts" in mods, "Y3.suspect", f"{mods}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[Y3.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_y4_evt_dup(client) -> list[str]:
    errors: list[str] = []
    try:
        # (a) duplicate event_id → EVT-DUP lead + evt_status ran.
        _delete_session(client)
        _create_session(client, "cl_evtdup")
        mem = _mem_dir()
        _write_json(mem / "facts.json", [
            {"id": "f1", "text": "普通事实一二三四五", "entity": "master",
             "created_at": _RECENT}])
        lines = [
            {"event_id": "evt-1", "type": "fact_added", "ts": _RECENT, "payload": {}},
            {"event_id": "evt-2", "type": "fact_added", "ts": _RECENT, "payload": {}},
            {"event_id": "evt-1", "type": "fact_added", "ts": _RECENT, "payload": {}},
        ]
        (mem / "events.ndjson").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
            encoding="utf-8")
        body = client.get("/api/memory/code_leads").json()
        leads = _leads_by_code(body)
        _check("EVT-DUP" in leads, "Y4.present", f"{list(leads)}")
        _check(leads["EVT-DUP"]["strength"] == "high", "Y4.strength",
               f"{leads['EVT-DUP']['strength']}")
        _check("memory/event_log.py" in leads["EVT-DUP"]["suspect_modules"],
               "Y4.suspect", f"{leads['EVT-DUP']['suspect_modules']}")
        _check(body["evt_status"] == "ran", "Y4.status_ran", f"{body['evt_status']}")

        # (b) all-unique event_ids → no EVT-DUP, evt_status ran.
        _delete_session(client)
        _create_session(client, "cl_evtok")
        mem = _mem_dir()
        _write_json(mem / "facts.json", [
            {"id": "f1", "text": "普通事实一二三四五", "entity": "master",
             "created_at": _RECENT}])
        uniq = [
            {"event_id": "u-1", "type": "fact_added", "ts": _RECENT, "payload": {}},
            {"event_id": "u-2", "type": "fact_added", "ts": _RECENT, "payload": {}},
        ]
        (mem / "events.ndjson").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in uniq) + "\n",
            encoding="utf-8")
        body = client.get("/api/memory/code_leads").json()
        _check("EVT-DUP" not in _leads_by_code(body), "Y4.no_dup", "unexpected EVT-DUP")
        _check(body["evt_status"] == "ran", "Y4.ok_ran", f"{body['evt_status']}")

        # (c) no events.ndjson → evt_status unavailable (NOT "passed").
        _delete_session(client)
        _create_session(client, "cl_evtnone")
        mem = _mem_dir()
        _write_json(mem / "facts.json", [
            {"id": "f1", "text": "普通事实一二三四五", "entity": "master",
             "created_at": _RECENT}])
        body = client.get("/api/memory/code_leads").json()
        _check(body["evt_status"] == "unavailable", "Y4.unavailable",
               f"{body['evt_status']}")
        _check("EVT-DUP" not in _leads_by_code(body), "Y4.none_no_dup", "")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[Y4.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_y6_errors(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        r = client.get("/api/memory/code_leads")
        _check(r.status_code == 404, "Y6.no_session", f"{r.status_code}")
        _create_session(client, "cl_nochar", with_character=False)
        r = client.get("/api/memory/code_leads")
        _check(r.status_code == 409, "Y6.no_char", f"{r.status_code} {r.text[:160]}")
        detail = (r.json() or {}).get("detail", {})
        err_type = detail.get("error_type") if isinstance(detail, dict) else None
        _check(err_type == "NoCharacterSelected", "Y6.no_char_type", f"{err_type!r}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[Y6.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_y7_honest_empty(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "cl_clean")
        mem = _mem_dir()
        # Clean: one embedded fact referenced by a promoted reflection → no
        # dangling refs, no orphans, no dup ids, no stale/corrupt vectors.
        _write_json(mem / "facts.json", [
            _entry("f_ok", "主人喜欢清晰的架构", [1, 0, 0, 0, 0, 0, 0, 0],
                   extra={"importance": 5})])
        _write_json(mem / "reflections.json", [
            _entry("r_ok", "主人重视代码质量", [0, 1, 0, 0, 0, 0, 0, 0],
                   extra={"status": "promoted", "source_fact_ids": ["f_ok"]})])
        body = client.get("/api/memory/code_leads").json()
        _check(body.get("leads") == [], "Y7.empty_leads", f"{body.get('leads')}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[Y7.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_y8_no_vectors(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "cl_novec")
        mem = _mem_dir()
        # Structural-only: a reflection citing a hard-deleted fact (D4) but NO
        # vectors at all → embedding_status unavailable, no E* leads.
        _write_json(mem / "facts.json", [
            {"id": "f1", "text": "无向量的事实一二三", "entity": "master",
             "importance": 3, "created_at": _RECENT}])
        _write_json(mem / "reflections.json", [
            {"id": "r1", "text": "引用了不存在事实的反思", "entity": "master",
             "status": "pending", "source_fact_ids": ["f_gone"],
             "created_at": _RECENT}])
        body = client.get("/api/memory/code_leads").json()
        _check(body["embedding_status"] == "unavailable", "Y8.emb_status",
               f"{body['embedding_status']}")
        leads = _leads_by_code(body)
        for code in ("E2", "E3", "E4"):
            _check(code not in leads, "Y8.no_e_lead",
                   f"{code} present without vectors")
        # A structural lead (D4) still runs without vectors.
        _check("D4" in leads, "Y8.structural_ok", f"{list(leads)}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[Y8.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
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
    print(" P45 (P32) — memory code leads aggregator smoke")
    print("=" * 66)

    _setup_env()

    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)

    total = 0
    total += _report("Y1/Y2/Y5/Y9 — rich: mechanical→leads, content→excluded, full class",
                     check_y1_y2_y9_rich(client))
    total += _report("Y3 — ID-DUP (flat + nested persona)", check_y3_id_dup(client))
    total += _report("Y4 — EVT-DUP (dup / unique / missing)", check_y4_evt_dup(client))
    total += _report("Y6 — error mapping (no session / no character)",
                     check_y6_errors(client))
    total += _report("Y7 — honest empty (clean character)", check_y7_honest_empty(client))
    total += _report("Y8 — honest status (no vectors → unavailable)",
                     check_y8_no_vectors(client))

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in code leads smoke.")
        return 1
    print(" [PASS] code leads contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
