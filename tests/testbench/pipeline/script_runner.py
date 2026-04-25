"""Script runner — 脚本化对话 (Scripted) pipeline for P12.

PLAN §P12 约定: 读 ``dialog_templates/*.json`` (builtin + user 合并, 同 name
时 user 覆盖 builtin), 支持 ``Next turn`` 逐轮 / ``Run all`` 一键跑完; 每轮
消费 ``turns[].time`` 推进 virtual clock; ``role=assistant`` 的 ``expected``
字段自动写入紧随其后的真实 AI 回复的 ``reference_content``, 为后续
ComparativeJudger (P15+) 提供对照参考; ``bootstrap`` 字段重设会话起点.

设计要点
--------
* **脚本游标独立于 ``session.messages``**: 脚本里 ``role=assistant`` 的 turn
  只是"expected 的载体", 不是一个真实要发送的消息, 不占 ``session.messages``
  一格. 如果把游标和消息下标绑在一起, 混跑 ``inject_system`` / 编辑消息就
  会立即错位. 我们采用独立 ``cursor`` + 每次 ``advance_one_user_turn`` 把
  assistant turn 的 expected 暂存 ``pending_reference``, 等下次 LLM 回复落
  盘时再回填. 这样对消息编辑无感, 脚本"下一轮"的语义永远指"下一条 user
  turn 的发送".
* **多条连续 assistant turn 的 expected 合并**: 极少见但合法 (模板里同一
  位置写两条 assistant, 两条都带 expected), 用 ``\\n---\\n`` 拼接, 避免后者
  把前者覆盖.
* **bootstrap 的幂等性**: 只在 ``session.messages`` 为空时应用, 否则给
  warning. 已经产生消息的会话硬改 cursor 会让后续消息出现负时间差.
* **stream_send 复用**: 脚本的 user turn 执行走与手动 ``/chat/send``
  **完全同一条路径** (``chat_runner.get_chat_backend().stream_send(...,
  source='script')``). 前端收到的 SSE 事件里 ``user`` / ``assistant_start``
  / ``delta`` / ``assistant`` / ``done`` / ``error`` 等全部与手动一致,
  额外 yield ``script_turn_done`` / ``script_exhausted`` 承载脚本进度.
* **reference 回填时机**: 在 ``{event:'assistant'}`` 出来时回填 — 这时
  完整 content 已经 join 并写回 ``assistant_msg['content']``. 如果流式中
  断/失败 (``{event:'error'}``), 就丢弃 ``pending_reference`` 保持不变
  (下次脚本重试这条 user turn 会重新消费到).
* **错误语义**: :class:`ScriptError` 的 code 覆盖前端所有需要分 toast 的
  场景: ``ScriptNotFound`` / ``ScriptSchemaInvalid`` / ``ScriptNotLoaded``
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
        * ``NoActiveSession`` — 还没建会话 (404).
        * ``ScriptNotFound`` — 合并列表里找不到指定 name (404).
        * ``ScriptSchemaInvalid`` — JSON 读上来但 schema 不对 (422).
        * ``ScriptNotLoaded`` — 调 next/run_all 之前没 /load (412).
        * ``ScriptExhausted`` — 已经跑到末尾还点 next (412).
        * ``ScriptTurnFailed`` — 内部异常 (500).
    """

    def __init__(self, code: str, message: str, status: int = 500) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}")


# ── template loading ────────────────────────────────────────────────


_DURATION_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)


def _parse_duration_text(text: str) -> int | None:
    """``"1h30m" / "2d" / "45s" / "3600"`` → seconds (``None`` if unparsable).

    纯数字按秒; 单位接受 d/h/m/s (大小写不敏感). 与 static/core/time_utils.js
    的 ``parseDurationText`` 行为对齐, 避免 JS / Python 两端解析分歧.
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
                "expected": None,  # user turn 不能带 expected (语义上无意义), 忽略.
            })
        else:  # assistant
            expected = t.get("expected")
            normalized_turns.append({
                "role": ROLE_ASSISTANT,
                "content": None,
                "time": None,  # assistant turn 的 time 字段无意义, 统一 drop.
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
    """读一个目录下所有 ``*.json`` 并返回规范化后的 meta 列表.

    单个模板 JSON 读失败不会导致整个目录空 — 只记 warning 日志, 跳过
    这一条. 这样加用户手动 mv 一个坏 JSON 进来不会把 UI 列表搞空.
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

    - Builtin 先扫 (``tests/testbench/dialog_templates/``).
    - User 后扫 (``tests/testbench_data/dialog_templates/``); 同 ``name`` 时
      user 覆盖 builtin, 保留一条 ``overriding_builtin: True`` 标记让 UI
      可以标角标/提示 (当前 composer.js 未使用, 留给 Diagnostics 页).
    - 返回的字段只放前端可用的 meta (name / description / user_persona_hint
      / turns_count / source / path / overriding_builtin), **不**带 turns
      数组 — 避免下拉渲染时一次性传几十 KB.
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
    # User 后覆盖.
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
# 注意和上面 `load_template` 的差别:
#   * `load_template` 是给 session 的 "加载时" 用的, 返回值已做了规范化 (drop
#     了 user turn 的 expected / assistant turn 的 time 等无意义字段), 适合
#     pipeline 跑.
#   * 下面五个 (`read_template` / `save_user_template` / `delete_user_template`
#     / `validate_template_dict` / `duplicate_builtin_to_user`) 是给 "编辑器"
#     用的, 需要同时暴露 builtin 原文 + user 覆盖状态 + 软校验错误清单, 方便
#     UI 把字段级错误红框高亮.
#
# 语义约定:
#   * user 模板文件名 = ``<name>.json``.  name 字段 = 唯一 id.
#   * user 的 name 与 builtin 重名 = 覆盖 builtin (跟加载器优先级一致).
#   * builtin 不可原地改, 要改就先 `duplicate_builtin_to_user` 得到 user 副本.
#   * `save_user_template` / `delete_user_template` 只碰
#     `USER_DIALOG_TEMPLATES_DIR`, 碰到 builtin 目录就 403.


_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


def _is_safe_template_name(name: str) -> bool:
    """文件名级安全校验.  阻断路径穿越 / 非法字符 / 空名.

    测试人员可能会手写 name, 如果我们直接拼 ``USER_DIR / f'{name}.json'`` 里
    带 ``../`` 或者设备盘符, 就会把文件写到数据目录外. 白名单: 字母数字下划
    线短横线, 首字不能是分隔符, 长度 ≤ 64. 够用且不给路径穿越留缝.
    """
    return bool(_NAME_RE.match(name or ""))


def _collect_template_errors(raw: Any) -> list[dict[str, str]]:
    """软校验: 返回字段级错误清单 (空列表 = 通过).

    和 `_normalize_template` 语义一致但"不 raise, 而是把所有错都收集起来",
    让编辑器一次看到所有红点. path 字段是 JSON pointer 风格 (``turns[2].content``),
    UI 用它定位 turn 卡片.
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
    "防掉电" in its docstring but had no ``fsync`` — a power loss during
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
    """软校验入口 (给 UI validate 按钮 + save 前置).

    返回:
        ``{"ok": bool, "errors": [{path, message}, ...], "normalized": <dict>?}``

    ``normalized`` 仅在 ``ok=True`` 时存在, 是 save 到磁盘的规范化形态 (跟
    ``_normalize_template`` 产出一致). 用 ``_normalize_template`` 在软校验
    通过后再硬跑一遍, 把去冗余 / 补默认值这步统一跑在一个实现上.
    """
    errors = _collect_template_errors(raw)
    if errors:
        return {"ok": False, "errors": errors}
    # 软校验全过 → 硬 normalize 一遍 (此时不应该 raise, 真 raise 了说明两边
    # 检查不一致, 是 script_runner 内部 bug — 兜底成一个通用错.
    try:
        normalized = _normalize_template(raw, source="user", path=Path("<inline>"))
    except ScriptError as exc:
        return {
            "ok": False,
            "errors": [{"path": "", "message": f"内部规范化失败: {exc.message}"}],
        }
    return {"ok": True, "errors": [], "normalized": normalized}


def read_template(name: str) -> dict[str, Any]:
    """给编辑器用的"富详情": 活动版本 + builtin/user 双边存在性.

    返回:
        {
            "active": <规范化 dict, user 优先>,
            "has_builtin": bool,
            "has_user": bool,
            "overriding_builtin": bool,  # has_user AND has_builtin
        }

    找不到任一版本 → ScriptError(ScriptNotFound, 404).
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

    流程:
        1. ``validate_template_dict`` 校验, 失败抛 ScriptSchemaInvalid (422).
        2. 原子写 ``USER_DIALOG_TEMPLATES_DIR/<name>.json`` (dumps 的是
           normalized 形态 — 剥掉了 user turn 的 expected / assistant 的 time
           这种无意义字段, 让磁盘 JSON 尽量干净).
        3. 调 ``read_template(name)`` 返回新鲜详情, 顺带 ``overriding_builtin``
           让前端决定是否弹"覆盖 builtin"提示.

    写入格式: 使用 normalized (_normalize_template 输出), 不直接写原始 raw —
    这样无论 UI 传什么乱七八糟冗余字段, 磁盘落下来都是干净一致的.
    """
    result = validate_template_dict(raw)
    if not result["ok"]:
        # 走 ScriptSchemaInvalid — 但我们想把字段级 errors 一并带回给前端.
        # ScriptError 的默认签名只接 message, 所以用 attribute 附加; router
        # 层 handle 时优先取 ``exc.errors``.
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
        # 二次兜底 (上面已校验, 这里是 defense-in-depth).
        raise ScriptError(
            "ScriptSchemaInvalid",
            f"name={name!r} 不是合法文件名 (只允许字母/数字/下划线/短横线).",
            status=422,
        )

    # 丢掉 source/path 这两个"读出来才有"的元字段, 磁盘 JSON 不应该带.
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
    """Delete one user template by name.  Builtin is protected.

    - 只碰 user 目录; builtin 的 sample_*.json 永不删.
    - 如果该 name 仅存在 builtin 没有 user 版本 → 404 (没东西可删).
    - 如果 user 版本删完, builtin 仍然存在 → 返回 ``resurfaces_builtin: True``
      让前端提示"内置版本重新生效".

    Note: 不直接动 session.script_state — router 层判断当前加载剧本跟被删的
    是否同名, 再塞 warning 到响应. 这里保持纯.
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
    # builtin 仍在?
    builtin_list = _scan_dir(tb_config.BUILTIN_DIALOG_TEMPLATES_DIR, source="builtin")
    resurfaces = any(t["name"] == name for t in builtin_list)
    return {
        "deleted_name": name,
        "resurfaces_builtin": resurfaces,
    }


def duplicate_builtin_to_user(
    source_name: str, target_name: str, *, overwrite: bool = False,
) -> dict[str, Any]:
    """Copy a template (usually builtin, but user→user 也行) to a new user name.

    典型用法: 测试人员在 UI 点 builtin 的 ``[复制为可编辑]``, 我们把 builtin
    全量 copy 到 user 目录, 并改 name 为 target_name. 保存时走 `save_user_template`
    跑一遍校验, 跟手写新建等价.

    冲突策略:
        * target_name 的 user 版本已存在 + ``overwrite=False`` → 409 Conflict.
        * target_name 与 builtin 的某个 name 重名 (非 source 自身也算) →
          允许 (视作"覆盖 builtin"语义), UI 自己决定要不要弹 confirm.
    """
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

    # 深拷贝, 改 name. 把 source/path 字段剥掉 (那是元信息, 不写磁盘).
    new_raw: dict[str, Any] = copy.deepcopy(src)
    new_raw["name"] = target_name
    new_raw.pop("source", None)
    new_raw.pop("path", None)
    new_raw.pop("overriding_builtin", None)
    # turns 里 _normalize_template 产出的 expected/time=None 保留进 save 会被
    # 清理, 所以不用提前剥.

    return save_user_template(new_raw)


# ── bootstrap + per-turn time ───────────────────────────────────────


def apply_bootstrap(
    session: Session, bootstrap: dict[str, Any] | None,
) -> list[str]:
    """Apply script ``bootstrap`` to ``session.clock``; return warnings.

    仅当 ``session.messages`` 为空时真的应用 — 否则硬覆盖会导致后续消息
    出现负时间差, UI 的时间分隔符 / prompt builder 都会错乱. 跳过时
    返回一条 warning, 由 router 塞进 /script/load 的响应供前端 toast.
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

    支持的字段 (PLAN §8 明文约定):
        * ``advance``       → 文本解析 ("1h30m" / "45s" / "2d"), 追加到 stage.
        * ``advance_seconds`` → 纯秒数.
        * ``at``            → 绝对 ISO 时间.
        * 字段缺省 / ``time`` 本身为 ``None`` → 不 stage, 交给
          ``stream_send`` 的 ``per_turn_default_seconds`` 默认行为.

    冲突解决: ``at`` 优先于 ``advance*``; ``advance`` 优先于 ``advance_seconds``;
    与 :meth:`VirtualClock.stage_next_turn` 内部的优先级一致.
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
    # 不把完整 turns 数组发回前端 (可能十几条); UI 只需要游标+总数+名字.
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
    """合并连续多条 assistant turn 的 expected.

    少见 (模板里同一位置写两条 assistant 各带 expected), 但合法 — 用
    ``\\n---\\n`` 拼接避免后者覆盖前者.
    """
    new_text = (new_expected or "").strip()
    if not new_text:
        return existing or ""
    if not existing:
        return new_text
    return f"{existing}{_REFERENCE_JOIN}{new_text}"


async def advance_one_user_turn(session: Session) -> AsyncIterator[dict[str, Any]]:
    """Run one scripted user turn; yield the same SSE events as /chat/send.

    语义 (见 AGENT_NOTES §4.17 #35): 模板里 ``role=assistant`` 的 turn 携带的
    ``expected`` 是 **"对上一条 user turn 的理想 AI 回复"**. 因此本函数的流
    程是 **"先把 cursor 处的 user 发出去, AI 回复产出之后, 再看紧随其后的
    assistant turn 们把它们的 expected 合并回填到这条 AI 回复的
    reference_content"**. 简单说: expected 是向前看 (lookahead) 的, 不是向后
    看的.

    流程:
        1. 如果 ``cursor`` 刚好落在 ``role=assistant`` 上 (孤儿 expected, 没
           有匹配的上一轮 user), 跳过并收集一条 warning.
        2. ``cursor`` 指向一条 ``role=user`` → (a) 消费 ``time`` 到
           stage_next_turn; (b) **先 lookahead** 把 ``cursor+1`` 之后连续
           的 assistant turn 的 expected 合并成 ``fill_reference``;
           (c) 调 ``chat_runner.stream_send(..., source='script')`` 转发
           事件.
        3. 在收到 ``{event:'assistant'}`` 时把 ``fill_reference`` 写到
           assistant 消息的 ``reference_content``.
        4. stream 成功 → ``cursor`` 推进到 user turn + 连续 assistant turn
           之后的第一条 (= ``lookahead_end``); 失败 → 保持 ``cursor`` 不
           动, 让 tester 修完配置再重试同一轮.
        5. yield ``{event:'script_turn_done', cursor, turns_count}``.
        6. 若 cursor 已到末尾, 再 yield ``{event:'script_exhausted'}``.

    设计备注 ``pending_reference`` 字段 (session.script_state 里): 目前这版
    lookahead 实现不再需要跨调用保留 pending, 所以每轮结束统一清零; 字段
    保留是为了后续扩展 (例如 "一条 user 被拆成多轮注释" 场景).
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

    # Step 1: 跳过落在 cursor 处的孤儿 assistant turn (= 没有前置 user 就出现
    # 的 expected). 合法模板里不该出现这种情形, 但容错跳过 + 记 warning.
    orphan_warnings: list[str] = []
    while cursor < len(turns) and turns[cursor]["role"] == ROLE_ASSISTANT:
        expected = turns[cursor].get("expected") or ""
        orphan_warnings.append(
            f"turns[{cursor}] 是孤儿 assistant (前面没有待匹配的 user turn), "
            f"expected 已丢弃: {expected[:40]!r}{'...' if len(expected) > 40 else ''}",
        )
        cursor += 1

    if cursor >= len(turns):
        # 全是孤儿 assistant, 脚本直接耗尽.
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

    # Step 2: 当前 cursor 指向一条 user turn.
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

    # Step 2b: lookahead — 把 cursor+1 之后连续的 assistant turn 的 expected
    # 合并成本轮要回填的 reference.  遇到下一条 user (或末尾) 就停.
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
    state["cursor"] = cursor  # 还没推进, 要等 stream 成功走完.

    # Step 3: 转发 stream_send 事件, 并在 'assistant' 出来时回填 reference.
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
                # 消息对象本身是 session.messages[-1] 的同一引用 (见
                # OfflineChatBackend.stream_send 末尾), 改它等同于改 session.
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
                remaining_reference = None  # 只回填一次, 防止 assistant 事件重复.
            if ev == "done":
                stream_ok = True
            yield event
    except Exception as exc:  # noqa: BLE001
        # stream_send 本应把内部异常转成 {event:'error'} 再 return, 这里兜
        # 底是为了 ScriptError 用户别因为底层异常泄漏栈.
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

    # Step 4: 根据 stream 成败决定游标走向.
    if stream_ok:
        # user turn + 被消费掉的 assistant turn(s) 都跑完了 → 直接跳到
        # lookahead_end (= 下一条 user 或末尾).
        cursor = lookahead_end
        state["cursor"] = cursor
        state["pending_reference"] = None
    else:
        # stream 失败 — 保持 cursor 不动 (仍指向失败的 user turn),
        # pending_reference 清零 (下次重试 lookahead 重新扫一遍, 不用旧值).
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

    停止条件 (任一触发):
        * ``{event:'script_exhausted'}``: 脚本末尾.
        * ``{event:'error'}``: stream_send 内部错 (配置缺失 / LLM 超时等).
        * ``ScriptExhausted`` 异常: 循环守护 (理论到不了, 因为前面会先看
          到 ``script_exhausted`` 事件). 留着防并发竞争.
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
            # 上一轮里 stream_send 报错 — 不要咬着继续跑, 交给前端决定.
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
