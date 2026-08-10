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

"""The one place ``/query_memory`` results become prompt text.

Every recall consumer in this repo renders through `render_recall_block`.
That is the point of the module: recall hits go straight into a prompt and
a single merged reflection can be thousands of tokens, so the block needs a
ceiling — and a ceiling that lives in two hand-written twins drifts.

It used to live in two. ``QQMemoryBridge.render_relevant_memory`` and the
main app's ``recall_memory`` tool handler each built the same
``1. [fact/user] text  (2026-05-01, 3 months ago)`` line, each applied the
same two budgets, and issue #2588 measured 12 distinct ways for a third
renderer — or an edit to either twin — to reach the prompt un-budgeted
while the structural guards stayed green. Those guards inferred "the budget
runs" from the SHAPE of the source (does this function call
``take_lines_within_token_budget``?), and that inference was defeated five
times running, because "will this call execute, and is its result used" is
not decidable from an AST.

Collapsing the twins does not make the inference sound; it makes the
inference unnecessary. There is one render path, it has behavioural tests
that feed oversized input and measure the tokens that come out, and the
remaining structural check is a much cruder one that does not need to
reason about execution: nobody else may call the recall label table, and
every module that posts to ``/query_memory`` must import this one.

Budget shape (constants in ``config.memory_settings``):

- each entry's TEXT truncated to ``RECALL_RENDER_ENTRY_MAX_TOKENS`` —
  truncated, never dropped: recall is relevance-ranked, so half of the top
  hit still beats none of it;
- each rendered LINE truncated to entry + ``RECALL_RENDER_LINE_OVERHEAD_TOKENS``,
  because the tier/entity tag echoes unknown enum values verbatim and a
  hand-edited ``facts.json`` can hold an arbitrarily long ``entity``;
- the block as a whole through ``take_lines_within_token_budget`` against
  ``RECALL_RENDER_TOTAL_MAX_TOKENS``, minus the header when the caller asks
  for one — the header lands in the same string the model reads, so it
  comes out of the same allowance.

Deliberately synchronous. ``truncate_to_tokens`` encodes the text BEFORE
truncation and tiktoken degrades quadratically on a chunk the pretokenizer
cannot split, so every caller hands this to ``asyncio.to_thread`` rather
than running it on the event loop.
"""

from __future__ import annotations

from collections.abc import Mapping as _MappingABC
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RenderedRecallBlock:
    """What `render_recall_block` produced, plus what it had to cut.

    ``text`` is the block as the model will see it. ``kept`` / ``dropped``
    are entry counts for the caller's diagnostics — callers log the drop on
    their own side, since a missing logger must never cost the user their
    memory block.
    """

    text: str
    kept: int
    dropped: int


def _time_suffix(entry: Mapping[str, Any], lang: str) -> str:
    """Localized ``  (2026-05-01, 3 months ago)`` for one entry.

    The anchor prefers when the event actually happened —
    ``event_end_at`` → ``event_start_at`` → ``created_at``, the same order
    as the persona stale block and temporal ``_past_anchor`` — so the model
    sees when the event happened rather than when the memory was written.

    First anchor that PARSES, not first that is truthy: a manual edit or a
    migration can leave a high-priority field non-empty but unparseable,
    and picking by truthiness would stop there and render a garbage date
    instead of falling through to a usable lower-priority field.
    """
    from memory.temporal import (
        _parse_iso_safe,
        time_since_label as _time_label,
        to_naive_local,
    )

    anchor_dt = None
    for candidate in (
        entry.get("event_end_at"),
        entry.get("event_start_at"),
        entry.get("created_at"),
    ):
        # _parse_iso_safe returns None for None / int / list, so malformed
        # persisted values cannot raise out of a render.
        anchor_dt = to_naive_local(_parse_iso_safe(candidate))
        if anchor_dt is not None:
            break
    if anchor_dt is None:
        return ""
    date_part = anchor_dt.strftime("%Y-%m-%d")
    rel = _time_label(anchor_dt.isoformat(), lang=lang)
    return f"  ({date_part}, {rel})" if rel else f"  ({date_part})"


def render_recall_block(
    results: Sequence[Mapping[str, Any]] | None,
    lang: str = "zh",
    *,
    header_template: str | None = None,
) -> RenderedRecallBlock:
    """Render recall hits as the numbered block the model receives.

    ``header_template`` is an already-localized string with an ``{n}``
    placeholder (the main app's overview line); pass ``None`` for callers
    that wrap the block in fixed template text of their own. When present
    it is charged to the block budget, and it is formatted with the number
    of entries that SURVIVED the budget — announcing "found 10" above a
    list of 3 makes the model believe it lost seven results and call the
    tool again.

    Entries with no text are skipped before numbering, so the visible
    numbers stay 1..n with no gaps.
    """
    from config import (
        RECALL_RENDER_ENTRY_MAX_TOKENS,
        RECALL_RENDER_LINE_OVERHEAD_TOKENS,
        RECALL_RENDER_TOTAL_MAX_TOKENS,
    )
    from config.prompts.prompts_memory import render_recall_entry_tag
    from utils.tokenize import (
        count_tokens,
        take_lines_within_token_budget,
        truncate_to_tokens,
    )

    entries = [item for item in (results or []) if isinstance(item, _MappingABC)]
    lines: list[str] = []
    for item in entries:
        # str() coerce: facts/reflections.json round-trips through JSON, so
        # text SHOULD be a str — but manual edits, legacy formats and
        # migration bugs all produce truthy non-strings, and a .strip() on
        # one of those would take down the whole tool call.
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        text = truncate_to_tokens(text, RECALL_RENDER_ENTRY_MAX_TOKENS)
        # tier / entity are internal enums (a scoped entry's entity is
        # always its subject.kind); rendering them raw would push
        # `[fact/group_chat]` into a Chinese prompt.
        tag = render_recall_entry_tag(item.get("tier"), item.get("entity"), lang)
        lines.append(
            f"{len(lines) + 1}. {tag} {text}{_time_suffix(item, lang)}"
        )
    # 行级兜底与编号分开做：编号要按最终顺序连续，而截断只能在整行拼好之后
    # 量。单条截断只管 text，tag 里的未知枚举是原样透出的，而整段预算的
    # "至少留一条"规则会无条件留下第一行——畸形 entity 正是从这里进 prompt 的。
    lines = [
        truncate_to_tokens(
            line, RECALL_RENDER_ENTRY_MAX_TOKENS + RECALL_RENDER_LINE_OVERHEAD_TOKENS,
        )
        for line in lines
    ]

    budget = RECALL_RENDER_TOTAL_MAX_TOKENS
    if header_template is not None:
        # 用 n=条目总数 估表头开销：最终 n 只会更小、位数只会更少，往大了
        # 估是安全方向。
        budget -= count_tokens(header_template.format(n=len(lines))) + count_tokens("\n")
    kept, dropped = take_lines_within_token_budget(lines, max(0, budget))

    block = list(kept)
    if header_template is not None:
        block.insert(0, header_template.format(n=len(kept)))
    return RenderedRecallBlock(
        text="\n".join(block), kept=len(kept), dropped=dropped,
    )
