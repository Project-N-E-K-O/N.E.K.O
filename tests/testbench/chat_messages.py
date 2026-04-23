"""Testbench chat message schema helpers (P09).

:attr:`Session.messages` is the **authoritative record** of the conversation
for the Testbench UI. Upstream's ``recent.json`` (managed by
:class:`memory.recent.CompressedRecentHistoryManager`) only stores the
"recent conversation" slice that eventually lands in the system prompt's
``recent_history`` section — it carries no ids, no source tags and no
timestamps. The Testbench instead keeps a richer per-session list so that
the UI can:

* Render every single turn (user / assistant / injected system) in order.
* Show virtual timestamps and time separators (``— 2h later —``).
* Edit / delete / retime any message (``/api/chat/messages/{id}*``).
* "Re-run from here" (truncate + resend).
* Attribute each message to its source: manual typing, SimUser AI, a
  scripted turn, or a ``POST /chat/inject_system`` injection.

To keep snapshots / save-load / wire conversion simple we store messages
as **plain dicts** (not dataclasses) with a stable field shape. This
module is the single place that knows that shape.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import uuid4

# ── canonical field values ──────────────────────────────────────────

#: Role must be one of the OpenAI-standard strings — the prompt builder
#: passes them straight into ``wire_messages`` and upstream
#: :mod:`utils.llm_client` maps them to HumanMessage/AIMessage/SystemMessage.
ROLE_USER: Final[str] = "user"
ROLE_ASSISTANT: Final[str] = "assistant"
ROLE_SYSTEM: Final[str] = "system"
ALLOWED_ROLES: Final[frozenset[str]] = frozenset({ROLE_USER, ROLE_ASSISTANT, ROLE_SYSTEM})

#: ``source`` is a Testbench-only audit tag, never sent to the LLM. It
#: tells the UI *who/what* produced the message so the tester can tell
#: "manually typed user line" from "SimUser generated user line" etc.
SOURCE_MANUAL: Final[str] = "manual"          # User typed it into the composer.
SOURCE_INJECT: Final[str] = "inject"          # /chat/inject_system (mid-conversation system note).
SOURCE_LLM: Final[str] = "llm"                # Target chat LLM reply.
SOURCE_SIMUSER: Final[str] = "simuser"        # Reserved for P11 (假想用户 AI).
SOURCE_SCRIPT: Final[str] = "script"          # Reserved for P12 (脚本化对话).
SOURCE_AUTO: Final[str] = "auto"              # Reserved for P13 (双 AI 自动对话).
# P25 Day 2 polish r5 — pseudo-message inserted by external-event simulators
# to mark "tester triggered a non-dialog event here" in the chat timeline.
# The banner is a VISUAL-ONLY marker rendered like the auto-generated
# ``time-sep`` bars (". X min later .") but anchored to a real message so
# it survives across refreshes / session save-load. Crucially:
#   * the banner must NOT be sent to the LLM — ``prompt_builder.build_
#     prompt_bundle`` filters ``source == external_event_banner`` before
#     building ``wire_messages`` so the LLM never sees the meta-note;
#   * the banner can be either ``role=system`` (legacy pattern) or
#     ``role=user`` (neutral pattern) — filter is source-driven, not
#     role-driven, to avoid accidentally dropping real system injects.
SOURCE_EXTERNAL_EVENT_BANNER: Final[str] = "external_event_banner"
ALLOWED_SOURCES: Final[frozenset[str]] = frozenset({
    SOURCE_MANUAL, SOURCE_INJECT, SOURCE_LLM,
    SOURCE_SIMUSER, SOURCE_SCRIPT, SOURCE_AUTO,
    SOURCE_EXTERNAL_EVENT_BANNER,
})


def new_message_id() -> str:
    """Return a short hex id unique within a session's lifetime.

    12 hex chars (48 bits) is plenty for the number of messages a single
    testing session produces; keeping it short makes debug logs and URL
    paths (``/api/chat/messages/{id}``) readable.
    """
    return uuid4().hex[:12]


def make_message(
    *,
    role: str,
    content: str,
    timestamp: datetime | str,
    source: str = SOURCE_MANUAL,
    reference_content: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Build a validated message dict ready to append to ``session.messages``.

    Parameters
    ----------
    role: one of :data:`ALLOWED_ROLES`.
    content: the message text (always a string; images/multimodal are out of
        scope for P09 and will be introduced as a richer payload later).
    timestamp: virtual time at which the message "happened". Accepted as a
        :class:`datetime` (converted to ISO second-precision string) or as a
        pre-formatted string (left as-is so callers can feed
        ``clock.now().isoformat(...)`` directly).
    source: one of :data:`ALLOWED_SOURCES`. Defaults to
        :data:`SOURCE_MANUAL` since most early-phase users come from the
        composer form.
    reference_content: optional "expected assistant response" used by
        scripted / evaluation flows. Not shown in P09 UI but kept here so
        later phases can populate it without a schema migration.
    message_id: override auto-generated id (used on load/import).
    """
    if role not in ALLOWED_ROLES:
        raise ValueError(f"unsupported role: {role!r}")
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"unsupported source: {source!r}")
    if isinstance(timestamp, datetime):
        ts = timestamp.replace(microsecond=0).isoformat()
    else:
        ts = str(timestamp)
    return {
        "id": message_id or new_message_id(),
        "role": role,
        "content": content,
        "timestamp": ts,
        "source": source,
        # ``reference_content`` stays as an explicit key (not ``None`` filtered)
        # so the serialized shape is stable — UI can test ``ref !== undefined``
        # rather than guessing whether the field exists.
        "reference_content": reference_content,
    }


def find_message_index(
    messages: list[dict[str, Any]], message_id: str,
) -> int:
    """Return the index of the message with ``id == message_id``, or -1."""
    for i, m in enumerate(messages):
        if m.get("id") == message_id:
            return i
    return -1


def _parse_stored_ts(msg: dict[str, Any]) -> datetime | None:
    """Best-effort parse of a stored ``timestamp`` field.

    Returns ``None`` when the field is missing, empty, or malformed — the
    ordering check is skipped in that case rather than erroring, because
    a legacy / half-written message shouldn't block a legitimate edit.
    """
    raw = msg.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _compat_ts(a: datetime, b: datetime) -> tuple[datetime, datetime]:
    """Strip tzinfo on one side when the two datetimes disagree on tz-awareness.

    Testbench sessions have historically mixed naive clock ticks with
    ISO strings that sometimes carry an offset; ``<`` / ``>`` would raise
    ``TypeError`` on a direct compare. We don't actually support
    multi-timezone conversations in the Testbench, so the tz component is
    informational only — normalise by dropping it when it would block the
    comparison.
    """
    if (a.tzinfo is None) == (b.tzinfo is None):
        return a, b
    return a.replace(tzinfo=None), b.replace(tzinfo=None)


def check_timestamp_monotonic(
    messages: list[dict[str, Any]],
    idx: int,
    new_ts: datetime,
) -> tuple[str, str] | None:
    """Ensure placing ``new_ts`` at row ``idx`` preserves non-decreasing order.

    ``idx`` is the **target slot** the new timestamp is supposed to occupy:

    * For :func:`add_message` (append at tail) pass ``len(messages)`` —
      the helper compares only against the current last row.
    * For :func:`patch_message_timestamp` (in-place retime) pass the
      row's current index — the helper compares against its predecessor
      and successor, skipping the row itself (its old timestamp is
      irrelevant since we're replacing it).

    Returns ``None`` when ordering is preserved, otherwise a
    ``(error_code, human_detail)`` tuple the router maps to HTTP 422.

    Equality is permitted (``<=``) because two back-to-back messages
    sharing a second boundary is a real scenario (streaming chunks that
    straddle the wall-clock tick, scripted dialogs with rapid turns).
    Only *strictly earlier than the predecessor* or *strictly later than
    the successor* trips the check — matching the UI invariant that
    ``ChatStream`` renders messages in list order and derives the
    "— 2h later —" separator from neighbour deltas, both of which break
    when the list is out of order.
    """
    prev_idx = idx - 1
    if prev_idx >= 0:
        prev_ts = _parse_stored_ts(messages[prev_idx])
        if prev_ts is not None:
            a, b = _compat_ts(new_ts, prev_ts)
            if a < b:
                return (
                    "TimestampOutOfOrder",
                    (
                        f"新时间戳 {new_ts.isoformat()} 早于上一条消息的时间戳 "
                        f"{messages[prev_idx].get('timestamp')} (index {prev_idx})。"
                        " 消息列表必须按时间戳单调不减排序, 否则会话时间线"
                        " / 时间流逝提示 / UI 顺序都会错乱。请调大新时间戳,"
                        " 或先修改上一条消息的时间戳。"
                    ),
                )

    next_idx = idx + 1
    if next_idx < len(messages):
        next_ts = _parse_stored_ts(messages[next_idx])
        if next_ts is not None:
            a, b = _compat_ts(new_ts, next_ts)
            if a > b:
                return (
                    "TimestampOutOfOrder",
                    (
                        f"新时间戳 {new_ts.isoformat()} 晚于下一条消息的时间戳 "
                        f"{messages[next_idx].get('timestamp')} (index {next_idx})。"
                        " 消息列表必须按时间戳单调不减排序。请调小新时间戳,"
                        " 或先修改下一条消息的时间戳。"
                    ),
                )

    return None


__all__ = [
    "ALLOWED_ROLES",
    "ALLOWED_SOURCES",
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_USER",
    "SOURCE_AUTO",
    "SOURCE_EXTERNAL_EVENT_BANNER",
    "SOURCE_INJECT",
    "SOURCE_LLM",
    "SOURCE_MANUAL",
    "SOURCE_SCRIPT",
    "SOURCE_SIMUSER",
    "check_timestamp_monotonic",
    "find_message_index",
    "make_message",
    "new_message_id",
]
