"""Setup workspace backend — session persona + character imports.

Scope (PLAN §Workspace 1 + §Setup workspace + P10 preset 补丁):

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
                                                      serves as "reset sandbox to
                                                      known state" when re-applied.

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
      only the *source directory* differs. This keeps "whatever was imported"
      indistinguishable from the sandbox's POV, so downstream code never
      needs to branch on import origin.
    * Nothing in this router ever writes to the *real* (un-sandboxed)
      filesystem — the whole design treats the host filesystem as read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.prompts_chara import get_lanlan_prompt, is_default_prompt
from utils.config_manager import get_config_manager

from tests.testbench.logger import python_logger
from tests.testbench.persona_config import PersonaConfig
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

    P24 Day 8 §12.4.A (2026-04-22): 用户 dev_note L17 反馈 "本地有数据但
    列表不显示只有默认小天". Backend scan 逻辑与主程序 ``raw['猫娘']`` 契约
    同源, 代码层面无 bug. 真正根因大概率在用户本地 characters.json 的
    具体内容 (手动编辑过 / 主程序新增字段 / 异常 entry 被 ``isinstance(entry,
    dict)`` 过滤). 加诊断字段 ``note`` 覆盖所有空态路径 + ``skipped_entries``
    列出被过滤掉的 key + 原因, 让用户手测时**自己诊断**不需要来回贴文件.
    """
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

    # 配置路径分裂检测 (2026-04-22 dev_note L17 根因):
    # 用户报告"Documents\<app>\config\characters.json 改了但程序读不到".
    # 两种可能:
    #   (a) Windows CFA 把 Documents 判为只读, ConfigManager 回退到
    #       AppData\Local → `cm._readable_docs_dir` 非 None;
    #   (b) 主程序新版本直接以 AppData\Local 作 primary 候选, 根本不
    #       尝试 Documents; 用户历史遗留的 Documents\<app>\ 目录仍然
    #       存在但主程序永远不读 → `cm._readable_docs_dir = None`.
    # 两种场景的用户表现完全一致 ("改了没生效"), 用 **启发式**: 直接查
    # `%USERPROFILE%\Documents\<app_name>\config\characters.json` 是否存在,
    # 存在且与 active 路径不同 → 有分裂风险, 警告用户.
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
                # 把被过滤的条目透出 + 原因, 让用户自查.
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

    # 不管成功与否都 log 一次给 live_runtime_log / session log 追溯, 用户手测时
    # 可以去看日志看扫到的完整真相.
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
# Preset 与 real-character 导入**共享** `_write_sandbox_characters_json` +
# `_copytree_safe` + session.persona 回填逻辑, 只是数据源换成仓库里的
# `tests/testbench/presets/<preset_id>/`. 设计成"内置虚拟 real"而不是另一套
# endpoint + service, 是为了保证:
#   (a) 测试人员感知一致 — 两种来源的导入效果完全相同 (覆盖 characters.json +
#       覆盖 memory/<char>/ 文件 + 更新 session.persona);
#   (b) 未来新增 "从 Hugging Face 拉" / "从压缩包导入" 时能复用这条管线;
#   (c) 不留"只灌 memory 不灌 characters.json"之类的半成品调用路径.


def _load_preset_meta(preset_id: str) -> dict[str, Any] | None:
    """Read ``presets/<id>/meta.json``; return None if missing/invalid.

    ``meta.json`` schema::

        {
          "id": "<preset_id>",                ← 必须与目录名一致
          "display_name": "<人类可读名>",
          "description": "<一句话说明>",
          "language": "zh-CN" | "en" | ...,
          "character_name": "<在 characters.json 里的 key>"
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

    返回按 id 字典序排好的 ``(preset_id, preset_dir)`` 列表. 忽略
    ``__pycache__``、``__init__.py`` 这类非预设条目.
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

    主程序 ``FactStore`` 用 ``sha256(text)[:16]`` 做 dedup key; 预设文件里我们
    故意把 ``hash`` 留空字符串, 导入时现算 — 这样维护预设不用手算哈希, 也
    避免复制时文本微调却忘改 hash 造成 dedup 混乱.
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
            with facts_path.open("w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
        except (OSError, json.JSONDecodeError) as exc:
            python_logger().warning(
                "persona_router: normalize facts.json in %s failed: %s",
                facts_path, exc,
            )
    return copied


@router.get("/builtin_presets")
async def list_builtin_presets() -> dict[str, Any]:
    """Enumerate git-tracked character presets bundled with the testbench.

    用于 Setup → Import 页渲染"内置预设"区. 无需 sandbox / session — 即使空会话
    也可看列表, 这样用户可以在"建会话 → 灌预设"两步之间先预览有哪些预设.
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
        (覆盖同名文件, 其他文件保留)
      * Fills empty ``hash`` fields in copied ``facts.json``
      * Updates ``session.persona`` with preset's master/character/system_prompt

    用户反复调用同一个 preset 相当于**一键清零为预设状态**: characters.json 会
    被整体覆写, memory 里同名 JSON 会被覆盖 (facts/persona/recent 都是源头真
    相). 但**额外**存在于 sandbox (preset 里没有) 的文件 — 例如用户自己新建的
    ``reflections.json`` / ``persona_corrections.json`` / ``surfaced.json`` /
    ``time_indexed.db`` — 不会被动, 需要用户自己去 memory 子页或磁盘上清. 这是
    故意的: "覆盖性 reset" 而不是 "清空式 reset", 避免丢失用户手动加进去的
    调试数据.
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
