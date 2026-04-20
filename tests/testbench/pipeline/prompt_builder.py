"""Prompt builder — single source of truth for what the AI model sees.

Produces a :class:`PromptBundle` that contains **both** views required by
the Testbench UI:

* ``structured`` — a dict of named sections (``session_init`` /
  ``character_prompt`` / ``persona_header`` / ``persona_content`` / ...)
  meant for the human-oriented "Structured" view in the Prompt Preview
  panel. Never sent to the model.
* ``system_prompt`` — the flat concatenation of the structured sections,
  i.e. the actual system message string that lands in ``wire_messages[0]``.
* ``wire_messages`` — the OpenAI-style ``[{role, content}, ...]`` array
  that ``chat_runner`` (P09) will pass to ``create_chat_llm().astream(...)``.
* ``char_counts`` — per-section char counts + total + rough token estimate,
  for UI badges.
* ``metadata`` — resolved ``master_name`` / ``character_name`` / ``language``
  plus flags describing whether the character prompt was taken from the
  session persona's ``system_prompt`` field or defaulted via
  :func:`config.prompts_chara.get_lanlan_prompt`.
* ``warnings`` — soft messages the UI can surface as info/warn chips
  (e.g. "character_name 未填", "memory_dir 不存在", "stored_system_prompt
  被识别为默认模板").

Design notes
------------
The upstream scripts (:mod:`tests.dump_llm_input`, ``memory_server.py``,
``main_logic.core``) reuse the runtime's :class:`memory.persona.PersonaManager`
family, which internally calls ``ConfigManager.get_character_data()`` and
uses ``datetime.now()``. We do the same **but**:

* The sandbox's :class:`ConfigManager` is already patched by the active
  session, so memory managers naturally read/write sandbox paths. Each
  preview call constructs fresh manager instances — they cache nothing
  important at construction time, and the preview is low-frequency.
* ``datetime.now()`` calls inside the upstream functions are avoided by
  re-implementing the memory context assembly locally (see
  :func:`_build_memory_context_structured_with_clock`) so the session's
  :class:`~tests.testbench.virtual_clock.VirtualClock` drives both
  ``inner_thoughts_dynamic`` and the "distance to last conversation" gap.
* ``name_mapping`` is built from ``session.persona.master_name`` rather
  than from ``characters.json`` — persona edits take effect in Preview
  without requiring an Import. The upstream memory managers do read
  ``characters.json`` internally, but only for ancillary lookups we don't
  touch here (e.g. auto-promotion of pending reflections, character card
  merging at first ``ensure_persona``) — these side-effects are the same
  as during a real ``chat_turn`` and we accept that.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from config.prompts_chara import get_lanlan_prompt, is_default_prompt
from config.prompts_memory import (
    CHAT_GAP_CURRENT_TIME,
    CHAT_GAP_LONG_HINT,
    CHAT_GAP_NOTICE,
    CHAT_HOLIDAY_CONTEXT,
    INNER_THOUGHTS_DYNAMIC,
    INNER_THOUGHTS_HEADER,
    PERSONA_HEADER,
)
from config.prompts_sys import CONTEXT_SUMMARY_READY, SESSION_INIT_PROMPT, _loc
from memory import (
    CompressedRecentHistoryManager,
    FactStore,
    ImportantSettingsManager,
    PersonaManager,
    ReflectionEngine,
    TimeIndexedMemory,
)
from tests.testbench.session_store import Session
from utils.time_format import format_elapsed as _format_elapsed

# Same pattern upstream uses to strip parenthetical asides from recent history.
_BRACKETS_RE = re.compile(r"(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)")

# Upstream uses `utils.frontend_utils.get_timestamp`, which pins the POSIX
# C locale so the weekday/month names render in English; we mirror that to
# preserve the model-facing prompt bit-for-bit.
_TIMESTAMP_FORMAT = "%A, %B %d, %Y at %I:%M %p"


@dataclass
class PromptBundle:
    """Everything the UI needs to display a Prompt Preview.

    ``structured`` / ``system_prompt`` / ``wire_messages`` are the three
    views described in PLAN §技术点 3. ``metadata`` and ``warnings`` are
    Testbench-specific UX affordances.
    """

    session_id: str
    structured: dict[str, Any]
    system_prompt: str
    wire_messages: list[dict[str, str]]
    char_counts: dict[str, int]
    metadata: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Render as the JSON payload consumed by the frontend."""
        return {
            "session_id": self.session_id,
            "structured": self.structured,
            "system_prompt": self.system_prompt,
            "wire_messages": self.wire_messages,
            "char_counts": self.char_counts,
            "metadata": self.metadata,
            "warnings": list(self.warnings),
        }


class PreviewNotReady(Exception):
    """Raised when the session state cannot yield a meaningful preview.

    Routers translate this to a 409 with a ``code`` matching the error
    kind so the UI can render a targeted empty-state (e.g. "先去 Setup →
    Persona 填写 character_name" vs "先新建会话").
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# ── language / naming helpers ────────────────────────────────────────


def _normalize_short_lang(lang: str | None) -> str:
    """Return a short language code ('zh' / 'en' / 'ja' / ...) for prompt dicts.

    Prompt dicts in ``config.prompts_*`` keyed by two-letter codes; the
    session persona stores ``zh-CN`` / ``en`` / ``ja`` etc. Falls back to
    ``utils.language_utils.normalize_language_code`` so we inherit the
    same mapping upstream uses, and defaults to ``zh`` when empty.
    """
    if not lang:
        return "zh"
    try:
        from utils.language_utils import normalize_language_code

        short = normalize_language_code(lang, format="short")
        return short or "zh"
    except Exception:
        # Defensive: never let language resolution break a preview.
        short = lang.lower().split("-")[0]
        return short or "zh"


def _build_name_mapping(master_name: str) -> dict[str, str]:
    """Mirror :meth:`ConfigManager.get_character_data`'s name_mapping shape.

    Upstream code accesses ``name_mapping['human']`` / ``['system']`` /
    ``['ai']``; the latter is injected per-character at call sites. We only
    build the two static keys here.
    """
    master = master_name or "主人"
    return {"human": master, "system": "SYSTEM_MESSAGE"}


def _resolve_character_prompt(
    persona: dict[str, Any], lang_short: str,
) -> tuple[str, str, bool, str]:
    """Mirror the prompt resolution logic in ``dump_llm_input.main``.

    Returns ``(lanlan_prompt, template_used, stored_is_default, template_raw)``
    where:

    * ``lanlan_prompt`` — the character prompt with ``{LANLAN_NAME}`` and
      ``{MASTER_NAME}`` substituted, ready to paste into the system string.
    * ``template_used`` — ``"default"`` if we fell back to
      :func:`get_lanlan_prompt`, ``"stored"`` otherwise.
    * ``stored_is_default`` — upstream sometimes stores a verbatim copy of
      the default template; :func:`is_default_prompt` detects that case.
      The UI surfaces a warning because editing a "stored=default" prompt
      is pointless (upstream would still fall back to the default).
    * ``template_raw`` — the template text *before* placeholder substitution,
      shown in the Preview for side-by-side diffing.
    """
    stored = (persona.get("system_prompt") or "").strip()
    master_name = persona.get("master_name") or ""
    character_name = persona.get("character_name") or ""

    stored_is_default = bool(stored) and is_default_prompt(stored)
    if not stored or stored_is_default:
        template_raw = get_lanlan_prompt(lang_short)
        template_used = "default"
    else:
        template_raw = stored
        template_used = "stored"

    lanlan_prompt = (
        template_raw
        .replace("{LANLAN_NAME}", character_name or "{LANLAN_NAME}")
        .replace("{MASTER_NAME}", master_name or "{MASTER_NAME}")
    )
    return lanlan_prompt, template_used, stored_is_default, template_raw


# ── legacy settings fallback ─────────────────────────────────────────


def _format_legacy_settings_as_text(settings: dict, lanlan_name: str) -> str:
    """Verbatim copy of :func:`memory_server._format_legacy_settings_as_text`.

    Kept local so the preview renders the same fallback string the runtime
    would produce when ``persona.json`` is empty and only legacy settings
    exist.
    """
    if not settings:
        return f"{lanlan_name}记得：（暂无记录）"
    sections: list[str] = []
    for name, data in settings.items():
        if not isinstance(data, dict) or not data:
            continue
        lines: list[str] = []
        for key, value in data.items():
            if value is None or value == "" or value == []:
                continue
            if isinstance(value, list):
                value_str = "、".join(str(v) for v in value)
            elif isinstance(value, dict):
                parts = [
                    f"{k}: {v}" for k, v in value.items()
                    if v is not None and v != ""
                ]
                value_str = "、".join(parts) if parts else str(value)
            else:
                value_str = str(value)
            lines.append(f"- {key}：{value_str}")
        if lines:
            sections.append(f"关于{name}：\n" + "\n".join(lines))
    if not sections:
        return f"{lanlan_name}记得：（暂无记录）"
    return f"{lanlan_name}记得：\n" + "\n".join(sections)


# ── memory context assembly (clock-injected) ─────────────────────────


def _build_memory_context_structured_with_clock(
    *,
    lanlan_name: str,
    master_name: str,
    name_mapping: dict[str, str],
    lang_short: str,
    now: datetime,
    recent_history_manager: CompressedRecentHistoryManager,
    settings_manager: ImportantSettingsManager,
    time_manager: TimeIndexedMemory,
    persona_manager: PersonaManager,
    reflection_engine: ReflectionEngine,
) -> dict[str, Any]:
    """Parallel of :func:`tests.dump_llm_input.build_memory_context_structured`
    that substitutes ``now`` for ``datetime.now()`` everywhere it matters.

    Changes from upstream:

    * ``inner_thoughts_dynamic`` uses ``now.strftime(_TIMESTAMP_FORMAT)``
      instead of :func:`utils.frontend_utils.get_timestamp` (which reads
      wall-clock time).
    * The "chat gap" computation (``gap = datetime.now() - last_time``) is
      replaced with ``gap = now - last_time``. This lets testers scrub
      the virtual clock to exercise the "first contact in 5 hours" branch.
    * Everything else (persona markdown, recent history rendering,
      holiday hint) is identical to upstream.
    """
    local_name_mapping = dict(name_mapping)
    local_name_mapping["ai"] = lanlan_name

    # ── Persona (long-term memory) ──
    pending_reflections: list[dict] = []
    confirmed_reflections: list[dict] = []
    try:
        pending_reflections = reflection_engine.get_pending_reflections(lanlan_name)
        confirmed_reflections = reflection_engine.get_confirmed_reflections(lanlan_name)
    except Exception:
        # Empty reflection store → upstream returns []; defensive catch for
        # malformed files so a single bad record doesn't nuke the preview.
        pass

    persona_header = _loc(PERSONA_HEADER, lang_short).format(name=lanlan_name)
    try:
        persona_md = persona_manager.render_persona_markdown(
            lanlan_name, pending_reflections, confirmed_reflections,
        )
    except Exception:
        persona_md = ""
    if not persona_md:
        try:
            settings = settings_manager.get_settings(lanlan_name)
        except Exception:
            settings = {}
        persona_md = _format_legacy_settings_as_text(settings, lanlan_name) + "\n"

    # ── Inner thoughts header ──
    inner_thoughts_header = _loc(INNER_THOUGHTS_HEADER, lang_short).format(name=lanlan_name)
    inner_thoughts_dynamic = _loc(INNER_THOUGHTS_DYNAMIC, lang_short).format(
        name=lanlan_name,
        time=now.strftime(_TIMESTAMP_FORMAT),
    )

    # ── Recent history ──
    recent_history_entries: list[dict[str, str]] = []
    try:
        recent_items: Iterable = recent_history_manager.get_recent_history(lanlan_name)
    except Exception:
        recent_items = []
    for item in recent_items:
        try:
            speaker = local_name_mapping.get(
                getattr(item, "type", ""),
                getattr(item, "type", "") or "?",
            )
            if isinstance(item.content, str):
                cleaned = _BRACKETS_RE.sub("", item.content).strip()
            else:
                texts = [
                    _BRACKETS_RE.sub("", j.get("text", "")).strip()
                    for j in item.content if isinstance(j, dict) and j.get("type") == "text"
                ]
                cleaned = "\n".join(texts)
            recent_history_entries.append({"speaker": speaker, "content": cleaned})
        except Exception:
            # Skip malformed entry rather than fail the whole preview.
            continue

    # ── Chat gap hint (virtual-clock driven) ──
    time_context = ""
    try:
        last_time = time_manager.get_last_conversation_time(lanlan_name)
        if last_time is not None:
            gap = now - last_time
            gap_seconds = gap.total_seconds()
            if gap_seconds >= 1800:
                elapsed = _format_elapsed(lang_short, gap_seconds)
                if gap_seconds >= 18000:
                    now_str = now.strftime("%Y-%m-%d %H:%M")
                    time_context += _loc(CHAT_GAP_CURRENT_TIME, lang_short).format(now=now_str)
                    time_context += _loc(CHAT_GAP_NOTICE, lang_short).format(
                        master=master_name, elapsed=elapsed,
                    )
                    time_context += _loc(CHAT_GAP_LONG_HINT, lang_short).format(
                        name=lanlan_name, master=master_name,
                    ) + "\n"
                else:
                    time_context += _loc(CHAT_GAP_NOTICE, lang_short).format(
                        master=master_name, elapsed=elapsed,
                    ) + "\n"
    except Exception:
        pass

    # ── Holiday context (real-world calendar; not virtual) ──
    # Upstream reads the OS calendar; we preserve that because "which
    # holiday is today" has no virtual-clock analogue. Testers who want
    # to simulate different dates can patch the holiday cache separately.
    holiday_context = ""
    try:
        from utils.holiday_cache import get_holiday_context_line

        holiday_name = get_holiday_context_line(lang_short)
        if holiday_name:
            holiday_context = _loc(CHAT_HOLIDAY_CONTEXT, lang_short).format(holiday=holiday_name)
    except Exception:
        pass

    return {
        "persona_header": persona_header,
        "persona_content": persona_md,
        "inner_thoughts_header": inner_thoughts_header,
        "inner_thoughts_dynamic": inner_thoughts_dynamic,
        "recent_history": recent_history_entries,
        "time_context": time_context,
        "holiday_context": holiday_context,
    }


def _flatten_memory_components(components: dict[str, Any]) -> str:
    """Verbatim copy of :func:`tests.dump_llm_input._flatten_memory_components`.

    Kept local so we aren't at the mercy of that script being refactored,
    and so the preview can be generated in a fully offline sandbox even
    if ``tests/dump_llm_input.py`` gets moved.
    """
    result = components["persona_header"]
    result += components["persona_content"]
    result += components["inner_thoughts_header"]
    result += components["inner_thoughts_dynamic"]
    for entry in components["recent_history"]:
        result += f"{entry['speaker']} | {entry['content']}\n"
    result += components["time_context"]
    result += components["holiday_context"]
    return result


# ── main entry ───────────────────────────────────────────────────────


def build_prompt_bundle(session: Session) -> PromptBundle:
    """Assemble the PromptBundle for the given session.

    Raises :class:`PreviewNotReady` if the session has no ``character_name``
    in its persona (the Preview is meaningless without it — name_mapping,
    memory paths, and placeholder substitution all key off it).
    """
    persona = session.persona or {}
    character_name = (persona.get("character_name") or "").strip()
    master_name = (persona.get("master_name") or "").strip()
    lang_full = persona.get("language") or "zh-CN"
    lang_short = _normalize_short_lang(lang_full)

    if not character_name:
        raise PreviewNotReady(
            "PersonaCharacterMissing",
            "请先在 Setup → Persona 填写 character_name。",
        )

    warnings: list[str] = []
    if not master_name:
        warnings.append(
            "master_name 为空 (预览中 {MASTER_NAME} 占位符将无法替换, 真实运行时会"
            " 退回到 ConfigManager 默认值)。",
        )

    (
        lanlan_prompt,
        template_used,
        stored_is_default,
        template_raw,
    ) = _resolve_character_prompt(persona, lang_short)
    if template_used == "default":
        if stored_is_default and (persona.get("system_prompt") or "").strip():
            warnings.append(
                "自定义 system_prompt 被识别为默认模板, 运行时仍会走默认路径。",
            )
        elif not (persona.get("system_prompt") or "").strip():
            warnings.append(
                "persona.system_prompt 留空, 正在使用语言 {lang} 的默认模板。".format(
                    lang=lang_short,
                ),
            )

    name_mapping = _build_name_mapping(master_name)

    # ── 构造记忆管理器 ──
    # 每次 preview 都构造新实例以避免跨会话泄露. 上游的各 manager __init__
    # 轻量, 只在第一次 getter 调用时懒加载磁盘文件, 所以这里不会触发昂贵 IO.
    # 失败时降级到空 context — preview 永远不应该因为角色第一次装配就崩溃.
    try:
        recent_history_manager = CompressedRecentHistoryManager()
    except Exception as exc:
        warnings.append(f"RecentHistoryManager 构造失败: {exc}")
        recent_history_manager = None  # type: ignore[assignment]
    try:
        settings_manager = ImportantSettingsManager()
    except Exception as exc:
        warnings.append(f"ImportantSettingsManager 构造失败: {exc}")
        settings_manager = None  # type: ignore[assignment]
    try:
        time_manager = TimeIndexedMemory(recent_history_manager) if recent_history_manager else None  # type: ignore[arg-type]
    except Exception as exc:
        warnings.append(f"TimeIndexedMemory 构造失败: {exc}")
        time_manager = None  # type: ignore[assignment]
    try:
        fact_store = FactStore(time_indexed_memory=time_manager) if time_manager else None
    except Exception as exc:
        warnings.append(f"FactStore 构造失败: {exc}")
        fact_store = None  # type: ignore[assignment]
    try:
        persona_manager = PersonaManager()
    except Exception as exc:
        warnings.append(f"PersonaManager 构造失败: {exc}")
        persona_manager = None  # type: ignore[assignment]
    try:
        reflection_engine = (
            ReflectionEngine(fact_store, persona_manager)
            if fact_store and persona_manager else None
        )
    except Exception as exc:
        warnings.append(f"ReflectionEngine 构造失败: {exc}")
        reflection_engine = None  # type: ignore[assignment]

    now = session.clock.now()

    if all(m is not None for m in (
        recent_history_manager, settings_manager, time_manager,
        persona_manager, reflection_engine,
    )):
        memory_components = _build_memory_context_structured_with_clock(
            lanlan_name=character_name,
            master_name=master_name or "主人",
            name_mapping=name_mapping,
            lang_short=lang_short,
            now=now,
            recent_history_manager=recent_history_manager,  # type: ignore[arg-type]
            settings_manager=settings_manager,              # type: ignore[arg-type]
            time_manager=time_manager,                      # type: ignore[arg-type]
            persona_manager=persona_manager,                # type: ignore[arg-type]
            reflection_engine=reflection_engine,            # type: ignore[arg-type]
        )
    else:
        # Some manager failed to construct — fall back to an empty skeleton
        # so the UI can still render everything else (session_init /
        # character_prompt / closing) and show the warnings chain.
        memory_components = {
            "persona_header": _loc(PERSONA_HEADER, lang_short).format(name=character_name),
            "persona_content": "",
            "inner_thoughts_header": _loc(INNER_THOUGHTS_HEADER, lang_short).format(name=character_name),
            "inner_thoughts_dynamic": _loc(INNER_THOUGHTS_DYNAMIC, lang_short).format(
                name=character_name, time=now.strftime(_TIMESTAMP_FORMAT),
            ),
            "recent_history": [],
            "time_context": "",
            "holiday_context": "",
        }

    session_init = _loc(SESSION_INIT_PROMPT, lang_short).format(name=character_name)
    closing = _loc(CONTEXT_SUMMARY_READY, lang_short).format(
        name=character_name,
        master=master_name or "主人",
    )
    memory_flat = _flatten_memory_components(memory_components)
    system_prompt = session_init + lanlan_prompt + memory_flat + closing

    # wire_messages = [system] + 每条 session.messages (OpenAI {role, content} 对).
    # session.messages 由 P09 引入 (chat_runner + /chat/messages CRUD), 采用
    # tests.testbench.chat_messages.make_message 规范化的 dict 结构: role ∈
    # {user, assistant, system} 已与 OpenAI 直通, 无需翻译. 多模态 content
    # (list[dict]) 会原样保留 — 上游 ChatOpenAI._normalize_messages 能接受.
    wire_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    for msg in session.messages or []:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        wire_messages.append({"role": role, "content": content})

    # ── 字符数 / 估算 token ──
    structured_for_ui = {
        "session_init": session_init,
        "character_prompt": lanlan_prompt,
        "character_prompt_template_raw": template_raw,
        "persona_header": memory_components["persona_header"],
        "persona_content": memory_components["persona_content"],
        "inner_thoughts_header": memory_components["inner_thoughts_header"],
        "inner_thoughts_dynamic": memory_components["inner_thoughts_dynamic"],
        "recent_history": memory_components["recent_history"],
        "time_context": memory_components["time_context"],
        "holiday_context": memory_components["holiday_context"],
        "closing": closing,
    }
    char_counts = {
        "session_init":          len(session_init),
        "character_prompt":      len(lanlan_prompt),
        "persona_header":        len(memory_components["persona_header"]),
        "persona_content":       len(memory_components["persona_content"]),
        "inner_thoughts_header": len(memory_components["inner_thoughts_header"]),
        "inner_thoughts_dynamic": len(memory_components["inner_thoughts_dynamic"]),
        "recent_history": sum(
            len(e.get("speaker", "")) + len(e.get("content", "")) + 3  # "| " + "\n"
            for e in memory_components["recent_history"]
        ),
        "time_context":    len(memory_components["time_context"]),
        "holiday_context": len(memory_components["holiday_context"]),
        "closing":         len(closing),
        "system_prompt_total": len(system_prompt),
        # 粗略估算: CJK ~1 char ≈ 0.5 token; 这里用一半字符数作为快速 hint,
        # 精确计数到 P15 Judger 要算预算时再引入 tiktoken.
        "approx_tokens":   len(system_prompt) // 2,
    }

    metadata = {
        "character_name": character_name,
        "master_name": master_name,
        "language_full": lang_full,
        "language_short": lang_short,
        "template_used": template_used,
        "stored_is_default": stored_is_default,
        "clock": session.clock.to_dict(),
        "message_count": len(session.messages or []),
        "built_at_virtual": now.isoformat(timespec="seconds"),
        "built_at_real": datetime.now().isoformat(timespec="seconds"),
    }

    # ── dispose managers that hold OS-level resources ──
    # `TimeIndexedMemory` opens a SQLAlchemy engine per character when
    # `get_last_conversation_time` is called during memory context
    # assembly. The engine keeps an OS-level handle on `time_indexed.db`
    # until its connection pool is disposed. Python GC would eventually
    # do it, but on Windows the next rewind / reset rmtree can race the
    # GC and fail with WinError 32. Dispose explicitly here — the
    # manager is a throwaway local, we have no reason to keep its
    # engines alive past this function.
    try:
        if time_manager is not None and hasattr(time_manager, "cleanup"):
            time_manager.cleanup()  # closes all per-character engines
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup
        warnings.append(f"TimeIndexedMemory.cleanup 失败: {exc}")

    return PromptBundle(
        session_id=session.id,
        structured=structured_for_ui,
        system_prompt=system_prompt,
        wire_messages=wire_messages,
        char_counts=char_counts,
        metadata=metadata,
        warnings=warnings,
    )
