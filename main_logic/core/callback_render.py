# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Pure rendering helpers for agent-task callbacks and voice-swap injections.

Split out of the former single-file ``main_logic/core.py`` as a pure move (no
behavior change): these helpers render agent_task_callback payloads and
voice-mode ``pending_extra_replies`` into LLM prompt strings. The package
``__init__`` re-exports every name defined here, so existing
``main_logic.core.<name>`` imports keep working (plugins import
``apply_role_placeholders`` through that path).
"""
from config.prompts.prompts_sys import (
    _loc,
    SYSTEM_NOTIFICATION_TASK_ACTIVE,
    SYSTEM_NOTIFICATION_TASK_PASSIVE,
    SYSTEM_NOTIFICATION_EVENT_ACTIVE,
    SYSTEM_NOTIFICATION_EVENT_PASSIVE,
    SOURCE_DESCRIPTORS,
    TASK_STATUS_PHRASES,
    TASK_ACTION_PHRASES,
    CONTEXT_SUMMARY_TASK_HEADER, CONTEXT_SUMMARY_TASK_FOOTER,
    CONTEXT_SUMMARY_EVENT_HEADER, CONTEXT_SUMMARY_EVENT_FOOTER,
    RESULT_PARSER_PHRASES,
)

from ._shared import logger


# 内部 item 渲染时的视觉标记。状态信息已在外层 SYSTEM_NOTIFICATION_TASK_ACTIVE
# 表达，emoji 仅作快速视觉识别用。
_STATUS_EMOJI = {
    "completed": "✅",
    "partial": "⚠️",
    "blocked": "⚠️",
    "failed": "❌",
    "cancelled": "🚫",
}


def _format_callback_source(cb: dict, lang: str) -> str:
    """Render an agent_task_callback's source as user-facing text in ``lang``.

    Reads ``cb["source_kind"]`` (one of SOURCE_DESCRIPTORS keys) and
    ``cb["source_name"]`` (free-form string used as ``{name}`` slot). Falls
    back to the ``unknown`` descriptor for missing/unrecognized kinds.
    """
    kind = (cb.get("source_kind") or "unknown").strip()
    descriptor = SOURCE_DESCRIPTORS.get(kind) or SOURCE_DESCRIPTORS["unknown"]
    name = (cb.get("source_name") or "").strip()
    return _loc(descriptor, lang).format(name=name)


def apply_role_placeholders(
    text: str,
    *,
    lanlan_name: str = "",
    master_name: str = "",
) -> str:
    """Substitute ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholders in
    plugin-supplied text at the LLM-injection boundary.

    Plugin authors don't know which ``LLMSessionManager`` (and therefore which
    ``master_name`` / ``lanlan_name`` pair) the text will route to — that's a
    host-side visibility decision. So the canonical contract is:

        plugin writes ``"Report to {MASTER_NAME}…"`` →
        host expands at the injection site, per session.

    Uses ``str.replace`` rather than ``str.format`` so that other braces in
    the text (JSON fragments, code snippets, user content containing stray
    ``{``) don't raise ``KeyError``. Empty names short-circuit — the
    placeholder is left in place rather than replaced with ``""``, on the
    theory that the literal token is less misleading than an empty hole.

    This is the SINGLE source of truth for the placeholder contract. New
    plugin-text injection sites should funnel through this helper.
    """
    if not text:
        return text
    if isinstance(master_name, str) and master_name:
        text = text.replace("{MASTER_NAME}", master_name)
    if isinstance(lanlan_name, str) and lanlan_name:
        text = text.replace("{LANLAN_NAME}", lanlan_name)
    return text


def _render_callback_inner_item(
    cb: dict,
    lang: str,
    *,
    lanlan_name: str = "",
    master_name: str = "",
) -> str:
    """Render one callback as a single inline string for the LLM prompt.

    Returns ``""`` when there is genuinely nothing to convey (both summary
    and detail empty); the caller can then drop the line and rely on the
    outer header alone to express that something happened.

    Plugin-supplied ``summary``/``detail`` may contain ``{MASTER_NAME}`` /
    ``{LANLAN_NAME}`` placeholders; see :func:`apply_role_placeholders`.
    """
    summary = apply_role_placeholders(
        (cb.get("summary") or "").strip(),
        lanlan_name=lanlan_name, master_name=master_name,
    )
    detail = apply_role_placeholders(
        (cb.get("detail") or "").strip(),
        lanlan_name=lanlan_name, master_name=master_name,
    )
    text = summary or detail
    if not text:
        return ""
    status = cb.get("status") or "completed"
    emoji = _STATUS_EMOJI.get(status, "•")
    line = f"{emoji} {text}"
    if summary and detail and detail != summary and len(detail) > len(summary):
        label = _loc(RESULT_PARSER_PHRASES["detail_result"], lang)
        line += f"\n{label}{detail}"
    return line


def _build_callback_instruction(
    callbacks,
    *,
    lang: str,
    lanlan_name: str,
    master_name: str,
    passive: bool = False,
) -> str:
    """Render a list of agent_task_callbacks into the LLM injection string.

    Each callback carries an ``origin`` tag stamped by the host at the
    EventBus → callback boundary:
      - ``"task_result"`` — real task completion (agent_server._emit_task_result),
        e.g. Computer Use / Browser Use / plugin entry / MCP tool result.
      - ``"event"`` — plugin push_message stream (proactive_bridge),
        e.g. danmaku / gift / external notification.

    Plugin authors cannot set ``origin``; it is derived structurally from
    which SDK method they called (``finish()`` vs ``push_message()``) by
    way of the event_type the upstream producer emitted.

    Two axes (origin × passive) pick one of four outer templates:

    +--------------+----------------------+-----------------------------+
    | origin       | active (proactive)   | passive                     |
    +==============+======================+=============================+
    | task_result  | TASK_ACTIVE          | TASK_PASSIVE                |
    |              | ("done, report it")  | ("task result")             |
    +--------------+----------------------+-----------------------------+
    | event        | EVENT_ACTIVE         | EVENT_PASSIVE               |
    |              | ("new msg, respond") | ("message")                 |
    +--------------+----------------------+-----------------------------+

    Unknown origin defaults to ``"event"`` + warning. Rationale: rather
    have the AI naturally react than fabricate "I completed a task".

    Callbacks are grouped by (passive, origin, status, source) so each
    group can pick the right outer template and (for task_result+active)
    slot in the right status/action phrases. Event templates ignore
    status/action — the concept doesn't apply to passive event streams.
    """
    if not callbacks:
        return ""
    from collections import OrderedDict

    grouped: "OrderedDict[tuple, list]" = OrderedDict()
    for cb in callbacks:
        # passive=True call = drain path; treat all as passive regardless
        # of per-callback delivery_mode.
        cb_passive = passive or (cb.get("delivery_mode") == "passive")
        origin = cb.get("origin")
        if origin not in ("task_result", "event"):
            if origin:
                logger.warning(
                    "[callback_instruction] unknown origin=%r, falling back to 'event'; "
                    "source=%s/%s",
                    origin, cb.get("source_kind"), cb.get("source_name"),
                )
            origin = "event"
        key = (
            cb_passive,
            origin,
            cb.get("status") or "completed",
            cb.get("source_kind") or "unknown",
            (cb.get("source_name") or ""),
        )
        grouped.setdefault(key, []).append(cb)

    parts: list[str] = []
    for (cb_passive, origin, status, _src_kind, _src_name), cbs in grouped.items():
        source_text = _format_callback_source(cbs[0], lang)
        if origin == "task_result":
            if cb_passive:
                header = _loc(SYSTEM_NOTIFICATION_TASK_PASSIVE, lang).format(source=source_text)
            else:
                status_phrase = _loc(
                    TASK_STATUS_PHRASES.get(status) or TASK_STATUS_PHRASES["completed"],
                    lang,
                )
                action_phrase = _loc(
                    TASK_ACTION_PHRASES.get(status) or TASK_ACTION_PHRASES["completed"],
                    lang,
                )
                header = _loc(SYSTEM_NOTIFICATION_TASK_ACTIVE, lang).format(
                    source=source_text,
                    status_phrase=status_phrase,
                    action_phrase=action_phrase,
                    name=lanlan_name,
                    master=master_name,
                )
        else:  # origin == "event"
            if cb_passive:
                header = _loc(SYSTEM_NOTIFICATION_EVENT_PASSIVE, lang).format(source=source_text)
            else:
                header = _loc(SYSTEM_NOTIFICATION_EVENT_ACTIVE, lang).format(
                    source=source_text,
                    name=lanlan_name,
                    master=master_name,
                )
        items = [
            _render_callback_inner_item(
                cb, lang, lanlan_name=lanlan_name, master_name=master_name,
            )
            for cb in cbs
        ]
        items = [s for s in items if s]
        if items:
            parts.append(header + "\n".join(items))
        else:
            # No item text — outer header alone (e.g. "task X failed") still
            # tells the AI that something happened. Strip trailing newline so
            # the joined output is clean.
            parts.append(header.rstrip())
    rendered = "\n\n".join(parts)
    # Total input budget: many callbacks accumulating must not blow up the turn.
    from utils.tokenize import truncate_to_tokens
    from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
    return truncate_to_tokens(rendered, AGENT_CALLBACK_TOTAL_MAX_TOKENS)


def _format_voice_swap_item(
    entry: dict,
    lang: str,
    *,
    lanlan_name: str = "",
    master_name: str = "",
) -> str:
    """Render a single voice-mode pending_extra_replies entry to a bulleted
    line for the hot-swap injection.

    Priority: ``summary`` → ``detail`` → synthesized "{status_phrase} from
    {source}[: error_message]" placeholder. The placeholder path matters for
    failure callbacks whose body is empty — without it, header information
    like "execution failed / from plugin X / Connection refused" would be
    silently dropped (the voice-mode equivalent of the header-only branch in
    ``_build_callback_instruction``).

    Plugin-supplied ``summary``/``detail`` may contain ``{MASTER_NAME}`` /
    ``{LANLAN_NAME}`` placeholders; see :func:`apply_role_placeholders`. The
    synthesized placeholder fallback uses host-side localized phrases so it
    needs no role substitution.

    Returns ``""`` when the entry is genuinely empty (no body, no error, and
    a benign ``completed`` status) — caller filters those out.
    """
    summary = apply_role_placeholders(
        (entry.get("summary") or "").strip(),
        lanlan_name=lanlan_name, master_name=master_name,
    )
    detail = apply_role_placeholders(
        (entry.get("detail") or "").strip(),
        lanlan_name=lanlan_name, master_name=master_name,
    )
    text = summary or detail
    status = entry.get("status") or "completed"
    emoji = _STATUS_EMOJI.get(status, "•")

    if text:
        return f"- {emoji} {text}"

    # No body text — synthesize from header info so the failure status
    # doesn't disappear silently.
    error_message = (entry.get("error_message") or "").strip()
    source_name = (entry.get("source_name") or "").strip()
    if not error_message and not source_name and status == "completed":
        # Truly nothing to convey; drop. (enqueue_agent_callback already
        # filters these out, but be defensive against legacy entries.)
        return ""

    source_text = _format_callback_source(entry, lang)
    status_phrase = _loc(
        TASK_STATUS_PHRASES.get(status) or TASK_STATUS_PHRASES["completed"],
        lang,
    )
    line = f"- {emoji} {source_text} {status_phrase}"
    if error_message:
        line += f"：{error_message}"
    return line


def _render_pending_extra_replies_by_origin(
    entries,
    *,
    lang: str,
    lanlan_name: str,
    master_name: str,
) -> str:
    """Render voice-mode ``pending_extra_replies`` into the hot-swap injection
    string, grouped by ``origin``.

    Each entry should be a structured dict with at least ``origin``;
    ``summary``/``detail``/``status``/``source_kind``/``source_name``/
    ``error_message`` are consumed by :func:`_format_voice_swap_item`. Legacy
    plain-string entries (pre-migration code paths) are tolerated and
    treated as ``origin="event"`` event-stream content — the safer default,
    since the "report the result of a previously executed task" framing on
    what may actually be a push event is the bug this refactor fixes.

    Returns a single string suitable for appending to ``final_prime_text``.
    Order: task block first (if any), then event block — matches the original
    single-block placement where everything followed the cache dump.
    """
    if not entries:
        return ""

    task_entries: list[dict] = []
    event_entries: list[dict] = []
    for entry in entries:
        if isinstance(entry, dict):
            normalized = dict(entry)
            origin = normalized.get("origin")
            if origin not in ("task_result", "event"):
                normalized["origin"] = "event"  # fail-safe
                origin = "event"
            if origin == "task_result":
                task_entries.append(normalized)
            else:
                event_entries.append(normalized)
        elif isinstance(entry, str):
            stripped = entry.strip()
            if stripped:
                event_entries.append({
                    "origin": "event",
                    "summary": stripped,
                    "detail": "",
                    "status": "completed",
                    "source_kind": "unknown",
                    "source_name": "",
                    "error_message": "",
                })

    blocks: list[str] = []
    if task_entries:
        items = [
            _format_voice_swap_item(e, lang, lanlan_name=lanlan_name, master_name=master_name)
            for e in task_entries
        ]
        items = [s for s in items if s]
        if items:
            blocks.append(
                _loc(CONTEXT_SUMMARY_TASK_HEADER, lang).format(name=lanlan_name, master=master_name)
                + "\n".join(items)
                + _loc(CONTEXT_SUMMARY_TASK_FOOTER, lang)
            )
    if event_entries:
        items = [
            _format_voice_swap_item(e, lang, lanlan_name=lanlan_name, master_name=master_name)
            for e in event_entries
        ]
        items = [s for s in items if s]
        if items:
            blocks.append(
                _loc(CONTEXT_SUMMARY_EVENT_HEADER, lang).format(name=lanlan_name, master=master_name)
                + "\n".join(items)
                + _loc(CONTEXT_SUMMARY_EVENT_FOOTER, lang)
            )
    rendered = "".join(blocks)
    # Total input budget for the voice hot-swap injection (mirror of the
    # text-mode cap in _build_callback_instruction). Backstop only — callers
    # should pre-select within budget via _select_callbacks_within_token_budget
    # so whole callbacks are never silently dropped after a successful ack.
    from utils.tokenize import truncate_to_tokens
    from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
    return truncate_to_tokens(rendered, AGENT_CALLBACK_TOTAL_MAX_TOKENS)


def _select_callbacks_within_token_budget(callbacks, total_budget):
    """Greedily take the oldest prefix of ``callbacks`` whose cumulative
    summary/detail token count stays within ``total_budget``.

    Returns ``(selected, deferred)``. Always selects at least one item so the
    queue makes forward progress (each item is already per-item capped at
    enqueue). The point: a caller that acks + clears must ack/clear only the
    *selected* items and re-queue ``deferred`` for the next turn — otherwise
    callbacks beyond the cap would be acked as delivered but never reach the
    model (see PR review)."""
    from utils.tokenize import count_tokens
    # Per-item overhead for the emoji/bullet, the per-group outer header, and the
    # template wrapper that the renderer adds around the body. Over-counting is
    # the SAFE direction: we select fewer, so the rendered instruction stays
    # under budget and the builder's backstop truncation never cuts an already
    # selected (and acked) callback.
    _ITEM_OVERHEAD_TOKENS = 48
    selected: list = []
    used = 0
    for i, cb in enumerate(callbacks):
        if isinstance(cb, dict):
            # Count every field the renderer may emit — body line (summary or
            # detail) plus the error/source fallback line — not just summary.
            t = (
                count_tokens(cb.get("summary") or "")
                + count_tokens(cb.get("detail") or "")
                + count_tokens(cb.get("error_message") or "")
                + count_tokens(cb.get("source_name") or "")
                + _ITEM_OVERHEAD_TOKENS
            )
        else:
            t = count_tokens(str(cb)) + _ITEM_OVERHEAD_TOKENS
        if selected and used + t > total_budget:
            return selected, list(callbacks[i:])
        selected.append(cb)
        used += t
    return selected, []
