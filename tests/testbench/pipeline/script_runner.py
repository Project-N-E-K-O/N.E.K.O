"""Script runner — scripted-dialogue pipeline for P12.

PLAN §P12: read ``dialog_templates/*.json`` (merge builtin + user, user overrides
same-name builtin); support ``Next turn`` and ``Run all``; consume each
``turns[].time`` to advance virtual clock. ``role=assistant`` ``expected``
automatically fills real AI reply's ``reference_content``, providing
ComparativeJudger (P15+) reference; ``bootstrap`` resets session origin.

Design points
--------
* **Script cursor is independent of ``session.messages``**: ``role=assistant`` turns
  only carry expected, not real messages, and occupy no ``session.messages``
  slot. Binding cursor to message indices breaks with ``inject_system`` / edits.
  Use independent ``cursor``; each ``advance_one_user_turn`` stages assistant
  expected in ``pending_reference`` until next LLM reply
  persists. Message edits cannot affect it; next script turn always means next user
  turn submission.
* **Merge consecutive assistant expected values**: rare but valid (multiple assistant
  turns at one position with expected); join using ``\\n---\\n`` rather than letting latter
  overwrite former.
* **Idempotent bootstrap**: apply only with empty ``session.messages``, warning otherwise;
  changing clock after messages creates negative time differences later.
* **Reuse stream_send**: scripted user turns follow exactly manual ``/chat/send``
  path (``chat_runner.get_chat_backend().stream_send(...,
  source='script')``). Frontend receives identical SSE ``user`` / ``assistant_start``
  / ``delta`` / ``assistant`` / ``done`` / ``error`` events,
  plus ``script_turn_done`` / ``script_exhausted`` for progress.
* **Reference fill timing**: fill at ``{event:'assistant'}``, after full
  content joins and persists in ``assistant_msg['content']``. Stream interruption /
  failure (``{event:'error'}``) discards ``pending_reference``, keeping cursor unchanged
  so next script retry consumes same user turn again.
* **Error semantics**: :class:`ScriptError` codes cover distinct frontend toasts:
  ``ScriptNotFound`` / ``ScriptSchemaInvalid`` / ``ScriptNotLoaded``
  / ``ScriptExhausted`` / ``NoActiveSession``.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Final

from tests.testbench import config as tb_config
from tests.testbench.chat_messages import (
    ROLE_ASSISTANT,
    ROLE_USER,
    SOURCE_SCRIPT,
)
from tests.testbench.logger import python_logger
from tests.testbench.pipeline.chat_runner import get_chat_backend
from tests.testbench.session_store import Session


# ── errors ──────────────────────────────────────────────────────────


class ScriptError(RuntimeError):
    """Raised by the script pipeline; maps to a specific HTTP status.

    Codes:
        * ``NoActiveSession`` — no session created (404).
        * ``ScriptNotFound`` — name absent from merged list (404).
        * ``ScriptSchemaInvalid`` — readable JSON with invalid schema (422).
        * ``ScriptNotLoaded`` — next/run_all before /load (412).
        * ``ScriptExhausted`` — next requested after end (412).
        * ``ScriptTurnFailed`` — internal error (500).
    """

    def __init__(self, code: str, message: str, status: int = 500) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}")


# ── template loading ────────────────────────────────────────────────


_DURATION_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)


def _parse_duration_text(text: str) -> int | None:
    """``\"1h30m\" / \"2d\" / \"45s\" / \"3600\"`` → seconds (``None`` if unparsable).

    Bare numbers mean seconds; d/h/m/s units are case-insensitive. Match static/core/time_utils.js
    ``parseDurationText`` to prevent JS/Python parsing disagreement.
    """
    t = (text or "").strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    total = 0
    any_match = False
    for m in _DURATION_RE.finditer(t):
        any_match = True
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "d":
            total += n * 86400
        elif unit == "h":
            total += n * 3600
        elif unit == "m":
            total += n * 60
        else:
            total += n
    return total if any_match else None


def _valid_turn_role(role: Any) -> bool:
    return role in (ROLE_USER, ROLE_ASSISTANT)


def _normalize_template(raw: dict[str, Any], *, source: str, path: Path) -> dict[str, Any]:
    """Validate + normalize one template JSON, raise :class:`ScriptError`.

    Accepts a dict freshly loaded from ``json.load`` and returns a cleaner
    dict with default fields filled in. We don't use pydantic here — the
    schema is small enough that hand-rolled validation produces better
    zh-CN error messages than pydantic's default.
    """
    if not isinstance(raw, dict):
        raise ScriptError(
            "ScriptSchemaInvalid",
            f"{path.name}: 顶层必须是 JSON object, 收到 {type(raw).__name__}.",
            status=422,
        )
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ScriptError(
            "ScriptSchemaInvalid",
            f"{path.name}: 缺少非空 'name' 字段.",
            status=422,
        )
    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ScriptError(
            "ScriptSchemaInvalid",
            f"{path.name}: 'turns' 必须是非空数组.",
            status=422,
        )
    normalized_turns: list[dict[str, Any]] = []
    for i, t in enumerate(turns):
        if not isinstance(t, dict):
            raise ScriptError(
                "ScriptSchemaInvalid",
                f"{path.name}: turns[{i}] 必须是 object.",
                status=422,
            )
        role = t.get("role")
        if not _valid_turn_role(role):
            raise ScriptError(
                "ScriptSchemaInvalid",
                f"{path.name}: turns[{i}].role={role!r} 不合法, 只支持 user / assistant.",
                status=422,
            )
        if role == ROLE_USER:
            content = t.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ScriptError(
                    "ScriptSchemaInvalid",
                    f"{path.name}: turns[{i}] role=user 必须有非空 'content'.",
                    status=422,
                )
            normalized_turns.append({
                "role": ROLE_USER,
                "content": content,
                "time": t.get("time") if isinstance(t.get("time"), dict) else None,
                "expected": None,  # User turns cannot meaningfully carry expected; ignore it.
            })
        else:  # assistant
            expected = t.get("expected")
            normalized_turns.append({
                "role": ROLE_ASSISTANT,
                "content": None,
                "time": None,  # Assistant-turn time is meaningless; always drop it.
                "expected": (expected if isinstance(expected, str) else None),
            })
    bootstrap = raw.get("bootstrap")
    if bootstrap is not None and not isinstance(bootstrap, dict):
        raise ScriptError(
            "ScriptSchemaInvalid",
            f"{path.name}: 'bootstrap' 若存在必须是 object (含 virtual_now / last_gap_minutes).",
            status=422,
        )
    return {
        "name": name.strip(),
        "description": (raw.get("description") or "").strip(),
        "user_persona_hint": (raw.get("user_persona_hint") or "").strip(),
        "bootstrap": bootstrap,
        "turns": normalized_turns,
        "source": source,
        "path": str(path),
    }


def _scan_dir(directory: Path, *, source: str) -> list[dict[str, Any]]:
    """Read all ``*.json`` in a directory and return normalized metadata.

    Individual JSON read failure warns and skips only that template, never empties directory;
    manually moving one invalid JSON into user directory cannot blank UI list.
    """
    if not directory.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            norm = _normalize_template(raw, source=source, path=path)
            results.append(norm)
        except ScriptError as exc:
            python_logger().warning(
                "[script_runner] 跳过格式不合法的模板 %s: %s", path, exc,
            )
        except (OSError, json.JSONDecodeError) as exc:
            python_logger().warning(
                "[script_runner] 读模板失败 %s: %s", path, exc,
            )
    return results


def list_templates() -> list[dict[str, Any]]:
    """Return merged (builtin + user) template meta list for the UI dropdown.

    - Scan builtin first (``tests/testbench/dialog_templates/``).
    - Scan user later (``tests/testbench_data/dialog_templates/``); matching ``name``
      overrides builtin, retaining ``overriding_builtin: True`` for UI badges/hints
      (not yet used by composer.js; reserved for Diagnostics).
    - Return frontend metadata only (name / description / user_persona_hint
      / turns_count / source / path / overriding_builtin), **without** turns
      array, avoiding tens of KB during dropdown rendering.
    """
    builtin_list = _scan_dir(tb_config.BUILTIN_DIALOG_TEMPLATES_DIR, source="builtin")
    user_list = _scan_dir(tb_config.USER_DIALOG_TEMPLATES_DIR, source="user")

    merged: dict[str, dict[str, Any]] = {}
    for t in builtin_list:
        merged[t["name"]] = t
    for t in user_list:
        overriding = t["name"] in merged
        t["overriding_builtin"] = overriding
        merged[t["name"]] = t

    meta_list: list[dict[str, Any]] = []
    for t in merged.values():
        meta_list.append({
            "name": t["name"],
            "description": t["description"],
            "user_persona_hint": t["user_persona_hint"],
            "turns_count": len(t["turns"]),
            "source": t["source"],
            "path": t["path"],
            "overriding_builtin": t.get("overriding_builtin", False),
        })
    meta_list.sort(key=lambda x: (x["source"] != "user", x["name"]))
    return meta_list


def load_template(name: str) -> dict[str, Any]:
    """Return the full normalized template dict by ``name``.

    User overrides builtin at same name, matching :func:`list_templates`.
    Raises :class:`ScriptError(ScriptNotFound)` if no match.
    """
    builtin_list = _scan_dir(tb_config.BUILTIN_DIALOG_TEMPLATES_DIR, source="builtin")
    user_list = _scan_dir(tb_config.USER_DIALOG_TEMPLATES_DIR, source="user")
    # User overrides later.
    by_name: dict[str, dict[str, Any]] = {}
    for t in builtin_list:
        by_name[t["name"]] = t
    for t in user_list:
        by_name[t["name"]] = t
    tmpl = by_name.get(name)
    if tmpl is None:
        raise ScriptError(
            "ScriptNotFound",
            f"找不到名为 {name!r} 的脚本模板. 请检查 dialog_templates 目录.",
            status=404,
        )
    return tmpl


# ── P12.5: CRUD for the Setup → Scripts subpage editor ─────────────
#
# Difference from `load_template` above:
#   * `load_template` loads sessions, returning normalized values (dropping
#     meaningless user expected / assistant time fields), suitable for
#     pipeline execution.
#   * Five methods below (`read_template` / `save_user_template` / `delete_user_template`
#     / `validate_template_dict` / `duplicate_builtin_to_user`) serve the editor,
#     exposing builtin originals + user-override state + soft-validation errors so
#     UI can highlight field errors.
#
# Semantics:
#   * User filename = ``<name>.json``; name is unique id.
#   * User name matching builtin overrides builtin, matching loader precedence.
#   * Builtins cannot change in place; use `duplicate_builtin_to_user` first.
#   * `save_user_template` / `delete_user_template` touch only
#     `USER_DIALOG_TEMPLATES_DIR`; builtin-directory access returns 403.


_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


def _is_safe_template_name(name: str) -> bool:
    """Filename security validation blocks traversal, invalid characters, and empty names.

    Testers may enter names manually; directly joining ``USER_DIR / f'{name}.json'``
    with ``../`` or device prefixes writes outside data directory. Allow alphanumerics/underscores/
    hyphens, no leading separator, length ≤64; sufficient without traversal gaps.
    """
    return bool(_NAME_RE.match(name or ""))


def _collect_template_errors(raw: Any) -> list[dict[str, str]]:
    """Soft validation returns all field errors (empty list means success).

    Same semantics as `_normalize_template`, collecting all errors instead of raising,
    so editor highlights everything together. JSON-pointer-style path (``turns[2].content``)
    locates UI turn cards.
    """
    errors: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if not isinstance(raw, dict):
        add("", f"顶层必须是 JSON object, 收到 {type(raw).__name__}.")
        return errors

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        add("name", "缺少非空 'name' 字段.")
    elif not _is_safe_template_name(name.strip()):
        add(
            "name",
            "name 只能包含字母/数字/下划线/短横线, 首字不能是符号, 长度 ≤ 64.",
        )

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        add("description", "description 若存在必须是字符串.")

    hint = raw.get("user_persona_hint")
    if hint is not None and not isinstance(hint, str):
        add("user_persona_hint", "user_persona_hint 若存在必须是字符串.")

    bootstrap = raw.get("bootstrap")
    if bootstrap is not None:
        if not isinstance(bootstrap, dict):
            add("bootstrap", "bootstrap 若存在必须是 object.")
        else:
            vnow_raw = bootstrap.get("virtual_now")
            if vnow_raw is not None and vnow_raw != "":
                try:
                    datetime.fromisoformat(str(vnow_raw))
                except (TypeError, ValueError):
                    add(
                        "bootstrap.virtual_now",
                        f"virtual_now={vnow_raw!r} 不是合法 ISO 时间 (e.g. 2025-01-01T09:00).",
                    )
            gap = bootstrap.get("last_gap_minutes")
            if gap is not None and gap != "":
                try:
                    float(gap)
                except (TypeError, ValueError):
                    add(
                        "bootstrap.last_gap_minutes",
                        f"last_gap_minutes={gap!r} 不是数字.",
                    )

    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns:
        add("turns", "turns 必须是非空数组.")
        return errors

    for i, t in enumerate(turns):
        path = f"turns[{i}]"
        if not isinstance(t, dict):
            add(path, "必须是 object.")
            continue
        role = t.get("role")
        if not _valid_turn_role(role):
            add(f"{path}.role", f"role={role!r} 不合法, 只支持 user / assistant.")
            continue
        if role == ROLE_USER:
            content = t.get("content")
            if not isinstance(content, str) or not content.strip():
                add(f"{path}.content", "role=user 必须有非空 content.")
            time_dict = t.get("time")
            if time_dict is not None and not isinstance(time_dict, dict):
                add(f"{path}.time", "time 若存在必须是 object (含 advance / at / advance_seconds).")
        else:  # assistant
            expected = t.get("expected")
            if expected is not None and not isinstance(expected, str):
                add(f"{path}.expected", "expected 若存在必须是字符串.")

    return errors


def _write_user_template_atomic(path: Path, data: dict[str, Any]) -> None:
    """Crash-safe write of a user-authored script template JSON.

    P24 §4.1.2 (2026-04-21): the previous bespoke implementation claimed
    \"power-loss safe\" in its docstring but had no ``fsync`` — a power loss during
    write could leave a zero-byte file masquerading as a valid template.
    Now delegates to the unified ``atomic_io.atomic_write_json`` helper
    which includes ``fh.flush() + os.fsync(...)`` before the rename.

    Note: ``atomic_write_json`` uses ``<path>.tmp`` (same stem) rather
    than the ``tempfile.mkstemp(prefix='.<name>.')`` pattern used here
    previously — same atomicity guarantees (both same-directory renames),
    just a slightly different tmp filename shape. No observable diff
    for callers.
    """
    from tests.testbench.pipeline.atomic_io import atomic_write_json
    atomic_write_json(path, data)


def validate_template_dict(raw: Any) -> dict[str, Any]:
    """Soft validation for UI validate button and save prerequisite.

    Returns:
        ``{\"ok\": bool, \"errors\": [{path, message}, ...], \"normalized\": <dict>?}``

    ``normalized`` exists only if ``ok=True``, representing disk-save form, matching
    ``_normalize_template`` output. Run ``_normalize_template`` after soft validation
    to centralize redundant-field removal/default filling in one implementation.
    """
    errors = _collect_template_errors(raw)
    if errors:
        return {"ok": False, "errors": errors}
    # Soft validation passed → hard normalize (should not raise; an exception means inconsistent
    # checks, an internal script_runner bug; fall back to generic error).
    try:
        normalized = _normalize_template(raw, source="user", path=Path("<inline>"))
    except ScriptError as exc:
        return {
            "ok": False,
            "errors": [{"path": "", "message": f"内部规范化失败: {exc.message}"}],
        }
    return {"ok": True, "errors": [], "normalized": normalized}


def read_template(name: str) -> dict[str, Any]:
    """Editor details: active version + builtin/user existence.

    Returns:
        {
            \"active\": <normalized dict, user preferred>,
            \"has_builtin\": bool,
            \"has_user\": bool,
            \"overriding_builtin\": bool,  # has_user AND has_builtin
        }

    Neither version exists → ScriptError(ScriptNotFound, 404).
    """
    builtin_list = _scan_dir(tb_config.BUILTIN_DIALOG_TEMPLATES_DIR, source="builtin")
    user_list = _scan_dir(tb_config.USER_DIALOG_TEMPLATES_DIR, source="user")
    builtin = next((t for t in builtin_list if t["name"] == name), None)
    user = next((t for t in user_list if t["name"] == name), None)
    if builtin is None and user is None:
        raise ScriptError(
            "ScriptNotFound",
            f"找不到名为 {name!r} 的脚本模板.",
            status=404,
        )
    active = user if user is not None else builtin
    return {
        "active": active,
        "has_builtin": builtin is not None,
        "has_user": user is not None,
        "overriding_builtin": (user is not None and builtin is not None),
    }


def save_user_template(raw: Any) -> dict[str, Any]:
    """Save (create or overwrite) one user template; returns read_template result.

    Flow:
        1. ``validate_template_dict``; failure raises ScriptSchemaInvalid (422).
        2. Atomically write ``USER_DIALOG_TEMPLATES_DIR/<name>.json`` with
           normalized data, removing meaningless user expected / assistant time
           to keep disk JSON clean.
        3. ``read_template(name)`` returns fresh details plus ``overriding_builtin``
           for frontend overwrite-builtin notification.

    Persist normalized (_normalize_template output), never raw input,
    ensuring consistent clean JSON regardless of redundant UI fields.
    """
    result = validate_template_dict(raw)
    if not result["ok"]:
        # Use ScriptSchemaInvalid while returning field-level errors to frontend.
        # ScriptError accepts only message by default, so attach errors as attribute; router
        # prefers ``exc.errors`` when handling.
        exc = ScriptError(
            "ScriptSchemaInvalid",
            "模板校验未通过, 请修正后重新保存.",
            status=422,
        )
        exc.errors = result["errors"]  # type: ignore[attr-defined]
        raise exc

    normalized = result["normalized"]
    name = normalized["name"]
    if not _is_safe_template_name(name):
        # Second safeguard (already validated above; defense in depth).
        raise ScriptError(
            "ScriptSchemaInvalid",
            f"name={name!r} 不是合法文件名 (只允许字母/数字/下划线/短横线).",
            status=422,
        )

    # Remove read-only source/path metadata; disk JSON should not contain it.
    disk_payload = {
        "name": normalized["name"],
        "description": normalized.get("description", ""),
        "user_persona_hint": normalized.get("user_persona_hint", ""),
        "bootstrap": normalized.get("bootstrap"),
        "turns": [],
    }
    for t in normalized["turns"]:
        clean: dict[str, Any] = {"role": t["role"]}
        if t["role"] == ROLE_USER:
            clean["content"] = t["content"]
            if t.get("time"):
                clean["time"] = t["time"]
        else:  # assistant
            if t.get("expected"):
                clean["expected"] = t["expected"]
        disk_payload["turns"].append(clean)
    if disk_payload["bootstrap"] is None:
        disk_payload.pop("bootstrap")

    target = tb_config.USER_DIALOG_TEMPLATES_DIR / f"{name}.json"
    _write_user_template_atomic(target, disk_payload)
    python_logger().info(
        "[script_runner] saved user template %s -> %s", name, target,
    )
    details = read_template(name)
    return {
        "template": details["active"],
        "overriding_builtin": details["overriding_builtin"],
        "path": str(target),
    }


def delete_user_template(name: str) -> dict[str, Any]:
    """Delete one user template by name. Builtin is protected.

    - Touch user directory only; never delete builtin sample_*.json.
    - Name exists only as builtin, no user version →404 (nothing to delete).
    - Builtin remains after user deletion → return ``resurfaces_builtin: True``
      for frontend notice that built-in version is active again.

    Note: never mutate session.script_state here; router checks if loaded script
    matches deleted name and adds response warning. Keep this function pure.
    """
    if not _is_safe_template_name(name or ""):
        raise ScriptError(
            "ScriptSchemaInvalid",
            f"name={name!r} 非法, 不能删.",
            status=422,
        )
    target = tb_config.USER_DIALOG_TEMPLATES_DIR / f"{name}.json"
    if not target.exists():
        raise ScriptError(
            "ScriptNotFound",
            f"user 目录下没有 {name!r} 模板可删 (内置模板不可删).",
            status=404,
        )
    target.unlink()
    python_logger().info(
        "[script_runner] deleted user template %s (was at %s)", name, target,
    )
    # Builtin still exists?
    builtin_list = _scan_dir(tb_config.BUILTIN_DIALOG_TEMPLATES_DIR, source="builtin")
    resurfaces = any(t["name"] == name for t in builtin_list)
    return {
        "deleted_name": name,
        "resurfaces_builtin": resurfaces,
    }


def duplicate_builtin_to_user(
    source_name: str, target_name: str, *, overwrite: bool = False,
) -> dict[str, Any]:
    """Copy a template (usually builtin, also user→user) to a new user name.

    Typical UI action ``[复制为可编辑]`` copies full builtin to user directory,
    changing name to target_name. Validate via `save_user_template` on save,
    equivalent to manual creation.

    Conflicts:
        * User target_name exists + ``overwrite=False`` →409 Conflict.
        * target_name matches any builtin (even another source) →
          allowed as builtin override; UI decides whether confirmation is needed.
    """  # noqa: DOCSTRING_CJK
    if not _is_safe_template_name(target_name or ""):
        raise ScriptError(
            "ScriptSchemaInvalid",
            f"target_name={target_name!r} 非法 (只允许字母/数字/下划线/短横线, 首字非符号, ≤64 字).",
            status=422,
        )
    source_details = read_template(source_name)  # raises ScriptNotFound if missing.
    src = source_details["active"]

    target_path = tb_config.USER_DIALOG_TEMPLATES_DIR / f"{target_name}.json"
    if target_path.exists() and not overwrite:
        raise ScriptError(
            "ScriptTargetExists",
            f"user 目录已有同名模板 {target_name!r}. 请换个名字, 或带 overwrite=true 覆盖.",
            status=409,
        )

    # Deep-copy and change name, removing source/path metadata from disk output.
    new_raw: dict[str, Any] = copy.deepcopy(src)
    new_raw["name"] = target_name
    new_raw.pop("source", None)
    new_raw.pop("path", None)
    new_raw.pop("overriding_builtin", None)
    # Saving cleans expected/time=None values created by _normalize_template in turns,
    # so no need to strip beforehand.

    return save_user_template(new_raw)


# ── bootstrap + per-turn time ───────────────────────────────────────


def apply_bootstrap(
    session: Session, bootstrap: dict[str, Any] | None,
) -> list[str]:
    """Apply script ``bootstrap`` to ``session.clock``; return warnings.

    Apply only when ``session.messages`` is empty, or forced overwrite gives later messages
    negative time differences, breaking UI separators/prompt builder. On skip,
    return warning for router's /script/load response toast.
    """
    warnings: list[str] = []
    if not bootstrap:
        return warnings
    if session.messages:
        warnings.append(
            "脚本包含 bootstrap 但会话已有消息, 已跳过时钟重设. 若要让"
            " bootstrap 生效, 请先清空对话 (Re-run from here 到空) 或重"
            "建会话后再加载脚本."
        )
        return warnings

    vnow_raw = bootstrap.get("virtual_now")
    last_gap_min = bootstrap.get("last_gap_minutes")

    bootstrap_at: datetime | None = None
    if vnow_raw is not None:
        try:
            bootstrap_at = datetime.fromisoformat(str(vnow_raw))
        except ValueError:
            warnings.append(
                f"bootstrap.virtual_now={vnow_raw!r} 不是合法 ISO 时间, 已忽略."
            )
            bootstrap_at = None

    gap_seconds: int | None = None
    if last_gap_min is not None:
        try:
            gap_seconds = int(float(last_gap_min) * 60)
            if gap_seconds < 0:
                warnings.append(
                    f"bootstrap.last_gap_minutes={last_gap_min!r} 是负数, 已忽略."
                )
                gap_seconds = None
        except (TypeError, ValueError):
            warnings.append(
                f"bootstrap.last_gap_minutes={last_gap_min!r} 不是数字, 已忽略."
            )
            gap_seconds = None

    if bootstrap_at is None and gap_seconds is None:
        return warnings

    session.clock.set_bootstrap(
        bootstrap_at=bootstrap_at if bootstrap_at is not None else None,
        initial_last_gap_seconds=gap_seconds if gap_seconds is not None else None,
        sync_cursor=True,
    )
    session.logger.log_sync(
        "script.bootstrap.apply",
        payload={
            "bootstrap_at": bootstrap_at.isoformat() if bootstrap_at else None,
            "last_gap_seconds": gap_seconds,
        },
    )
    return warnings


def apply_turn_time(session: Session, time_dict: dict[str, Any] | None) -> list[str]:
    """Consume ``turns[i].time`` into ``clock.stage_next_turn``; return warnings.

    Supported fields (PLAN §8):
        * ``advance``       → parse duration (\"1h30m\" / \"45s\" / \"2d\") and add to stage.
        * ``advance_seconds`` → seconds directly.
        * ``at``            → absolute ISO time.
        * Missing field / ``time`` is ``None`` → no staging; use
          ``stream_send``'s ``per_turn_default_seconds`` default.

    Precedence: ``at`` over ``advance*``; ``advance`` over ``advance_seconds``,
    matching :meth:`VirtualClock.stage_next_turn`.
    """
    warnings: list[str] = []
    if not time_dict:
        return warnings

    at_raw = time_dict.get("at")
    if at_raw is not None:
        try:
            absolute = datetime.fromisoformat(str(at_raw))
            session.clock.stage_next_turn(absolute=absolute)
            return warnings
        except ValueError:
            warnings.append(
                f"time.at={at_raw!r} 不是合法 ISO 时间, 已忽略."
            )

    advance_text = time_dict.get("advance")
    if advance_text is not None:
        seconds = _parse_duration_text(str(advance_text))
        if seconds is None or seconds < 0:
            warnings.append(
                f"time.advance={advance_text!r} 不是合法时长, 已忽略."
            )
        else:
            session.clock.stage_next_turn(delta=timedelta(seconds=seconds))
            return warnings

    advance_seconds = time_dict.get("advance_seconds")
    if advance_seconds is not None:
        try:
            # OverflowError covers ``int(float('inf'))`` from a script
            # author writing ``advance_seconds: 1e400`` (YAML parses
            # to ±inf). 2nd-batch AI review #4 family extension.
            secs = int(advance_seconds)
            if secs < 0:
                raise ValueError("negative")
            session.clock.stage_next_turn(delta=timedelta(seconds=secs))
        except (TypeError, ValueError, OverflowError):
            warnings.append(
                f"time.advance_seconds={advance_seconds!r} 非法, 已忽略."
            )

    return warnings


# ── load / unload ──────────────────────────────────────────────────


def load_script_into_session(
    session: Session, name: str,
) -> tuple[dict[str, Any], list[str]]:
    """Install ``name`` as the session's current script; return (state, warns)."""
    tmpl = load_template(name)
    warnings = apply_bootstrap(session, tmpl.get("bootstrap"))

    now_iso = session.clock.now().isoformat(timespec="seconds")
    session.script_state = {
        "template_name": tmpl["name"],
        "template_source": tmpl["source"],
        "turns": list(tmpl["turns"]),
        "cursor": 0,
        "turns_count": len(tmpl["turns"]),
        "pending_reference": None,
        "loaded_at": now_iso,
        "description": tmpl["description"],
        "user_persona_hint": tmpl["user_persona_hint"],
    }
    session.logger.log_sync(
        "script.load",
        payload={
            "template_name": tmpl["name"],
            "source": tmpl["source"],
            "turns_count": len(tmpl["turns"]),
            "warnings": warnings,
        },
    )
    return describe_script_state(session), warnings


def unload_script_from_session(session: Session) -> None:
    prev = session.script_state
    session.script_state = None
    if prev is not None:
        session.logger.log_sync(
            "script.unload",
            payload={"template_name": prev.get("template_name")},
        )


def describe_script_state(session: Session) -> dict[str, Any] | None:
    """Return a JSON-safe view of the current script state (for /script/state)."""
    s = session.script_state
    if s is None:
        return None
    # Do not return full turns array (possibly dozens); UI needs cursor + total + name only.
    return {
        "template_name": s["template_name"],
        "template_source": s["template_source"],
        "turns_count": s["turns_count"],
        "cursor": s["cursor"],
        "pending_reference": s.get("pending_reference"),
        "loaded_at": s["loaded_at"],
        "description": s.get("description", ""),
        "user_persona_hint": s.get("user_persona_hint", ""),
        "exhausted": s["cursor"] >= s["turns_count"],
    }


# ── per-turn execution ─────────────────────────────────────────────


_REFERENCE_JOIN: Final[str] = "\n---\n"


def _merge_pending_reference(existing: str | None, new_expected: str) -> str:
    """Merge expected from consecutive assistant turns.

    Rare but valid (multiple assistant turns with expected at same position);
    join with ``\\n---\\n`` to avoid overwriting earlier values.
    """
    new_text = (new_expected or "").strip()
    if not new_text:
        return existing or ""
    if not existing:
        return new_text
    return f"{existing}{_REFERENCE_JOIN}{new_text}"


async def advance_one_user_turn(session: Session) -> AsyncIterator[dict[str, Any]]:
    """Run one scripted user turn; yield the same SSE events as /chat/send.

    Semantics (AGENT_NOTES §4.17 #35): ``role=assistant`` turns carry
    ``expected``, the **ideal AI reply to the preceding user turn**. Therefore
    send user at cursor first; after AI reply, look at immediately following
    assistant turns and merge expected into that reply's
    reference_content. Expected uses lookahead,
    not lookbehind.

    Flow:
        1. If ``cursor`` points to ``role=assistant`` (orphan expected without
           matching preceding user), skip with warning.
        2. ``cursor`` points to ``role=user`` → (a) consume ``time`` into
           stage_next_turn; (b) **look ahead first**, merging consecutive assistant
           expected after ``cursor+1`` into ``fill_reference``;
           (c) call ``chat_runner.stream_send(..., source='script')``, forwarding
           events.
        3. On ``{event:'assistant'}``, write ``fill_reference`` to
           assistant message's ``reference_content``.
        4. Success → advance ``cursor`` beyond user + consecutive assistant turns
           to ``lookahead_end``; failure → leave ``cursor`` unchanged
           so tester can fix config and retry same turn.
        5. Yield ``{event:'script_turn_done', cursor, turns_count}``.
        6. At end also yield ``{event:'script_exhausted'}``.

    ``pending_reference`` design note (session.script_state): this lookahead
    implementation no longer needs pending across calls, clearing after each turn;
    retain field for future extensions such as one user split across multiple annotations.
    """
    state = session.script_state
    if state is None:
        raise ScriptError(
            "ScriptNotLoaded",
            "请先 POST /api/chat/script/load 加载脚本再调用 next / run_all.",
            status=412,
        )
    turns: list[dict[str, Any]] = state["turns"]
    cursor: int = state["cursor"]

    if cursor >= len(turns):
        raise ScriptError(
            "ScriptExhausted",
            f"脚本 {state['template_name']!r} 已经跑到末尾 ({cursor}/{len(turns)}), 没有下一轮可执行. 请重新加载或卸载.",
            status=412,
        )

    # Step 1: skip orphan assistant turns at cursor (expected without preceding user).
    # Valid templates should not contain these, but skip tolerantly and warn.
    orphan_warnings: list[str] = []
    while cursor < len(turns) and turns[cursor]["role"] == ROLE_ASSISTANT:
        expected = turns[cursor].get("expected") or ""
        orphan_warnings.append(
            f"turns[{cursor}] 是孤儿 assistant (前面没有待匹配的 user turn), "
            f"expected 已丢弃: {expected[:40]!r}{'...' if len(expected) > 40 else ''}",
        )
        cursor += 1

    if cursor >= len(turns):
        # All orphan assistant turns: script immediately exhausted.
        state["pending_reference"] = None
        state["cursor"] = cursor
        yield {
            "event": "script_turn_done",
            "cursor": cursor,
            "turns_count": len(turns),
            "had_user_turn": False,
            **({"warning": orphan_warnings[-1]} if orphan_warnings else {}),
        }
        yield {"event": "script_exhausted"}
        return

    # Step 2: cursor now points to a user turn.
    user_turn = turns[cursor]
    user_content = user_turn["content"]
    time_dict = user_turn.get("time")

    time_warnings = apply_turn_time(session, time_dict)
    all_warnings = orphan_warnings + time_warnings
    if all_warnings:
        yield {
            "event": "script_turn_warnings",
            "warnings": all_warnings,
        }

    # Step 2b: look ahead through consecutive assistant turns after cursor+1,
    # merging expected into this reply's reference; stop at next user or end.
    lookahead_end = cursor + 1
    fill_reference: str = ""
    while (
        lookahead_end < len(turns)
        and turns[lookahead_end]["role"] == ROLE_ASSISTANT
    ):
        expected = turns[lookahead_end].get("expected") or ""
        if expected.strip():
            fill_reference = _merge_pending_reference(fill_reference, expected)
        lookahead_end += 1

    state["pending_reference"] = fill_reference or None
    state["cursor"] = cursor  # Do not advance until stream finishes successfully.

    # Step 3: forward stream_send events and fill reference when assistant appears.
    backend = get_chat_backend()
    remaining_reference: str | None = fill_reference if fill_reference.strip() else None
    stream_ok = False
    try:
        async for event in backend.stream_send(
            session,
            user_content=user_content,
            role=ROLE_USER,
            source=SOURCE_SCRIPT,
        ):
            ev = event.get("event")
            if ev == "assistant" and remaining_reference:
                msg = event.get("message") or {}
                msg_id = msg.get("id")
                # Message is the same object as session.messages[-1] (see
                # OfflineChatBackend.stream_send's end); changing it changes session.
                if msg_id:
                    msg["reference_content"] = _merge_pending_reference(
                        msg.get("reference_content"), remaining_reference,
                    )
                    session.logger.log_sync(
                        "script.reference.fill",
                        payload={
                            "message_id": msg_id,
                            "reference_chars": len(msg["reference_content"] or ""),
                            "script_name": state["template_name"],
                            "user_cursor": cursor,
                            "consumed_assistant_turns": lookahead_end - cursor - 1,
                        },
                    )
                remaining_reference = None  # Fill once only, guarding duplicate assistant events.
            if ev == "done":
                stream_ok = True
            yield event
    except Exception as exc:  # noqa: BLE001
        # stream_send should convert internal exceptions to {event:'error'} and return; this fallback
        # wraps ScriptError without exposing underlying stack traces to users.
        python_logger().exception(
            "[script_runner] stream_send crashed mid-turn (session=%s): %s",
            session.id, exc,
        )
        yield {
            "event": "error",
            "error": {
                "type": "ScriptTurnFailed",
                "message": f"脚本第 {cursor + 1} 轮执行失败: {type(exc).__name__}: {exc}",
            },
        }

    # Step 4: advance cursor according to stream success/failure.
    if stream_ok:
        # User turn + consumed assistant turns completed → jump directly to
        # lookahead_end (next user or end).
        cursor = lookahead_end
        state["cursor"] = cursor
        state["pending_reference"] = None
    else:
        # Stream failure: keep cursor on failed user turn,
        # clear pending_reference; retry rescans lookahead rather than reusing stale value.
        state["pending_reference"] = None

    exhausted = cursor >= len(turns)

    yield {
        "event": "script_turn_done",
        "cursor": cursor,
        "turns_count": len(turns),
        "had_user_turn": True,
        "stream_ok": stream_ok,
    }
    if exhausted:
        yield {"event": "script_exhausted"}


async def run_all_turns(session: Session) -> AsyncIterator[dict[str, Any]]:
    """Iterate ``advance_one_user_turn`` until exhausted or an error occurs.

    Stop on any of:
        * ``{event:'script_exhausted'}``: script end.
        * ``{event:'error'}``: stream_send error (missing config / LLM timeout, etc.).
        * ``ScriptExhausted``: defensive loop guard, theoretically unreachable since
          ``script_exhausted`` event arrives first; retained for concurrency races.
    """
    while True:
        state = session.script_state
        if state is None:
            return
        if state["cursor"] >= state["turns_count"]:
            yield {"event": "script_exhausted"}
            return

        saw_error = False
        try:
            async for event in advance_one_user_turn(session):
                yield event
                if event.get("event") == "error":
                    saw_error = True
                if event.get("event") == "script_exhausted":
                    return
        except ScriptError as exc:
            yield {
                "event": "error",
                "error": {"type": exc.code, "message": exc.message},
            }
            return

        if saw_error:
            # Previous stream_send failed; stop instead of persisting, leaving frontend to decide.
            return


__all__ = [
    "ScriptError",
    "advance_one_user_turn",
    "apply_bootstrap",
    "apply_turn_time",
    "delete_user_template",
    "describe_script_state",
    "duplicate_builtin_to_user",
    "list_templates",
    "load_script_into_session",
    "load_template",
    "read_template",
    "run_all_turns",
    "save_user_template",
    "unload_script_from_session",
    "validate_template_dict",
]
