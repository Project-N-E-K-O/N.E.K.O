"""P32 (P27.1) — memory lineage aggregator + conversation corpus smoke.

Guards the read-only ``GET /api/memory/lineage`` chokepoint and the P27.0
conversation corpus reader it depends on.

Contracts under test
--------------------
L1 — **Tier A structural DAG**:
     facts + reflections(source_fact_ids) + persona(source=reflection) →
     snapshot has the exact ``source_fact`` (fact→reflection) and
     ``promoted_from`` (reflection→persona) edges, all ``confidence=persisted``.

L2 — **node/edge shape stability** (locks the frontend contract): every node
     has ``{id,type,lane,label,status,entity,created_at,meta,warnings}`` and
     every edge ``{source,target,relation,confidence,score,note}``.

L3 — **db two-state + corpus**:
     (a) with ``time_indexed.db`` present → message nodes appear,
         ``meta.sources_present.time_indexed_db == True``, and the db file is
         deletable right after the request (handle released — Windows lock
         regression guard);
     (b) without a db → no message nodes from db, flag False, still 200.

L4 — **soft error**: a corrupt ``reflections.json`` does NOT 500; the snapshot
     still renders facts/persona and reports ``meta.file_warnings``.

L5 — **error mapping**: no active session → 404; session w/o character → 409
     ``NoCharacterSelected``.

L6 — **correction node**: a queued contradiction whose ``old_text`` matches a
     persona entry produces a ``correction`` node + ``corrects`` edge.

Environment isolation mirrors p25_r6_import_recent_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p32_memory_lineage_smoke.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── Env setup — must run before any testbench import ────────────────────


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p32_lineage_"))
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


def _seed_structural_memory() -> None:
    """facts + reflections(source_fact_ids) + persona(source=reflection)."""
    mem = _mem_dir()
    _write_json(mem / "facts.json", [
        {"id": "fact_a", "text": "主人喜欢深夜调试", "importance": 6,
         "entity": "master", "tags": ["习惯"], "created_at": "2026-04-18T12:00:00",
         "absorbed": True},
        {"id": "fact_b", "text": "小天是傲娇猫娘", "importance": 5,
         "entity": "neko", "tags": ["设定"], "created_at": "2026-04-18T12:01:00",
         "absorbed": True},
        {"id": "fact_c", "text": "未吸收的事实", "importance": 3,
         "entity": "master", "tags": [], "created_at": "2026-04-18T12:02:00",
         "absorbed": False},
    ])
    _write_json(mem / "reflections.json", [
        {"id": "ref_1", "text": "主人和小天关系融洽", "entity": "relationship",
         "status": "promoted", "source_fact_ids": ["fact_a", "fact_b"],
         "created_at": "2026-04-18T13:00:00"},
    ])
    _write_json(mem / "persona.json", {
        "relationship": {
            "facts": [
                {"id": "prom_ref_1", "text": "主人和小天关系融洽",
                 "source": "reflection", "source_id": "ref_1",
                 "merged_from_ids": [], "recent_mentions": [],
                 "suppress": False, "suppressed_at": None, "protected": False},
            ]
        },
        "master": {
            "facts": [
                {"id": "seed_master_x", "text": "主人正在测试", "source": "settings",
                 "source_id": None, "merged_from_ids": [], "recent_mentions": [],
                 "suppress": False, "suppressed_at": None, "protected": False},
            ]
        },
    })


def _seed_db_turns() -> None:
    """Write a couple original turns into time_indexed.db via main-program API."""
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import AIMessage, HumanMessage
    writer = TimeIndexedMemory(None)
    try:
        writer.store_conversation(
            "evt-1",
            [HumanMessage(content="历史问题"), AIMessage(content="历史回答")],
            "NEKO",
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    finally:
        writer.cleanup()


# ── Cases ───────────────────────────────────────────────────────────────


def check_l1_l2_l3a_l6(client) -> list[str]:
    """Tier A edges + shape stability + db present + correction node."""
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "l1_dag")
        _seed_structural_memory()
        _seed_db_turns()
        # recent.json with a normal message + a memo system line.
        mem = _mem_dir()
        _write_json(mem / "recent.json", [
            {"type": "human", "data": {"content": "你好"}},
            {"type": "system", "data": {"content": "先前对话的备忘录: 主人测试过记忆"}},
        ])
        # correction whose old_text matches a persona entry.
        _write_json(mem / "persona_corrections.json", [
            {"old_text": "主人正在测试", "new_text": "主人正在开发新功能",
             "entity": "master", "created_at": "2026-04-19T09:00:00"},
        ])

        r = client.get("/api/memory/lineage")
        _check(r.status_code == 200, "L1.status", f"{r.status_code} {r.text[:200]}")
        snap = r.json()
        nodes = {n["id"]: n for n in snap["nodes"]}
        edges = snap["edges"]
        meta = snap["meta"]

        # L2 shape stability.
        node_keys = {"id", "type", "lane", "label", "status", "entity",
                     "created_at", "meta", "warnings"}
        for n in snap["nodes"]:
            _check(set(n.keys()) == node_keys, "L2.node_keys",
                   f"node {n.get('id')!r} keys={sorted(n.keys())}")
        edge_keys = {"source", "target", "relation", "confidence", "score", "note"}
        for e in edges:
            _check(set(e.keys()) == edge_keys, "L2.edge_keys",
                   f"edge keys={sorted(e.keys())}")

        # L1 Tier A edges.
        def _has_edge(src, tgt, rel):
            return any(
                e["source"] == src and e["target"] == tgt
                and e["relation"] == rel and e["confidence"] == "persisted"
                for e in edges
            )
        _check(_has_edge("fact_a", "ref_1", "source_fact"), "L1.edge_fa",
               f"edges={edges}")
        _check(_has_edge("fact_b", "ref_1", "source_fact"), "L1.edge_fb")
        _check(_has_edge("ref_1", "prom_ref_1", "promoted_from"), "L1.edge_prom")

        # fact node status badges.
        _check(nodes["fact_a"]["status"] == "absorbed", "L1.fact_absorbed")
        _check(nodes["fact_c"]["status"] == "active", "L1.fact_active")
        _check(nodes["fact_a"]["lane"] == 2, "L1.fact_lane")
        _check(nodes["ref_1"]["lane"] == 3, "L1.ref_lane")
        _check(nodes["prom_ref_1"]["lane"] == 4, "L1.persona_lane")

        # L6 correction.
        corr_nodes = [n for n in snap["nodes"] if n["type"] == "correction"]
        _check(len(corr_nodes) == 1, "L6.corr_count", f"{len(corr_nodes)}")
        cid = corr_nodes[0]["id"]
        _check(any(e["source"] == cid and e["target"] == "seed_master_x"
                   and e["relation"] == "corrects" for e in edges),
               "L6.corr_edge", f"edges={edges}")

        # L3a db present + message node + recent_memo node.
        _check(meta["sources_present"]["time_indexed_db"] is True,
               "L3a.db_flag", f"meta={meta['sources_present']}")
        msg_nodes = [n for n in snap["nodes"] if n["type"] == "message"]
        memo_nodes = [n for n in snap["nodes"] if n["type"] == "recent_memo"]
        _check(len(msg_nodes) >= 2, "L3a.msg_nodes",
               f"got {len(msg_nodes)}")  # 2 db turns + recent 'you' = >=3 actually
        _check(len(memo_nodes) == 1, "L3a.memo_node", f"{len(memo_nodes)}")
        _check(memo_nodes[0]["lane"] == 1, "L3a.memo_lane")
        # db turn ids start with tdb:, recent message ids start with msg:
        _check(any(n["id"].startswith("tdb:") for n in msg_nodes),
               "L3a.tdb_id", f"ids={[n['id'] for n in msg_nodes]}")

        # L3a handle release: db file deletable right after the request.
        import gc
        gc.collect()
        db_file = mem / "time_indexed.db"
        _check(db_file.is_file(), "L3a.db_exists_pre")
        db_file.unlink()
        _check(not db_file.exists(), "L3a.db_deletable",
               "db handle still locked after GET /lineage")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[L1.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_l3b_no_db(client) -> list[str]:
    """No db → no db-origin message nodes, flag False, still 200."""
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "l3b_nodb")
        _seed_structural_memory()  # no db seeded
        r = client.get("/api/memory/lineage")
        _check(r.status_code == 200, "L3b.status", f"{r.status_code}")
        snap = r.json()
        _check(snap["meta"]["sources_present"]["time_indexed_db"] is False,
               "L3b.db_flag", f"{snap['meta']['sources_present']}")
        db_origin = [
            n for n in snap["nodes"]
            if n["type"] == "message"
            and (n["meta"].get("origin") == "time_indexed_db")
        ]
        _check(len(db_origin) == 0, "L3b.no_db_msgs", f"{len(db_origin)}")
        # structural graph still present.
        _check(any(n["id"] == "ref_1" for n in snap["nodes"]), "L3b.ref_present")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[L3b.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_l4_soft_error(client) -> list[str]:
    """Corrupt reflections.json → 200 + file_warnings + partial graph."""
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "l4_soft")
        _seed_structural_memory()
        mem = _mem_dir()
        (mem / "reflections.json").write_text("{not valid json", encoding="utf-8")
        r = client.get("/api/memory/lineage")
        _check(r.status_code == 200, "L4.status",
               f"corrupt file should soft-fail, got {r.status_code} {r.text[:200]}")
        snap = r.json()
        warns = snap["meta"]["file_warnings"]
        _check(any("reflections.json" in w for w in warns), "L4.warning",
               f"warnings={warns}")
        # facts + persona still rendered.
        _check(any(n["id"] == "fact_a" for n in snap["nodes"]), "L4.fact_present")
        _check(any(n["id"] == "prom_ref_1" for n in snap["nodes"]),
               "L4.persona_present")
        # no reflection node (file was unreadable).
        _check(not any(n["type"] == "reflection" for n in snap["nodes"]),
               "L4.no_reflection")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[L4.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_l5_error_mapping(client) -> list[str]:
    """404 no session; 409 no character."""
    errors: list[str] = []
    try:
        _delete_session(client)
        r = client.get("/api/memory/lineage")
        _check(r.status_code == 404, "L5.no_session", f"{r.status_code}")

        _create_session(client, "l5_nochar", with_character=False)
        r = client.get("/api/memory/lineage")
        _check(r.status_code == 409, "L5.no_char_status", f"{r.status_code} {r.text[:200]}")
        detail = (r.json() or {}).get("detail", {})
        err_type = detail.get("error_type") if isinstance(detail, dict) else None
        _check(err_type == "NoCharacterSelected", "L5.no_char_type",
               f"error_type={err_type!r}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[L5.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_l7_llm_fallback(client) -> list[str]:
    """L7 — Tier C LLM attribution that degrades is NOT silent.

    With no memory model configured (the smoke env), requesting the LLM
    precision pass must degrade to text similarity AND hand back a structured
    ``llm_fallback`` (requested/used/reason) + a warning naming the reason, so
    the UI can surface the fall-back instead of silently showing text results.
    """
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "l7_fallback")
        _seed_structural_memory()
        # A conversation turn identical to fact_a's text guarantees a non-empty
        # shortlist, so the code actually reaches the LLM-refine (then degrades).
        mem = _mem_dir()
        _write_json(mem / "recent.json", [
            {"type": "human", "data": {"content": "主人喜欢深夜调试"}},
        ])
        r = client.post("/api/memory/lineage/attribute",
                        json={"node_id": "fact_a", "use_llm": True})
        _check(r.status_code == 200, "L7.status", f"{r.status_code} {r.text[:200]}")
        data = r.json()
        # Degraded to text (no model), and the fall-back is reported, not silent.
        _check(data.get("method") == "text", "L7.method", f"{data.get('method')}")
        fb = data.get("llm_fallback")
        _check(isinstance(fb, dict), "L7.fallback_present", f"{fb!r}")
        _check(fb.get("requested") == "llm" and fb.get("used") == "text",
               "L7.fallback_shape", f"{fb}")
        _check(bool(str(fb.get("reason") or "").strip()), "L7.fallback_reason", f"{fb}")
        _check(any("回退" in w for w in (data.get("warnings") or [])),
               "L7.warning_echo", f"{data.get('warnings')}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[L7.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_l8_recent_dedup(client) -> list[str]:
    """L8 — repeated identical recent.json turns are NOT collapsed.

    Regression guard for the ``_recent_msg_id`` ordinal fix: two byte-identical
    turns (same role + content) must yield two distinct message nodes, not one,
    so real conversation history isn't silently dropped by graph de-duplication.
    """
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "l8_dedup")
        _seed_structural_memory()
        mem = _mem_dir()
        _write_json(mem / "recent.json", [
            {"type": "human", "data": {"content": "嗯"}},
            {"type": "human", "data": {"content": "嗯"}},
            {"type": "ai", "data": {"content": "好的喵"}},
        ])
        r = client.get("/api/memory/lineage")
        _check(r.status_code == 200, "L8.status", f"{r.status_code} {r.text[:200]}")
        snap = r.json()
        msg_nodes = [n for n in snap["nodes"] if n["type"] == "message"]
        dup = [n for n in msg_nodes
               if (n.get("meta") or {}).get("content") == "嗯"]
        _check(len(dup) == 2, "L8.two_distinct_dups",
               f"expected 2 message nodes for the repeated '嗯', got {len(dup)}")
        ids = {n["id"] for n in dup}
        _check(len(ids) == 2, "L8.distinct_ids", f"ids={ids}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[L8.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
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
    print(" P32 (P27.1) — memory lineage aggregator smoke")
    print("=" * 66)

    _setup_env()

    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)

    total = 0
    total += _report(
        "L1/L2/L3a/L6 — Tier A DAG + shape + db present + correction",
        check_l1_l2_l3a_l6(client),
    )
    total += _report("L3b — no db (flag False, partial graph)",
                     check_l3b_no_db(client))
    total += _report("L4 — soft error on corrupt reflections.json",
                     check_l4_soft_error(client))
    total += _report("L5 — error mapping (404 / 409)",
                     check_l5_error_mapping(client))
    total += _report("L7 — Tier C LLM fallback is surfaced, not silent",
                     check_l7_llm_fallback(client))
    total += _report("L8 — repeated recent turns not collapsed (ordinal id)",
                     check_l8_recent_dedup(client))

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in memory lineage smoke.")
        return 1
    print(" [PASS] memory lineage contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
