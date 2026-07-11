"""P34 (P27.3) — Tier C reverse attribution endpoint smoke.

Guards ``POST /api/memory/lineage/attribute``.

A1 — **text similarity path** (default, free, deterministic): a fact whose text
     overlaps a specific conversation turn attributes to that turn with the
     highest score; every produced edge is ``relation="attributed_from"`` +
     ``confidence="heuristic"`` (dashed, never solid Tier A) with ``score>0``;
     ``method=="text"``. Candidate nodes carry the full lineage-node shape so
     the frontend can inject any budget-trimmed turn.

A2 — **LLM precision path** (``use_llm=true``): with the memory model stubbed,
     the wire is stamped via ``record_last_llm_wire(source="memory.llm")``
     BEFORE the call (R2 / p25 stamp coverage), the LLM's index selection is
     honored, edges stay ``heuristic``, ``method=="llm"``.

A3 — **error mapping**: unknown node -> 404 UnknownNode; a conversation
     (message) node -> 409 NotAttributable; no character -> 409; no session ->
     404; missing node_id -> 422.

Env isolation mirrors p32_memory_lineage_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p34_lineage_attribute_smoke.py
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


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p34_attr_"))
    os.environ["TESTBENCH_DATA_DIR"] = str(tmp_data)
    from tests.testbench import config as tb_config
    tb_config.DATA_DIR = tmp_data
    tb_config.SAVED_SESSIONS_DIR = tmp_data / "saved_sessions"
    tb_config.AUTOSAVE_DIR = tmp_data / "saved_sessions" / "_autosave"
    tb_config.LOGS_DIR = tmp_data / "logs"
    tb_config.SANDBOXES_DIR = tmp_data / "sandboxes"
    for d in [
        tb_config.SAVED_SESSIONS_DIR, tb_config.AUTOSAVE_DIR,
        tb_config.LOGS_DIR, tb_config.SANDBOXES_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
    return tmp_data


class _AssertFail(Exception):
    pass


def _check(cond: bool, label: str, msg: str = "") -> None:
    if not cond:
        raise _AssertFail(f"[{label}]" + (f" — {msg}" if msg else ""))


def _create_session(client, name: str, *, with_character: bool = True) -> None:
    r = client.post("/api/session", json={"name": name})
    assert r.status_code == 201, f"create session failed: {r.status_code} {r.text}"
    if with_character:
        r = client.put("/api/persona", json={
            "character_name": "NEKO", "master_name": "Master",
            "language": "zh-CN", "system_prompt": "You are {LANLAN_NAME}.",
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


_OVERLAP_TEXT = "我最喜欢在深夜写代码调试程序"
_FACT_TEXT = "主人最喜欢在深夜写代码调试程序"


def _seed() -> None:
    mem = _mem_dir()
    _write_json(mem / "facts.json", [
        {"id": "fact_x", "text": _FACT_TEXT, "importance": 6, "entity": "master",
         "tags": [], "created_at": "2026-04-18T12:00:00", "absorbed": True},
    ])
    _write_json(mem / "recent.json", [
        {"type": "human", "data": {"content": _OVERLAP_TEXT}},
        {"type": "human", "data": {"content": "今天天气真好我们去散步吧"}},
    ])
    # one db turn, unrelated, to exercise corpus union.
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import AIMessage, HumanMessage
    writer = TimeIndexedMemory(None)
    try:
        writer.store_conversation(
            "evt-1",
            [HumanMessage(content="晚饭吃什么"), AIMessage(content="吃火锅吧")],
            "NEKO",
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    finally:
        writer.cleanup()


# ── A1 text path ─────────────────────────────────────────────────────


def check_a1_text(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "a1_text")
        _seed()
        r = client.post("/api/memory/lineage/attribute",
                        json={"node_id": "fact_x", "use_llm": False})
        _check(r.status_code == 200, "A1.status", f"{r.status_code} {r.text[:200]}")
        data = r.json()
        _check(data["method"] == "text", "A1.method", str(data.get("method")))
        edges = data["edges"]
        _check(len(edges) >= 1, "A1.has_edges", f"{edges}")
        for e in edges:
            _check(e["relation"] == "attributed_from", "A1.relation", str(e))
            _check(e["confidence"] == "heuristic", "A1.heuristic", str(e))
            _check(isinstance(e["score"], (int, float)) and e["score"] > 0,
                   "A1.score", str(e))
        # candidate nodes carry full node shape.
        cands = {c["id"]: c for c in data["candidates"]}
        _check(len(cands) >= 1, "A1.has_cands")
        for c in data["candidates"]:
            _check({"id", "type", "lane", "label", "meta"} <= set(c.keys()),
                   "A1.cand_shape", f"{sorted(c.keys())}")
        # the overlapping turn must be the top-scored edge.
        top_edge = max(edges, key=lambda e: e["score"])
        top_node = cands.get(top_edge["source"])
        _check(top_node is not None, "A1.top_node_present")
        _check(_OVERLAP_TEXT in (top_node["meta"].get("content") or ""),
               "A1.top_is_overlap",
               f"top content={top_node['meta'].get('content')!r}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[A1.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


# ── A2 LLM path (stubbed model + stamp assertion) ────────────────────


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, prompt):  # noqa: ANN001
        class _Resp:
            pass
        r = _Resp()
        r.content = self._content
        return r

    async def aclose(self):
        return None


def check_a2_llm(client) -> list[str]:
    errors: list[str] = []
    import tests.testbench.pipeline.memory_attribution as attr_mod
    orig_llm = attr_mod._llm_for_memory
    orig_stamp = attr_mod.record_last_llm_wire
    stamp_calls: list[dict] = []

    def _spy_stamp(session, wire, *, source, note=None):  # noqa: ANN001
        stamp_calls.append({"source": source, "note": note, "wire": wire})
        return orig_stamp(session, wire, source=source, note=note)

    # LLM selects candidate index 0 with confidence 0.9.
    attr_mod._llm_for_memory = lambda session, *, temperature=0.0: _FakeLLM(
        json.dumps([{"index": 0, "confidence": 0.9}]))
    attr_mod.record_last_llm_wire = _spy_stamp
    try:
        _delete_session(client)
        _create_session(client, "a2_llm")
        _seed()
        r = client.post("/api/memory/lineage/attribute",
                        json={"node_id": "fact_x", "use_llm": True})
        _check(r.status_code == 200, "A2.status", f"{r.status_code} {r.text[:200]}")
        data = r.json()
        _check(data["method"] == "llm", "A2.method", str(data.get("method")))
        # R2: wire stamped exactly with source memory.llm.
        _check(len(stamp_calls) == 1, "A2.stamp_count", f"{len(stamp_calls)}")
        _check(stamp_calls[0]["source"] == "memory.llm", "A2.stamp_source",
               str(stamp_calls[0]["source"]))
        edges = data["edges"]
        _check(len(edges) == 1, "A2.edge_count", f"{edges}")
        _check(edges[0]["confidence"] == "heuristic", "A2.heuristic", str(edges[0]))
        _check(abs(edges[0]["score"] - 0.9) < 1e-6, "A2.llm_score", str(edges[0]))
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[A2.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    finally:
        attr_mod._llm_for_memory = orig_llm
        attr_mod.record_last_llm_wire = orig_stamp
    return errors


# ── A5 LLM unparsable reply -> VISIBLE fallback (not silent) ─────────


def check_a5_llm_fallback(client) -> list[str]:
    """An unparsable LLM reply must degrade *visibly* via ``llm_fallback``.

    Regression guard for the "silent llm success" bug: when the model returns
    prose (or anything that isn't a JSON array), attribution must fall back to
    text similarity AND emit a structured ``llm_fallback`` reason — never report
    ``method="llm"`` with zero edges and no signal. A *valid* empty ``[]`` is the
    one honest "no sources" answer that stays ``method="llm"`` with no fallback.
    """
    errors: list[str] = []
    import tests.testbench.pipeline.memory_attribution as attr_mod
    orig_llm = attr_mod._llm_for_memory
    try:
        # (a) unparsable prose reply -> method=text + structured fallback.
        attr_mod._llm_for_memory = lambda session, *, temperature=0.0: _FakeLLM(
            "我觉得这条记忆没有明确来源呢")
        _delete_session(client)
        _create_session(client, "a5_unparsable")
        _seed()
        r = client.post("/api/memory/lineage/attribute",
                        json={"node_id": "fact_x", "use_llm": True})
        _check(r.status_code == 200, "A5.status", f"{r.status_code} {r.text[:200]}")
        data = r.json()
        _check(data["method"] == "text", "A5.method_text", str(data.get("method")))
        fb = data.get("llm_fallback")
        _check(isinstance(fb, dict), "A5.fallback_present", f"{fb!r}")
        _check(fb.get("requested") == "llm" and fb.get("used") == "text",
               "A5.fallback_shape", f"{fb!r}")
        _check(bool((fb.get("reason") or "").strip()), "A5.fallback_reason",
               f"{fb!r}")
        # the degrade reason is also surfaced in warnings (non-silent).
        _check(any("回退" in str(w) for w in (data.get("warnings") or [])),
               "A5.warning_surfaced", f"{data.get('warnings')}")

        # (b) a VALID empty [] is an honest "no sources" -> stays method=llm.
        attr_mod._llm_for_memory = lambda session, *, temperature=0.0: _FakeLLM("[]")
        r = client.post("/api/memory/lineage/attribute",
                        json={"node_id": "fact_x", "use_llm": True})
        _check(r.status_code == 200, "A5.empty_status", f"{r.status_code}")
        data = r.json()
        _check(data["method"] == "llm", "A5.empty_method_llm", str(data.get("method")))
        _check(data.get("llm_fallback") is None, "A5.empty_no_fallback",
               f"{data.get('llm_fallback')!r}")
        _check(len(data.get("edges") or []) == 0, "A5.empty_no_edges",
               f"{data.get('edges')}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[A5.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    finally:
        attr_mod._llm_for_memory = orig_llm
    return errors


# ── A4 batch attribute_all (one-click) ───────────────────────────────


def check_a4_attribute_all(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "a4_all")
        _seed()
        r = client.post("/api/memory/lineage/attribute_all")
        _check(r.status_code == 200, "A4.status", f"{r.status_code} {r.text[:200]}")
        data = r.json()
        _check(data["method"] == "text", "A4.method", str(data.get("method")))
        _check(data.get("target_total", 0) >= 1, "A4.target_total",
               f"{data.get('target_total')}")
        edges = data["edges"]
        _check(len(edges) >= 1, "A4.has_edges", f"{edges}")
        for e in edges:
            _check(e["relation"] == "attributed_from", "A4.relation", str(e))
            _check(e["confidence"] == "heuristic", "A4.heuristic", str(e))
            _check(isinstance(e["score"], (int, float)) and e["score"] > 0,
                   "A4.score", str(e))
        # fact_x must be attributed to the overlapping recent turn.
        cands = {c["id"]: c for c in data["candidates"]}
        fact_edges = [e for e in edges if e["target"] == "fact_x"]
        _check(len(fact_edges) >= 1, "A4.fact_attributed", f"{edges}")
        top = max(fact_edges, key=lambda e: e["score"])
        top_node = cands.get(top["source"])
        _check(top_node is not None, "A4.top_node_present")
        _check(_OVERLAP_TEXT in (top_node["meta"].get("content") or ""),
               "A4.top_is_overlap",
               f"top content={top_node['meta'].get('content')!r}")
        _check(data.get("attributed_nodes", 0) >= 1, "A4.attributed_count",
               f"{data.get('attributed_nodes')}")

        # no character -> 409
        _delete_session(client)
        _create_session(client, "a4_nochar", with_character=False)
        r = client.post("/api/memory/lineage/attribute_all")
        _check(r.status_code == 409, "A4.no_char", f"{r.status_code} {r.text[:160]}")

        # no session -> 404
        _delete_session(client)
        r = client.post("/api/memory/lineage/attribute_all")
        _check(r.status_code == 404, "A4.no_session", f"{r.status_code}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[A4.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


# ── A3 error mapping ─────────────────────────────────────────────────


def check_a3_errors(client) -> list[str]:
    errors: list[str] = []
    try:
        # no session -> 404
        _delete_session(client)
        r = client.post("/api/memory/lineage/attribute", json={"node_id": "x"})
        _check(r.status_code == 404, "A3.no_session", f"{r.status_code}")

        # no character -> 409
        _create_session(client, "a3_nochar", with_character=False)
        r = client.post("/api/memory/lineage/attribute", json={"node_id": "x"})
        _check(r.status_code == 409, "A3.no_char", f"{r.status_code} {r.text[:160]}")

        # with character + seed
        _delete_session(client)
        _create_session(client, "a3_main")
        _seed()

        # unknown node -> 404 UnknownNode
        r = client.post("/api/memory/lineage/attribute",
                        json={"node_id": "does_not_exist"})
        _check(r.status_code == 404, "A3.unknown_status", f"{r.status_code}")
        et = (r.json().get("detail") or {}).get("error_type")
        _check(et == "UnknownNode", "A3.unknown_type", f"{et}")

        # missing node_id -> 422 (pydantic) 
        r = client.post("/api/memory/lineage/attribute", json={})
        _check(r.status_code == 422, "A3.missing_id", f"{r.status_code}")

        # a message node -> 409 NotAttributable. Find one via GET /lineage.
        rg = client.get("/api/memory/lineage")
        _check(rg.status_code == 200, "A3.lineage_ok", f"{rg.status_code}")
        msg_nodes = [n for n in rg.json()["nodes"] if n["type"] == "message"]
        _check(len(msg_nodes) >= 1, "A3.has_msg_node",
               "expected at least one message node from recent/db corpus")
        r = client.post("/api/memory/lineage/attribute",
                        json={"node_id": msg_nodes[0]["id"]})
        _check(r.status_code == 409, "A3.notattr_status", f"{r.status_code} {r.text[:160]}")
        et2 = (r.json().get("detail") or {}).get("error_type")
        _check(et2 == "NotAttributable", "A3.notattr_type", f"{et2}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[A3.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


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
    print(" P34 (P27.3) — memory lineage reverse attribution smoke")
    print("=" * 66)

    _setup_env()
    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    client = TestClient(create_app())

    total = 0
    total += _report("A1 — text similarity path", check_a1_text(client))
    total += _report("A2 — LLM precision path (stamp + heuristic)", check_a2_llm(client))
    total += _report("A5 — LLM unparsable -> visible fallback (not silent)",
                     check_a5_llm_fallback(client))
    total += _report("A4 — batch attribute_all (one-click)", check_a4_attribute_all(client))
    total += _report("A3 — error mapping", check_a3_errors(client))

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in attribution smoke.")
        return 1
    print(" [PASS] reverse attribution contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
