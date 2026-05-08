"""Multi-format / multi-scope session export (P23).

PLAN §P23 约定: 把一份正在进行的 testbench session 根据用户选择的
**scope** (裁剪范围) 和 **format** (格式) 落成可分享的产物. 这一层是
纯 Python, 不拿 ``asyncio.Lock`` 也不 import ``session_store`` — 与
:mod:`judge_export` / :mod:`persistence` 保持同样的 "session-agnostic"
风格 (§3A A11). router 层 (:mod:`session_router`) 负责锁定 + 把
``Response`` 按 ``Content-Disposition: attachment`` 发回浏览器.

Scope × Format 矩阵
-------------------

::

                       │ json  │ markdown │ dialog_template │
    ───────────────────┼───────┼──────────┼─────────────────┤
    full               │  ✓    │    ✓     │       ✗         │
    persona_memory     │  ✓    │    ✓     │       ✗         │
    conversation       │  ✓    │    ✓     │       ✓         │
    conversation_eval  │  ✓    │    ✓     │       ✗         │
    evaluations        │  ✓    │    ✓     │       ✗         │

(共 **11 种合法组合**.) ``dialog_template`` 故意只对 ``conversation``
scope 开放, 因为它的语义是 "把已发生的对话回流成可重复的 script schema"
— 带 eval_results 的版本无意义 (script 不跑 judger), 带 persona_memory
的版本也与 script 的 turn-level 语义不符.

Scope 裁剪语义
--------------

* ``full`` — 完整序列化 (复用 :func:`persistence.serialize_session`
  的结果), 给 "我要离线分析整条测试会话" 用. JSON 输出结构与
  ``<name>.json`` 存档 **一致** (schema_version 对齐), 这样 P23 导出
  payload 可以用 ``/api/session/import`` 逆向加载回来 (需要配套的
  tarball — 后续 "include_memory" 选项会把 tarball base64 内嵌到
  payload 里, 用法同 :func:`persistence.export_to_payload`).

* ``persona_memory`` — 只保留 persona + clock + model_config (脱敏).
  用途: 复用同一套人设在另一组 session 里起测. memory tar.gz 仍可通过
  ``include_memory=True`` 附带.

* ``conversation`` — 只保留 messages + 必要的 clock/persona 元信息
  (character_name/master_name), 无 eval_results. 用途: 把对话过程分享
  给评审, 或作为下一轮脚本的输入 (配 ``format=dialog_template``).

* ``conversation_evaluations`` — 同 ``conversation`` + 全部 eval_results
  + aggregate 统计. 用途: 一份"对话 + 评分"报告, 给 reviewer 对照看.

* ``evaluations`` — 只保留 eval_results + aggregate + 对话元信息
  (session name/persona 头部), 不展开 messages. 用途: 只关心评分结果
  的批量对比场景 (跨会话).

Format 契约
-----------

* ``json`` — pretty-printed UTF-8, ``ensure_ascii=False`` (CJK 直出),
  根节点 ``{kind, scope, format, generated_at, session_ref, payload}``.
  ``kind="testbench_session_export"`` 在所有 scope 下相同, 前端/下游
  识别只看 ``scope`` 字段. 对 ``scope=full`` 而言, ``payload`` 兼容
  :meth:`persistence.SessionArchive.to_json_dict` 的结构.

* ``markdown`` — 按 scope 拼接不同 section (见 :func:`build_export_markdown`).
  **Comparative 差距表** 通过复用 :func:`judge_export.build_report_markdown`
  的 "gap_trajectory" + "By schema" 块呈现, 不在本模块重复实现.

* ``dialog_template`` — 直接落 :mod:`script_runner` 认的 JSON schema
  (name / description / bootstrap / turns), UTF-8 + 双空格缩进.
  ``turns`` 按 session.messages 逆推: assistant 消息保留其 content 作为
  ``expected``, user 消息的 ``time.advance`` 字段从与前一条消息的
  ``timestamp`` 差分得到 (只在 >= 60 秒时写入, 小于的忽略, 与
  :mod:`script_runner._parse_duration_text` 的语义对齐).

API Key 脱敏
------------

默认 ``redact_api_keys=True`` 与 :mod:`persistence` 对齐; 并且在 P23
层面作为**硬约束**导出到 ``redact_api_keys`` 字段永远 ``True`` —
export 是主动共享行为, 不给"明文导出"选项. router 层 Pydantic body 里
甚至不暴露这个开关 (§3A A5 "只允许放宽契约, 不允许放宽安全").

export -> import 的往返性
-------------------------

只有 ``scope=full`` + ``format=json`` + ``include_memory=True`` 的输出
与 :func:`persistence.import_from_payload` 完全兼容 (三条都满足才能做
无损导入). 其它 scope 故意不对齐 import 格式, 因为它们的 payload 按
"可读性 / 小体积" 优化, 不包含 import 路径必需的 ``archive_kind`` 等
字段.
"""
from __future__ import annotations

import base64
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Literal

from tests.testbench.pipeline import judge_export, persistence
from tests.testbench.pipeline.judge_export import aggregate_results

# ── enums / constants ──────────────────────────────────────────────

Scope = Literal[
    "full",
    "persona_memory",
    "conversation",
    "conversation_evaluations",
    "evaluations",
]
Format = Literal["json", "markdown", "dialog_template"]

SESSION_EXPORT_SCOPES: Final[tuple[str, ...]] = (
    "full",
    "persona_memory",
    "conversation",
    "conversation_evaluations",
    "evaluations",
)
EXPORT_FORMATS: Final[tuple[str, ...]] = ("json", "markdown", "dialog_template")

#: ``(scope, format)`` 白名单. router 层用 :func:`is_valid_combination`
#: 做入口校验; 非白名单组合直接 400, 避免走进 builder 产出一堆半残输出.
VALID_COMBINATIONS: Final[frozenset[tuple[str, str]]] = frozenset({
    ("full", "json"),
    ("full", "markdown"),
    ("persona_memory", "json"),
    ("persona_memory", "markdown"),
    ("conversation", "json"),
    ("conversation", "markdown"),
    ("conversation", "dialog_template"),
    ("conversation_evaluations", "json"),
    ("conversation_evaluations", "markdown"),
    ("evaluations", "json"),
    ("evaluations", "markdown"),
})

EXPORT_KIND: Final[str] = "testbench_session_export"
EXPORT_SCHEMA_VERSION: Final[int] = 1

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SessionExportError(ValueError):
    """Raised for invalid scope/format combinations or missing session data.

    Subclasses :class:`ValueError` so the router maps it cleanly to a
    400 via :func:`session_router._persistence_error_to_http`-style
    handling; callers outside HTTP land can ``except SessionExportError``
    without worrying about HTTPException import graph.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── combination validation ─────────────────────────────────────────


def is_valid_combination(scope: str, fmt: str) -> bool:
    """Return ``True`` iff ``(scope, fmt)`` is in :data:`VALID_COMBINATIONS`."""
    return (scope, fmt) in VALID_COMBINATIONS


def ensure_valid_combination(scope: str, fmt: str) -> None:
    """Raise :class:`SessionExportError` when ``(scope, fmt)`` is not allowed."""
    if scope not in SESSION_EXPORT_SCOPES:
        raise SessionExportError(
            "UnknownScope",
            f"unknown export scope {scope!r}; expected one of "
            f"{SESSION_EXPORT_SCOPES}",
        )
    if fmt not in EXPORT_FORMATS:
        raise SessionExportError(
            "UnknownFormat",
            f"unknown export format {fmt!r}; expected one of {EXPORT_FORMATS}",
        )
    if not is_valid_combination(scope, fmt):
        raise SessionExportError(
            "InvalidCombination",
            f"combination scope={scope!r} × format={fmt!r} is not supported. "
            f"dialog_template only applies to scope=conversation; all other "
            f"scopes accept json or markdown only.",
        )


# ── filename helper ────────────────────────────────────────────────


_FORMAT_EXT: Final[dict[str, str]] = {
    "json": "json",
    "markdown": "md",
    # dialog_template 是 JSON 结构, 但扩展名用 .json, 前缀标 "dialog_template"
    # 让用户一眼看出它是 script schema 而不是普通 JSON.
    "dialog_template": "json",
}


def session_export_filename(
    *,
    session_name: str,
    scope: str,
    fmt: str,
    now: datetime | None = None,
) -> str:
    """Return ``tbsession_<session>_<scope>_<YYYYMMDD_HHMMSS>.<ext>``.

    ``session_name`` 经 :data:`_SAFE_FILENAME_RE` 过滤, 保证 Windows/
    macOS/Linux 三端下载不会因为冒号 / 空格 / 中文冲突出错 (中文在 HTTP
    ``Content-Disposition`` 里要额外 encode, 过滤成 ASCII 最简单).
    dialog_template 前缀用 ``tbscript`` 以区分 (下游 script_runner 的
    user template 目录会自动识别).
    """
    prefix = "tbscript" if fmt == "dialog_template" else "tbsession"
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    safe_name = _SAFE_FILENAME_RE.sub("_", session_name or "session") or "session"
    safe_scope = _SAFE_FILENAME_RE.sub("_", scope) or scope
    ext = _FORMAT_EXT.get(fmt, "txt")
    # dialog_template 不带 scope 段 (它隐含 scope=conversation), 文件名
    # 更简短也避免冗余.
    if fmt == "dialog_template":
        return f"{prefix}_{safe_name}_{ts}.{ext}"
    return f"{prefix}_{safe_name}_{safe_scope}_{ts}.{ext}"


# ── helpers ────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_persona_summary(session: Any) -> dict[str, Any]:
    """Return a small, always-JSON-safe view of the persona.

    Export payloads only quote a handful of persona fields for header /
    metadata sections; the full persona dict (with long system_prompt
    blocks) still ships in ``scope=full`` via serialize_session, but we
    don't want to inline it in every other scope's metadata.
    """
    persona = getattr(session, "persona", None) or {}
    if not isinstance(persona, dict):
        return {}
    return {
        "master_name": persona.get("master_name"),
        "character_name": persona.get("character_name"),
        "language": persona.get("language"),
    }


def _session_ref(session: Any) -> dict[str, Any]:
    """Shared top-level ``session_ref`` stamp included in every export payload.

    Small, stable shape so downstream tooling can diff two exports by
    reading ``session_ref`` alone (id / name / message_count / eval_count
    / clock cursor) without scanning the full payload.
    """
    created = getattr(session, "created_at", None)
    created_iso = created.isoformat(timespec="seconds") if created else None
    clock = getattr(session, "clock", None)
    clock_dict = clock.to_dict() if clock is not None else {}
    return {
        "session_id": getattr(session, "id", None),
        "session_name": getattr(session, "name", None),
        "session_created_at": created_iso,
        "message_count": len(getattr(session, "messages", []) or []),
        "eval_count": len(getattr(session, "eval_results", []) or []),
        "clock_cursor": clock_dict.get("cursor"),
        "persona": _safe_persona_summary(session),
    }


def _parse_ts(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _format_advance(seconds: int) -> str:
    """Pretty-print an integer second count as ``script_runner`` duration text.

    Mirror of :func:`script_runner._parse_duration_text` 的逆向: the
    parser 接受 ``Ns / Nm / Nh / Nd``; 我们选就近的单位以减少小数损失,
    并且优先选 script JSON 里人最常写的 ``m`` / ``h`` / ``d``. 秒级精度
    的对话间隔在测试里几乎没有, 无需支持带小数的 "1.5h".
    """
    if seconds <= 0:
        return "0s"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


# ── JSON builders ──────────────────────────────────────────────────


def _build_json_full(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=full`` (superset of SessionArchive)."""
    archive = persistence.serialize_session(
        session,
        name=getattr(session, "name", "export"),
        # 硬约束: export 永远脱敏 (前文 §"API Key 脱敏" 决策).
        redact_api_keys=True,
    )
    return archive.to_json_dict()


def _build_json_persona_memory(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=persona_memory`` (no messages/evals)."""
    model_cfg = dict(getattr(session, "model_config", {}) or {})
    model_cfg = persistence.redact_model_config(model_cfg)
    clock = getattr(session, "clock", None)
    return {
        "persona": copy.deepcopy(getattr(session, "persona", {}) or {}),
        "model_config": model_cfg,
        "clock": clock.to_dict() if clock is not None else {},
        # stage_state 跟 persona 编辑流程强关联 (P14), 保持一起导出;
        # 大小可控 (固定 current + history list).
        "stage_state": copy.deepcopy(
            getattr(session, "stage_state", {}) or {},
        ),
    }


def _build_json_conversation(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=conversation`` (messages + light meta)."""
    clock = getattr(session, "clock", None)
    return {
        "messages": copy.deepcopy(getattr(session, "messages", []) or []),
        "persona": _safe_persona_summary(session),
        "clock": clock.to_dict() if clock is not None else {},
    }


def _build_json_conversation_evaluations(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=conversation_evaluations``."""
    results = list(getattr(session, "eval_results", []) or [])
    body = _build_json_conversation(session)
    body["eval_results"] = copy.deepcopy(results)
    body["aggregate"] = aggregate_results(results)
    return body


def _build_json_evaluations(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=evaluations`` (no messages)."""
    results = list(getattr(session, "eval_results", []) or [])
    return {
        "persona": _safe_persona_summary(session),
        "eval_results": copy.deepcopy(results),
        "aggregate": aggregate_results(results),
    }


_SCOPE_JSON_BUILDERS: Final[dict[str, Any]] = {
    "full": _build_json_full,
    "persona_memory": _build_json_persona_memory,
    "conversation": _build_json_conversation,
    "conversation_evaluations": _build_json_conversation_evaluations,
    "evaluations": _build_json_evaluations,
}


def build_export_payload(
    session: Any, *, scope: str, include_memory: bool = False,
) -> dict[str, Any]:
    """Build the ``format=json`` payload envelope for ``scope``.

    ``include_memory`` only applies to ``scope in {full, persona_memory}``
    — for the other scopes the memory tar has no natural place to attach
    (conversation/evaluations exports are about narrative content, not
    sandbox state) and requesting it is silently ignored (no hard error,
    since the frontend defaults the checkbox to ``False`` and users who
    flip it for a non-memory scope likely just didn't realise).

    Returns a dict with top-level keys::

        {
            "kind": "testbench_session_export",
            "schema_version": 1,
            "scope": "<scope>",
            "format": "json",
            "generated_at": "<iso>",
            "session_ref": {...},          # see _session_ref
            "payload": {...},              # scope-specific body
            # Only when include_memory=True and scope supports it:
            "memory_tarball_b64": "<base64>",
            "memory_sha256": "<hex>"
        }
    """
    if scope not in _SCOPE_JSON_BUILDERS:
        raise SessionExportError(
            "UnknownScope",
            f"unknown export scope {scope!r}; expected one of "
            f"{SESSION_EXPORT_SCOPES}",
        )
    body = _SCOPE_JSON_BUILDERS[scope](session)
    envelope: dict[str, Any] = {
        "kind": EXPORT_KIND,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "scope": scope,
        "format": "json",
        "generated_at": _now_iso(),
        "session_ref": _session_ref(session),
        "payload": body,
    }

    if include_memory and scope in ("full", "persona_memory"):
        sandbox = getattr(session, "sandbox", None)
        if sandbox is not None:
            app_docs = getattr(sandbox, "_app_docs", None)
            if app_docs is not None:
                tar_bytes = persistence.pack_memory_tarball(app_docs)
                envelope["memory_tarball_b64"] = base64.b64encode(
                    tar_bytes,
                ).decode("ascii")
                envelope["memory_sha256"] = persistence.compute_memory_sha256(
                    tar_bytes,
                )
                envelope["memory_tarball_bytes"] = len(tar_bytes)

    return envelope


def serialize_json(payload: dict[str, Any]) -> str:
    """Render ``payload`` with stable formatting (2-space indent, UTF-8)."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# ── dialog_template builder ────────────────────────────────────────


def build_dialog_template(
    session: Any, *,
    name: str | None = None,
    description: str | None = None,
    user_persona_hint: str | None = None,
) -> dict[str, Any]:
    """Reverse-engineer a ``script_runner``-compatible template from messages.

    Walk the session's ``messages`` list in order; for each user turn we
    compute ``time.advance`` from the timestamp delta to the previous
    stored message (rounded to whole seconds). Assistant turns drop their
    content into ``expected`` and omit ``time`` (per script schema).
    system messages are skipped (they're synthetic injects, not part of
    a reproducible script).

    Bootstrap is derived from :attr:`session.clock`:
    ``bootstrap.virtual_now`` = ``clock.bootstrap_at or cursor``,
    ``bootstrap.last_gap_minutes`` = ``initial_last_gap_seconds // 60``
    (only when present; else omitted).

    Empty user/assistant content is tolerated (the turn is still emitted
    so the sequence length survives the round-trip) but the caller
    should ideally gate the export on ``message_count > 0`` at the UI.
    """
    messages = list(getattr(session, "messages", []) or [])
    clock = getattr(session, "clock", None)
    persona = _safe_persona_summary(session)

    base_name = (name or "").strip()
    if not base_name:
        sess_name = str(getattr(session, "name", "") or "")
        base_name = f"{sess_name}_replay" if sess_name else "session_replay"

    turns: list[dict[str, Any]] = []
    prev_ts: datetime | None = None
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        ts = _parse_ts(msg.get("timestamp"))

        if role == "user":
            turn: dict[str, Any] = {"role": "user", "content": content}
            # 首条 user turn 的 time 字段省略 (bootstrap 已承担首轮时间锚).
            # 后续 user turn 用 "与上一条消息的差值" 填 time.advance.
            # P24 §12.5 L3 defensive: clamp negative deltas to 0. The
            # `append_message` chokepoint should guarantee monotonicity
            # at the write path, but archives saved before Day 2 / hand-
            # edited archives / imports from external sources may still
            # contain non-monotonic messages. Writing a negative advance
            # would produce a dialog_template script_runner refuses to
            # parse — clamping to 0 keeps the export usable, and the
            # user-visible archive's Chat UI will show 0-second gaps
            # (same visual as "same-second messages") rather than crash.
            if prev_ts is not None and ts is not None:
                delta = max(0, int((ts - prev_ts).total_seconds()))
                if delta >= 60:
                    turn["time"] = {"advance": _format_advance(delta)}
            turns.append(turn)
            prev_ts = ts or prev_ts
        elif role == "assistant":
            turns.append({"role": "assistant", "expected": content})
            prev_ts = ts or prev_ts
        else:
            # system / other roles: 跳过但更新 prev_ts, 这样后续 user turn
            # 的 time.advance 不会把 inject 之间的沉默时间一起塞给下一条.
            prev_ts = ts or prev_ts

    bootstrap: dict[str, Any] = {}
    if clock is not None:
        clock_dict = clock.to_dict()
        bootstrap_at = clock_dict.get("bootstrap_at") or clock_dict.get("cursor")
        if bootstrap_at:
            bootstrap["virtual_now"] = bootstrap_at
        last_gap_seconds = clock_dict.get("initial_last_gap_seconds")
        if isinstance(last_gap_seconds, (int, float)) and last_gap_seconds > 0:
            bootstrap["last_gap_minutes"] = int(last_gap_seconds // 60)

    default_desc = ""
    if persona.get("character_name") or persona.get("master_name"):
        default_desc = (
            f"Exported from session {getattr(session, 'name', '?')!r} "
            f"(character={persona.get('character_name') or '-'}, "
            f"master={persona.get('master_name') or '-'})."
        )

    template: dict[str, Any] = {
        "name": base_name,
        "description": (description or default_desc).strip(),
        "user_persona_hint": (user_persona_hint or "").strip(),
        "turns": turns,
    }
    if bootstrap:
        template["bootstrap"] = bootstrap
    return template


# ── Markdown builders ──────────────────────────────────────────────


def _md_header(session: Any, *, scope: str) -> list[str]:
    lines = ["# Testbench Session Export", ""]
    lines.append(f"_Generated at {_now_iso()}_")
    lines.append("")
    ref = _session_ref(session)
    lines.append("## Context")
    lines.append("")
    lines.append(
        f"- **Session**: {ref.get('session_name') or '(unnamed)'} "
        f"(`{ref.get('session_id') or '-'}`)",
    )
    lines.append(f"- **Scope**: `{scope}`")
    p = ref.get("persona") or {}
    if p.get("character_name") or p.get("master_name"):
        lines.append(
            f"- **Persona**: character=`{p.get('character_name') or '-'}`, "
            f"master=`{p.get('master_name') or '-'}`",
        )
    if ref.get("session_created_at"):
        lines.append(f"- **Created at**: `{ref['session_created_at']}`")
    if ref.get("clock_cursor"):
        lines.append(f"- **Clock cursor**: `{ref['clock_cursor']}`")
    lines.append(f"- **Message count**: {ref.get('message_count', 0)}")
    lines.append(f"- **Evaluation count**: {ref.get('eval_count', 0)}")
    lines.append("")
    return lines


def _md_persona_block(session: Any) -> list[str]:
    persona = getattr(session, "persona", {}) or {}
    if not isinstance(persona, dict) or not persona:
        return []
    lines = ["## Persona", ""]
    for key in ("master_name", "character_name", "language"):
        val = persona.get(key)
        if val is None or val == "":
            continue
        lines.append(f"- **{key}**: `{val}`")
    sys_prompt = persona.get("system_prompt")
    if isinstance(sys_prompt, str) and sys_prompt.strip():
        lines.append("")
        lines.append("**System prompt**:")
        lines.append("")
        lines.append("```text")
        lines.append(sys_prompt.rstrip())
        lines.append("```")
    lines.append("")
    return lines


def _md_clock_block(session: Any) -> list[str]:
    clock = getattr(session, "clock", None)
    if clock is None:
        return []
    c = clock.to_dict()
    lines = ["## Virtual clock", ""]
    if c.get("is_real_time"):
        lines.append("- Real-time mode (no virtual cursor)")
    else:
        lines.append(f"- Cursor: `{c.get('cursor') or '-'}`")
        if c.get("bootstrap_at"):
            lines.append(f"- Bootstrap at: `{c['bootstrap_at']}`")
    if c.get("initial_last_gap_seconds"):
        lines.append(
            f"- Initial last-gap: {int(c['initial_last_gap_seconds']) // 60} min",
        )
    if c.get("per_turn_default_seconds"):
        lines.append(
            f"- Per-turn default: {int(c['per_turn_default_seconds'])} s",
        )
    lines.append("")
    return lines


def _md_model_config_block(session: Any) -> list[str]:
    mc = dict(getattr(session, "model_config", {}) or {})
    if not mc:
        return []
    mc = persistence.redact_model_config(mc)
    lines = ["## Model config (api_key redacted)", ""]
    for group, cfg in mc.items():
        if not isinstance(cfg, dict):
            continue
        lines.append(f"### {group}")
        for k in ("provider", "model", "base_url", "temperature", "max_tokens", "timeout"):
            if k in cfg and cfg[k] not in (None, ""):
                lines.append(f"- `{k}`: `{cfg[k]}`")
        # api_key 永远 redacted, 单独列一行避免被误以为没填.
        lines.append(f"- `api_key`: `{cfg.get('api_key', '-')}`")
        lines.append("")
    return lines


def _md_memory_sizes_block(session: Any) -> list[str]:
    sandbox = getattr(session, "sandbox", None)
    if sandbox is None:
        return []
    app_docs = getattr(sandbox, "_app_docs", None)
    if app_docs is None or not app_docs.exists():
        return []
    # 只报顶层 memory/ 目录下每个文件的尺寸, 不递归 (避免几千个 snapshot
    # 把报告撑爆).
    try:
        mem_dir = app_docs / "memory"
        if not mem_dir.exists() or not mem_dir.is_dir():
            return []
        files = []
        for p in sorted(mem_dir.rglob("*")):
            if p.is_file():
                try:
                    files.append((p.relative_to(app_docs).as_posix(), p.stat().st_size))
                except OSError:
                    continue
    except OSError:
        return []
    if not files:
        return []
    lines = ["## Memory files", ""]
    lines.append("| Path | Size (bytes) |")
    lines.append("|---|---|")
    for relpath, size in files:
        lines.append(f"| `{relpath}` | {size} |")
    lines.append("")
    return lines


def _md_messages_block(session: Any, *, include_wire: bool = False) -> list[str]:
    """Render the conversation messages in chronological order."""
    messages = list(getattr(session, "messages", []) or [])
    lines = ["## Conversation", ""]
    if not messages:
        lines.append("_No messages yet._")
        lines.append("")
        return lines
    for idx, m in enumerate(messages, start=1):
        role = m.get("role") or "?"
        ts = m.get("timestamp") or "-"
        source = m.get("source") or "-"
        content = (m.get("content") or "").rstrip()
        # Markdown 尊重原文换行: blockquote 每行前缀 "> ", 空行保留为 ">"
        # 以保证连续段落显示.
        lines.append(
            f"### {idx}. `{role}` · `{ts}` · source=`{source}`",
        )
        lines.append("")
        if content:
            for ln in content.split("\n"):
                lines.append(f"> {ln}" if ln else ">")
        else:
            lines.append("_(empty)_")
        ref = m.get("reference_content")
        if isinstance(ref, str) and ref.strip():
            lines.append("")
            lines.append("**Reference (expected)**:")
            lines.append("")
            for ln in ref.rstrip().split("\n"):
                lines.append(f"> {ln}" if ln else ">")
        lines.append("")
    return lines


def _md_evaluations_block(session: Any) -> list[str]:
    """Reuse judge_export.build_report_markdown for the evaluations section.

    Trimmed: we drop the "Filter" header (our export has no filter) and
    shift the heading from H1 to H2 so it nests correctly under the
    session export top heading.
    """
    results = list(getattr(session, "eval_results", []) or [])
    if not results:
        return [
            "## Evaluations",
            "",
            "_No evaluation results._",
            "",
        ]
    persona = _safe_persona_summary(session)
    metadata = {
        "session_id": getattr(session, "id", None),
        "session_name": getattr(session, "name", None),
        "character_name": persona.get("character_name"),
        "master_name": persona.get("master_name"),
    }
    agg = aggregate_results(results)
    full = judge_export.build_report_markdown(
        results=results,
        aggregate=agg,
        metadata=metadata,
    )
    # 原报告首行是 "# Evaluation Report" + "_Generated at_" 两行 —
    # 替换成 session export 的 "## Evaluations" 以避免 H1 冲突.
    body = full.split("\n", 3)[-1] if full.count("\n") >= 3 else full
    lines = ["## Evaluations", ""]
    lines.extend(body.splitlines())
    if lines[-1] != "":
        lines.append("")
    return lines


def _md_snapshot_meta_block(session: Any) -> list[str]:
    store = getattr(session, "snapshot_store", None)
    if store is None:
        return []
    try:
        metas = store.list_metadata()
    except Exception:  # noqa: BLE001
        return []
    if not metas:
        return []
    lines = ["## Snapshots", ""]
    lines.append("| # | ID | Trigger | Label | Created at (virtual) |")
    lines.append("|---|---|---|---|---|")
    for i, m in enumerate(metas, start=1):
        lines.append(
            f"| {i} | `{m.get('id') or '-'}` | `{m.get('trigger') or '-'}` "
            f"| {m.get('label') or '-'} | `{m.get('created_at_virtual') or '-'}` |",
        )
    lines.append("")
    return lines


def build_export_markdown(
    session: Any, *, scope: str,
) -> str:
    """Render a scope-specific Markdown report (no ``dialog_template``).

    Layout per scope:

    ``full``                      → header + persona + clock + model_config
                                    + memory sizes + snapshot meta +
                                    conversation + evaluations
    ``persona_memory``            → header + persona + clock +
                                    model_config + memory sizes
    ``conversation``              → header + persona + clock + conversation
    ``conversation_evaluations``  → header + persona + clock + conversation
                                    + evaluations
    ``evaluations``               → header + persona + clock + evaluations
    """
    lines: list[str] = []
    lines.extend(_md_header(session, scope=scope))

    want_persona = True  # every scope benefits from persona header
    want_clock = True

    want_model_config = scope in ("full", "persona_memory")
    want_memory = scope in ("full", "persona_memory")
    want_snapshots = scope == "full"
    want_messages = scope in ("full", "conversation", "conversation_evaluations")
    want_evaluations = scope in (
        "full", "conversation_evaluations", "evaluations",
    )

    if want_persona:
        lines.extend(_md_persona_block(session))
    if want_clock:
        lines.extend(_md_clock_block(session))
    if want_model_config:
        lines.extend(_md_model_config_block(session))
    if want_memory:
        lines.extend(_md_memory_sizes_block(session))
    if want_snapshots:
        lines.extend(_md_snapshot_meta_block(session))
    if want_messages:
        lines.extend(_md_messages_block(session))
    if want_evaluations:
        lines.extend(_md_evaluations_block(session))

    return "\n".join(lines).rstrip() + "\n"


# ── Top-level dispatcher ───────────────────────────────────────────


def export_session(
    session: Any,
    *,
    scope: str,
    fmt: str,
    include_memory: bool = False,
) -> tuple[str, str]:
    """Produce ``(body_text, media_type)`` for the given scope × format.

    Single entry point the router calls. Does combination validation
    up front, then dispatches to the right builder. Memory inclusion
    only applies to ``fmt=json`` + ``scope in {full, persona_memory}``;
    other combinations ignore it silently so the frontend doesn't have
    to gate the checkbox.
    """
    ensure_valid_combination(scope, fmt)
    if fmt == "dialog_template":
        template = build_dialog_template(session)
        body = json.dumps(template, ensure_ascii=False, indent=2) + "\n"
        return body, "application/json; charset=utf-8"
    if fmt == "markdown":
        return build_export_markdown(session, scope=scope), \
            "text/markdown; charset=utf-8"
    payload = build_export_payload(
        session, scope=scope, include_memory=include_memory,
    )
    return serialize_json(payload), "application/json; charset=utf-8"


__all__ = [
    "EXPORT_FORMATS",
    "EXPORT_KIND",
    "EXPORT_SCHEMA_VERSION",
    "SESSION_EXPORT_SCOPES",
    "SessionExportError",
    "VALID_COMBINATIONS",
    "build_dialog_template",
    "build_export_markdown",
    "build_export_payload",
    "ensure_valid_combination",
    "export_session",
    "is_valid_combination",
    "serialize_json",
    "session_export_filename",
]
