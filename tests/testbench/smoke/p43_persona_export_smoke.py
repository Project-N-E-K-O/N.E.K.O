"""P31 — real character export endpoint + packer smoke.

Guards ``GET /api/persona/export_real/{name}`` and the pure packer
``persona_router._zip_character_memory``.

Pure packer:
  X1 — structure: top folder == character name; ``<name>/characters.json`` +
       every memory file; nested ``reflection_archive/`` sub-dir preserved.
  X2 — faithful bytes: extracted ``facts.json`` and the binary
       ``time_indexed.db`` match the source byte-for-byte (no redaction).
  X3 — missing characters.json: still packs the memory files, no crash.
  X4 — size cap: a shrunk ``_MAX_ARCHIVE_UNCOMPRESSED_BYTES`` raises 413
       ``ArchiveTooLarge``.
  X9 — symlink escape: a symlinked file pointing OUTSIDE the memory root is
       skipped, never packed (defence in depth; skipped if the OS/account
       cannot create symlinks).
  X10 — unreadable in-tree file is FATAL: an OSError on a regular member aborts
        with 500 ``ExportReadFailed`` instead of a silent partial backup.

Endpoint (TestClient + monkeypatched ``session.sandbox.real_paths``):
  X5 — happy: 200 / application/zip / Content-Disposition carries the CJK
       ``<角色名>.zip`` via RFC 5987; the zip structure round-trips.
  X6 — no session -> 404 NoActiveSession.
  X7 — unknown character -> 404 NoSuchRealCharacter.
  X8 — round-trip: the exported zip imports back cleanly through
       ``POST /api/persona/import_from_archive`` (export/import closure).

Env isolation mirrors p35_persona_archive_import_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p43_persona_export_smoke.py
"""  # noqa: DOCSTRING_CJK
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


CHAR = "小天"
TIME_DB_BYTES = b"\x00\x01SQLite-ish\xff\xfe binary payload \x00" * 8
FACTS = [{"id": "f1", "text": "主人喜欢喝咖啡", "importance": 5, "entity": "master",
          "tags": [], "hash": "abc", "created_at": "2026-04-18T12:00:00",
          "absorbed": False}]
ARCHIVE_JSON = {"date": "2026-01-01", "reflections": ["回顾一"]}


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p43_export_"))
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


def _characters_json() -> dict[str, Any]:
    return {
        "主人": {"档案名": "天凌"},
        "猫娘": {CHAR: {"_reserved": {"system_prompt": f"You are {CHAR}."}}},
        "当前猫娘": CHAR,
    }


def _build_real_dir(*, with_chars: bool = True) -> tuple[Path, Path]:
    """Create a fake real config_dir + memory_dir; return (config_dir, memory_dir)."""
    root = Path(tempfile.mkdtemp(prefix="p43_real_"))
    config_dir = root / "config"
    memory_dir = root / "memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    if with_chars:
        (config_dir / "characters.json").write_text(
            json.dumps(_characters_json(), ensure_ascii=False), encoding="utf-8")
    char_mem = memory_dir / CHAR
    (char_mem / "reflection_archive").mkdir(parents=True, exist_ok=True)
    (char_mem / "persona.json").write_text(
        json.dumps({"master": {"facts": []}}, ensure_ascii=False), encoding="utf-8")
    (char_mem / "facts.json").write_text(
        json.dumps(FACTS, ensure_ascii=False), encoding="utf-8")
    (char_mem / "recent.json").write_text("[]", encoding="utf-8")
    (char_mem / "time_indexed.db").write_bytes(TIME_DB_BYTES)
    (char_mem / "reflection_archive" / "2026-01-01_x.json").write_text(
        json.dumps(ARCHIVE_JSON, ensure_ascii=False), encoding="utf-8")
    return config_dir, memory_dir


# ── X1-X4 pure packer ────────────────────────────────────────────────


def check_packer() -> list[str]:
    errors: list[str] = []
    import tests.testbench.routers.persona_router as pr
    try:
        config_dir, memory_dir = _build_real_dir()

        # X1 structure
        zip_bytes = pr._zip_character_memory(config_dir, memory_dir, CHAR)
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = set(zf.namelist())
        for expected in (
            f"{CHAR}/characters.json", f"{CHAR}/persona.json",
            f"{CHAR}/facts.json", f"{CHAR}/recent.json",
            f"{CHAR}/time_indexed.db",
            f"{CHAR}/reflection_archive/2026-01-01_x.json",
        ):
            _check(expected in names, "X1.member", f"missing {expected}; got {sorted(names)}")

        # X2 faithful bytes (no redaction / no rewrite)
        _check(zf.read(f"{CHAR}/time_indexed.db") == TIME_DB_BYTES,
               "X2.binary", "time_indexed.db bytes differ")
        _check(json.loads(zf.read(f"{CHAR}/facts.json")) == FACTS,
               "X2.facts", "facts.json content differs")
        chars_back = json.loads(zf.read(f"{CHAR}/characters.json"))
        _check(chars_back == _characters_json(), "X2.chars",
               "characters.json content differs")

        # X3 missing characters.json — still packs memory files
        cfg_empty, mem2 = _build_real_dir(with_chars=False)
        zb2 = pr._zip_character_memory(cfg_empty, mem2, CHAR)
        names2 = set(zipfile.ZipFile(io.BytesIO(zb2)).namelist())
        _check(f"{CHAR}/characters.json" not in names2, "X3.no_chars",
               "characters.json should be absent")
        _check(f"{CHAR}/facts.json" in names2, "X3.mem_present",
               f"memory files should still be packed; got {sorted(names2)}")

        # X4b no duplicate members when memory dir ALSO holds characters.json
        # (round-trip / flattened-dump case). The config-dir copy wins; the
        # memory-dir duplicate must be skipped so the zip has unique members.
        cfg_dup, mem_dup = _build_real_dir()
        (mem_dup / CHAR / "characters.json").write_text(
            json.dumps({"stale": "duplicate-in-memdir"}, ensure_ascii=False),
            encoding="utf-8")
        zbd = pr._zip_character_memory(cfg_dup, mem_dup, CHAR)
        namelist = zipfile.ZipFile(io.BytesIO(zbd)).namelist()
        _check(len(namelist) == len(set(namelist)), "X4b.unique",
               f"duplicate zip members: {sorted(namelist)}")
        _check(namelist.count(f"{CHAR}/characters.json") == 1, "X4b.one_chars",
               f"characters.json appears {namelist.count(f'{CHAR}/characters.json')}x")
        # config-dir copy wins (real characters.json, not the memdir stale one)
        chars_out = json.loads(
            zipfile.ZipFile(io.BytesIO(zbd)).read(f"{CHAR}/characters.json"))
        _check(chars_out == _characters_json(), "X4b.config_wins",
               "memory-dir characters.json should NOT win over config-dir")

        # X4 size cap -> 413 ArchiveTooLarge
        from fastapi import HTTPException
        orig = pr._MAX_ARCHIVE_UNCOMPRESSED_BYTES
        pr._MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8
        try:
            pr._zip_character_memory(config_dir, memory_dir, CHAR)
            _check(False, "X4.raise", "expected ArchiveTooLarge, none raised")
        except HTTPException as exc:
            _check(exc.status_code == 413, "X4.status", f"{exc.status_code}")
            _check((exc.detail or {}).get("error_type") == "ArchiveTooLarge",
                   "X4.type", f"{exc.detail}")
        finally:
            pr._MAX_ARCHIVE_UNCOMPRESSED_BYTES = orig

        # X9 symlink escape is skipped (defence in depth): a symlinked file
        # inside the char dir pointing OUTSIDE the memory root must not be
        # packed. Skip on platforms/accounts that cannot create symlinks.
        cfg_s, mem_s = _build_real_dir()
        outside = Path(tempfile.mkdtemp(prefix="p43_outside_"))
        secret = outside / "host_secret.txt"
        secret.write_text("SYMLINK-ESCAPE-CANARY", encoding="utf-8")
        link = mem_s / CHAR / "escape_link.txt"
        symlink_ok = True
        try:
            os.symlink(secret, link)
        except (OSError, NotImplementedError, AttributeError):
            symlink_ok = False
        if symlink_ok:
            zbs = pr._zip_character_memory(cfg_s, mem_s, CHAR)
            zfs = zipfile.ZipFile(io.BytesIO(zbs))
            joined = "\n".join(
                zfs.read(n).decode("utf-8", "replace") for n in zfs.namelist())
            _check("SYMLINK-ESCAPE-CANARY" not in joined, "X9.symlink_escape",
                   "symlinked out-of-tree file leaked into export")
            _check(f"{CHAR}/escape_link.txt" not in set(zfs.namelist()),
                   "X9.symlink_member", "symlink packed as a member")

        # X10 unreadable in-tree file is FATAL (no silent partial backup): a
        # regular file whose read raises OSError aborts with HTTP 500
        # ExportReadFailed rather than shipping an incomplete zip with 200.
        cfg_u, mem_u = _build_real_dir()
        real_read_bytes = Path.read_bytes
        target = (mem_u / CHAR / "facts.json").resolve()

        def _boom(self, *a, _t=target, _orig=real_read_bytes, **k):
            if Path(self).resolve() == _t:
                raise OSError("simulated unreadable file")
            return _orig(self, *a, **k)

        Path.read_bytes = _boom  # type: ignore[assignment]
        try:
            pr._zip_character_memory(cfg_u, mem_u, CHAR)
            _check(False, "X10.raise", "expected ExportReadFailed, none raised")
        except HTTPException as exc:
            _check(exc.status_code == 500, "X10.status", f"{exc.status_code}")
            _check((exc.detail or {}).get("error_type") == "ExportReadFailed",
                   "X10.type", f"{exc.detail}")
        finally:
            Path.read_bytes = real_read_bytes  # type: ignore[assignment]
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[packer.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return errors


# ── X5-X8 endpoint ───────────────────────────────────────────────────


def _create_session(client, name: str) -> None:
    r = client.post("/api/session", json={"name": name})
    assert r.status_code == 201, f"create session failed: {r.status_code} {r.text}"


def _delete_session(client) -> None:
    try:
        client.delete("/api/session")
    except Exception:
        pass


def _patch_real_paths(config_dir: Path, memory_dir: Path) -> None:
    """Point the active session's sandbox.real_paths() at our fake real dirs."""
    from tests.testbench.session_store import get_session_store
    session = get_session_store().get()
    assert session is not None, "no active session to patch"
    session.sandbox.real_paths = lambda: {  # type: ignore[method-assign]
        "docs_dir": config_dir.parent,
        "app_docs_dir": config_dir.parent,
        "config_dir": config_dir,
        "memory_dir": memory_dir,
        "chara_dir": config_dir,
        "readable_docs_dir": None,
    }


def check_endpoint(client) -> list[str]:
    errors: list[str] = []
    try:
        config_dir, memory_dir = _build_real_dir()

        # X6 no session -> 404 (check first, before creating a session)
        _delete_session(client)
        r = client.get(f"/api/persona/export_real/{CHAR}")
        _check(r.status_code == 404, "X6.no_session", f"{r.status_code} {r.text[:160]}")

        _create_session(client, "p43_export")
        _patch_real_paths(config_dir, memory_dir)

        # X5 happy
        r = client.get(f"/api/persona/export_real/{CHAR}")
        _check(r.status_code == 200, "X5.status", f"{r.status_code} {r.text[:200]}")
        ctype = r.headers.get("content-type", "")
        _check("application/zip" in ctype, "X5.ctype", ctype)
        cd = r.headers.get("content-disposition", "")
        # CJK name rides in the RFC 5987 form.
        from urllib.parse import quote
        _check(f"filename*=UTF-8''{quote(CHAR + '.zip', safe='')}" in cd,
               "X5.disposition", cd)
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(zf.namelist())
        _check(f"{CHAR}/characters.json" in names and f"{CHAR}/facts.json" in names,
               "X5.structure", f"{sorted(names)}")
        exported_zip = r.content

        # X7 unknown character -> 404 NoSuchRealCharacter
        r = client.get("/api/persona/export_real/不存在角色")
        _check(r.status_code == 404, "X7.status", f"{r.status_code} {r.text[:160]}")
        _check((r.json().get("detail") or {}).get("error_type") == "NoSuchRealCharacter",
               "X7.type", r.text[:160])

        # X8 round-trip: exported zip imports back via import_from_archive
        body = {"archive_b64": base64.b64encode(exported_zip).decode("ascii"),
                "filename": f"{CHAR}.zip"}
        r = client.post("/api/persona/import_from_archive", json=body)
        _check(r.status_code == 200, "X8.import_status", f"{r.status_code} {r.text[:200]}")
        data = r.json()
        _check(data.get("character_name") == CHAR, "X8.char", str(data.get("character_name")))
        copied = set(data.get("copied_files") or [])
        _check("facts.json" in copied and "persona.json" in copied, "X8.copied",
               f"{copied}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[endpoint.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
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
    print(" P43 (P31) — real character export smoke")
    print("=" * 66)

    _setup_env()
    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    client = TestClient(create_app())

    total = 0
    total += _report("X1-X4/X9/X10 — pure packer (structure / faithful / missing / "
                     "cap / symlink-escape / unreadable-fatal)",
                     check_packer())
    total += _report("X5-X8 — endpoint (happy / no-session / unknown / round-trip)",
                     check_endpoint(client))

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in real character export smoke.")
        return 1
    print(" [PASS] real character export contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
