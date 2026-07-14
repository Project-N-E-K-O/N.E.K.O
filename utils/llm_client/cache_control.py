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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_CACHE_CONTROL_EPHEMERAL = {"type": "ephemeral"}

_TEXT_PART_TYPES = ("text", "input_text", "output_text")

def _attach_cache_control(message: dict) -> dict | None:
    """Return a NEW message dict with ``cache_control: {"type": "ephemeral"}``
    attached to a text content block, or ``None`` if there's nothing markable.

    Anthropic (and Anthropic-compat gateways speaking OpenAI wire format) carry
    the cache breakpoint on a content *block*, not on the message itself. A
    plain-string ``content`` is promoted to a single text part so the marker
    has somewhere to live; an existing parts list gets the marker on its last
    text part. Defensive copy throughout — never mutates the input.

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

def _inject_cache_control(messages: list) -> list:
    """Return a NEW messages list with a single body-level cache breakpoint
    marker on the most stable prefix — the END of the leading contiguous run of
    ``system`` messages, falling back to the last message when there's no
    leading system message.

    The "leading contiguous" choice (rather than "last system message anywhere")
    is deliberate: some role-tagged histories in this codebase append a
    *trailing*, non-instructional system message later in the conversation
    (status notices, archive markers). Anchoring to the leading system block
    keeps the breakpoint on the large stable prefix instead of letting a tiny
    trailing system note steal it and cache almost nothing.

    Used only for providers whose caching needs a request-*body* flag rather
    than a header (see ``config.providers.CacheProviderConfig.requires_body_flag``
    / ``ChatOpenAI.enable_cache_control``). Header-based providers (DashScope)
    never reach here. Defensive copy — the input list and its dicts are left
    untouched, so repeated calls are idempotent. No-op (returns the input) when
    the list is empty or the chosen message has no markable text content.
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
        target = len(messages) - 1
    chosen = messages[target]
    if not isinstance(chosen, dict):
        return messages
    marked = _attach_cache_control(chosen)
    if marked is None:
        return messages
    out = list(messages)
    out[target] = marked
    return out
