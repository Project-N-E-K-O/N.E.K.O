"""Setup workspace backend — session persona + import from real character.

Scope (PLAN §Workspace 1 + §Setup workspace):

- ``GET  /api/persona``                         full persona of active session.
- ``PUT  /api/persona``                         replace persona (whole body).
- ``PATCH /api/persona``                        patch persona (partial body).
- ``GET  /api/persona/real_characters``         list cat girls defined in
                                                the tester's *real* (un-sandboxed)
                                                ``characters.json`` so the UI
                                                can populate the Import list.
- ``POST /api/persona/import_from_real/{name}`` copy memory/ files + persona
                                                metadata from a real character
                                                into the current sandbox.

All mutating endpoints hold the per-session lock via
:meth:`SessionStore.session_operation`; reads bypass the lock because they
never touch session state.

Implementation notes:
    * The sandbox patches ``cm.memory_dir`` / ``cm.chara_dir`` to point at
      the session's scratch tree. To read the tester's real character files
      during an active session we grab :meth:`Sandbox.real_paths` which
      returns the pre-patch values.
    * Import is **filesystem-first**: we recursively copy the real
      character's memory subdirectory into the sandbox so upstream code
      (``PersonaManager`` / ``FactStore`` / …) can operate unchanged in
      P07+. The persona metadata form fields (``master_name`` / prompt) are
      pulled from the real ``characters.json`` to keep the Setup UI in sync.
    * Nothing in this router ever writes to the *real* (un-sandboxed)
      filesystem — the whole design treats the host filesystem as read-only.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.prompts_chara import get_lanlan_prompt, is_default_prompt
from utils.config_manager import get_config_manager

from tests.testbench.logger import python_logger
from tests.testbench.persona_config import PersonaConfig
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
            return {"persona": merged_persona.summary()}
    except SessionConflictError as exc:
        raise _session_conflict_to_http(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc


# ── effective system prompt preview (P05 补强) ─────────────────────
#
# 测试人员在 Persona 子页编辑时想知道"留空 / 用当前语言, 实际生成的是什么".
# 真实运行时组装在两个地方做: `config_manager.get_character_data()` 用
# `is_default_prompt()` 兜底换成 `get_lanlan_prompt(lang)`, 然后在
# `tests/dump_llm_input.py` 里用 `{LANLAN_NAME}/{MASTER_NAME}` 替换为真名.
# 这里把同样的两步在路由里复刻出来做预览, 不触发任何 IO/存储.
#
# 不加锁 (纯读); 允许 query 参数覆盖 (master_name / character_name / lang),
# 这样 UI 可以用 textarea 正在编辑的 draft 值做实时预览, 而无需先 Save.


@router.get("/effective_system_prompt")
async def effective_system_prompt(
    lang: str | None = None,
    master_name: str | None = None,
    character_name: str | None = None,
) -> dict[str, Any]:
    """Preview the system prompt that will actually be fed to the LLM.

    Flow mirrors upstream exactly:
        1. ``is_default_prompt(stored)`` → 空串或任一语言默认文 → 用
           ``get_lanlan_prompt(lang)`` 取当前 language 版本. 否则保留 stored.
        2. 对结果做 ``{LANLAN_NAME} / {MASTER_NAME}`` 替换.

    Query 参数, 皆可省略:
        - ``lang``            覆盖 session.persona.language (想看"切语言会怎样")
        - ``master_name``     覆盖当前主人名
        - ``character_name``  覆盖当前角色名

    Return shape::

        {
            "language":          "zh-CN",
            "master_name":       "天凌",
            "character_name":    "N.E.K.O",
            "stored_prompt":     "<textarea 里/已保存的内容>",
            "stored_is_default": true,      # upstream 会把它当"空"处理
            "template_used":     "default" | "stored",
            "template_raw":      "<含 {LANLAN_NAME} 占位符>",
            "resolved":          "<替换完名字, 真实送给 LLM 的字符串>"
        }

    空名字**不做替换**—保留占位符原样, 让 tester 一眼看到"这里还需要填角色名",
    避免替换成空串后默默消失造成混淆.
    """
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
    """
    session = _require_session()
    paths = session.sandbox.real_paths()
    if not paths:
        return {
            "config_dir": None,
            "memory_dir": None,
            "master_name": "",
            "characters": [],
            "note": "Sandbox not applied; cannot introspect real paths.",
        }

    config_dir = paths["config_dir"]
    memory_dir = paths["memory_dir"]
    raw = _read_real_characters_json(config_dir)
    if raw is None:
        return {
            "config_dir": str(config_dir),
            "memory_dir": str(memory_dir),
            "master_name": "",
            "characters": [],
            "note": "characters.json missing or unreadable.",
        }

    master_name = ""
    master_block = raw.get("主人")
    if isinstance(master_block, dict):
        master_name = str(master_block.get("档案名", "") or "")

    catgirls = raw.get("猫娘")
    summaries: list[dict[str, Any]] = []
    if isinstance(catgirls, dict):
        current = str(raw.get("当前猫娘", "") or "")
        for name, entry in catgirls.items():
            if not isinstance(entry, dict):
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

    return {
        "config_dir": str(config_dir),
        "memory_dir": str(memory_dir),
        "master_name": master_name,
        "characters": summaries,
    }


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
    :func:`ConfigManager.load_characters` expects a ``{"猫娘": ..., "主人": ...}``
    dict. We keep the shape faithful to upstream so later phases (P07/P08) can
    just call ``cm.load_characters()`` without special-casing testbench.
    """
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
