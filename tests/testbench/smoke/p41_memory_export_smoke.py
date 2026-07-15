"""P41 (P30) — memory analysis export (redacted shareable ZIP) smoke.

Guards the read-only export chokepoint
(``tests/testbench/pipeline/memory_export.py`` + ``redact.redact_export_bundle``)
behind ``GET /api/memory/export``.

Contracts under test
--------------------
X1 — **ZIP structure**: README.md + manifest.json + raw_data/{recent,facts,
     reflections,persona,conversation_corpus}.json + analysis/{overview,lineage,
     embedding_health,embedding_duplicates,embedding_clusters,embedding_bridges}
     .json + analysis/summary.md. Every .json parses.
X2 — **zero credential leak (all tiers)**: a canary api_key AND a canary cookie
     seeded into the memory never appear in ANY tier's ZIP bytes.
X3 — **identity pseudonymisation (standard/strict)**: the real character /
     master names never appear in the ZIP; minimal keeps BOTH real names.
X4 — **cross-layer consistency (blueprint §5.1)**: in standard, the SAME
     placeholder shows up for BOTH identities in both the raw dialogue
     (conversation_corpus) and the derived facts — never "dialogue says A but
     the fact says B"; standard keeps the dialogue canary in lineage too.
X5 — **strict omits raw transcript, keeps derived**: strict replaces
     conversation_corpus / recent message content with ``<omitted ...>`` while
     facts/reflections derived text survives (pseudonymised). Also (C1) the
     verbatim dialogue canary must NOT leak through analysis/lineage.json —
     message-node label/meta.content are scrubbed there too.
X6 — **corpus gating**: include_corpus=false drops raw_data/conversation_corpus.
X7 — **manifest**: has tier/character/files/warnings; identity_map_size is a
     count only — NO pseudonym→real reverse map (no real name in manifest).
X8 — **errors**: no session → 404; no character → 409 NoCharacterSelected;
     bad redaction tier → 400 UnknownRedactionTier.
X9 — **no LLM**: export never stamps session.last_llm_wire (offline/zero-cost).

Environment isolation mirrors p39_memory_overview_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p41_memory_export_smoke.py
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── Env setup — must run before any testbench import ────────────────────


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p41_export_"))
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


# Distinctive identity names (>= 2 chars, unlikely to collide with fixtures).
_CHARACTER = "Nekozilla"
_MASTER = "Zephyrus"
_CANARY_APIKEY = "sk-CANARY-APIKEY-9182"
_CANARY_COOKIE = "COOKIEVAL-SECRET-7766"
#: A verbatim dialogue phrase that lives ONLY in raw conversation (recent.json),
#: never in derived facts/reflections. Used to prove strict / corpus-off exports
#: do not leak the transcript through the analysis layer (lineage nodes).
_CANARY_DIALOGUE = "晚上好呀"
_RECENT = "2026-06-29T12:00:00"


def _create_session(client, name: str, *, with_character: bool = True) -> None:
    r = client.post("/api/session", json={"name": name})
    assert r.status_code == 201, f"create session failed: {r.status_code} {r.text}"
    if with_character:
        r = client.put("/api/persona", json={
            "character_name": _CHARACTER,
            "master_name": _MASTER,
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
    p = Path(str(cm.memory_dir)) / _CHARACTER
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_memory() -> None:
    """Structural (no-vector) fixture engineered for redaction assertions.

    Both identity names appear in dialogue AND in derived facts/reflections so
    the cross-layer consistency check has something to compare. Canary
    credentials are seeded as sensitive-keyed leaves.
    """
    mem = _mem_dir()
    _write_json(mem / "facts.json", [
        {"id": "f1", "text": f"{_MASTER} 喜欢 {_CHARACTER} 泡的美式咖啡",
         "entity": "master", "importance": 5, "created_at": _RECENT,
         "api_key": _CANARY_APIKEY},
        {"id": "f2", "text": f"{_CHARACTER} 会在深夜陪 {_MASTER} 调代码",
         "entity": "master", "importance": 4, "created_at": _RECENT},
    ])
    _write_json(mem / "reflections.json", [
        {"id": "r1", "text": f"{_CHARACTER} 认为 {_MASTER} 值得信任",
         "entity": "master", "status": "pending", "source_fact_ids": ["f1"],
         "created_at": _RECENT},
    ])
    _write_json(mem / "persona.json", {
        _MASTER: {"facts": [
            {"id": "p1", "text": f"{_MASTER} 是夜猫子", "source": "manual"},
        ]},
    })
    _write_json(mem / "recent.json", [
        {"type": "human",
         "data": {"content": f"{_MASTER} 对 {_CHARACTER} 说: 晚上好呀"},
         "cookie": _CANARY_COOKIE},
        {"type": "ai", "data": {"content": f"{_CHARACTER} 回答 {_MASTER}: 晚上好"}},
    ])


def _open_zip(resp) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(resp.content))


def _zip_text(zf: zipfile.ZipFile) -> str:
    return "\n".join(
        zf.read(n).decode("utf-8", errors="replace") for n in zf.namelist()
    )


# ── Cases ───────────────────────────────────────────────────────────────


def check_x1_structure(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "x_struct")
        _seed_memory()
        r = client.get("/api/memory/export", params={"redaction": "standard"})
        _check(r.status_code == 200, "X1.status", f"{r.status_code} {r.text[:200]}")
        _check(r.headers.get("content-type", "").startswith("application/zip"),
               "X1.ctype", r.headers.get("content-type", ""))
        _check("attachment" in (r.headers.get("content-disposition") or ""),
               "X1.cdisp", r.headers.get("content-disposition", ""))
        zf = _open_zip(r)
        names = set(zf.namelist())
        expected = {
            "README.md", "manifest.json",
            "raw_data/recent.json", "raw_data/facts.json",
            "raw_data/reflections.json", "raw_data/persona.json",
            "raw_data/conversation_corpus.json",
            "analysis/overview.json", "analysis/lineage.json",
            "analysis/embedding_health.json", "analysis/embedding_duplicates.json",
            "analysis/embedding_clusters.json", "analysis/embedding_bridges.json",
            "analysis/summary.md",
        }
        missing = expected - names
        _check(not missing, "X1.files", f"missing {missing}")
        # Every .json parses.
        for n in names:
            if n.endswith(".json"):
                try:
                    json.loads(zf.read(n).decode("utf-8"))
                except Exception as exc:  # noqa: BLE001
                    _check(False, "X1.parse", f"{n}: {exc}")
        ov = json.loads(zf.read("analysis/overview.json").decode("utf-8"))
        _check("cards" in ov and "findings" in ov, "X1.overview_shape", f"{list(ov)}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[X1.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_x2_x3_redaction(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "x_red")
        _seed_memory()
        for tier in ("minimal", "standard", "strict"):
            r = client.get("/api/memory/export", params={"redaction": tier})
            _check(r.status_code == 200, f"X2.{tier}.status", f"{r.status_code}")
            full = _zip_text(_open_zip(r))
            # X2 — credentials removed in ALL tiers.
            _check(_CANARY_APIKEY not in full, f"X2.{tier}.apikey", "api_key leaked")
            _check(_CANARY_COOKIE not in full, f"X2.{tier}.cookie", "cookie leaked")
            # X3 — identity handling per tier.
            if tier == "minimal":
                _check(_MASTER in full, "X3.minimal.keeps_master", "master name gone")
                _check(_CHARACTER in full, "X3.minimal.keeps_char",
                       "character name gone (minimal must keep BOTH real names)")
            else:
                _check(_CHARACTER not in full, f"X3.{tier}.char",
                       "character name leaked")
                _check(_MASTER not in full, f"X3.{tier}.master",
                       "master name leaked")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[X2.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_x4_x5_consistency_strict(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "x_cons")
        _seed_memory()

        # X4 — standard: same placeholder in dialogue AND facts.
        r = client.get("/api/memory/export", params={"redaction": "standard"})
        zf = _open_zip(r)
        facts_txt = zf.read("raw_data/facts.json").decode("utf-8")
        corpus_txt = zf.read("raw_data/conversation_corpus.json").decode("utf-8")
        from tests.testbench.pipeline.redact import IDENTITY_PLACEHOLDERS
        ph_master = IDENTITY_PLACEHOLDERS["master_name"]
        ph_char = IDENTITY_PLACEHOLDERS["character_name"]
        _check(ph_master in facts_txt, "X4.facts_master_placeholder",
               f"{ph_master!r} not in facts")
        _check(ph_master in corpus_txt, "X4.corpus_master_placeholder",
               f"{ph_master!r} not in corpus (standard keeps content)")
        # Symmetry: the OTHER identity (character) must be consistent across
        # layers too, not just the master name.
        _check(ph_char in facts_txt, "X4.facts_char_placeholder",
               f"{ph_char!r} not in facts")
        _check(ph_char in corpus_txt, "X4.corpus_char_placeholder",
               f"{ph_char!r} not in corpus (standard keeps content)")
        # standard keeps conversation text ⇒ the dialogue canary survives in
        # the analysis lineage layer (proves the scrub below is conditional).
        lineage_std = zf.read("analysis/lineage.json").decode("utf-8")
        _check(_CANARY_DIALOGUE in lineage_std, "X4.lineage_keeps_dialogue",
               "standard export should keep dialogue text in lineage")

        # X5 — strict: corpus/recent content omitted; derived facts survive.
        r = client.get("/api/memory/export", params={"redaction": "strict"})
        zf = _open_zip(r)
        corpus = json.loads(zf.read("raw_data/conversation_corpus.json").decode("utf-8"))
        turns = corpus.get("turns", [])
        _check(turns and all(str(t.get("content", "")).startswith("<omitted")
                             for t in turns),
               "X5.corpus_omitted", f"{[t.get('content') for t in turns]}")
        recent = json.loads(zf.read("raw_data/recent.json").decode("utf-8"))
        _check(all(str((e.get("data") or {}).get("content", "")).startswith("<omitted")
                   for e in recent),
               "X5.recent_omitted", f"{recent}")
        facts = json.loads(zf.read("raw_data/facts.json").decode("utf-8"))
        # Derived fact text kept (pseudonymised) — coffee word survives.
        _check(any("咖啡" in str(f.get("text", "")) for f in facts),
               "X5.facts_kept", f"{facts}")
        # X5b (C1 regression) — the raw transcript must NOT survive through the
        # analysis layer either. The verbatim dialogue canary must be gone from
        # the ENTIRE strict zip, and lineage message nodes must be scrubbed.
        _check(_CANARY_DIALOGUE not in _zip_text(zf), "X5.no_dialogue_leak",
               "strict export leaked raw dialogue (analysis/lineage.json?)")
        lineage = json.loads(zf.read("analysis/lineage.json").decode("utf-8"))
        msg_nodes = [n for n in lineage.get("nodes", [])
                     if n.get("type") in ("message", "recent_memo")]
        _check(all(str(n.get("label", "")).startswith("<omitted")
                   for n in msg_nodes),
               "X5.lineage_label_omitted", f"{[n.get('label') for n in msg_nodes]}")
        _check(all(str((n.get("meta") or {}).get("content", "")).startswith("<omitted")
                   for n in msg_nodes if isinstance(n.get("meta"), dict)),
               "X5.lineage_content_omitted", "lineage meta.content not omitted")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[X4.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_x6_x7_gating_manifest(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "x_gate")
        _seed_memory()

        # X6 — corpus gating.
        r = client.get("/api/memory/export",
                       params={"redaction": "standard", "include_corpus": "false"})
        zf = _open_zip(r)
        _check("raw_data/conversation_corpus.json" not in zf.namelist(),
               "X6.no_corpus", f"{zf.namelist()}")

        # X7 — manifest shape + no reverse map.
        r = client.get("/api/memory/export", params={"redaction": "standard"})
        zf = _open_zip(r)
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        for k in ("kind", "character", "redaction", "files", "warnings"):
            _check(k in manifest, "X7.manifest_keys", f"missing {k}")
        red = manifest["redaction"]
        _check(red.get("tier") == "standard", "X7.tier", f"{red}")
        _check(red.get("identity_map_size") == 2, "X7.map_size", f"{red}")
        manifest_txt = json.dumps(manifest, ensure_ascii=False)
        _check(_CHARACTER not in manifest_txt and _MASTER not in manifest_txt,
               "X7.no_reverse_map", "real name in manifest")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[X6.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_x8_errors(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        r = client.get("/api/memory/export")
        _check(r.status_code == 404, "X8.no_session", f"{r.status_code}")

        _create_session(client, "x_nochar", with_character=False)
        r = client.get("/api/memory/export")
        _check(r.status_code == 409, "X8.no_char", f"{r.status_code} {r.text[:160]}")
        detail = (r.json() or {}).get("detail", {})
        _check(isinstance(detail, dict) and detail.get("error_type") == "NoCharacterSelected",
               "X8.no_char_type", f"{detail}")

        _delete_session(client)
        _create_session(client, "x_badtier")
        _seed_memory()
        r = client.get("/api/memory/export", params={"redaction": "bogus"})
        _check(r.status_code == 400, "X8.bad_tier", f"{r.status_code}")
        detail = (r.json() or {}).get("detail", {})
        _check(isinstance(detail, dict) and detail.get("error_type") == "UnknownRedactionTier",
               "X8.bad_tier_type", f"{detail}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[X8.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


def check_x9_no_llm(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "x_nollm")
        _seed_memory()
        from tests.testbench.session_store import get_session_store
        before = getattr(get_session_store().get(), "last_llm_wire", None)
        r = client.get("/api/memory/export", params={"redaction": "standard"})
        _check(r.status_code == 200, "X9.status", f"{r.status_code}")
        after = getattr(get_session_store().get(), "last_llm_wire", None)
        _check(after == before, "X9.no_wire_stamp",
               "export stamped last_llm_wire (should be LLM-free)")
        # Static guard: the export module must not import the LLM wire helpers.
        src = Path("tests/testbench/pipeline/memory_export.py").read_text(encoding="utf-8")
        _check("record_last_llm_wire" not in src and "ainvoke" not in src,
               "X9.no_llm_import", "memory_export references LLM machinery")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[X9.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
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
    print(" P41 (P30) — memory analysis export (redacted ZIP) smoke")
    print("=" * 66)

    _setup_env()

    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)

    total = 0
    total += _report("X1 — ZIP structure + parseable", check_x1_structure(client))
    total += _report("X2/X3 — credential + identity redaction (all tiers)",
                     check_x2_x3_redaction(client))
    total += _report("X4/X5 — cross-layer consistency + strict omission",
                     check_x4_x5_consistency_strict(client))
    total += _report("X6/X7 — corpus gating + manifest (no reverse map)",
                     check_x6_x7_gating_manifest(client))
    total += _report("X8 — error mapping (404/409/400)", check_x8_errors(client))
    total += _report("X9 — export is LLM-free", check_x9_no_llm(client))

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in memory export smoke.")
        return 1
    print(" [PASS] memory export contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
