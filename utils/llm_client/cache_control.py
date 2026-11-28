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
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_CACHE_CONTROL_EPHEMERAL = {"type": "ephemeral"}
_DEFAULT_EPHEMERAL_TTL_SECONDS = 300
_TTL_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_SCHEMA_CACHE_CONTROL_RE = re.compile(
    r"(?:input_schema|parameters)[^\n]{0,160}cache[_-]control",
    re.IGNORECASE,
)

_TEXT_PART_TYPES = ("text", "input_text", "output_text")
_ANTHROPIC_CACHEABLE_BLOCK_TYPES = frozenset({"text", "tool_result", "tool_use", "image", "document"})
_ANTHROPIC_MAX_CACHE_BREAKPOINTS = 4


def _attach_cache_control(message: dict) -> dict | None:
    """Return a NEW message dict with ``cache_control: {"type": "ephemeral"}``
    attached to a text content block, or ``None`` if there's nothing markable.

    Anthropic (and Anthropic-compat gateways speaking OpenAI wire format) carry
    the cache breakpoint on a content *block*, not on the message itself. A
    plain-string ``content`` is promoted to a single text part so the marker
    has somewhere to live; an existing parts list gets the marker on its last
    non-empty text part. Defensive copy throughout — never mutates the input.

    Caveat for whoever flips a provider on: this promotes a string ``system``
    message into a content-parts array. Native Anthropic and most compat
    gateways accept that, but a few stricter OpenAI-compatible endpoints only
    allow array content on the ``user`` role — verify the target gateway, and
    if needed steer the breakpoint to the last user message for that provider.
    """
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return None
        part = {"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
        return {**message, "content": [part]}
    if isinstance(content, list):
        idx = next(
            (
                i
                for i in range(len(content) - 1, -1, -1)
                if isinstance(content[i], dict)
                and content[i].get("type") in _TEXT_PART_TYPES
                and bool(content[i].get("text"))
            ),
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
    rolling_tail: bool = False,
    skip_cache_write: bool = False,
) -> list:
    """Return a NEW messages list with body-level cache breakpoint marker(s).

    Always marks the end of the leading contiguous run of ``system`` messages
    (falling back to the last markable message when there is no leading system
    message). The leading-contiguous rule prevents trailing status/archive
    system notices from stealing the prefix breakpoint.

    When ``rolling_tail`` is True and non-system conversation messages follow
    the leading system block, a second rolling breakpoint is placed on the
    newest markable non-system message — or on the penultimate non-system
    message when ``skip_cache_write`` is True (so one-shot requests like
    compaction summaries read the warm prefix without paying cache-creation
    cost for their disposable tail).

    Used only for providers whose caching needs a request-*body* flag rather
    than a header (see ``config.providers.CacheProviderConfig.requires_body_flag``
    / ``ChatOpenAI.enable_cache_control``). Header-based providers (DashScope)
    never reach here. Defensive copy — the input list and its dicts are left
    untouched, so repeated calls are idempotent. No-op (returns the input) when
    the list is empty or no chosen message has markable text content.
    """
    if not messages:
        return messages
    target: int | None = None
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "system":
            target = i
        else:
            break

    if target is None:
        if skip_cache_write and len(messages) < 2:
            # One-shot request with no leading system block and no reusable
            # history: do not stamp a cache-write breakpoint on the lone message.
            return messages
        fallback_idx = len(messages) - 2 if skip_cache_write else len(messages) - 1
        chosen = messages[fallback_idx]
        if not isinstance(chosen, dict):
            return messages
        marked = _attach_cache_control(chosen)
        if marked is None:
            return messages
        out = list(messages)
        out[fallback_idx] = marked
        return out

    out: list | None = None
    chosen = messages[target]
    if isinstance(chosen, dict):
        marked = _attach_cache_control(chosen)
        if marked is not None:
            out = list(messages)
            out[target] = marked

    if rolling_tail:
        non_system = [
            i
            for i in range(target + 1, len(messages))
            if isinstance(messages[i], dict) and messages[i].get("role") != "system"
        ]
        if skip_cache_write:
            non_system = non_system[:-1]
        for idx in reversed(non_system):
            base = out if out is not None else messages
            marked_tail = _attach_cache_control(base[idx])
            if marked_tail is not None:
                if out is None:
                    out = list(messages)
                out[idx] = marked_tail
                break
            # Already marked or non-markable: stop on an already-marked block so
            # we do not duplicate breakpoints on earlier turns.
            content = base[idx].get("content")
            if isinstance(content, list) and any(
                isinstance(part, dict) and "cache_control" in part for part in content
            ):
                break

    return out if out is not None else messages


def _count_anthropic_breakpoints(payload: dict) -> int:
    """Count existing ``cache_control`` markers in an Anthropic Messages payload."""
    count = 0
    system = payload.get("system")
    if isinstance(system, list):
        count += sum(1 for blk in system if isinstance(blk, dict) and "cache_control" in blk)
    tools = payload.get("tools")
    if isinstance(tools, list):
        count += sum(1 for tool in tools if isinstance(tool, dict) and "cache_control" in tool)
    messages = payload.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                count += sum(1 for blk in content if isinstance(blk, dict) and "cache_control" in blk)
    return count


def _has_anthropic_cache_control(payload: dict) -> bool:
    """Return whether an Anthropic Messages payload currently carries any ``cache_control`` marker."""
    return _count_anthropic_breakpoints(payload) > 0


def _cache_control_ttl_seconds(marker: object) -> int:
    """Return the TTL in seconds represented by a ``cache_control`` dict (0 if absent)."""
    if not isinstance(marker, dict):
        return 0
    ttl = marker.get("ttl")
    if ttl is None:
        return _DEFAULT_EPHEMERAL_TTL_SECONDS
    if isinstance(ttl, (int, float)) and not isinstance(ttl, bool):
        return max(0, int(ttl))
    if isinstance(ttl, str):
        match = _TTL_DURATION_RE.match(ttl)
        if match is not None:
            amount = int(match.group(1))
            unit = match.group(2).lower()
            scale = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
            return amount * scale
    # Unknown explicit TTL string: treat conservatively as longer than 5m so we
    # never inject a shorter default breakpoint before it.
    return 3600


def _max_ttl_in_blocks(blocks: object) -> int:
    if not isinstance(blocks, list):
        return 0
    return max(
        (_cache_control_ttl_seconds(blk.get("cache_control")) for blk in blocks if isinstance(blk, dict) and "cache_control" in blk),
        default=0,
    )


def _max_ttl_in_messages(messages: object, start: int = 0) -> int:
    if not isinstance(messages, list):
        return 0
    best = 0
    for msg in messages[start:]:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            best = max(best, _max_ttl_in_blocks(content))
    return best


def _is_anthropic_cacheable_block(block: object) -> bool:
    """Return whether an Anthropic content block can carry ``cache_control``.

    Thinking blocks (``thinking``, ``redacted_thinking``) and empty text blocks
    are rejected by the Messages API when stamped with ``cache_control``.
    """
    if not isinstance(block, dict):
        return False
    block_type = block.get("type")
    if block_type not in _ANTHROPIC_CACHEABLE_BLOCK_TYPES:
        return False
    if block_type == "text":
        return bool(block.get("text"))
    return True


def _apply_anthropic_cache_control(
    payload: dict,
    *,
    skip_cache_write: bool = False,
    skip_tail_blocks: int = 0,
) -> dict:
    """Return a NEW Anthropic Messages payload with up to 3 ephemeral cache breakpoints.

    Places breakpoints at:
      1. ``tools[-1]`` (stable tool definitions),
      2. ``system`` (stable prompt prefix),
      3. Rolling conversation tail — the last cacheable content block of the
         newest message, or before the disposable tail when ``skip_cache_write``
         is True so one-shot turns (compaction summaries, side questions) read
         the warm prefix without paying cache-creation cost for their tail.

    Respects Anthropic's 4-breakpoint limit, preserves caller-specified TTL
    ordering (``tools -> system -> messages`` requires longer TTLs before
    shorter ones), and never mutates the caller's dicts or lists.
    """
    budget = _ANTHROPIC_MAX_CACHE_BREAKPOINTS - _count_anthropic_breakpoints(payload)
    if budget <= 0:
        return payload

    out = dict(payload)
    max_sys_ttl = _max_ttl_in_blocks(out.get("system"))
    max_msg_ttl = _max_ttl_in_messages(out.get("messages"))

    # 1) System prefix breakpoint (only when no message after it carries a longer TTL).
    system = out.get("system")
    if budget > 0 and max_msg_ttl <= _DEFAULT_EPHEMERAL_TTL_SECONDS:
        if isinstance(system, str) and system:
            out["system"] = [{"type": "text", "text": system, "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}]
            budget -= 1
        elif isinstance(system, list) and system:
            if not any(isinstance(blk, dict) and "cache_control" in blk for blk in system):
                sys_idx = next(
                    (i for i in range(len(system) - 1, -1, -1) if _is_anthropic_cacheable_block(system[i])),
                    None,
                )
                if sys_idx is not None:
                    new_sys = list(system)
                    new_sys[sys_idx] = {**new_sys[sys_idx], "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
                    out["system"] = new_sys
                    budget -= 1

    # 2) Tools tail breakpoint (wire order: tools precede system and messages).
    tools = out.get("tools")
    if budget > 0 and isinstance(tools, list) and tools and max(max_sys_ttl, max_msg_ttl) <= _DEFAULT_EPHEMERAL_TTL_SECONDS:
        if not any(isinstance(tool, dict) and "cache_control" in tool for tool in tools):
            last_tool = tools[-1]
            if isinstance(last_tool, dict):
                new_tools = list(tools)
                new_tools[-1] = {**last_tool, "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
                out["tools"] = new_tools
                budget -= 1

    # 3) Rolling message tail breakpoint.
    messages = out.get("messages")
    if budget > 0 and isinstance(messages, list) and messages:
        placed = False
        # When skip_cache_write is True and consecutive same-role messages were
        # merged into messages[-1], preserve the boundary before the disposable
        # suffix so we still hit the warm cache on the preceding blocks.
        if skip_cache_write and skip_tail_blocks > 0:
            last_msg = messages[-1]
            if isinstance(last_msg, dict) and isinstance(last_msg.get("content"), list):
                content = last_msg["content"]
                prefix_end = len(content) - skip_tail_blocks
                if prefix_end > 0:
                    suffix_ttl = _max_ttl_in_blocks(content[prefix_end:])
                    if suffix_ttl <= _DEFAULT_EPHEMERAL_TTL_SECONDS:
                        if any(isinstance(blk, dict) and "cache_control" in blk for blk in content[:prefix_end]):
                            placed = True
                        else:
                            blk_idx = next(
                                (i for i in range(prefix_end - 1, -1, -1) if _is_anthropic_cacheable_block(content[i])),
                                None,
                            )
                            if blk_idx is not None:
                                new_blocks = list(content)
                                new_blocks[blk_idx] = {
                                    **new_blocks[blk_idx],
                                    "cache_control": dict(_CACHE_CONTROL_EPHEMERAL),
                                }
                                new_msgs = list(messages)
                                new_msgs[-1] = {**last_msg, "content": new_blocks}
                                out["messages"] = new_msgs
                                placed = True
        if not placed:
            search_end = len(messages) - 1 if skip_cache_write else len(messages)
            for msg_idx in range(search_end - 1, -1, -1):
                if _max_ttl_in_messages(messages, start=msg_idx + 1) > _DEFAULT_EPHEMERAL_TTL_SECONDS:
                    break
                msg = messages[msg_idx]
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if isinstance(content, str) and content:
                    new_msgs = list(messages)
                    new_msgs[msg_idx] = {
                        **msg,
                        "content": [{"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}],
                    }
                    out["messages"] = new_msgs
                    break
                if isinstance(content, list) and content:
                    if any(isinstance(blk, dict) and "cache_control" in blk for blk in content):
                        break
                    blk_idx = next(
                        (i for i in range(len(content) - 1, -1, -1) if _is_anthropic_cacheable_block(content[i])),
                        None,
                    )
                    if blk_idx is not None:
                        new_blocks = list(content)
                        new_blocks[blk_idx] = {**new_blocks[blk_idx], "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
                        new_msgs = list(messages)
                        new_msgs[msg_idx] = {**msg, "content": new_blocks}
                        out["messages"] = new_msgs
                        break

    return out


def _strip_anthropic_cache_control(payload: dict) -> dict:
    """Return a copy of an Anthropic Messages payload with all ``cache_control`` keys removed."""
    out = dict(payload)

    system = out.get("system")
    if isinstance(system, list):
        cleaned_sys = [
            ({k: v for k, v in blk.items() if k != "cache_control"} if isinstance(blk, dict) else blk)
            for blk in system
        ]
        if (
            len(cleaned_sys) == 1
            and isinstance(cleaned_sys[0], dict)
            and set(cleaned_sys[0].keys()) == {"type", "text"}
            and cleaned_sys[0].get("type") == "text"
            and isinstance(cleaned_sys[0].get("text"), str)
        ):
            out["system"] = cleaned_sys[0]["text"]
        else:
            out["system"] = cleaned_sys

    tools = out.get("tools")
    if isinstance(tools, list):
        out["tools"] = [
            ({k: v for k, v in tool.items() if k != "cache_control"} if isinstance(tool, dict) else tool)
            for tool in tools
        ]

    messages = out.get("messages")
    if isinstance(messages, list):
        cleaned_msgs = []
        for msg in messages:
            if not isinstance(msg, dict):
                cleaned_msgs.append(msg)
                continue
            content = msg.get("content")
            if isinstance(content, list):
                cleaned_blocks = [
                    ({k: v for k, v in blk.items() if k != "cache_control"} if isinstance(blk, dict) else blk)
                    for blk in content
                ]
                cleaned_msgs.append({**msg, "content": cleaned_blocks})
            else:
                cleaned_msgs.append(msg)
        out["messages"] = cleaned_msgs

    return out


def _is_cache_control_rejection(exc: BaseException) -> bool:
    """Return True when an upstream 400/422 error rejects protocol-level ``cache_control``.

    Inspects structured error fields when available and avoids treating tool
    schema validation errors (e.g. ``input_schema.properties.cache_control``)
    as a cache-control capability rejection.
    """
    status = getattr(exc, "status_code", None)
    if status is not None and status not in (400, 422):
        return False
    parts: list[str] = []
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            for key in ("message", "param", "type"):
                val = err.get(key)
                if isinstance(val, str) and val:
                    parts.append(val)
        elif isinstance(body.get("message"), str):
            parts.append(body["message"])
    if not parts:
        parts.append(str(exc))
    text = "\n".join(parts)
    low = text.lower()
    if "cache_control" not in low and "cache-control" not in low:
        return False
    stripped = _SCHEMA_CACHE_CONTROL_RE.sub("", text).lower()
    return "cache_control" in stripped or "cache-control" in stripped
