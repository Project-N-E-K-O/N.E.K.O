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
"""Message-level cache-control injection helpers."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from .messages import _TYPE_TO_ROLE, message_metadata

_CACHE_CONTROL_EPHEMERAL = {"type": "ephemeral"}

_TEXT_PART_TYPES = ("text", "input_text", "output_text")

CACHE_STATIC_PREFIX_LEN_KEY = "cache_static_prefix_len"
CACHE_EPHEMERAL_KEY = "cache_ephemeral"


def _message_role(msg: Any) -> str:
    if isinstance(msg, dict):
        if "role" in msg:
            return str(msg.get("role") or "")
        if "type" in msg and "data" in msg:
            t = str(msg.get("type") or "")
            return _TYPE_TO_ROLE.get(t, t)
        return ""
    role = getattr(msg, "role", None)
    if role is not None:
        return str(role)
    t = getattr(msg, "type", None)
    if t is not None:
        return _TYPE_TO_ROLE.get(str(t), str(t))
    return ""


def _extract_cache_hints(messages: Any) -> tuple[dict[int, int], set[int]]:
    """Extract prefix-split offsets and ephemeral indices from message metadata.

    Returns ``(prefix_splits, ephemeral_indices)`` before ``_normalize_messages``
    strips internal ``metadata`` fields. ``prefix_splits`` only records entries
    inside the leading contiguous ``system`` run; ``ephemeral_indices`` records
    messages marked ``cache_ephemeral`` or ``turn_context == "ephemeral"``.
    """
    if isinstance(messages, str):
        return {}, set()
    prefix_splits: dict[int, int] = {}
    ephemeral_indices: set[int] = set()
    in_leading_system = True
    try:
        iterator = enumerate(messages)
    except TypeError:
        return {}, set()
    for i, msg in iterator:
        role = _message_role(msg)
        if role != "system":
            in_leading_system = False
        meta = message_metadata(msg)
        if not meta:
            continue
        if in_leading_system and CACHE_STATIC_PREFIX_LEN_KEY in meta:
            val = meta.get(CACHE_STATIC_PREFIX_LEN_KEY)
            if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                prefix_splits[i] = val
        if meta.get(CACHE_EPHEMERAL_KEY) or meta.get("turn_context") == "ephemeral":
            ephemeral_indices.add(i)
    return prefix_splits, ephemeral_indices


def _split_system_prefix_blocks(
    messages: list,
    prefix_splits: dict[int, int],
    ephemeral_indices: set[int] | None = None,
) -> tuple[list, set[int], set[int]]:
    """Split marked leading system strings into two consecutive ``system`` messages.

    DashScope applies ``cache_control`` at message boundaries rather than between
    intra-message content parts, so splitting a marked ``system`` message into
    ``[static_system, dynamic_system]`` allows BP1 to isolate the stable prefix.

    Returns ``(messages_or_copy, split_static_indices, remapped_ephemeral_indices)``.
    Leaves the input untouched when no valid split applies.
    """
    orig_ephemeral = set(ephemeral_indices or ())
    if not messages or not prefix_splits:
        return messages, set(), orig_ephemeral
    out: list = []
    split_static_indices: set[int] = set()
    new_ephemeral: set[int] = set()
    for idx, msg in enumerate(messages):
        split_pos = prefix_splits.get(idx)
        content = msg.get("content") if isinstance(msg, dict) else None
        if (
            split_pos is not None
            and isinstance(msg, dict)
            and msg.get("role") == "system"
            and isinstance(content, str)
            and 0 < split_pos < len(content)
        ):
            static_text = content[:split_pos]
            dynamic_text = content[split_pos:]
            if static_text and dynamic_text:
                static_idx = len(out)
                out.append({**msg, "content": static_text})
                out.append({"role": "system", "content": dynamic_text})
                split_static_indices.add(static_idx)
                if idx in orig_ephemeral:
                    new_ephemeral.add(static_idx)
                continue
        out_idx = len(out)
        out.append(msg)
        if idx in orig_ephemeral:
            new_ephemeral.add(out_idx)
    if not split_static_indices:
        return messages, set(), orig_ephemeral
    return out, split_static_indices, new_ephemeral


def _has_cache_control_at_target(message: dict) -> bool:
    """Return True when the message's last text part already carries a cache marker."""
    content = message.get("content")
    if not isinstance(content, list):
        return False
    idx = next(
        (
            i
            for i in range(len(content) - 1, -1, -1)
            if isinstance(content[i], dict) and content[i].get("type") in _TEXT_PART_TYPES
        ),
        None,
    )
    if idx is None:
        return False
    return "cache_control" in content[idx]


def _attach_cache_control(message: dict) -> dict | None:
    """Return a NEW message dict with ``cache_control: {"type": "ephemeral"}``
    attached to its last text content block, or ``None`` if there's nothing markable.

    Anthropic (and Anthropic-compat / DashScope gateways speaking OpenAI wire
    format) carry the cache breakpoint on a content *block*, not on the message
    itself. A plain-string ``content`` is promoted to a single text part so the
    marker has somewhere to live; an existing parts list gets the marker on its
    last text part. Defensive copy throughout — never mutates the input.
    """
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return None
        part = {"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
        return {**message, "content": [part]}
    if isinstance(content, list):
        idx = next(
            (i for i in range(len(content) - 1, -1, -1)
             if isinstance(content[i], dict) and content[i].get("type") in _TEXT_PART_TYPES),
            None,
        )
        if idx is None:
            return None
        # Idempotent: if the breakpoint part already carries a marker, leave it
        # (and any richer TTL it may hold) untouched rather than clobbering it.
        if "cache_control" in content[idx]:
            return None
        new_parts = list(content)
        new_parts[idx] = {**new_parts[idx], "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
        return {**message, "content": new_parts}
    return None


def _inject_cache_control(
    messages: list,
    *,
    cache_conversation: bool = False,
    split_system_indices: set[int] | None = None,
    ephemeral_indices: set[int] | None = None,
) -> list:
    """Return a NEW messages list with body-level ``cache_control`` breakpoints.

    Breakpoint 1 anchors on the most stable prefix — either the split static
    ``system`` message recorded in ``split_system_indices`` (leaving trailing
    dynamic ``system`` messages unmarked) or the END of the leading contiguous
    run of ``system`` messages. When ``cache_conversation`` is True, breakpoint 2
    additionally marks the last non-ephemeral ``user`` message after the leading
    system prefix (never promoting ``tool`` or ``assistant`` messages to
    content-parts lists). When there is no leading system run, falls back to the
    last non-ephemeral user message (when ``cache_conversation``) or last
    non-ephemeral message.

    Used only when ``ChatOpenAI.enable_cache_control`` is True (activated by
    ``create_chat_llm`` for providers with ``requires_body_flag`` when a caller
    opts in via ``explicit_cache`` / ``cache_conversation``). Defensive copy —
    never mutates inputs, idempotent on already-marked messages, and returns
    ``messages`` itself when no modifications are needed.
    """
    if not messages:
        return messages
    system_end: int | None = None
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "system":
            system_end = i
        else:
            break

    ephemeral = ephemeral_indices or set()
    split_sys = split_system_indices or set()
    out: list | None = None

    if system_end is not None:
        split_in_run = [i for i in split_sys if 0 <= i <= system_end]
        bp1_idx = max(split_in_run) if split_in_run else system_end
        chosen = messages[bp1_idx]
        if isinstance(chosen, dict):
            marked = _attach_cache_control(chosen)
            if marked is not None:
                out = list(messages)
                out[bp1_idx] = marked
        if cache_conversation:
            for idx in range(len(messages) - 1, system_end, -1):
                if idx in ephemeral:
                    continue
                msg = messages[idx]
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                if _has_cache_control_at_target(msg):
                    break
                marked = _attach_cache_control(msg)
                if marked is not None:
                    if out is None:
                        out = list(messages)
                    out[idx] = marked
                    break
        return messages if out is None else out

    if cache_conversation:
        marked_conv = False
        for idx in range(len(messages) - 1, -1, -1):
            if idx in ephemeral:
                continue
            msg = messages[idx]
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            if _has_cache_control_at_target(msg):
                marked_conv = True
                break
            marked = _attach_cache_control(msg)
            if marked is not None:
                out = list(messages)
                out[idx] = marked
                marked_conv = True
                break
        if not marked_conv:
            for idx in range(len(messages) - 1, -1, -1):
                if idx in ephemeral:
                    continue
                msg = messages[idx]
                if not isinstance(msg, dict):
                    continue
                if _has_cache_control_at_target(msg):
                    break
                marked = _attach_cache_control(msg)
                if marked is not None:
                    out = list(messages)
                    out[idx] = marked
                    break
        return messages if out is None else out

    for idx in range(len(messages) - 1, -1, -1):
        if idx in ephemeral:
            continue
        chosen = messages[idx]
        if not isinstance(chosen, dict):
            return messages
        marked = _attach_cache_control(chosen)
        if marked is None:
            return messages
        out = list(messages)
        out[idx] = marked
        return out
    return messages
