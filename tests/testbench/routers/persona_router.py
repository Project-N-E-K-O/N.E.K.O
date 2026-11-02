"""Setup workspace backend — session persona + character imports.

Scope (PLAN §Workspace 1 + §Setup workspace + P10 preset patch):

- ``GET   /api/persona``                              full persona of active session.
- ``PUT   /api/persona``                              replace persona (whole body).
- ``PATCH /api/persona``                              patch persona (partial body).
- ``GET   /api/persona/effective_system_prompt``      resolve + placeholder-replace.
- ``GET   /api/persona/real_characters``              list cat girls in the tester's
                                                      *real* (un-sandboxed)
                                                      ``characters.json``.
- ``POST  /api/persona/import_from_real/{name}``      copy memory/ files + persona
                                                      metadata from a real character
                                                      into the current sandbox.
- ``GET   /api/persona/builtin_presets``              list git-tracked character
                                                      presets bundled with testbench
                                                      (``tests/testbench/presets/``).
- ``POST  /api/persona/import_builtin_preset/{id}``   apply a bundled preset — also
                                                      serves as \"reset sandbox to
                                                      known state\" when re-applied.

All mutating endpoints hold the per-session lock via
:meth:`SessionStore.session_operation`; reads bypass the lock because they
never touch session state.

Implementation notes:
    * The sandbox patches ``cm.memory_dir`` / ``cm.chara_dir`` to point at
      the session's scratch tree. To read the tester's real character files
      during an active session we grab :meth:`Sandbox.real_paths` which
      returns the pre-patch values.
    * Import (both *real* and *builtin preset*) is **filesystem-first**: we
      recursively copy the source character's memory subdirectory into the
      sandbox so upstream code (``PersonaManager`` / ``FactStore`` / …) can
      operate unchanged in P07+. The persona metadata form fields
      (``master_name`` / prompt) are pulled from the source ``characters.json``
      to keep the Setup UI in sync.
    * Built-in presets share the same write path as real-character imports —
      only the *source directory* differs. This keeps \"whatever was imported\"
      indistinguishable from the sandbox's POV, so downstream code never
      needs to branch on import origin.
    * Nothing in this router ever writes to the *real* (un-sandboxed)
      filesystem — the whole design treats the host filesystem as read-only.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from config.prompts.prompts_chara import get_lanlan_prompt, is_default_prompt
from utils.config_manager import get_config_manager

from tests.testbench.logger import python_logger
from tests.testbench.persona_config import PersonaConfig
from tests.testbench.pipeline.atomic_io import atomic_write_json as _atomic_write_json
from tests.testbench.pipeline.snapshot_store import capture_safe as _snapshot_capture
from tests.testbench.presets import PRESETS_ROOT
from tests.testbench.session_store import (
    SessionConflictError,
    get_session_store,
)

router = APIRouter(prefix="/api/persona", tags=["persona"])


# ── helpers ─────────────────────────────────────────────────────────


def _require_session():
    """Return active session, HTTP 404 when none."""
    session = get_session_store().get()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "NoActiveSession",
                "message": "No active session. POST /api/session first.",
            },
        )
    return session


def _load(session) -> PersonaConfig:
    return PersonaConfig.from_session_value(session.persona)


def _store(session, persona: PersonaConfig) -> None:
    session.persona = persona.model_dump()


def _session_conflict_to_http(exc: SessionConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error_type": "SessionConflict",
            "message": str(exc),
            "state": exc.state.value,
            "busy_op": exc.busy_op,
        },
    )


# Memory filenames we know how to copy. Anything else in ``memory_dir/{name}``
# (e.g. ``surfaced.json``, ``persona_corrections.json``, ``time_indexed.db``)
# is copied *too* via :func:`_copytree_safe` — this list is just for reporting
# which files were expected vs unexpected in the import response.
_KNOWN_MEMORY_FILES: tuple[str, ...] = (
    "persona.json",
    "persona_corrections.json",
    "facts.json",
    "reflections.json",
    "surfaced.json",
    "settings.json",
    "recent.json",
    "time_indexed.db",
)


# ── request models ──────────────────────────────────────────────────


class _PatchPersonaRequest(BaseModel):
    """Partial persona body — only set fields are applied."""

    master_name: str | None = None
    character_name: str | None = None
    language: str | None = None
    system_prompt: str | None = None


# ── persona CRUD ────────────────────────────────────────────────────


@router.get("")
async def get_persona() -> dict[str, Any]:
    """Return the persona stored on the active session."""
    session = _require_session()
    persona = _load(session)
    return {"persona": persona.summary()}


@router.put("")
async def replace_persona(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace the whole persona bundle (Pydantic validates shape)."""
    try:
        new_persona = PersonaConfig.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc

    store = get_session_store()
    try:
        async with store.session_operation("persona.replace") as session:
            _store(session, new_persona)
            _snapshot_capture(session, trigger="persona_update")
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc

    return {"persona": new_persona.summary()}


@router.patch("")
async def patch_persona(body: _PatchPersonaRequest) -> dict[str, Any]:
    """Apply a partial update; unspecified fields keep their current value."""
    store = get_session_store()
    try:
        async with store.session_operation("persona.patch") as session:
            current = _load(session).model_dump()
            patch = body.model_dump(exclude_unset=True)
            merged = {**current, **patch}
            try:
                merged_persona = PersonaConfig.model_validate(merged)
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"error_type": type(exc).__name__, "message": str(exc)},
                ) from exc
            _store(session, merged_persona)
            _snapshot_capture(session, trigger="persona_update")
            return {"persona": merged_persona.summary()}
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc


# ── Effective system-prompt preview (P05 enhancement) ─────────────────────
#
# Persona editors need to see what leaving prompt empty / using current language actually generates.
# Runtime assembly has two steps: `config_manager.get_character_data()` uses
# `is_default_prompt()` to fall back to `get_lanlan_prompt(lang)`, then
# `tests/dump_llm_input.py` replaces `{LANLAN_NAME}/{MASTER_NAME}` with real names.
# Reproduce both steps in router preview without IO/storage.
#
# No lock (pure read); allow query overrides (master_name / character_name / lang)
# so UI previews current textarea draft without saving first.


@router.get("/effective_system_prompt")
async def effective_system_prompt(
    lang: str | None = None,
    master_name: str | None = None,
    character_name: str | None = None,
) -> dict[str, Any]:
    """Preview the system prompt that will actually be fed to the LLM.

    Flow mirrors upstream exactly:
        1. ``is_default_prompt(stored)`` → empty or default in any language → use
           ``get_lanlan_prompt(lang)`` for current language; otherwise retain stored.
        2. Replace ``{LANLAN_NAME} / {MASTER_NAME}`` placeholders in the result.

    Optional query parameters:
        - ``lang``            override session.persona.language to preview language changes
        - ``master_name``     override current master's name
        - ``character_name``  override current character name

    Return shape::

        {
            \"language\":          \"zh-CN\",
            \"master_name\":       \"天凌\",
            \"character_name\":    \"N.E.K.O\",
            \"stored_prompt\":     \"<textarea draft / saved content>\",
            \"stored_is_default\": true,      # upstream treats it as empty
            \"template_used\":     \"default\" | \"stored\",
            \"template_raw\":      \"<contains {LANLAN_NAME} placeholder>\",
            \"resolved\":          \"<names replaced, actual string sent to LLM>\"
        }

    **Do not replace** empty names: keep placeholders visible so testers see required names,
    avoiding silent disappearance through empty-string replacement.
    """  # noqa: DOCSTRING_CJK
    session = _require_session()
    persona = _load(session)

    use_lang = lang if lang is not None else persona.language
    use_master = master_name if master_name is not None else persona.master_name
    use_character = character_name if character_name is not None else persona.character_name

    stored_prompt = persona.system_prompt or ""
    stored_is_default = is_default_prompt(stored_prompt)

    if not stored_prompt or stored_is_default:
        template_used = "default"
        template_raw = get_lanlan_prompt(use_lang)
    else:
        template_used = "stored"
        template_raw = stored_prompt

    resolved = template_raw
    if use_character:
        resolved = resolved.replace("{LANLAN_NAME}", use_character)
    if use_master:
        resolved = resolved.replace("{MASTER_NAME}", use_master)

    return {
        "language": use_lang,
        "master_name": use_master,
        "character_name": use_character,
        "stored_prompt": stored_prompt,
        "stored_is_default": stored_is_default,
        "template_used": template_used,
        "template_raw": template_raw,
        "resolved": resolved,
    }


# ── real-character discovery / import ───────────────────────────────


def _user_documents_dir() -> Path | None:
    """Best-effort resolve the user's real Documents directory.

    Used for the split-config detection in ``list_real_characters`` — we
    want to know if there's a **legacy** ``<Documents>/<app>/config/`` that
    the user might have edited but the main program stopped reading from
    (either CFA fallback or new-version primary-is-AppData).

    Returns ``None`` when the platform doesn't expose a stable "Documents"
    concept or when lookup fails — callers treat None as "can't check, skip
    warning". Never raises.
    """
    try:
        if os.name == "nt":
            userprofile = os.environ.get("USERPROFILE")
            if userprofile:
                p = Path(userprofile) / "Documents"
                return p if p.exists() else None
            return None
        home = Path.home()
        p = home / "Documents"
        return p if p.exists() else None
    except OSError:
        return None


def _read_real_characters_json(config_dir: Path) -> dict[str, Any] | None:
    """Load the tester's real ``characters.json`` if present.

    Returns ``None`` when the file is missing or unreadable so the caller can
    surface an empty list instead of a hard error — fresh installs may have
    no characters at all.
    """
    path = config_dir / "characters.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            return data
        python_logger().warning(
            "persona_router: real characters.json is not a dict (%s); treating as empty",
            path,
        )
    except (OSError, json.JSONDecodeError) as exc:
        python_logger().warning(
            "persona_router: failed to read real characters.json at %s: %s",
            path,
            exc,
        )
    return None


def _extract_catgirl_entry(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Pull one cat girl's config from a ``characters.json`` dump."""
    catgirls = data.get("猫娘")
    if not isinstance(catgirls, dict):
        return None
    entry = catgirls.get(name)
    return entry if isinstance(entry, dict) else None


def _get_reserved_system_prompt(entry: dict[str, Any]) -> str:
    """Best-effort extraction of ``_reserved.system_prompt`` with legacy fallback."""
    reserved = entry.get("_reserved")
    if isinstance(reserved, dict):
        sp = reserved.get("system_prompt")
        if isinstance(sp, str):
            return sp
    legacy = entry.get("system_prompt")
    return legacy if isinstance(legacy, str) else ""


@router.get("/real_characters")
async def list_real_characters() -> dict[str, Any]:
    """Enumerate cat girls defined in the tester's **real** ``characters.json``.

    Works only when a session (hence a sandbox) is active — that's how we know
    where the real (pre-patch) ``config_dir`` lives. Each entry is a compact
    summary; the full payload is fetched by :func:`import_from_real`.

    P24 Day 8 §12.4.A (2026-04-22): user dev_note L17 reports local data absent
    from list, showing only default Xiaotian. Backend scan shares main program's ``raw['猫娘']`` contract,
    with no code bug; root cause likely local characters.json
    contents (manual edits / added fields / abnormal entries filtered by ``isinstance(entry,
    dict)``). Diagnostic ``note`` covers every empty-state path, while ``skipped_entries``
    lists filtered keys + reasons, enabling user self-diagnosis during manual testing without repeated file sharing.
    """  # noqa: DOCSTRING_CJK
    session = _require_session()
    paths = session.sandbox.real_paths()
    if not paths:
        return {
            "config_dir": None,
            "memory_dir": None,
            "master_name": "",
            "characters": [],
            "skipped_entries": [],
            "cfa_fallback": None,
            "note": "Sandbox not applied; cannot introspect real paths.",
        }

    config_dir = paths["config_dir"]
    memory_dir = paths["memory_dir"]

    # Detect split configuration paths (2026-04-22 dev_note L17 root cause):
    # User reports changes to Documents\<app>\config\characters.json are not read.
    # Two possibilities:
    #   (a) Windows CFA makes Documents read-only, ConfigManager falls back to
    #       AppData\Local → `cm._readable_docs_dir` non-None;
    #   (b) New main-program version chooses AppData\Local as primary and never
    #       tries Documents; legacy Documents\<app>\ directory remains
    #       but is never read → `cm._readable_docs_dir = None`.
    # Both look identical to users ('changes ineffective'); use **heuristic** existence check for
    # `%USERPROFILE%\Documents\<app_name>\config\characters.json`;
    # if present and different from active path, warn about split-path risk.
    cfa_fallback: dict[str, str] | None = None
    active_characters_path = config_dir / "characters.json"
    user_docs = _user_documents_dir()
    if user_docs is not None:
        legacy_config = user_docs / session.sandbox.app_name / "config"
        legacy_characters = legacy_config / "characters.json"
        if (legacy_characters.exists()
                and legacy_characters.resolve() != active_characters_path.resolve()):
            cfa_fallback = {
                "readable_docs_dir": str(user_docs),
                "write_docs_dir": str(paths["docs_dir"]),
                "active_characters_path": str(active_characters_path),
                "readable_characters_path": str(legacy_characters),
            }
    raw = _read_real_characters_json(config_dir)
    if raw is None:
        return {
            "config_dir": str(config_dir),
            "memory_dir": str(memory_dir),
            "master_name": "",
            "characters": [],
            "skipped_entries": [],
            "note": "characters.json missing or unreadable.",
        }

    master_name = ""
    master_block = raw.get("主人")
    if isinstance(master_block, dict):
        master_name = str(master_block.get("档案名", "") or "")

    catgirls = raw.get("猫娘")
    summaries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    note: str | None = None

    if catgirls is None:
        note = (
            "characters.json 有 '主人' 但没有 '猫娘' 字段. "
            "主程序的预期格式是 {主人: {...}, 猫娘: {角色名: 角色 dict}, 当前猫娘: '...'}; "
            "请检查本地 config/characters.json."
        )
    elif not isinstance(catgirls, dict):
        note = (
            f"characters.json 的 '猫娘' 字段不是对象而是 {type(catgirls).__name__} — "
            "可能文件被手动编辑过, 或者主程序更新了 schema. "
            "预期: 猫娘 应该是 {角色名: {...}} 形式的 dict."
        )
    else:
        current = str(raw.get("当前猫娘", "") or "")
        for name, entry in catgirls.items():
            if not isinstance(entry, dict):
                # Expose filtered entries + reasons for user diagnosis.
                skipped.append({
                    "name": str(name),
                    "reason": (
                        f"entry 不是对象而是 {type(entry).__name__} — "
                        "预期是 dict 格式的角色卡"
                    ),
                })
                continue
            mem_subdir = memory_dir / name
            has_mem_dir = mem_subdir.is_dir()
            present = sorted(p.name for p in mem_subdir.iterdir()) if has_mem_dir else []
            summaries.append({
                "name": name,
                "is_current": name == current,
                "has_system_prompt": bool(_get_reserved_system_prompt(entry)),
                "memory_dir_exists": has_mem_dir,
                "memory_files": present,
            })
        if not summaries and not skipped:
            note = (
                "characters.json 的 '猫娘' 字段是对象但内部为空. "
                "如果你的本地 characters.json 里明明有角色, 请把文件发给开发者确认 "
                "(通常在 ~/Documents/N.E.K.O/config/characters.json)."
            )

    # Log once regardless of success for live_runtime_log / session tracing; manual testers
    # can inspect full scan truth in logs.
    python_logger().info(
        "persona.list_real_characters: session=%s, config_dir=%s, "
        "catgirls_type=%s, summaries=%d, skipped=%d",
        getattr(session, "id", "?"), config_dir,
        type(catgirls).__name__, len(summaries), len(skipped),
    )

    return {
        "config_dir": str(config_dir),
        "memory_dir": str(memory_dir),
        "master_name": master_name,
        "characters": summaries,
        "skipped_entries": skipped,
        "cfa_fallback": cfa_fallback,
        "note": note,
    }


# ── real character export (P31) ─────────────────────────────────────
#
# Mirror image of ``import_from_real``: the Import sub-page lists local real
# characters; each row now also offers one-click **Export** that packs that
# character's whole main-program memory directory into a ``<name>.zip``. The
# layout matches the reference ``悠怡.zip`` (top folder = character name holding
# ``characters.json`` + every memory file flat, ``reflection_archive/`` nested),
# so the produced zip round-trips straight back through
# ``import_from_archive`` (its rglob fallback recognises a folder named after
# the character that holds a known memory file).
#
# Contrast with P30 ``/memory/export``: that one is **redacted** analysis for
# sharing; THIS one is a **faithful, un-redacted** full dump for backup /
# migration. No secret scrubbing, no pseudonymisation — the UI tooltip is
# honest that it contains raw private data. Pure read of the host filesystem;
# never writes the real (un-sandboxed) tree, takes no session lock (no autosave
# side-effect — L63), streams the zip straight from memory (no temp file).


def _content_disposition(filename: str) -> str:
    """Build a ``Content-Disposition`` that survives a CJK ``<角色名>.zip`` name.

    A bare ``filename=\"...\"`` header is latin-1, so a Chinese name cannot ride
    in it. Emit both: an ASCII fallback plus the RFC 5987 ``filename*=UTF-8''``
    form modern browsers prefer (mirrors ``memory_router._content_disposition``).
    """  # noqa: DOCSTRING_CJK
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii").strip()
    # A stray double quote in the fallback would prematurely close filename="…"
    # and corrupt the whole header, so neutralise it before interpolation.
    ascii_fallback = ascii_fallback.replace('"', "_")
    if not ascii_fallback or ascii_fallback in {".zip", "_.zip"}:
        ascii_fallback = "NEKO_character_export.zip"
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"


def _zip_character_memory(config_dir: Path, memory_dir: Path, name: str) -> bytes:
    """Pack one real character's config + memory dir into ``<name>/...`` zip bytes.

    Faithful full dump (no redaction, no rename) of real files:
      * ``<name>/characters.json`` — the real, whole ``characters.json`` (if any).
      * ``<name>/<relpath>`` — every regular file under ``memory_dir/<name>/``
        (recursive; preserves ``reflection_archive/`` sub-dirs; binary
        ``time_indexed.db`` is written verbatim).

    Guards, in order, before OOM/leak can happen:
      * **Size cap** — each entry's on-disk size is added to a running total and
        the export aborts with HTTP 413 the moment it crosses
        :data:`_MAX_ARCHIVE_UNCOMPRESSED_BYTES`, *before* the file is read into
        memory.
      * **Stay in tree** — a character dir that is itself a symlink is rejected
        outright (HTTP 500 ``UnsafeCharacterDir``), whether it points outside
        ``memory_dir`` or at a SIBLING character dir (which would otherwise leak
        the wrong character's files under this name); within it, symlinks are
        skipped and any entry whose resolved path escapes the root is skipped,
        so ``rglob`` can never follow a link out to unrelated host files.
      * **No partial backup** — an in-tree file we cannot stat/read aborts the
        whole export with HTTP 500 instead of silently shipping an incomplete
        "faithful" archive.
    """
    prefix = f"{name}/"
    total = 0
    # Track arcnames already written so we never emit a duplicate zip member.
    # This matters when the memory dir *also* holds a ``characters.json`` (e.g.
    # a character whose memory dir was seeded from a flattened dump that put
    # config + memory in one folder): the config-dir copy is authoritative and
    # wins; the memory-dir duplicate is skipped. Without this, zipfile emits a
    # "Duplicate name" warning and a malformed archive with two same-named
    # members (last one wins on extraction).
    written: set[str] = set()

    def _guard(added: int) -> None:
        nonlocal total
        total += added
        if total > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "error_type": "ArchiveTooLarge",
                    "message": (
                        f"角色 {name!r} 的记忆目录解压后体积超过导出上限 "
                        f"({_MAX_ARCHIVE_UNCOMPRESSED_BYTES // (1024 * 1024)} MiB)."
                    ),
                },
            )

    def _read_guarded(item: Path) -> bytes:
        # Guard on the on-disk size *before* loading, so a single pathological
        # file (e.g. a huge time_indexed.db) trips 413 instead of being read
        # fully into memory first. An in-tree file we cannot stat/read is a
        # FATAL export error — silently skipping it would hand the user a
        # partial "faithful backup" with a 200 (LESSONS §7.14 no silent
        # fallback). Symlinks / out-of-tree paths are filtered out before here.
        try:
            _guard(item.stat().st_size)
            return item.read_bytes()
        except HTTPException:
            raise
        except OSError as exc:
            python_logger().warning(
                "persona_router: export read failed %s (%s)", item, exc)
            raise HTTPException(
                status_code=500,
                detail={
                    "error_type": "ExportReadFailed",
                    "message": (
                        f"角色 {name!r} 的记忆文件读取失败, 已中止导出以免产出残缺备份 "
                        f"({type(exc).__name__})。"
                    ),
                },
            ) from exc

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        chars_path = config_dir / "characters.json"
        if chars_path.is_file() and not chars_path.is_symlink():
            arc = prefix + "characters.json"
            zf.writestr(arc, _read_guarded(chars_path))
            written.add(arc)

        char_mem = memory_dir / name
        # A faithful dump must stay inside ``memory_dir/<name>``. Reject a
        # character dir that is ITSELF a symlink, up front and unconditionally:
        #   * pointing OUTSIDE memory_dir → would pack unrelated host files;
        #   * pointing at a SIBLING (e.g. ``memory_dir/A`` → ``memory_dir/B``)
        #     stays "in-tree" and passes the resolved-root check below, yet
        #     would silently return B's UN-redacted memory under the ``A/``
        #     prefix — the wrong character's private files (greptile P1).
        # A legitimately relocated memory root (the WHOLE ``memory_dir``
        # symlinked elsewhere) is unaffected: ``memory_dir/<name>`` is then a
        # real subdir, not a symlink, so this check does not fire.
        if char_mem.is_symlink():
            raise HTTPException(
                status_code=500,
                detail={
                    "error_type": "UnsafeCharacterDir",
                    "message": (
                        f"角色 {name!r} 的记忆目录是符号链接, 已中止导出 "
                        "(避免打包到其它角色或宿主目录的文件)。"
                    ),
                },
            )
        if char_mem.is_dir():
            # ``rglob`` would also follow a symlinked file INSIDE the dir into
            # unrelated readable host directories. Resolve the root once and,
            # per entry, skip symlinks and anything whose real path escapes the
            # resolved root (defence in depth on top of the root check above).
            mem_root = char_mem.resolve()
            try:
                mem_root.relative_to(memory_dir.resolve())
            except ValueError as exc:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error_type": "UnsafeCharacterDir",
                        "message": (
                            f"角色 {name!r} 的记忆目录解析到记忆根目录之外 "
                            "(疑似符号链接逃逸), 已中止导出。"
                        ),
                    },
                ) from exc
            # Then, per entry, skip symlinks and anything whose real path escapes
            # the resolved root (defence in depth; keeps the docstring promise).
            for item in sorted(char_mem.rglob("*")):
                if item.is_symlink() or not item.is_file():
                    continue
                try:
                    item.resolve().relative_to(mem_root)
                except (OSError, ValueError):
                    python_logger().warning(
                        "persona_router: export skip out-of-tree entry %s", item)
                    continue
                rel = item.relative_to(char_mem).as_posix()
                arc = prefix + rel
                if arc in written:
                    # Duplicate (e.g. memory dir also holds characters.json);
                    # the config-dir copy already written wins.
                    continue
                zf.writestr(arc, _read_guarded(item))
                written.add(arc)
    return buf.getvalue()


@router.get("/export_real/{name}")
async def export_real_character(name: str) -> Response:
    """P31 — one-click faithful export of a local real character as ``<name>.zip``.

    Mirror of :func:`import_from_real`. Reads the tester's **real** (pre-patch)
    ``config_dir`` / ``memory_dir`` via ``session.sandbox.real_paths()`` and
    packs ``<name>/characters.json`` + everything under ``memory_dir/<name>/``.
    The zip layout matches the reference ``悠怡.zip`` and round-trips through
    :func:`import_from_archive`.

    Errors: 404 ``NoActiveSession`` / ``NoSuchRealCharacter``; 422
    ``UnsafeCharacterName``; 413 ``ArchiveTooLarge``; 500 ``SandboxNotApplied`` /
    ``UnsafeCharacterDir`` (memory dir symlink-escapes the memory root) /
    ``ExportReadFailed`` (an in-tree memory file could not be read — abort rather
    than ship a partial backup).

    Pure read — takes NO session lock (mirrors ``/real_characters``); triggers no
    autosave / snapshot / write. I/O + compression run off the event loop via
    ``asyncio.to_thread``. The zip is streamed from memory (no temp file).
    """  # noqa: DOCSTRING_CJK
    session = _require_session()
    safe_name = _assert_safe_character_name(name)

    paths = session.sandbox.real_paths()
    if not paths:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "SandboxNotApplied",
                "message": "Sandbox is not applied; cannot read real paths.",
            },
        )
    config_dir: Path = paths["config_dir"]
    memory_dir: Path = paths["memory_dir"]

    raw = _read_real_characters_json(config_dir)
    if raw is None or _extract_catgirl_entry(raw, safe_name) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "NoSuchRealCharacter",
                "message": f"本地 characters.json 里没有角色 {safe_name!r}.",
            },
        )

    zip_bytes = await asyncio.to_thread(
        _zip_character_memory, config_dir, memory_dir, safe_name,
    )
    python_logger().info(
        "persona.export_real: session=%s, character=%s, bytes=%d",
        getattr(session, "id", "?"), safe_name, len(zip_bytes),
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(f"{safe_name}.zip")},
    )


def _copytree_safe(src: Path, dst: Path) -> list[str]:
    """Mirror ``src`` into ``dst``; tolerate partial failures.

    Returns the list of relative file paths actually copied. Directories that
    vanish mid-walk (rare on local FS, but possible) are logged and skipped.
    """
    copied: list[str] = []
    if not src.exists():
        return copied
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied.append(str(rel))
        except OSError as exc:
            python_logger().warning(
                "persona_router: copy %s -> %s failed: %s", item, target, exc,
            )
    return copied


def _write_sandbox_characters_json(
    *,
    sandbox_config_dir: Path,
    master_entry: dict[str, Any] | None,
    character_name: str,
    character_entry: dict[str, Any],
) -> None:
    """Seed ``sandbox/config/characters.json`` so upstream memory code works.

    PersonaManager / FactStore / ReflectionEngine all locate a character's
    memory via ``cm.memory_dir/{character_name}/...`` and upstream
    :func:`ConfigManager.load_characters` expects a ``{\"猫娘\": ..., \"主人\": ...}``
    dict. We keep the shape faithful to upstream so later phases (P07/P08) can
    just call ``cm.load_characters()`` without special-casing testbench.
    """  # noqa: DOCSTRING_CJK
    sandbox_config_dir.mkdir(parents=True, exist_ok=True)
    target = sandbox_config_dir / "characters.json"
    payload: dict[str, Any] = {
        "主人": master_entry or {},
        "猫娘": {character_name: character_entry},
        "当前猫娘": character_name,
    }
    with target.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


@router.post("/import_from_real/{name}")
async def import_from_real(name: str) -> dict[str, Any]:
    """Copy one real character's memory + metadata into the active sandbox.

    Side effects:
    * Writes ``sandbox/config/characters.json`` mirroring the real entry.
    * Copies ``real_memory_dir/{name}/*`` → ``sandbox_memory_dir/{name}/*``.
    * Updates ``session.persona`` to reflect the imported master/character/prompt.

    Returns a small report (files copied, persona summary) the UI renders as a
    success toast.
    """
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail={"message": "Empty character name"})

    store = get_session_store()
    try:
        async with store.session_operation(f"persona.import:{name}") as session:
            paths = session.sandbox.real_paths()
            if not paths:
                raise HTTPException(
                    status_code=500,
                    detail={"message": "Sandbox is not applied; cannot read real paths."},
                )
            real_config_dir: Path = paths["config_dir"]
            real_memory_dir: Path = paths["memory_dir"]

            raw = _read_real_characters_json(real_config_dir)
            if raw is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "NoRealCharactersJson",
                        "message": f"characters.json not found under {real_config_dir}",
                    },
                )
            entry = _extract_catgirl_entry(raw, name)
            if entry is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_type": "NoSuchRealCharacter",
                        "message": f"No real character named {name!r}",
                    },
                )

            master_entry = raw.get("主人") if isinstance(raw.get("主人"), dict) else {}
            master_name = str(master_entry.get("档案名", "") or "")
            system_prompt = _get_reserved_system_prompt(entry)

            # Sandbox is applied, so ConfigManager's current paths *are*
            # the sandbox paths — that's the public way to locate them.
            cm = get_config_manager()
            sb_config_dir = Path(cm.config_dir)
            sb_memory_dir = Path(cm.memory_dir)
            _write_sandbox_characters_json(
                sandbox_config_dir=sb_config_dir,
                master_entry=master_entry,
                character_name=name,
                character_entry=entry,
            )
            copied = _copytree_safe(real_memory_dir / name, sb_memory_dir / name)

            persona = PersonaConfig(
                master_name=master_name,
                character_name=name,
                language=session.persona.get("language") or "zh-CN",
                system_prompt=system_prompt,
            )
            _store(session, persona)
            session.logger.log_sync(
                "persona.import",
                payload={
                    "character_name": name,
                    "master_name": master_name,
                    "files_copied": copied,
                },
            )
            python_logger().info(
                "persona import: %s -> sandbox %s (%d files)",
                name, session.sandbox.root, len(copied),
            )

            known = [f for f in copied if f in _KNOWN_MEMORY_FILES]
            extra = [f for f in copied if f not in _KNOWN_MEMORY_FILES]
            _snapshot_capture(session, trigger="persona_update")
            return {
                "ok": True,
                "persona": persona.summary(),
                "copied_files": copied,
                "known_files": known,
                "extra_files": extra,
                "sandbox_memory_dir": str(sb_memory_dir / name),
            }
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc


# ── built-in preset discovery / import ──────────────────────────────
#
# Preset and real-character imports **share** `_write_sandbox_characters_json` +
# `_copytree_safe` + session.persona updates; only source changes to repository
# `tests/testbench/presets/<preset_id>/`. Built-in virtual real characters rather than separate
# endpoint/service ensure:
#   (a) Consistent tester experience: both imports overwrite characters.json +
#       same-name memory/<char>/ files + update session.persona;
#   (b) Future Hugging Face / archive imports reuse this pipeline;
#   (c) No incomplete paths injecting memory without characters.json.


def _load_preset_meta(preset_id: str) -> dict[str, Any] | None:
    """Read ``presets/<id>/meta.json``; return None if missing/invalid.

    ``meta.json`` schema::

        {
          \"id\": \"<preset_id>\",                ← must match directory name
          \"display_name\": \"<human-readable name>\",
          \"description\": \"<one-sentence description>\",
          \"language\": \"zh-CN\" | \"en\" | ...,
          \"character_name\": \"<key in characters.json>\"
        }
    """
    meta_path = PRESETS_ROOT / preset_id / "meta.json"
    if not meta_path.exists():
        return None
    try:
        with meta_path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        python_logger().warning(
            "persona_router: failed to read preset meta %s: %s", meta_path, exc,
        )
        return None
    if not isinstance(data, dict):
        return None
    return data


def _iter_preset_dirs() -> list[tuple[str, Path]]:
    """Enumerate ``presets/*/`` subdirs that contain a ``meta.json``.

    Return ``(preset_id, preset_dir)`` sorted lexicographically by id, ignoring
    non-preset entries such as ``__pycache__`` / ``__init__.py``.
    """
    if not PRESETS_ROOT.exists():
        return []
    items: list[tuple[str, Path]] = []
    for child in sorted(PRESETS_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        if not (child / "meta.json").exists():
            continue
        items.append((child.name, child))
    return items


def _normalize_preset_facts(facts: Any) -> Any:
    """Fill in missing ``hash`` fields for seed facts.

    Main program ``FactStore`` deduplicates by ``sha256(text)[:16]``; presets intentionally
    leave ``hash`` empty and compute on import, avoiding manual hash maintenance and
    stale hashes after small copied-text edits corrupting deduplication.
    """
    if not isinstance(facts, list):
        return facts
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        text = fact.get("text")
        if not isinstance(text, str) or not text:
            continue
        current_hash = fact.get("hash")
        if isinstance(current_hash, str) and current_hash:
            continue
        fact["hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return facts


def _read_preset_characters_json(preset_dir: Path) -> dict[str, Any] | None:
    """Load the preset's ``characters.json`` (same shape as real one)."""
    path = preset_dir / "characters.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        python_logger().warning(
            "persona_router: preset %s has broken characters.json: %s",
            preset_dir.name, exc,
        )
        return None
    return data if isinstance(data, dict) else None


def _summarize_preset(preset_id: str, preset_dir: Path) -> dict[str, Any] | None:
    """Produce a UI-facing summary for ``GET /api/persona/builtin_presets``.

    Invalid presets (missing meta / bad characters.json / character_name
    mismatch) return None so the caller can filter them out of the list.
    """
    meta = _load_preset_meta(preset_id)
    if meta is None:
        return None
    raw = _read_preset_characters_json(preset_dir)
    if raw is None:
        return None
    character_name = str(meta.get("character_name", "") or "")
    entry = _extract_catgirl_entry(raw, character_name) if character_name else None
    if entry is None:
        return None

    mem_dir = preset_dir / "memory" / character_name
    has_mem_dir = mem_dir.is_dir()
    present = sorted(p.name for p in mem_dir.iterdir()) if has_mem_dir else []

    master_entry = raw.get("主人") if isinstance(raw.get("主人"), dict) else {}
    master_name = str(master_entry.get("档案名", "") or "")

    return {
        "id": preset_id,
        "display_name": str(meta.get("display_name") or preset_id),
        "description": str(meta.get("description") or ""),
        "language": str(meta.get("language") or "zh-CN"),
        "character_name": character_name,
        "master_name": master_name,
        "has_system_prompt": bool(_get_reserved_system_prompt(entry)),
        "memory_files": present,
    }


def _copytree_safe_with_normalization(src: Path, dst: Path) -> list[str]:
    """Like :func:`_copytree_safe` but post-process ``facts.json`` to fill hashes.

    Only ``facts.json`` is touched; everything else is verbatim ``copy2``.
    """
    copied = _copytree_safe(src, dst)
    facts_path = dst / "facts.json"
    if facts_path.exists():
        try:
            with facts_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            _normalize_preset_facts(data)
            # Crash-safe write (tmp + fsync + os.replace); a hard-kill mid-write
            # must not leave facts.json half-overwritten (§3A F1 convention).
            _atomic_write_json(facts_path, data)
        except (OSError, json.JSONDecodeError) as exc:
            python_logger().warning(
                "persona_router: normalize facts.json in %s failed: %s",
                facts_path, exc,
            )
    return copied


@router.get("/builtin_presets")
async def list_builtin_presets() -> dict[str, Any]:
    """Enumerate git-tracked character presets bundled with the testbench.

    Render Setup → Import's built-in presets section without sandbox/session, even empty sessions,
    so users can preview presets between creating a session and importing one.
    """
    summaries: list[dict[str, Any]] = []
    for preset_id, preset_dir in _iter_preset_dirs():
        summary = _summarize_preset(preset_id, preset_dir)
        if summary is not None:
            summaries.append(summary)
    return {"presets": summaries}


@router.post("/import_builtin_preset/{preset_id}")
async def import_builtin_preset(preset_id: str) -> dict[str, Any]:
    """Apply a built-in preset to the active session sandbox.

    Side effects (mirror :func:`import_from_real`):
      * Writes ``sandbox/config/characters.json`` from the preset
      * Copies ``presets/<id>/memory/<character>/`` → ``sandbox/memory/<character>/``
        (overwrite same-name files, retaining others)
      * Fills empty ``hash`` fields in copied ``facts.json``
      * Updates ``session.persona`` with preset's master/character/system_prompt

    Repeated application is a one-click reset to preset state: overwrite characters.json
    entirely and same-name memory JSON (facts/persona/recent are authoritative).
    **Extra** sandbox files absent from preset, such as user-created
    ``reflections.json`` / ``persona_corrections.json`` / ``surfaced.json`` /
    ``time_indexed.db``, remain untouched; users must clear via memory page or disk. This
    deliberately overwrites rather than empties, preserving manually added
    debugging data.
    """
    preset_id = preset_id.strip()
    if not preset_id:
        raise HTTPException(status_code=400, detail={"message": "Empty preset id"})

    preset_dir = PRESETS_ROOT / preset_id
    if not preset_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "UnknownPreset",
                "message": f"No built-in preset named {preset_id!r}",
            },
        )

    meta = _load_preset_meta(preset_id)
    if meta is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "BrokenPreset",
                "message": f"Preset {preset_id!r} has missing or invalid meta.json",
            },
        )
    character_name = str(meta.get("character_name", "") or "").strip()
    if not character_name:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "BrokenPreset",
                "message": f"Preset {preset_id!r} meta.json missing 'character_name'",
            },
        )

    raw = _read_preset_characters_json(preset_dir)
    if raw is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "BrokenPreset",
                "message": f"Preset {preset_id!r} has missing or invalid characters.json",
            },
        )
    entry = _extract_catgirl_entry(raw, character_name)
    if entry is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "BrokenPreset",
                "message": (
                    f"Preset {preset_id!r} characters.json has no entry for "
                    f"{character_name!r}"
                ),
            },
        )

    master_entry = raw.get("主人") if isinstance(raw.get("主人"), dict) else {}
    master_name = str(master_entry.get("档案名", "") or "")
    system_prompt = _get_reserved_system_prompt(entry)
    preset_language = str(meta.get("language") or "") or None

    store = get_session_store()
    try:
        async with store.session_operation(f"persona.import_preset:{preset_id}") as session:
            cm = get_config_manager()
            sb_config_dir = Path(cm.config_dir)
            sb_memory_dir = Path(cm.memory_dir)

            _write_sandbox_characters_json(
                sandbox_config_dir=sb_config_dir,
                master_entry=master_entry,
                character_name=character_name,
                character_entry=entry,
            )
            copied = _copytree_safe_with_normalization(
                preset_dir / "memory" / character_name,
                sb_memory_dir / character_name,
            )

            persona = PersonaConfig(
                master_name=master_name,
                character_name=character_name,
                language=preset_language or session.persona.get("language") or "zh-CN",
                system_prompt=system_prompt,
            )
            _store(session, persona)
            session.logger.log_sync(
                "persona.import_builtin_preset",
                payload={
                    "preset_id": preset_id,
                    "character_name": character_name,
                    "master_name": master_name,
                    "files_copied": copied,
                },
            )
            python_logger().info(
                "persona import built-in preset: %s -> sandbox %s (%d files)",
                preset_id, session.sandbox.root, len(copied),
            )

            known = [f for f in copied if f in _KNOWN_MEMORY_FILES]
            extra = [f for f in copied if f not in _KNOWN_MEMORY_FILES]
            _snapshot_capture(session, trigger="persona_update")
            return {
                "ok": True,
                "preset_id": preset_id,
                "persona": persona.summary(),
                "copied_files": copied,
                "known_files": known,
                "extra_files": extra,
                "sandbox_memory_dir": str(sb_memory_dir / character_name),
            }
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc


# ── zip archive import (P05.z) ──────────────────────────────────────
#
# Third import source: a user-supplied **.zip persona archive**. Same
# filesystem-first write pipeline as import_from_real / import_builtin_preset
# (write sandbox characters.json -> copytree memory -> set persona -> snapshot)
# — only the *source* differs (an uploaded zip, extracted to a temp dir, instead
# of the real config dir or a bundled preset dir). Accepts the same layout a
# preset / a real N.E.K.O. data dump uses:
#
#     characters.json                      (or config/characters.json)
#     memory/<character_name>/{persona,facts,reflections,recent,...}.json
#     meta.json                            (optional: language / character_name)
#
# Upload transport mirrors session export (base64 in a JSON body) rather than
# multipart — testbench has no UploadFile endpoint and base64-over-JSON keeps
# the api.post() client path uniform.

#: Decoded zip byte ceiling (defends against a giant upload pre-extraction).
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
#: Total uncompressed byte ceiling across all members (zip-bomb guard).
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
#: Stream-copy chunk for zip extraction (also the size-guard granularity).
_ZIP_COPY_CHUNK = 1 << 16  # 64 KiB


class _ImportArchiveRequest(BaseModel):
    """Body for ``POST /api/persona/import_from_archive``.

    ``archive_b64`` is the base64 of a ``.zip``. ``character_name`` is optional
    and only needed to disambiguate an archive whose ``characters.json`` holds
    more than one cat girl. ``filename`` is for logging only.
    """

    archive_b64: str
    character_name: str | None = None
    filename: str | None = None


def _safe_extract_zip(data: bytes, dest: Path) -> None:
    """Extract ``data`` (zip bytes) into ``dest`` with traversal + size guards.

    Rejects absolute / ``..`` member paths (zip-slip) and aborts if the total
    uncompressed size exceeds :data:`_MAX_ARCHIVE_UNCOMPRESSED_BYTES`. Never
    extracts outside ``dest``.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_type": "InvalidArchive",
                    "message": f"不是有效的 zip 压缩包: {exc}"},
        ) from exc
    dest_resolved = dest.resolve()
    total = 0
    with zf:
        # NOTE: we deliberately do NOT trust ``info.file_size`` for the size
        # guard — it is attacker-controllable ZIP metadata (a crafted entry can
        # claim file_size=0 while its compressed payload decompresses to GBs).
        # We also drop the upfront ``zf.testzip()`` (it decompresses every member
        # before any guard runs — a free zip-bomb amplifier). Instead we count
        # *actual* decompressed bytes while streaming, aborting the moment the
        # running total crosses the limit. CRC corruption still surfaces:
        # zipfile verifies each member's CRC as ``ZipExtFile`` reaches EOF.
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (dest / info.filename).resolve()
            if os.path.commonpath([dest_resolved, target]) != str(dest_resolved):
                raise HTTPException(
                    status_code=400,
                    detail={"error_type": "UnsafeArchive",
                            "message": f"压缩包包含越界路径 (zip-slip): {info.filename!r}"},
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, target.open("wb") as out:
                    while True:
                        chunk = src.read(_ZIP_COPY_CHUNK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                            raise HTTPException(
                                status_code=400,
                                detail={"error_type": "ArchiveTooLarge",
                                        "message": "压缩包解压后体积超过上限 (500 MiB)."},
                            )
                        out.write(chunk)
            except zipfile.BadZipFile as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"error_type": "InvalidArchive",
                            "message": f"压缩包内文件损坏: {info.filename!r} ({exc})"},
                ) from exc


def _find_shallowest(root: Path, filename: str) -> Path | None:
    """Return the shallowest file named ``filename`` under ``root`` (or None)."""
    matches = [p for p in root.rglob(filename) if p.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda p: len(p.relative_to(root).parts))
    return matches[0]


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """Load a JSON object file; return None on missing / invalid / non-dict."""
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _assert_safe_character_name(name: str) -> str:
    """Reject a character name that is unsafe as a single path component.

    This is NOT content filtering (the project never sanitizes user *content*):
    ``name`` is used verbatim to build sandbox filesystem paths
    (``sandbox/memory/<name>/``), so a value containing a path separator,
    ``..``, an absolute-path marker, or NUL could escape the sandbox. We reject
    those structurally and leave the name otherwise untouched.
    """
    n = str(name or "").strip()
    if (not n or n in (".", "..")
            or "/" in n or "\\" in n or "\x00" in n
            or os.path.isabs(n)):
        raise HTTPException(
            status_code=422,
            detail={"error_type": "UnsafeCharacterName",
                    "message": f"角色名 {name!r} 含有非法路径字符, 无法用作目录名."},
        )
    return n


def _resolve_archive_character_name(
    raw: dict[str, Any], requested: str | None, meta: dict[str, Any] | None,
) -> str:
    """Pick which cat girl to import from the archive's ``characters.json``.

    Priority: explicit ``requested`` -> ``meta.json.character_name`` ->
    ``当前猫娘`` -> the single key when unambiguous. Raises 422 when the
    archive has no usable character or when it is ambiguous and no name was
    given.
    """  # noqa: DOCSTRING_CJK
    catgirls = raw.get("猫娘")
    catgirls = catgirls if isinstance(catgirls, dict) else {}
    names = [str(k) for k in catgirls.keys()]
    if requested:
        if requested in catgirls:
            return requested
        raise HTTPException(
            status_code=422,
            detail={"error_type": "UnknownCharacter",
                    "message": f"压缩包的 characters.json 里没有角色 {requested!r}. "
                               f"可选: {names or '（空）'}"},
        )
    meta_name = str((meta or {}).get("character_name", "") or "")
    if meta_name and meta_name in catgirls:
        return meta_name
    current = str(raw.get("当前猫娘", "") or "")
    if current in catgirls:
        return current
    if len(names) == 1:
        return names[0]
    if not names:
        raise HTTPException(
            status_code=422,
            detail={"error_type": "NoCharacterInArchive",
                    "message": "压缩包的 characters.json 里没有任何猫娘条目."},
        )
    raise HTTPException(
        status_code=422,
        detail={"error_type": "AmbiguousArchive",
                "message": f"压缩包内含多个角色 {names}, 请指定 character_name."},
    )


def _resolve_archive_memory_dir(
    extract_root: Path, char_json: Path, character_name: str,
) -> Path | None:
    """Locate ``memory/<character_name>/`` within the extracted archive.

    Tolerant of common layouts: ``<root>/memory/<char>`` (preset / data dump),
    ``config/characters.json`` sibling ``memory/`` (real dir dump), or a bare
    ``<char>/`` folder. Falls back to an rglob for any dir named ``character_name``
    that holds a known memory file.
    """
    candidates = [
        char_json.parent / "memory" / character_name,
        char_json.parent.parent / "memory" / character_name,
        char_json.parent / character_name,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    # rglob("*") + literal name compare — NOT rglob(character_name), whose
    # argument is a glob *pattern*: a name with metacharacters (``*?[]``) would
    # match unintended directories.
    for d in extract_root.rglob("*"):
        if (d.is_dir() and d.name == character_name
                and any((d / f).exists() for f in _KNOWN_MEMORY_FILES)):
            return d
    return None


@router.post("/import_from_archive")
async def import_from_archive(body: _ImportArchiveRequest) -> dict[str, Any]:
    """Import a persona archive (.zip) into the active session sandbox.

    Side effects mirror :func:`import_from_real` / :func:`import_builtin_preset`:
    writes ``sandbox/config/characters.json``, copies the archive's
    ``memory/<character>/`` into the sandbox (filling empty fact hashes), and
    updates ``session.persona``. Read of the host filesystem only — never writes
    outside the sandbox; the uploaded zip is extracted to a temp dir that is
    always removed.

    Errors: 404 no session, 409 SessionConflict, 400 invalid / unsafe / oversize
    zip, 422 missing characters.json / unknown or ambiguous character.
    """
    _require_session()  # pre-flight 404 before touching the lock

    raw_b64 = (body.archive_b64 or "").strip()
    if not raw_b64:
        raise HTTPException(
            status_code=422,
            detail={"error_type": "EmptyArchive", "message": "archive_b64 不能为空."},
        )
    # Tolerate a data-URL prefix (``data:application/zip;base64,....``).
    if raw_b64.startswith("data:") and "," in raw_b64:
        raw_b64 = raw_b64.split(",", 1)[1]
    try:
        data = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_type": "InvalidBase64",
                    "message": f"archive_b64 不是合法 base64: {exc}"},
        ) from exc
    if not data:
        raise HTTPException(
            status_code=422,
            detail={"error_type": "EmptyArchive", "message": "解码后压缩包为空."},
        )
    if len(data) > _MAX_ARCHIVE_BYTES:
        raise HTTPException(
            status_code=400,
            detail={"error_type": "ArchiveTooLarge",
                    "message": "上传压缩包超过上限 (200 MiB)."},
        )

    tmp_root = Path(tempfile.mkdtemp(prefix="tb_persona_zip_"))
    try:
        extract_dir = tmp_root / "x"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_zip(data, extract_dir)

        char_json = _find_shallowest(extract_dir, "characters.json")
        if char_json is None:
            raise HTTPException(
                status_code=422,
                detail={"error_type": "NoCharactersJson",
                        "message": "压缩包内未找到 characters.json. "
                                   "期望布局: characters.json + memory/<角色名>/..."},
            )
        raw = _read_json_file(char_json)
        if raw is None:
            raise HTTPException(
                status_code=422,
                detail={"error_type": "BrokenCharactersJson",
                        "message": "压缩包内 characters.json 无法解析或不是对象."},
            )
        meta_path = _find_shallowest(extract_dir, "meta.json")
        meta = _read_json_file(meta_path) if meta_path else None
        character_name = _assert_safe_character_name(
            _resolve_archive_character_name(
                raw, (body.character_name or "").strip() or None, meta))
        entry = _extract_catgirl_entry(raw, character_name)
        if entry is None:
            raise HTTPException(
                status_code=422,
                detail={"error_type": "UnknownCharacter",
                        "message": f"characters.json 内没有角色 {character_name!r} 的条目."},
            )

        mem_src = _resolve_archive_memory_dir(extract_dir, char_json, character_name)
        master_entry = raw.get("主人") if isinstance(raw.get("主人"), dict) else {}
        master_name = str(master_entry.get("档案名", "") or "")
        system_prompt = _get_reserved_system_prompt(entry)
        archive_language = str((meta or {}).get("language") or "") or None

        store = get_session_store()
        try:
            async with store.session_operation(
                f"persona.import_archive:{character_name}"
            ) as session:
                cm = get_config_manager()
                sb_config_dir = Path(cm.config_dir)
                sb_memory_dir = Path(cm.memory_dir)

                _write_sandbox_characters_json(
                    sandbox_config_dir=sb_config_dir,
                    master_entry=master_entry,
                    character_name=character_name,
                    character_entry=entry,
                )
                warnings: list[str] = []
                if mem_src is not None:
                    copied = _copytree_safe_with_normalization(
                        mem_src, sb_memory_dir / character_name)
                else:
                    copied = []
                    warnings.append(
                        f"压缩包内未找到 memory/{character_name}/ 目录, "
                        "仅导入了角色卡 (无事实 / 反思 / 对话归档).")

                persona = PersonaConfig(
                    master_name=master_name,
                    character_name=character_name,
                    language=archive_language
                    or session.persona.get("language") or "zh-CN",
                    system_prompt=system_prompt,
                )
                _store(session, persona)
                session.logger.log_sync(
                    "persona.import_from_archive",
                    payload={
                        "character_name": character_name,
                        "master_name": master_name,
                        "files_copied": copied,
                        "source_filename": body.filename,
                    },
                )
                python_logger().info(
                    "persona import archive: %r -> sandbox %s (%d files)",
                    body.filename or "<zip>", session.sandbox.root, len(copied),
                )

                known = [f for f in copied if f in _KNOWN_MEMORY_FILES]
                extra = [f for f in copied if f not in _KNOWN_MEMORY_FILES]
                _snapshot_capture(session, trigger="persona_update")
                return {
                    "ok": True,
                    "character_name": character_name,
                    "persona": persona.summary(),
                    "copied_files": copied,
                    "known_files": known,
                    "extra_files": extra,
                    "warnings": warnings,
                    "sandbox_memory_dir": str(sb_memory_dir / character_name),
                }
        except SessionConflictError as exc:
            raise _session_conflict_to_http(exc) from exc
        except LookupError as exc:
            raise HTTPException(
                status_code=404, detail={"message": str(exc)}) from exc
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
