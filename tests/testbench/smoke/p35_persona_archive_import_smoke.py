"""P35 (P05.z) — persona .zip archive import endpoint smoke.

Guards ``POST /api/persona/import_from_archive``.

B1 — **happy path**: a zip with ``characters.json`` + ``memory/<char>/{persona,
     facts}.json`` imports into the sandbox; response reports the character +
     copied files; the sandbox memory dir actually receives the files (verified
     via GET /api/memory/<kind>); empty fact ``hash`` gets filled (preset
     normalization reuse).

B2 — **nested wrapper folder**: a zip whose entries are all under a top-level
     ``my_export/`` folder still resolves characters.json + memory dir.

B3 — **ambiguous archive**: two cat girls + no ``character_name`` -> 422
     AmbiguousArchive; passing the name disambiguates -> 200.

B4 — **error mapping**: no session -> 404; invalid base64 -> 400 InvalidBase64;
     non-zip bytes -> 400 InvalidArchive; zip without characters.json -> 422
     NoCharactersJson; zip-slip member -> 400 UnsafeArchive.

Env isolation mirrors p34_lineage_attribute_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p35_persona_archive_import_smoke.py
"""
from __future__ import annotations

import base64
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


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p35_arch_"))
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
            "character_name": "Seed", "master_name": "Master",
            "language": "zh-CN", "system_prompt": "You are {LANLAN_NAME}.",
        })
        assert r.status_code == 200, f"persona PUT failed: {r.text}"


def _delete_session(client) -> None:
    try:
        client.delete("/api/session")
    except Exception:
        pass


def _characters_json(names: list[str], current: str | None = None) -> dict[str, Any]:
    return {
        "主人": {"档案名": "ZipMaster"},
        "猫娘": {
            n: {"_reserved": {"system_prompt": f"You are {n}."}} for n in names
        },
        "当前猫娘": current or (names[0] if names else ""),
    }


def _build_zip(
    entries: dict[str, bytes | str], *, prefix: str = "",
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(prefix + rel, data)
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _import(client, zip_bytes: bytes, *, character_name: str | None = None,
            filename: str = "persona.zip", expected: list[int] | None = None):
    body: dict[str, Any] = {"archive_b64": _b64(zip_bytes), "filename": filename}
    if character_name is not None:
        body["character_name"] = character_name
    return client.post("/api/persona/import_from_archive", json=body)


# ── B1 happy path ────────────────────────────────────────────────────


def check_b1_happy(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "b1_happy")
        chars = _characters_json(["小天"])
        facts = [{"id": "fact_1", "text": "主人喜欢喝咖啡", "importance": 5,
                  "entity": "master", "tags": [], "hash": "",
                  "created_at": "2026-04-18T12:00:00", "absorbed": False}]
        persona = {"master": {"facts": [{"id": "card_1", "text": "我是小天",
                   "source": "character_card", "source_id": None}]}}
        zip_bytes = _build_zip({
            "characters.json": json.dumps(chars, ensure_ascii=False),
            "meta.json": json.dumps({"character_name": "小天", "language": "zh-CN"},
                                    ensure_ascii=False),
            "memory/小天/facts.json": json.dumps(facts, ensure_ascii=False),
            "memory/小天/persona.json": json.dumps(persona, ensure_ascii=False),
        })
        r = _import(client, zip_bytes)
        _check(r.status_code == 200, "B1.status", f"{r.status_code} {r.text[:200]}")
        data = r.json()
        _check(data["ok"] is True, "B1.ok")
        _check(data["character_name"] == "小天", "B1.char", str(data.get("character_name")))
        _check(data["persona"]["character_name"] == "小天", "B1.persona_char")
        _check(data["persona"].get("master_name") == "ZipMaster", "B1.master",
               str(data["persona"].get("master_name")))
        copied = set(data["copied_files"])
        _check("facts.json" in copied and "persona.json" in copied, "B1.copied",
               f"{copied}")

        # sandbox actually received the files (read back via memory API).
        rf = client.get("/api/memory/facts")
        _check(rf.status_code == 200, "B1.read_facts", f"{rf.status_code}")
        fdata = rf.json().get("data")
        _check(isinstance(fdata, list) and len(fdata) == 1, "B1.facts_len", f"{fdata}")
        # empty hash filled by preset normalization.
        _check(bool(fdata[0].get("hash")), "B1.hash_filled", f"{fdata[0]}")
        _check(fdata[0]["text"] == "主人喜欢喝咖啡", "B1.fact_text")

        # persona endpoint reflects the imported character.
        rp = client.get("/api/persona")
        _check(rp.status_code == 200, "B1.persona_get", f"{rp.status_code}")
        _check((rp.json().get("persona") or {}).get("character_name") == "小天",
               "B1.persona_get_char", rp.text[:160])
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[B1.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


# ── B2 nested wrapper folder ─────────────────────────────────────────


def check_b2_nested(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "b2_nested")
        chars = _characters_json(["Nyx"])
        zip_bytes = _build_zip({
            "characters.json": json.dumps(chars, ensure_ascii=False),
            "memory/Nyx/facts.json": json.dumps(
                [{"id": "f", "text": "夜行性", "hash": "", "entity": "neko",
                  "tags": [], "created_at": "2026-04-18T00:00:00"}],
                ensure_ascii=False),
        }, prefix="my_export_2026/")
        r = _import(client, zip_bytes)
        _check(r.status_code == 200, "B2.status", f"{r.status_code} {r.text[:200]}")
        data = r.json()
        _check(data["character_name"] == "Nyx", "B2.char", str(data.get("character_name")))
        _check("facts.json" in set(data["copied_files"]), "B2.copied",
               f"{data['copied_files']}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[B2.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


# ── B3 ambiguous archive ─────────────────────────────────────────────


def check_b3_ambiguous(client) -> list[str]:
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "b3_ambig")
        # two cat girls + no 当前猫娘 -> ambiguous.
        chars = _characters_json(["A猫", "B猫"])
        chars["当前猫娘"] = ""  # force ambiguity (no current pointer)
        zip_bytes = _build_zip({
            "characters.json": json.dumps(chars, ensure_ascii=False),
            "memory/A猫/facts.json": json.dumps([], ensure_ascii=False),
            "memory/B猫/facts.json": json.dumps([], ensure_ascii=False),
        })
        r = _import(client, zip_bytes)
        _check(r.status_code == 422, "B3.ambig_status", f"{r.status_code} {r.text[:160]}")
        et = (r.json().get("detail") or {}).get("error_type")
        _check(et == "AmbiguousArchive", "B3.ambig_type", f"{et}")

        # disambiguate with explicit name.
        r2 = _import(client, zip_bytes, character_name="B猫")
        _check(r2.status_code == 200, "B3.named_status", f"{r2.status_code} {r2.text[:160]}")
        _check(r2.json()["character_name"] == "B猫", "B3.named_char")

        # unknown explicit name -> 422 UnknownCharacter.
        r3 = _import(client, zip_bytes, character_name="不存在")
        _check(r3.status_code == 422, "B3.unknown_status", f"{r3.status_code}")
        et3 = (r3.json().get("detail") or {}).get("error_type")
        _check(et3 == "UnknownCharacter", "B3.unknown_type", f"{et3}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[B3.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


# ── B4 error mapping ─────────────────────────────────────────────────


def check_b4_errors(client) -> list[str]:
    errors: list[str] = []
    try:
        # no session -> 404
        _delete_session(client)
        good = _build_zip({"characters.json": json.dumps(
            _characters_json(["X"]), ensure_ascii=False)})
        r = _import(client, good)
        _check(r.status_code == 404, "B4.no_session", f"{r.status_code}")

        _create_session(client, "b4_main")

        # invalid base64 -> 400 InvalidBase64
        r = client.post("/api/persona/import_from_archive",
                        json={"archive_b64": "!!!not base64!!!", "filename": "x.zip"})
        _check(r.status_code == 400, "B4.bad_b64_status", f"{r.status_code} {r.text[:160]}")
        _check((r.json().get("detail") or {}).get("error_type") == "InvalidBase64",
               "B4.bad_b64_type", r.text[:160])

        # non-zip bytes -> 400 InvalidArchive
        r = client.post("/api/persona/import_from_archive",
                        json={"archive_b64": _b64(b"this is not a zip file"),
                              "filename": "x.zip"})
        _check(r.status_code == 400, "B4.not_zip_status", f"{r.status_code} {r.text[:160]}")
        _check((r.json().get("detail") or {}).get("error_type") == "InvalidArchive",
               "B4.not_zip_type", r.text[:160])

        # zip without characters.json -> 422 NoCharactersJson
        nojson = _build_zip({"memory/X/facts.json": "[]"})
        r = _import(client, nojson)
        _check(r.status_code == 422, "B4.nojson_status", f"{r.status_code} {r.text[:160]}")
        _check((r.json().get("detail") or {}).get("error_type") == "NoCharactersJson",
               "B4.nojson_type", r.text[:160])

        # zip-slip member -> 400 UnsafeArchive
        slip = io.BytesIO()
        with zipfile.ZipFile(slip, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("characters.json", json.dumps(_characters_json(["X"])))
            zf.writestr("../../evil.txt", "pwned")
        r = _import(client, slip.getvalue())
        _check(r.status_code == 400, "B4.slip_status", f"{r.status_code} {r.text[:160]}")
        _check((r.json().get("detail") or {}).get("error_type") == "UnsafeArchive",
               "B4.slip_type", r.text[:160])
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[B4.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


# ── B5 hardening guards (size cap on real bytes + unsafe name) ───────


def check_b5_guards(client) -> list[str]:
    """Streaming size cap counts *decompressed* bytes; unsafe names are rejected.

    Guards two review fixes:
      * the 500 MiB cap no longer trusts the (forgeable) ``info.file_size`` —
        it sums actual extracted bytes while streaming. We shrink the limit and
        feed real oversized content to prove the cap bites on real bytes.
      * a character name that is unsafe as a path component (here ``../evil``)
        is rejected before any sandbox write, not used to build a path.
    """
    errors: list[str] = []
    import tests.testbench.routers.persona_router as pr
    orig_limit = pr._MAX_ARCHIVE_UNCOMPRESSED_BYTES
    try:
        # (a) oversized decompressed content -> 400 ArchiveTooLarge.
        pr._MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2048
        _delete_session(client)
        _create_session(client, "b5_size")
        big_text = "主人" * 4000  # well over 2 KiB once UTF-8 encoded
        chars = _characters_json(["X"])
        zip_bytes = _build_zip({
            "characters.json": json.dumps(chars, ensure_ascii=False),
            "memory/X/facts.json": json.dumps(
                [{"id": "f", "text": big_text, "hash": "", "entity": "neko",
                  "tags": [], "created_at": "2026-04-18T00:00:00"}],
                ensure_ascii=False),
        })
        r = _import(client, zip_bytes)
        _check(r.status_code == 400, "B5.size_status", f"{r.status_code} {r.text[:160]}")
        _check((r.json().get("detail") or {}).get("error_type") == "ArchiveTooLarge",
               "B5.size_type", r.text[:160])
        pr._MAX_ARCHIVE_UNCOMPRESSED_BYTES = orig_limit

        # (b) unsafe character name (path component) -> 422 UnsafeCharacterName.
        _delete_session(client)
        _create_session(client, "b5_name")
        evil_chars = _characters_json(["../evil"])
        zip_bytes = _build_zip({
            "characters.json": json.dumps(evil_chars, ensure_ascii=False),
        })
        r = _import(client, zip_bytes)
        _check(r.status_code == 422, "B5.name_status", f"{r.status_code} {r.text[:160]}")
        _check((r.json().get("detail") or {}).get("error_type") == "UnsafeCharacterName",
               "B5.name_type", r.text[:160])
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[B5.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    finally:
        pr._MAX_ARCHIVE_UNCOMPRESSED_BYTES = orig_limit
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
    print(" P35 (P05.z) — persona .zip archive import smoke")
    print("=" * 66)

    _setup_env()
    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    client = TestClient(create_app())

    total = 0
    total += _report("B1 — happy path (copy + hash fill + read back)", check_b1_happy(client))
    total += _report("B2 — nested wrapper folder resolution", check_b2_nested(client))
    total += _report("B3 — ambiguous archive + disambiguation", check_b3_ambiguous(client))
    total += _report("B4 — error mapping", check_b4_errors(client))
    total += _report("B5 — hardening (real-byte size cap + unsafe name)",
                     check_b5_guards(client))

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in archive import smoke.")
        return 1
    print(" [PASS] persona archive import contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
