"""External-event simulation handlers (P25 §2.1/§3 Day 1).

Reproduce the three main-program "runtime prompt injection + write memory"
external event classes so a tester can study their impact on LLM reply,
recent-history compression, facts extraction, and reflection:

* **avatar**: avatar tool interactions (PR #769) producing a user-role
  ``[主人摸了摸你的头]``-style memory note + an LLM reaction reply.
* **agent_callback**: background agent task completion producing an
  ``AGENT_CALLBACK_NOTIFICATION`` instruction prefix driving a target reply.
* **proactive**: time/queue-idle proactive chat producing an LLM-emitted
  opener or ``[PASS]`` skip signal.

Semantic reproduction scope (P25_BLUEPRINT §1.2 / §1.3)
-------------------------------------------------------
What we DO reproduce (LESSONS_LEARNED §1.6 semantic contract):

* Avatar prompt assembly via the nine ``config.prompts_avatar_interaction``
  pure helpers + seven constant tables (no re-implementation).
* Agent-callback instruction prefix via ``AGENT_CALLBACK_NOTIFICATION``
  five-language dict.
* Proactive dispatch via ``get_proactive_chat_prompt(kind, lang)`` across
  the seven kinds and five languages.
* Avatar dedupe semantics via the copy-protected region of
  :mod:`tests.testbench.pipeline.avatar_dedupe` (8000 ms window + rank
  upgrade short-circuit).
* ``append_message`` choke-point for every write into
  ``session.messages`` (A17 / messages_writer invariant).

What we do NOT reproduce (runtime mechanism, intentionally out-of-scope):

* WebSocket / multi-process queue transport.
* Real-time jitter cooling, N-second trigger-window cooling, and
  ``merge_unsynced_tail_assistants`` tail-merging heuristics.
* Actual avatar canvas click flow — a manual click is always a single
  request; dedupe repeats are exercised via tester re-posts.

Message sourcing convention
---------------------------
We re-use two existing sources from :mod:`tests.testbench.chat_messages`
rather than introducing a P25-only enum value, so the messages roundtrip
through persistence / export without schema migrations:

* user-role memory notes → ``SOURCE_INJECT`` (a system-origin user-facing
  line, exactly matching ``chat/inject_system`` intent).
* assistant replies → ``SOURCE_LLM`` (the reply genuinely came from the
  target LLM stream).

The caller-facing discriminator "this was a simulated external event" is
carried by the :class:`SimulationResult` + the diagnostics op record, not
by the persisted message.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from tests.testbench.chat_messages import (
    ROLE_ASSISTANT,
    ROLE_USER,
    SOURCE_INJECT,
    SOURCE_LLM,
    make_message,
)
from tests.testbench.logger import python_logger
from tests.testbench.pipeline import diagnostics_store
from tests.testbench.pipeline.atomic_io import atomic_write_json
from tests.testbench.pipeline.avatar_dedupe import _AvatarDedupeCache
from tests.testbench.pipeline.chat_runner import ChatConfigError, resolve_group_config
from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp
from tests.testbench.pipeline.messages_writer import append_message
from tests.testbench.pipeline.prompt_builder import PreviewNotReady, build_prompt_bundle
from tests.testbench.session_store import Session
from utils.llm_client import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    messages_to_dict,
)

# Map ``session.messages``'s role field → the corresponding LangChain
# message class so :func:`_apply_mirror_to_recent` can write
# ``recent.json`` in the main-program canonical on-disk shape
# ``{"type": "human"|"ai"|"system", "data": {"content": <str>}}``.
# Using the LangChain classes + ``messages_to_dict`` instead of hand-
# rolling the dict guarantees byte-exact round-trip with
# ``messages_from_dict`` (see ``utils.llm_client.py`` L71-L95).
_LANGCHAIN_ROLE_CLS: dict[str, type] = {
    "user": HumanMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
}

# ─────────────────────────────────────────────────────────────
# Public value objects
# ─────────────────────────────────────────────────────────────


class SimulationKind(str, Enum):
    """The three event classes exposed by POST /api/session/external-event."""

    AVATAR = "avatar"
    AGENT_CALLBACK = "agent_callback"
    PROACTIVE = "proactive"


#: SimulationResult.reason — enumerated rejection / short-circuit reasons.
#: Only reproduces the semantic-contract layer of the main program; runtime
#: reasons like "websocket closed" / "queue full" are OOS per §1.3.
ReasonCode = Literal[
    "dedupe_window_hit",   # avatar: same dedupe_key inside 8000 ms window, same or lower rank
    "invalid_payload",     # avatar: _normalize_avatar_interaction_payload returned None
    "empty_callbacks",     # agent_callback: no callback items in payload
    "pass_signaled",       # proactive: LLM returned [PASS] — recorded, not an error
    "llm_failed",          # wire assembled but target LLM call raised
    "persona_not_ready",   # build_prompt_bundle raised PreviewNotReady
    "chat_not_configured", # resolve_group_config raised ChatConfigError
]


@dataclass
class CoerceInfo:
    """Payload-level coercion surfacing (LESSONS_LEARNED §7.14).

    When ``_normalize_avatar_interaction_intensity`` silently corrects a
    bad ``intensity``, or ``_normalize_prompt_language`` falls back to
    English for unsupported languages, we return the before/after pair
    here so the UI can show "you asked for intensity=crazy, we used
    intensity=normal" rather than drop the fact on the floor.
    """

    field: str
    requested: Any
    applied: Any
    note: str = ""


@dataclass
class MirrorToRecentInfo:
    """Feature flag surfacing (LESSONS_LEARNED L17).

    ``requested`` tracks what the tester asked for; ``applied`` tracks
    what actually happened. A ``requested=True, applied=False`` pair
    MUST carry a ``fallback_reason`` string so the UI never silently
    drops a persistence intent.
    """

    requested: bool
    applied: bool
    fallback_reason: Optional[str] = None


@dataclass
class SimulationResult:
    """One-shot response for any of the three simulate_* handlers.

    Fields:

    * ``accepted`` — did we actually move any state (append a message /
      write memory / drive the LLM)? ``False`` on dedupe / pass / error.
    * ``reason`` — populated iff ``accepted=False`` (see :data:`ReasonCode`).
    * ``instruction`` — the wire-only prompt string that was sent to the
      LLM for this event (avatar system wrapper, agent_callback prefix,
      or proactive prompt). Returned for UI preview / audit. NEVER enters
      ``session.messages`` (P25_BLUEPRINT §A.8 #2).
    * ``memory_pair`` — the ``{user, assistant}`` message pair persisted
      when ``accepted=True`` (avatar always, agent_callback never on user
      side, proactive assistant-only). Entries are the full message dicts
      as returned by :func:`make_message` post ``append_message``.
    * ``persisted`` — quick bool: "did we write anything to session.messages".
    * ``dedupe_info`` — per-request dedupe snapshot: ``{hit, remaining_ms,
      cache_size}`` when avatar, ``None`` otherwise.
    * ``assistant_reply`` — the raw LLM reply text (before it becomes a
      message); useful to render immediately in the UI even when we also
      persisted it to ``session.messages``.
    * ``coerce_info`` — list of ``CoerceInfo`` for every silent payload
      correction this request made. Empty list = nothing coerced.
    * ``mirror_to_recent_info`` — :class:`MirrorToRecentInfo` triplet
      always returned (even when requested=False) so the UI can render a
      three-state "off / on-applied / on-fallback" badge uniformly.
    * ``elapsed_ms`` — wall clock from handler entry to handler return.
    """

    accepted: bool
    reason: Optional[ReasonCode] = None
    instruction: str = ""
    memory_pair: list[dict[str, Any]] = field(default_factory=list)
    persisted: bool = False
    dedupe_info: Optional[dict[str, Any]] = None
    assistant_reply: str = ""
    coerce_info: list[CoerceInfo] = field(default_factory=list)
    mirror_to_recent_info: MirrorToRecentInfo = field(
        default_factory=lambda: MirrorToRecentInfo(requested=False, applied=False),
    )
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialization for the router response."""
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "instruction": self.instruction,
            "memory_pair": list(self.memory_pair),
            "persisted": self.persisted,
            "dedupe_info": dict(self.dedupe_info) if self.dedupe_info else None,
            "assistant_reply": self.assistant_reply,
            "coerce_info": [asdict(c) for c in self.coerce_info],
            "mirror_to_recent_info": asdict(self.mirror_to_recent_info),
            "elapsed_ms": self.elapsed_ms,
        }


# ─────────────────────────────────────────────────────────────
# Per-session dedupe cache registry
# ─────────────────────────────────────────────────────────────

# We intentionally keep the cache off ``Session`` — avoiding a dataclass
# field touches zero persistence / export / diagnostics code. The cache is
# pure in-memory scratch that is fine to forget on process restart.
_DEDUPE_CACHES: dict[str, _AvatarDedupeCache] = {}


def _get_dedupe_cache(session: Session) -> _AvatarDedupeCache:
    """Return (or lazily create) the ``_AvatarDedupeCache`` for this session."""
    cache = _DEDUPE_CACHES.get(session.id)
    if cache is None:
        cache = _AvatarDedupeCache(on_full=_make_cache_full_notifier(session))
        _DEDUPE_CACHES[session.id] = cache
    return cache


def _make_cache_full_notifier(session: Session):
    """Bind a ``(cap: int) -> None`` hook that logs AVATAR_DEDUPE_CACHE_FULL."""
    session_id = session.id

    def _notify(cap: int) -> None:
        try:
            diagnostics_store.record_internal(
                DiagnosticsOp.AVATAR_DEDUPE_CACHE_FULL,
                (
                    f"avatar 事件去重缓存达到软上限 {cap} 条, LRU 丢弃最旧条目. "
                    "调 POST /api/session/external-event/dedupe-reset 清空即可 rearm."
                ),
                level="warning",
                session_id=session_id,
                detail={"max_entries": cap},
            )
        except Exception:  # noqa: BLE001 — diagnostics must not break dedupe
            python_logger().exception(
                "external_events: record_internal(AVATAR_DEDUPE_CACHE_FULL) failed"
            )

    return _notify


def peek_dedupe_info(session: Session) -> dict[str, Any]:
    """Snapshot of the session's dedupe cache for the diagnostics GET."""
    cache = _get_dedupe_cache(session)
    return {
        "size": len(cache),
        "max_entries": _AvatarDedupeCache._MAX_ENTRIES,
        "entries": cache.snapshot(),
    }


def reset_dedupe(session: Session) -> dict[str, Any]:
    """Clear the session's dedupe cache and rearm the overflow notice."""
    cache = _get_dedupe_cache(session)
    size_before = len(cache)
    cache.clear()
    return {"cleared": size_before}


def discard_session_caches(session_id: str) -> None:
    """Drop any per-session caches. Callable from SessionStore.destroy."""
    _DEDUPE_CACHES.pop(session_id, None)


# ─────────────────────────────────────────────────────────────
# Shared LLM plumbing
# ─────────────────────────────────────────────────────────────


def _resolve_language(session: Session) -> tuple[str, str]:
    """Return ``(full_language, short_language)`` for prompt dispatch.

    ``full`` is the raw ``session.persona.language`` (default ``zh-CN``).
    ``short`` is the same value normalised to a 2-char base used by the
    proactive dispatch table (``zh/en/ja/ko/ru``), mirroring the
    ``_normalize_prompt_language`` rule in ``config.prompts_proactive``.
    """
    persona = session.persona or {}
    full = str(persona.get("language") or "zh-CN").strip()
    lower = full.lower()
    if lower.startswith("zh"):
        return full, "zh"
    if lower.startswith("en"):
        return full, "en"
    if lower.startswith("ja"):
        return full, "ja"
    if lower.startswith("ko"):
        return full, "ko"
    if lower.startswith("ru"):
        return full, "ru"
    # es / pt / anything else — proactive falls back to en per main program.
    return full, "en"


def _resolve_names(session: Session) -> tuple[str, str]:
    """Return ``(character_name, master_name)`` with ``主人`` fallback."""
    persona = session.persona or {}
    lanlan = str(persona.get("character_name") or "").strip()
    master = str(persona.get("master_name") or "").strip() or "主人"
    return lanlan, master


async def _invoke_llm_once(
    session: Session,
    wire_messages: list[dict[str, Any]],
) -> str:
    """Call the chat-group LLM once (non-streaming) and return the text reply.

    Mirrors :meth:`OfflineChatBackend.stream_send` config resolution but
    uses ``streaming=False`` so we get the full reply in one shot — the
    three simulate_* handlers are synchronous-style (request/response)
    rather than SSE streams, so streaming buys nothing.
    """
    cfg = resolve_group_config(session, "chat")
    from utils.llm_client import ChatOpenAI

    client = ChatOpenAI(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout or 60.0,
        max_retries=1,
        streaming=False,
    )
    try:
        resp = await client.ainvoke(wire_messages)
        return (resp.content or "").strip()
    finally:
        try:
            await client.aclose()
        except Exception as close_exc:  # noqa: BLE001
            python_logger().debug(
                "external_events ChatOpenAI.aclose failed: %s", close_exc,
            )


def _build_base_wire(session: Session) -> list[dict[str, Any]]:
    """Build the base ``[system, *history]`` wire without any event-specific
    instruction attached. Handlers will tail-append their own instruction.
    """
    bundle = build_prompt_bundle(session)
    # We want a fresh copy so the handler can mutate (append the instruction)
    # without leaking back into the prompt builder cache.
    return [dict(m) for m in bundle.wire_messages]


# ─────────────────────────────────────────────────────────────
# Recent.json mirror (opt-in)
# ─────────────────────────────────────────────────────────────


def _apply_mirror_to_recent(
    session: Session,
    messages_to_mirror: list[dict[str, Any]],
    requested: bool,
) -> MirrorToRecentInfo:
    """Optionally append ``messages_to_mirror`` to the character's recent.json.

    P25_BLUEPRINT §2.4: **opt-in** semantics — caller asks via
    ``requested=True``; we surface ``applied`` + ``fallback_reason`` so a
    write failure is visible (L17 feature-flag surfacing), never silent.
    """
    if not requested:
        return MirrorToRecentInfo(requested=False, applied=False)

    character = (session.persona or {}).get("character_name") or ""
    character = str(character).strip()
    if not character:
        return MirrorToRecentInfo(
            requested=True,
            applied=False,
            fallback_reason="session.persona.character_name 为空; recent.json 路径无法解析",
        )
    if not messages_to_mirror:
        return MirrorToRecentInfo(
            requested=True,
            applied=False,
            fallback_reason="本次事件没有产生任何可 mirror 的消息 (dedupe/PASS 等)",
        )

    try:
        from utils.config_manager import get_config_manager  # deferred import — avoids startup cycle
    except Exception as exc:  # noqa: BLE001
        return MirrorToRecentInfo(
            requested=True,
            applied=False,
            fallback_reason=f"ConfigManager 不可用: {type(exc).__name__}: {exc}",
        )

    try:
        cm = get_config_manager()
        recent_path = Path(str(cm.memory_dir)) / character / "recent.json"
    except Exception as exc:  # noqa: BLE001
        return MirrorToRecentInfo(
            requested=True,
            applied=False,
            fallback_reason=f"ConfigManager.memory_dir 解析失败: {type(exc).__name__}: {exc}",
        )

    try:
        existing: list[dict[str, Any]] = []
        if recent_path.exists():
            import json
            with recent_path.open("r", encoding="utf-8") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, list):
                existing = [m for m in loaded if isinstance(m, dict)]
            # Invalid file: treat as empty; we refuse to overwrite arbitrary
            # content, so surface a fallback reason instead.
            elif loaded is not None:
                return MirrorToRecentInfo(
                    requested=True,
                    applied=False,
                    fallback_reason=(
                        f"recent.json 顶层非 list (实为 {type(loaded).__name__}); "
                        "拒绝覆盖, 请 tester 手动修复或从 Paths 子页删除"
                    ),
                )
    except Exception as exc:  # noqa: BLE001
        return MirrorToRecentInfo(
            requested=True,
            applied=False,
            fallback_reason=f"recent.json 读取失败: {type(exc).__name__}: {exc}",
        )

    # Mirror entries must be written in the main-program canonical
    # ``messages_to_dict`` shape — ``{"type": "human"|"ai"|"system",
    # "data": {"content": <str>}}`` — because downstream consumers
    # (``memory_runner._preview_recent_compress`` at line 456,
    # ``memory_runner._preview_facts_extract`` at line 621, plus the
    # main program's own ``memory/recent.py``) all round-trip via
    # ``messages_from_dict(_read_json_list(recent_path))``. Writing our
    # testbench-internal ``{role, content:[{type:text,text:...}]}``
    # shape would pass ``isinstance(loaded, list)`` but then silently
    # fall back to ``HumanMessage(content=str(d))`` inside
    # ``messages_from_dict`` (``utils.llm_client.py`` L113-114) — the
    # content would read as the stringified dict ``"{'role': ...}"``
    # instead of the actual user/assistant text, breaking compress /
    # facts-extract without raising.
    #
    # Build LangChain messages via ``_ROLE_CLS`` (which maps
    # user→HumanMessage, assistant→AIMessage, system→SystemMessage),
    # then serialize with ``messages_to_dict``. This is exactly the
    # write path the main program uses in ``memory/recent.py``
    # (``cm.memory_dir / character / "recent.json"``).
    lc_messages: list[Any] = []
    for msg in messages_to_mirror:
        role = str(msg.get("role") or "user").strip().lower()
        cls = _LANGCHAIN_ROLE_CLS.get(role, _LANGCHAIN_ROLE_CLS["user"])
        content = msg.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list) and content and isinstance(content[0], dict):
            # testbench richer content shape — flatten to plain text
            text = str(content[0].get("text") or "")
        else:
            text = str(content or "")
        lc_messages.append(cls(content=text))
    mirrored = messages_to_dict(lc_messages)

    try:
        atomic_write_json(recent_path, existing + mirrored)
    except Exception as exc:  # noqa: BLE001
        return MirrorToRecentInfo(
            requested=True,
            applied=False,
            fallback_reason=f"recent.json 原子写入失败: {type(exc).__name__}: {exc}",
        )

    return MirrorToRecentInfo(requested=True, applied=True)


# ─────────────────────────────────────────────────────────────
# Handler: avatar interaction
# ─────────────────────────────────────────────────────────────


async def simulate_avatar_interaction(
    session: Session,
    payload: dict[str, Any],
    *,
    mirror_to_recent: bool = False,
) -> SimulationResult:
    """Simulate one avatar interaction (PR #769) end-to-end.

    Pipeline:
      1. Normalise the raw UI payload via
         ``_normalize_avatar_interaction_payload``. Invalid → reject.
      2. Dedupe via ``_AvatarDedupeCache.should_persist`` (8000 ms window
         + rank upgrade). Dedupe hit → reject (no LLM call, no messages).
      3. Build instruction via ``_build_avatar_interaction_instruction``
         and memory meta via ``_build_avatar_interaction_memory_meta``
         (both main-program helpers, reused verbatim).
      4. Append instruction to a **throwaway** wire (not session.messages).
      5. Call LLM once, get assistant reply.
      6. Write the pair (user memory_note, assistant reply) to
         session.messages through ``append_message``.
      7. If LLM fails AFTER dedupe reserved the slot, roll back the
         cache entry so the tester can retry without an 8-second wait.
      8. Optionally mirror to recent.json (opt-in).
    """
    started = time.perf_counter()
    from config.prompts_avatar_interaction import (
        _build_avatar_interaction_instruction,
        _build_avatar_interaction_memory_meta,
        _normalize_avatar_interaction_payload,
    )

    coerce_info: list[CoerceInfo] = []
    full_lang, _short_lang = _resolve_language(session)
    lanlan_name, master_name = _resolve_names(session)

    normalized = _normalize_avatar_interaction_payload(payload)
    if normalized is None:
        return _record_and_return(
            session,
            SimulationKind.AVATAR,
            SimulationResult(
                accepted=False,
                reason="invalid_payload",
                elapsed_ms=_elapsed(started),
            ),
            detail={"payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else None},
        )

    # Intensity coercion surfacing — reverse-diff against raw request.
    raw_intensity = str(payload.get("intensity") or "").strip().lower()
    if raw_intensity and raw_intensity != normalized["intensity"]:
        coerce_info.append(CoerceInfo(
            field="intensity",
            requested=raw_intensity,
            applied=normalized["intensity"],
            note=(
                "intensity 被 _normalize_avatar_interaction_intensity 归一 — "
                "原值不在 tool/action 允许集合中, 已回退到 'normal' 或合法子集."
            ),
        ))

    meta = _build_avatar_interaction_memory_meta(full_lang, normalized)
    memory_note = str(meta.get("memory_note") or "")
    # Main program returns these under the ``memory_*`` prefix (see
    # config/prompts_avatar_interaction.py::_build_avatar_interaction_memory_meta
    # and main_logic/core.py L2864 / main_logic/cross_server.py L478). We
    # fall back to the tool_id only when the meta has none (should not
    # happen for the main tool_ids: lollipop / fist / hammer always set
    # memory_dedupe_key explicitly).
    dedupe_key = str(meta.get("memory_dedupe_key") or normalized["tool_id"])
    dedupe_rank = int(meta.get("memory_dedupe_rank") or 1)

    cache = _get_dedupe_cache(session)
    cache_size_before = len(cache)
    allowed = cache.should_persist(memory_note, dedupe_key, dedupe_rank)
    if not allowed:
        return _record_and_return(
            session,
            SimulationKind.AVATAR,
            SimulationResult(
                accepted=False,
                reason="dedupe_window_hit",
                coerce_info=coerce_info,
                dedupe_info={
                    "hit": True,
                    "cache_size": len(cache),
                    "dedupe_key": dedupe_key,
                    "dedupe_rank": dedupe_rank,
                },
                elapsed_ms=_elapsed(started),
            ),
            detail={"interaction_id": normalized.get("interaction_id"), "tool_id": normalized["tool_id"]},
        )

    instruction = _build_avatar_interaction_instruction(
        full_lang, lanlan_name, master_name, normalized,
    )

    try:
        base_wire = _build_base_wire(session)
    except PreviewNotReady as exc:
        # Roll back the dedupe entry — we reserved a slot expecting to
        # persist, but persona/bundle failed before any LLM call. Failing
        # without rollback would force the tester to wait 8 s just because
        # they forgot to fill in character_name.
        _rollback_dedupe(cache, dedupe_key)
        return _record_and_return(
            session,
            SimulationKind.AVATAR,
            SimulationResult(
                accepted=False,
                reason="persona_not_ready",
                instruction=instruction,
                coerce_info=coerce_info,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_code": exc.code, "error_message": exc.message},
        )
    # Instruction lands ONLY on the wire, NOT session.messages — per
    # P25_BLUEPRINT §A.8 #2, the testbench UI must never surface the
    # instruction as a persisted chat bubble.
    #
    # CRITICAL — wire role must be "user", NOT "system" (L36 第三轮证据 /
    # LESSONS_LEARNED §1.6 Semantic Contract vs Runtime Mechanism):
    # 主程序 ``OmniOfflineClient.prompt_ephemeral`` (main_logic/
    # omni_offline_client.py L718) 的语义契约是
    #   messages_to_send = history + [HumanMessage(content=instruction)]
    # 即 **以 user 角色** 临时注入 instruction. 早期我们错误地写成
    # ``role=system`` (只看了字符串 helper 没看 role), 触发两个级联 bug:
    #   (a) 空 session + 空 history 时 wire 变成 ``[system_prompt, system_
    #       instruction]`` — 零 user 消息. Gemini 会直接 400
    #       "Model input cannot be empty" (INVALID_ARGUMENT).
    #   (b) 非空 session 时 Gemini **偶尔** 对 "两条 system 尾接" 的 shape
    #       返回空字符串 + 200, 空 reply 被 append 进 session.messages;
    #       下一轮 LLM 读到 "上一轮 user memory_note + 上一轮空 assistant
    #       + 新 instruction" 的 wire, 基于上一轮事件生成回复 — tester
    #       观察到 "再次触发才拿到上次的 reply".
    # 两个 bug 共一个根因: 违反了主程序"instruction 入 wire 以 user 角色"
    # 的语义契约. Smoke 漏掉这是因为 offline_chat_client.py 的 fake LLM
    # 不会因为 wire 全 system 就返空, 反而照给 reply.
    base_wire.append({"role": "user", "content": instruction})

    try:
        reply_text = await _invoke_llm_once(session, base_wire)
    except ChatConfigError as exc:
        _rollback_dedupe(cache, dedupe_key)
        return _record_and_return(
            session,
            SimulationKind.AVATAR,
            SimulationResult(
                accepted=False,
                reason="chat_not_configured",
                instruction=instruction,
                coerce_info=coerce_info,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_code": exc.code, "error_message": exc.message},
        )
    except Exception as exc:  # noqa: BLE001
        _rollback_dedupe(cache, dedupe_key)
        return _record_and_return(
            session,
            SimulationKind.AVATAR,
            SimulationResult(
                accepted=False,
                reason="llm_failed",
                instruction=instruction,
                coerce_info=coerce_info,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_type": type(exc).__name__, "error_message": str(exc)},
        )

    now = session.clock.now()
    user_msg = make_message(
        role=ROLE_USER,
        content=memory_note,
        timestamp=now,
        source=SOURCE_INJECT,
    )
    user_result = append_message(session, user_msg, on_violation="coerce")

    assistant_msg = make_message(
        role=ROLE_ASSISTANT,
        content=reply_text,
        timestamp=session.clock.now(),
        source=SOURCE_LLM,
    )
    asst_result = append_message(session, assistant_msg, on_violation="coerce")

    mirror_info = _apply_mirror_to_recent(
        session,
        [user_result.msg, asst_result.msg],
        requested=mirror_to_recent,
    )

    result = SimulationResult(
        accepted=True,
        instruction=instruction,
        memory_pair=[user_result.msg, asst_result.msg],
        persisted=True,
        dedupe_info={
            "hit": False,
            "cache_size": len(cache),
            "cache_size_before": cache_size_before,
            "dedupe_key": dedupe_key,
            "dedupe_rank": dedupe_rank,
        },
        assistant_reply=reply_text,
        coerce_info=coerce_info,
        mirror_to_recent_info=mirror_info,
        elapsed_ms=_elapsed(started),
    )
    return _record_and_return(
        session,
        SimulationKind.AVATAR,
        result,
        detail={
            "interaction_id": normalized.get("interaction_id"),
            "tool_id": normalized["tool_id"],
            "action_id": normalized["action_id"],
            "intensity": normalized["intensity"],
            "reward_drop": normalized.get("reward_drop"),
            "easter_egg": normalized.get("easter_egg"),
            "reply_chars": len(reply_text),
        },
    )


def _rollback_dedupe(cache: _AvatarDedupeCache, dedupe_key: str) -> None:
    """Best-effort remove the reservation we made before a downstream fail.

    The copy-protected helper unconditionally inserts on the accept branch,
    so a post-check failure leaves a "ghost" entry that would reject the
    tester's next retry for 8 s. Dropping the entry restores retry-ability
    without touching the protected region.
    """
    key = str(dedupe_key or "").strip()
    if not key:
        return
    cache._cache.pop(key, None)  # pylint: disable=protected-access


# ─────────────────────────────────────────────────────────────
# Handler: agent callback
# ─────────────────────────────────────────────────────────────


async def simulate_agent_callback(
    session: Session,
    payload: dict[str, Any],
    *,
    mirror_to_recent: bool = False,
) -> SimulationResult:
    """Simulate one agent callback (``AGENT_CALLBACK_NOTIFICATION`` prefix)."""
    started = time.perf_counter()
    from config.prompts_sys import AGENT_CALLBACK_NOTIFICATION

    full_lang, short_lang = _resolve_language(session)

    raw_items = payload.get("callbacks") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        raw_items = []
    items: list[str] = []
    for item in raw_items:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
        elif isinstance(item, dict):
            text = item.get("text") or item.get("summary") or ""
            text = str(text or "").strip()
            if text:
                items.append(text)

    if not items:
        return _record_and_return(
            session,
            SimulationKind.AGENT_CALLBACK,
            SimulationResult(
                accepted=False,
                reason="empty_callbacks",
                elapsed_ms=_elapsed(started),
            ),
            detail={"raw_len": len(raw_items)},
        )

    prefix = AGENT_CALLBACK_NOTIFICATION.get(short_lang, AGENT_CALLBACK_NOTIFICATION["en"])
    instruction = prefix + "\n".join(f"- {t}" for t in items)

    try:
        base_wire = _build_base_wire(session)
    except PreviewNotReady as exc:
        return _record_and_return(
            session,
            SimulationKind.AGENT_CALLBACK,
            SimulationResult(
                accepted=False,
                reason="persona_not_ready",
                instruction=instruction,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_code": exc.code, "error_message": exc.message},
        )
    # instruction 以 user 角色入 wire (对齐主程序 prompt_ephemeral;
    # 详见 simulate_avatar_interaction 里的长 comment).
    base_wire.append({"role": "user", "content": instruction})

    try:
        reply_text = await _invoke_llm_once(session, base_wire)
    except ChatConfigError as exc:
        return _record_and_return(
            session,
            SimulationKind.AGENT_CALLBACK,
            SimulationResult(
                accepted=False,
                reason="chat_not_configured",
                instruction=instruction,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_code": exc.code, "error_message": exc.message},
        )
    except Exception as exc:  # noqa: BLE001
        return _record_and_return(
            session,
            SimulationKind.AGENT_CALLBACK,
            SimulationResult(
                accepted=False,
                reason="llm_failed",
                instruction=instruction,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_type": type(exc).__name__, "error_message": str(exc)},
        )

    assistant_msg = make_message(
        role=ROLE_ASSISTANT,
        content=reply_text,
        timestamp=session.clock.now(),
        source=SOURCE_LLM,
    )
    asst_result = append_message(session, assistant_msg, on_violation="coerce")

    mirror_info = _apply_mirror_to_recent(
        session, [asst_result.msg], requested=mirror_to_recent,
    )

    result = SimulationResult(
        accepted=True,
        instruction=instruction,
        memory_pair=[asst_result.msg],
        persisted=True,
        assistant_reply=reply_text,
        mirror_to_recent_info=mirror_info,
        elapsed_ms=_elapsed(started),
    )
    return _record_and_return(
        session,
        SimulationKind.AGENT_CALLBACK,
        result,
        detail={
            "callback_count": len(items),
            "total_chars": sum(len(t) for t in items),
            "instruction_lang": short_lang,
            "full_language": full_lang,
            "reply_len": len(reply_text),
        },
    )


# ─────────────────────────────────────────────────────────────
# Handler: proactive chat
# ─────────────────────────────────────────────────────────────


_PROACTIVE_KINDS = frozenset({
    "home", "screenshot", "window", "news", "video", "personal", "music",
})


async def simulate_proactive(
    session: Session,
    payload: dict[str, Any],
    *,
    mirror_to_recent: bool = False,
) -> SimulationResult:
    """Simulate one proactive-chat opener via ``get_proactive_chat_prompt``."""
    started = time.perf_counter()
    from config.prompts_proactive import (
        _normalize_prompt_language,
        get_proactive_chat_prompt,
    )

    coerce_info: list[CoerceInfo] = []

    full_lang, _short_lang = _resolve_language(session)
    requested_kind = str((payload or {}).get("kind") or "home").strip().lower()
    if requested_kind in _PROACTIVE_KINDS:
        kind = requested_kind
    else:
        kind = "home"
        coerce_info.append(CoerceInfo(
            field="kind",
            requested=requested_kind,
            applied="home",
            note=(
                "proactive kind 不在合法集合 (home/screenshot/window/news/"
                "video/personal/music), 已回退到 home."
            ),
        ))

    # proactive uses _normalize_prompt_language (zh/en/ja/ko/ru + es/pt→en).
    # We only surface a coerce when an explicitly-requested language silently
    # falls back to en — that is, when the requested short form is not in
    # {zh, en, ja, ko, ru} but the normaliser returned en anyway. Plain
    # zh-CN/zh-TW/zh-Hant all normalise to "zh" with no behaviour surprise,
    # so we don't wave a flag for them.
    normalized_lang = _normalize_prompt_language(full_lang)
    req_lower = full_lang.lower() if full_lang else ""
    _PROACTIVE_NATIVE_PREFIXES = ("zh", "en", "ja", "ko", "ru")
    is_native_prefix = any(req_lower.startswith(p) for p in _PROACTIVE_NATIVE_PREFIXES)
    if req_lower and not is_native_prefix and normalized_lang == "en":
        coerce_info.append(CoerceInfo(
            field="language",
            requested=full_lang,
            applied=normalized_lang,
            note=(
                "proactive 未原生翻译该语言, _normalize_prompt_language 回退 "
                f"到 {normalized_lang!r} (主程序同策略)."
            ),
        ))

    instruction_template = get_proactive_chat_prompt(kind, full_lang)

    # The proactive prompts are big templates parameterised by lanlan_name /
    # master_name / memory_context / recent_chats_section etc. For the P25
    # testbench simulation we let the LLM operate on the live session's
    # base wire (which already carries persona + memory + recent history via
    # build_prompt_bundle), and append the raw template with best-effort
    # placeholder substitution for names only. The other slots (memory,
    # recent) are intentionally left raw — the existing wire already carries
    # equivalent context, so double-injecting would over-count tokens.
    lanlan_name, master_name = _resolve_names(session)
    instruction = (
        instruction_template
        .replace("{lanlan_name}", lanlan_name)
        .replace("{master_name}", master_name)
    )

    try:
        base_wire = _build_base_wire(session)
    except PreviewNotReady as exc:
        return _record_and_return(
            session,
            SimulationKind.PROACTIVE,
            SimulationResult(
                accepted=False,
                reason="persona_not_ready",
                instruction=instruction,
                coerce_info=coerce_info,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_code": exc.code, "error_message": exc.message, "kind": kind},
        )
    # instruction 以 user 角色入 wire (对齐主程序 prompt_ephemeral;
    # 详见 simulate_avatar_interaction 里的长 comment).
    base_wire.append({"role": "user", "content": instruction})

    try:
        reply_text = await _invoke_llm_once(session, base_wire)
    except ChatConfigError as exc:
        return _record_and_return(
            session,
            SimulationKind.PROACTIVE,
            SimulationResult(
                accepted=False,
                reason="chat_not_configured",
                instruction=instruction,
                coerce_info=coerce_info,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_code": exc.code, "error_message": exc.message, "kind": kind},
        )
    except Exception as exc:  # noqa: BLE001
        return _record_and_return(
            session,
            SimulationKind.PROACTIVE,
            SimulationResult(
                accepted=False,
                reason="llm_failed",
                instruction=instruction,
                coerce_info=coerce_info,
                elapsed_ms=_elapsed(started),
            ),
            detail={"error_type": type(exc).__name__, "error_message": str(exc), "kind": kind},
        )

    # Main program proactive semantics: reply == "[PASS]" (case-insensitive,
    # exact match after strip) means "skip this opportunity, don't say
    # anything". We surface via reason="pass_signaled" + accepted=False
    # and DO NOT append to session.messages (§2.1 contract). Non-[PASS]
    # replies always append.
    stripped_reply = reply_text.strip()
    if stripped_reply.upper() == "[PASS]":
        result = SimulationResult(
            accepted=False,
            reason="pass_signaled",
            instruction=instruction,
            assistant_reply=reply_text,
            coerce_info=coerce_info,
            mirror_to_recent_info=MirrorToRecentInfo(requested=mirror_to_recent, applied=False, fallback_reason=("proactive LLM 返回 [PASS], 无消息可 mirror" if mirror_to_recent else None)),
            elapsed_ms=_elapsed(started),
        )
        return _record_and_return(
            session,
            SimulationKind.PROACTIVE,
            result,
            detail={
                "kind": kind,
                "lang": normalized_lang,
                "pass_signaled": True,
                "reply_len": len(reply_text),
            },
        )

    assistant_msg = make_message(
        role=ROLE_ASSISTANT,
        content=reply_text,
        timestamp=session.clock.now(),
        source=SOURCE_LLM,
    )
    asst_result = append_message(session, assistant_msg, on_violation="coerce")

    mirror_info = _apply_mirror_to_recent(
        session, [asst_result.msg], requested=mirror_to_recent,
    )

    result = SimulationResult(
        accepted=True,
        instruction=instruction,
        memory_pair=[asst_result.msg],
        persisted=True,
        assistant_reply=reply_text,
        coerce_info=coerce_info,
        mirror_to_recent_info=mirror_info,
        elapsed_ms=_elapsed(started),
    )
    return _record_and_return(
        session,
        SimulationKind.PROACTIVE,
        result,
        detail={
            "kind": kind,
            "lang": normalized_lang,
            "pass_signaled": False,
            "reply_len": len(reply_text),
        },
    )


# ─────────────────────────────────────────────────────────────
# Diagnostics recording
# ─────────────────────────────────────────────────────────────


_KIND_TO_OP: dict[SimulationKind, DiagnosticsOp] = {
    SimulationKind.AVATAR: DiagnosticsOp.AVATAR_INTERACTION_SIMULATED,
    SimulationKind.AGENT_CALLBACK: DiagnosticsOp.AGENT_CALLBACK_SIMULATED,
    SimulationKind.PROACTIVE: DiagnosticsOp.PROACTIVE_SIMULATED,
}


def _record_and_return(
    session: Session,
    kind: SimulationKind,
    result: SimulationResult,
    *,
    detail: Optional[dict[str, Any]] = None,
) -> SimulationResult:
    """Single exit point — record a diagnostics op + return the result.

    Funnelling every handler return through here keeps the op naming
    convention and ``detail`` shape consistent; future handlers only need
    to hand us a ``detail`` dict.
    """
    op = _KIND_TO_OP[kind]
    mirror_info = result.mirror_to_recent_info
    merged_detail: dict[str, Any] = {
        "accepted": result.accepted,
        "reason": result.reason,
        "persisted": result.persisted,
        "elapsed_ms": result.elapsed_ms,
        "mirror_to_recent": {
            "requested": mirror_info.requested,
            "applied": mirror_info.applied,
            "fallback_reason": mirror_info.fallback_reason,
        },
        "coerce_count": len(result.coerce_info),
    }
    if result.dedupe_info is not None:
        merged_detail["dedupe_hit"] = bool(result.dedupe_info.get("hit"))
    if detail:
        merged_detail.update(detail)

    try:
        # avatar dedupe hit is explicitly logged as info — not an error; we
        # still want the op in the ring for event-density inspection.
        diagnostics_store.record_internal(
            op,
            f"外部事件仿真 kind={kind.value} accepted={result.accepted} reason={result.reason or '-'}",
            level="info",
            session_id=getattr(session, "id", None),
            detail=merged_detail,
        )
    except Exception:  # noqa: BLE001
        python_logger().exception(
            "external_events: record_internal(%s) failed (non-fatal)", op.value,
        )

    try:
        session.logger.log_sync(
            f"external_event.{kind.value}",
            payload={
                "accepted": result.accepted,
                "reason": result.reason,
                "persisted": result.persisted,
                "elapsed_ms": result.elapsed_ms,
                "mirror_to_recent_info": asdict(mirror_info),
                "coerce_count": len(result.coerce_info),
                "instruction_chars": len(result.instruction),
                "reply_chars": len(result.assistant_reply),
                **(detail or {}),
            },
        )
    except Exception:  # noqa: BLE001
        python_logger().exception(
            "external_events: session.logger.log_sync failed (non-fatal)",
        )

    return result


# ─────────────────────────────────────────────────────────────
# Misc
# ─────────────────────────────────────────────────────────────


def _elapsed(started_perf: float) -> int:
    return int((time.perf_counter() - started_perf) * 1000)


__all__ = [
    "CoerceInfo",
    "MirrorToRecentInfo",
    "SimulationKind",
    "SimulationResult",
    "discard_session_caches",
    "peek_dedupe_info",
    "reset_dedupe",
    "simulate_agent_callback",
    "simulate_avatar_interaction",
    "simulate_proactive",
]
