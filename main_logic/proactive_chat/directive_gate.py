# -*- coding: utf-8 -*-
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

"""Shared ban-topic gate at the proactive delivery boundary.

Every text proactive channel ends in the same ``feed_tts_chunk`` →
``finish_proactive_delivery`` pair: the normal Phase 2 commit, the break
reminders, the mini-game invite and the postgame bubble. Those channels
assemble their own text and some attach cards, so a gate on the Phase 2 draft
alone misses both the early-returning channels and any banned term that only
appears in a card title (#3013 R1 / R2). This gate scans the whole
user-visible payload — the line plus the visible text of every attached card —
and every channel checks it before its line can be spoken.

Two entry points share one matcher: ``_proactive_delivery_directive_block``
drops a delivery and releases its claimed turn; the bare hit count is for
callers that react differently to a hit — the mini-game invite, which skips
the invite and lets the round continue as ordinary chat, and the break
reminder's switch to its fallback draft.

A hit drops the whole delivery, the same as the Phase 2 output gate: the line
usually points at the card ("look at this"), so removing only the card would
leave a dangling reference.

No model call: this is the same literal matcher the Phase 2 output gate uses.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .contracts import PROACTIVE_REASON_PASS_USER_DIRECTIVE, _proactive_pass_body
from .generation import _proactive_directive_hits
from .state import _release_proactive_turn
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# Card fields the frontend renders as text: source cards show ``title`` and
# ``source`` (site name), falling back to ``url`` when there is no title;
# music offers show ``title`` and ``artist``.
_VISIBLE_CARD_FIELDS = ("title", "artist", "source")
# Internal channel marker on music links. The frontend never displays it (a
# music card shows track and artist), so it must not be matched: a ban on
# "推荐" would otherwise block every music card.
_MUSIC_SOURCE_MARKER = "音乐推荐"


def _visible_delivery_text(text: str, source_links: Iterable[Any] | None) -> str:
    """Join the line and every visible card field, one per line.

    A newline separates the pieces so a term never matches across the end of
    one piece and the start of the next.
    """
    parts = [text or ""]
    for link in source_links or ():
        if not isinstance(link, dict):
            continue
        for field in _VISIBLE_CARD_FIELDS:
            value = link.get(field)
            if field == "source" and value == _MUSIC_SOURCE_MARKER:
                continue
            if isinstance(value, str) and value.strip():
                parts.append(value)
        title = link.get("title")
        url = link.get("url")
        if not (isinstance(title, str) and title.strip()) and isinstance(url, str):
            parts.append(url)
    return "\n".join(parts)


def _proactive_delivery_directive_hit_count(
    lanlan_name: str,
    text: str,
    source_links: Iterable[Any] | None = None,
) -> int:
    """Number of active ban-topic terms found in the visible delivery payload."""
    return len(
        _proactive_directive_hits(
            lanlan_name, _visible_delivery_text(text, source_links),
        )
    )


async def _proactive_delivery_directive_block(
    *,
    mgr: Any,
    proactive_sid: Any,
    lanlan_name: str,
    text: str,
    source_links: Iterable[Any] | None = None,
    channel: str,
) -> dict[str, Any] | None:
    """Return a pass body when the delivery hits a ban, otherwise None.

    On a hit, the turn this round claimed is released the same way the
    Phase 2 output gate releases it, if the round still owns it.

    ⚠️ Logs carry the match count and channel only, never the term or the
    line: banned topics are among the most sensitive things a user says.
    """
    hit_count = _proactive_delivery_directive_hit_count(
        lanlan_name, text, source_links,
    )
    if not hit_count:
        return None
    # Channel and count only, which is what makes a missing reminder
    # traceable from the log file; same shape as the Phase 2 output gate.
    logger.info(
        "[%s] proactive delivery blocked by user ban-topic directive "
        "(channel=%s, %d term(s) matched)",
        lanlan_name,
        channel,
        hit_count,
    )
    print(
        f"[{lanlan_name}] 主动搭话命中用户明确要求回避的话题，已拦截 "
        f"(channel={channel}, {hit_count} 项)"
    )
    await _release_proactive_turn(mgr, proactive_sid)
    return _proactive_pass_body(
        PROACTIVE_REASON_PASS_USER_DIRECTIVE,
        message="主动搭话命中用户明确要求回避的话题，已拦截",
        channel=channel,
        directive_match_count=hit_count,
    )


__all__ = (
    "_MUSIC_SOURCE_MARKER",
    "_proactive_delivery_directive_block",
    "_proactive_delivery_directive_hit_count",
)
