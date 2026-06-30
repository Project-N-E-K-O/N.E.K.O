"""P37 (P28) — memory embedding space aggregator smoke.

Guards the read-only embedding analysis chokepoint
(``tests/testbench/pipeline/embedding_space.py``) behind the 向量空间 sub-page:
``GET /api/memory/embedding/space|neighbors|bridges|duplicates|matrix``.

Contracts under test
--------------------
E1 — **health classification**: embedded / missing / stale / corrupt counted
     correctly; staleness keys on sha256(text) drift; dim grouping picks the
     primary vector space and reports ``other_space_count`` for off-dim rows.
E2 — **scatter shape + PCA**: ``points`` carry ``{id,type,entity,x,y,label}``,
     one per embedded primary-space entry, with finite float coords. (PCA is
     deterministic, but we assert structure/finiteness, not exact coords.)
E3 — **neighbors**: cosine top-1 of an entry is its known-closest entry;
     a non-embedded / unknown id returns ``found=False``.
E4 — **bridges (语义源 vs 结构源)**: a reflection whose nearest fact differs
     from its declared ``source_fact_ids`` surfaces the gap
     (``missing_in_declared`` / ``extra_in_declared``).
E5 — **data gate + error mapping**: a character with zero vectors returns a
     valid empty payload (200, embedded=0); no session → 404; no character →
     409 NoCharacterSelected.
E6 — **duplicates (④近重复对)**: upper-triangle cosine ≥ threshold surfaces the
     known near-duplicate pairs; a high threshold yields none; stale / missing /
     off-dim entries never participate.
E7 — **matrix (⑤相似度矩阵)**: NxN cosine over a subset (default = whole primary
     space), symmetric with unit diagonal; explicit ``ids`` subset drops unknown
     ids; reordered by seriation.
E8 — **clusters (⑦自动聚类)**: clustering the original high-dim cosine space groups
     known co-located entries together into ≥2 clusters; medoid ∈ members; stale /
     missing / off-dim entries never get a cluster; ``cluster_labels`` never 500s
     and covers every cluster (LLM or medoid fallback).

Environment isolation mirrors p32_memory_lineage_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p37_embedding_space_smoke.py
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
    tmp_data = Path(tempfile.mkdtemp(prefix="p37_embspace_"))
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


def _entry(eid: str, text: str, vec: list[float] | None, *,
           entity: str = "master", extra: dict | None = None,
           stale: bool = False) -> dict:
    """Build a memory entry; stamp a real base64 fp16 embedding when ``vec``
    given. ``stale=True`` stamps the vector then mutates the text so the
    stored sha no longer matches (simulates an edit after embedding)."""
    from memory.embeddings import stamp_embedding_fields
    e: dict[str, Any] = {"id": eid, "text": text, "entity": entity,
                         "created_at": "2026-06-30T12:00:00"}
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


def _seed_embedded_memory() -> None:
    """facts/reflections/persona with known vectors in an 8-d space.

    fact_a=e0, fact_b=e1, persona p_1≈e0 (nearest to fact_a),
    ref_1≈e1 but declares source_fact_ids=[fact_a, fact_c] (bridge gap +
    a declared-but-unembedded source so extra_in_declared exercises the
    non-embedded path: it must still carry the fact's real text, not a bare id),
    fact_c=missing, fact_d=stale, fact_e=4-d (other vector space).
    """
    mem = _mem_dir()
    _write_json(mem / "facts.json", [
        _entry("fact_a", "主人喜欢深夜调试", [1, 0, 0, 0, 0, 0, 0, 0]),
        _entry("fact_b", "主人爱喝美式咖啡", [0, 1, 0, 0, 0, 0, 0, 0]),
        _entry("fact_c", "未嵌入的事实", None),
        _entry("fact_d", "改过文的事实", [0, 0, 1, 0, 0, 0, 0, 0], stale=True),
        _entry("fact_e", "另一向量空间", [1, 0, 0, 0]),  # 4-d
    ])
    _write_json(mem / "reflections.json", [
        _entry("ref_1", "主人偏好黑咖啡", [0.05, 0.99, 0, 0, 0, 0, 0, 0],
               entity="master", extra={"status": "promoted",
                                        "source_fact_ids": ["fact_a", "fact_c"]}),
    ])
    _write_json(mem / "persona.json", {
        "master": {
            "facts": [
                _entry("p_1", "主人是夜猫子", [0.97, 0.05, 0, 0, 0, 0, 0, 0]),
            ]
        },
    })


# ── Cases ───────────────────────────────────────────────────────────────


def check_e1_e2_health_scatter(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "e1_health")
        _seed_embedded_memory()
        r = client.get("/api/memory/embedding/space")
        _check(r.status_code == 200, "E1.status", f"{r.status_code} {r.text[:200]}")
        body = r.json()
        meta = body["meta"]

        # E1 health counts: total 7 entries (5 facts + 1 refl + 1 persona).
        _check(meta["total"] == 7, "E1.total", f"{meta['total']}")
        _check(meta["missing"] == 1, "E1.missing", f"{meta['missing']}")  # fact_c
        _check(meta["stale"] == 1, "E1.stale", f"{meta['stale']}")        # fact_d
        # embedded = fact_a, fact_b, fact_e(4d), ref_1, p_1 = 5
        _check(meta["embedded"] == 5, "E1.embedded", f"{meta['embedded']}")
        _check(meta["primary_dim"] == 8, "E1.primary_dim", f"{meta['primary_dim']}")
        # 8-d primary group: fact_a, fact_b, ref_1, p_1 = 4; fact_e is the off-dim one
        _check(meta["primary_count"] == 4, "E1.primary_count", f"{meta['primary_count']}")
        _check(meta["other_space_count"] == 1, "E1.other_space", f"{meta['other_space_count']}")
        _check(meta["dims_present"].get("4") == 1 and meta["dims_present"].get("8") == 4,
               "E1.dims_present", f"{meta['dims_present']}")

        # E2 scatter shape.
        pts = body["points"]
        _check(len(pts) == 4, "E2.point_count", f"{len(pts)}")
        want_keys = {"id", "type", "entity", "x", "y", "label"}
        for p in pts:
            _check(set(p.keys()) == want_keys, "E2.point_keys", f"{sorted(p.keys())}")
            _check(isinstance(p["x"], float) and isinstance(p["y"], float)
                   and p["x"] == p["x"] and p["y"] == p["y"],  # not NaN
                   "E2.finite_coords", f"{p}")
        _check(meta["reducer_used"] == "pca", "E2.reducer", f"{meta['reducer_used']}")
        ids = {p["id"] for p in pts}
        _check(ids == {"fact_a", "fact_b", "ref_1", "p_1"}, "E2.point_ids", f"{ids}")

        # E2b reducer=umap: must not crash; reports umap_available flag and
        # falls back to PCA when umap-learn is not installed (P28.4).
        r2 = client.get("/api/memory/embedding/space", params={"reducer": "umap"})
        _check(r2.status_code == 200, "E2b.status", f"{r2.status_code}")
        m2 = r2.json()["meta"]
        _check(isinstance(m2.get("umap_available"), bool),
               "E2b.umap_flag", f"{m2.get('umap_available')}")
        _check(m2["reducer_requested"] == "umap", "E2b.requested", f"{m2['reducer_requested']}")
        if not m2["umap_available"]:
            _check(m2["reducer_used"] == "pca", "E2b.fallback", f"{m2['reducer_used']}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[E1.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_e3_neighbors(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "e3_nn")
        _seed_embedded_memory()
        # fact_a (e0) nearest should be p_1 (≈e0).
        r = client.get("/api/memory/embedding/neighbors", params={"id": "fact_a", "k": 3})
        _check(r.status_code == 200, "E3.status", f"{r.status_code}")
        body = r.json()
        _check(body["found"] is True, "E3.found", f"{body}")
        nbrs = body["neighbors"]
        _check(len(nbrs) >= 1, "E3.has_neighbors", f"{nbrs}")
        _check(nbrs[0]["id"] == "p_1", "E3.top1", f"top1={nbrs[0]}")
        _check(nbrs[0]["score"] > 0.9, "E3.top1_score", f"{nbrs[0]['score']}")

        # unknown / non-embedded id → found False.
        r = client.get("/api/memory/embedding/neighbors", params={"id": "fact_c"})
        _check(r.json()["found"] is False, "E3.missing_found_false")
        r = client.get("/api/memory/embedding/neighbors", params={"id": "nope"})
        _check(r.json()["found"] is False, "E3.unknown_found_false")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[E3.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_e4_bridges(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "e4_bridges")
        _seed_embedded_memory()
        # top_k=1 so semantic_top is just the single closest fact (fact_b);
        # the declared fact_a then correctly shows up as extra_in_declared.
        r = client.get("/api/memory/embedding/bridges", params={"top_k": 1})
        _check(r.status_code == 200, "E4.status", f"{r.status_code}")
        body = r.json()
        rows = body["rows"]
        _check(len(rows) == 1, "E4.row_count", f"{len(rows)}")
        row = rows[0]
        _check(row["reflection_id"] == "ref_1", "E4.refl_id", f"{row}")
        # ref_1 ≈ e1 → nearest fact is fact_b; declared = [fact_a].
        sem_ids = [s["fact_id"] for s in row["semantic_top"]]
        _check(sem_ids and sem_ids[0] == "fact_b", "E4.semantic_top1", f"{sem_ids}")
        miss = {s["fact_id"] for s in row["missing_in_declared"]}
        _check("fact_b" in miss, "E4.missing_in_declared", f"{miss}")
        extra = {x["fact_id"] for x in row["extra_in_declared"]}
        _check("fact_a" in extra, "E4.extra_in_declared", f"{extra}")
        # issue-1 regression: a declared-but-unembedded source must still carry
        # its real text (label), with exists=True / embedded=False — never a
        # bare id ("fact_xxx (∅)" gave the tester no information).
        xc = next((x for x in row["extra_in_declared"] if x["fact_id"] == "fact_c"), None)
        _check(xc is not None, "E4.extra_has_unembedded", f"{extra}")
        _check(xc.get("exists") is True, "E4.extra_unembedded_exists", f"{xc}")
        _check(xc.get("embedded") is False, "E4.extra_unembedded_not_embedded", f"{xc}")
        _check(xc.get("label") == "未嵌入的事实", "E4.extra_unembedded_label", f"{xc}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[E4.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_e6_duplicates(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "e6_dup")
        _seed_embedded_memory()
        # Two ~0.999 pairs exist: (fact_a, p_1) and (fact_b, ref_1).
        r = client.get("/api/memory/embedding/duplicates", params={"threshold": 0.95})
        _check(r.status_code == 200, "E6.status", f"{r.status_code}")
        body = r.json()
        _check(abs(body["threshold"] - 0.95) < 1e-9, "E6.threshold", f"{body['threshold']}")
        got = {frozenset((p["a"], p["b"])) for p in body["pairs"]}
        _check(frozenset(("fact_a", "p_1")) in got, "E6.pair_ap1", f"{got}")
        _check(frozenset(("fact_b", "ref_1")) in got, "E6.pair_bref", f"{got}")
        for p in body["pairs"]:
            _check(p["score"] >= 0.95, "E6.score_ge_thr", f"{p}")
        # stale/missing/off-dim entries never participate.
        flat = {x for p in body["pairs"] for x in (p["a"], p["b"])}
        _check(not ({"fact_c", "fact_d", "fact_e"} & flat), "E6.excludes_invalid", f"{flat}")

        # High threshold → no pairs (the ~0.999 pairs sit just below 0.9995).
        r = client.get("/api/memory/embedding/duplicates", params={"threshold": 0.9995})
        _check(r.json()["count"] == 0, "E6.high_thr_empty", f"{r.json()}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[E6.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_e7_matrix(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "e7_matrix")
        _seed_embedded_memory()
        # default (no ids) → whole primary space (4 entries).
        r = client.get("/api/memory/embedding/matrix")
        _check(r.status_code == 200, "E7.status", f"{r.status_code}")
        m = r.json()
        _check(m["n"] == 4, "E7.n", f"{m['n']}")
        _check(m["truncated"] is False, "E7.not_truncated", f"{m}")
        _check(set(m["order"]) == {"fact_a", "fact_b", "ref_1", "p_1"},
               "E7.order_set", f"{m['order']}")
        _check(len(m["cells"]) == 4 and all(len(row) == 4 for row in m["cells"]),
               "E7.cells_shape", f"{[len(r) for r in m['cells']]}")
        for i in range(4):
            _check(abs(m["cells"][i][i] - 1.0) < 1e-3, "E7.diag1", f"{m['cells'][i][i]}")
            for j in range(4):
                _check(abs(m["cells"][i][j] - m["cells"][j][i]) < 1e-3,
                       "E7.symmetric", f"({i},{j})")

        # explicit subset; unknown ids dropped.
        r = client.get("/api/memory/embedding/matrix", params={"ids": "fact_a,p_1,nope"})
        m = r.json()
        _check(m["n"] == 2, "E7.subset_n", f"{m['n']}")
        _check(set(m["order"]) == {"fact_a", "p_1"}, "E7.subset_order", f"{m['order']}")
        _check(m["cells"][0][1] > 0.99, "E7.subset_score", f"{m['cells']}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[E7.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_e8_clusters(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "e8_cluster")
        _seed_embedded_memory()
        # primary 8-d space = {fact_a≈e0, fact_b≈e1, ref_1≈e1, p_1≈e0}
        # → two natural clusters: {fact_a, p_1} and {fact_b, ref_1}.
        r = client.get("/api/memory/embedding/clusters")
        _check(r.status_code == 200, "E8.status", f"{r.status_code} {r.text[:200]}")
        body = r.json()
        _check(body["algo"] in ("hdbscan", "cosine_cc"), "E8.algo", f"{body['algo']}")
        _check(body["n_clusters"] >= 2, "E8.n_clusters", f"{body['n_clusters']}")

        assign = body["assignments"]
        for pid in ("fact_a", "fact_b", "ref_1", "p_1"):
            _check(pid in assign, "E8.assign_has", f"{pid} missing in {assign}")
        # known co-grouping (in original high-dim cosine space).
        _check(assign["fact_a"] == assign["p_1"], "E8.e0_group",
               f"a={assign['fact_a']} p1={assign['p_1']}")
        _check(assign["fact_b"] == assign["ref_1"], "E8.e1_group",
               f"b={assign['fact_b']} r1={assign['ref_1']}")
        _check(assign["fact_a"] != assign["fact_b"], "E8.distinct_clusters",
               f"{assign}")
        # off-dim / stale / missing entries never get a primary-space cluster.
        for bad in ("fact_c", "fact_d", "fact_e"):
            _check(bad not in assign, "E8.excludes_invalid", f"{bad} in {assign}")

        # per-cluster summary: medoid ∈ members, size matches member_ids.
        for cl in body["clusters"]:
            _check(cl["medoid_id"] in cl["member_ids"], "E8.medoid_in_members",
                   f"{cl}")
            _check(cl["size"] == len(cl["member_ids"]), "E8.size_matches", f"{cl}")
            _check(isinstance(cl["label"], str) and cl["label"], "E8.has_label",
                   f"{cl}")

        # cluster_labels (LLM-or-medoid): never 500s; labels cover every cluster.
        r2 = client.post("/api/memory/embedding/cluster_labels")
        _check(r2.status_code == 200, "E8.labels_status", f"{r2.status_code} {r2.text[:200]}")
        lb = r2.json()
        _check(lb["method"] in ("llm", "medoid"), "E8.labels_method", f"{lb['method']}")
        _check(lb["n_clusters"] == body["n_clusters"], "E8.labels_n", f"{lb}")
        for cl in body["clusters"]:
            key = str(cl["cluster"])
            _check(key in lb["labels"] and lb["labels"][key],
                   "E8.labels_cover", f"{key} not in {lb['labels']}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[E8.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_e5_gate_and_errors(client) -> list[str]:
    errors: list[str] = []
    try:
        # Data gate: character with no vectors → 200, embedded 0, empty points.
        _delete_session(client)
        _create_session(client, "e5_empty")
        mem = _mem_dir()
        _write_json(mem / "facts.json", [
            {"id": "f_novec", "text": "无向量", "entity": "master"},
        ])
        r = client.get("/api/memory/embedding/space")
        _check(r.status_code == 200, "E5.empty_status", f"{r.status_code}")
        meta = r.json()["meta"]
        _check(meta["embedded"] == 0, "E5.empty_embedded", f"{meta}")
        _check(meta["primary_dim"] is None, "E5.empty_primary", f"{meta}")
        _check(r.json()["points"] == [], "E5.empty_points")

        # no session → 404.
        _delete_session(client)
        r = client.get("/api/memory/embedding/space")
        _check(r.status_code == 404, "E5.no_session", f"{r.status_code}")

        # no character → 409 NoCharacterSelected.
        _create_session(client, "e5_nochar", with_character=False)
        r = client.get("/api/memory/embedding/space")
        _check(r.status_code == 409, "E5.no_char", f"{r.status_code} {r.text[:160]}")
        detail = (r.json() or {}).get("detail", {})
        err_type = detail.get("error_type") if isinstance(detail, dict) else None
        _check(err_type == "NoCharacterSelected", "E5.no_char_type", f"{err_type!r}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[E5.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
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
    print(" P37 (P28.1) — memory embedding space aggregator smoke")
    print("=" * 66)

    _setup_env()

    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)

    total = 0
    total += _report("E1/E2 — health classification + PCA scatter shape",
                     check_e1_e2_health_scatter(client))
    total += _report("E3 — cosine nearest neighbors", check_e3_neighbors(client))
    total += _report("E4 — bridges (语义源 vs 结构源)", check_e4_bridges(client))
    total += _report("E6 — duplicates (④近重复对 + 阈值)", check_e6_duplicates(client))
    total += _report("E7 — matrix (⑤相似度矩阵 + 子集)", check_e7_matrix(client))
    total += _report("E8 — clusters (⑦自动聚类 + 簇标签)", check_e8_clusters(client))
    total += _report("E5 — data gate + error mapping", check_e5_gate_and_errors(client))

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in embedding space smoke.")
        return 1
    print(" [PASS] embedding space contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
