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

"""Context management in four layers (design section 8.5).

1. **Result cap** - a tool result above ``result_inline_tokens`` keeps its head
   and tail; the full text is written to a file the model can read back.
2. **Pruning** - once the conversation nears the window, older large tool
   results are cut to head and tail. No model call.
3. **Summary** - if that is not enough, the older part of the conversation is
   replaced by a checkpoint written by the model with dsh's fixed template.
   What the user said recently is kept word for word, not summarized.
4. **Overflow recovery** - when the provider still rejects a request as too
   long, everything is pruned, a summary is forced and the request is retried
   once. That last step lives in the session, which owns the request.

Sizes are token budgets. The conversation size is anchored on the usage the
provider reported for the last request; only what was added since is
estimated.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

from config.prompts import prompts_nekopower as texts
from nekopower.runtime.loop.messages import (
    AgentMessage,
    AssistantMessage,
    CustomMessage,
    ImageContent,
    TextContent,
    ToolResultMessage,
    Usage,
    UserMessage,
    message_text,
)
from nekopower.runtime.loop.tool_history import repair_tool_history
from nekopower.runtime.loop.tools import AgentToolResult
from nekopower.runtime.tools.base import SpillStore

logger = logging.getLogger(__name__)

RowKind = Literal["human", "notice", "summary", "assistant", "tool"]

# Tools whose results are bounded by the tool itself and bypass the result cap.
_UNCAPPED_TOOLS = frozenset({"read", "read_image"})
_MESSAGE_OVERHEAD_TOKENS = 4
# Texts shorter than this many bytes are counted on the loop; longer ones in a thread.
_INLINE_COUNT_BYTES = 16_000
_SUMMARY_PREFIX = "Previous conversation summary:\n"
SUMMARY_FALLBACK = (
    "(The earlier part of this conversation could not be summarized and was dropped. Rely on "
    "the messages that remain and on the current state of the files.)"
)
SUMMARY_CUT_SHORT_NOTE = (
    "(This summary reached its length limit and ends early: what would have followed is missing. "
    "Where it matters, check the files and ask rather than assume.)"
)


class SummaryCutShort(RuntimeError):
    """The model's summary ended at its length limit; ``text`` is what there is of it.

    Only that: a reply that ended some other way without being a summary
    (a tool was called instead) is a summary that failed.
    """

    def __init__(self, text: str) -> None:
        super().__init__("the summary was cut short at its length limit")
        self.text = text


class SummaryUsedToolError(RuntimeError):
    """The model called a tool instead of writing the checkpoint summary."""


_ANALYSIS_OPEN_RE = re.compile(r"^\s*<analysis>", re.IGNORECASE)
_ANALYSIS_CLOSE_RE = re.compile(r"</analysis>", re.IGNORECASE)
_SUMMARY_OPEN_RE = re.compile(r"^\s*<summary>", re.IGNORECASE)
_SUMMARY_CLOSE_RE = re.compile(r"</summary>", re.IGNORECASE)


def extract_compaction_summary(raw_text: str) -> str:
    """Strip outer ``<analysis>`` scratchpad and unwrap outer ``<summary>`` tags.

    Only parses outer envelope tags so literal ``<analysis>`` or ``<summary>``
    mentions inside the summary body are preserved verbatim. Returns ``""``
    when the model produced only an ``<analysis>`` block (including a truncated
    unclosed ``<analysis>`` block that never reached ``<summary>``). Preserves
    truncated summary prose when output has already entered ``<summary>``, and
    falls back to trimmed plain prose when the model omitted XML tags.
    """
    text = raw_text.strip()
    if not text:
        return ""
    while True:
        open_analysis = _ANALYSIS_OPEN_RE.match(text)
        if open_analysis is None:
            break
        close_analysis = _ANALYSIS_CLOSE_RE.search(text, open_analysis.end())
        if close_analysis is None:
            # Cut off inside scratchpad before summary began: reject completely.
            return ""
        text = text[close_analysis.end() :].strip()
        if not text:
            return ""
    open_summary = _SUMMARY_OPEN_RE.match(text)
    if open_summary is not None:
        rest = text[open_summary.end() :]
        # Match up to the LAST closing </summary> so any literal </summary>
        # code examples inside the body stay intact.
        last_close = None
        for match in _SUMMARY_CLOSE_RE.finditer(rest):
            last_close = match
        if last_close is not None:
            return rest[: last_close.start()].strip()
        return rest.strip()
    return text


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


# The part of the window a conversation may fill before it is cut down and
# summed up, unless the session is told another (``compact_ratio``): 170,000
# of the preset 200,000 tokens.
COMPACT_RATIO = 0.85


def compact_ratio(window: int, compact_at: int | None) -> float:
    """Return the part of a window a conversation may fill, given the length it is to be summed up from."""
    if not compact_at or compact_at <= 0 or window <= 0:
        return COMPACT_RATIO
    # As early as it was set: a conversation is summed up from 1,000 tokens
    # on at the earliest whatever this says (``ContextPolicy.threshold``).
    return min(1.0, compact_at / window)


def headroom_of(window: int) -> int:
    """Return what is kept free of a window besides the output: a tenth of it, 65,536 tokens at the most.

    The conversation is measured before every request, so this is not room
    for what a turn adds; it is for what the measuring is off by. (An
    eighth, until the preset window became 200,000 summed up from 170,000:
    with an eighth kept free, and the output, that point would lie in it.)
    """
    return min(65_536, window // 10)


# The largest part of the window a request's output may take: one in this many.
OUTPUT_WINDOW_SHARE = 4


def output_allowance(window: int, configured: int) -> int:
    """Return how many tokens of output a request asks for in a window of ``window`` tokens.

    What the model is configured for, and a quarter of the window at the
    most. A provider counts the output a request asks for against the
    window together with what it sends; in a window near the configured
    output there would be no room left for the conversation, and a request
    would be refused as too long however much of it was summed up.
    """
    return max(256, min(configured, window // OUTPUT_WINDOW_SHARE))


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Token budgets of one session, derived from the model's window."""

    window: int
    max_output: int
    threshold_ratio: float = COMPACT_RATIO
    retain_ratio: float = 0.16
    headroom_tokens: int | None = None
    result_inline_tokens: int = 12_500
    prune_threshold_tokens: int = 2_048
    prune_head_tokens: int = 1_024
    prune_tail_tokens: int = 256
    user_keep_tokens: int = 20_000
    summary_max_tokens: int = 8_192
    image_tokens: int = 1_500

    @property
    def headroom(self) -> int:
        if self.headroom_tokens is not None:
            return self.headroom_tokens
        return headroom_of(self.window)

    @property
    def threshold(self) -> int:
        """Conversation size above which pruning and summarizing start."""
        by_ratio = round(self.window * self.threshold_ratio)
        by_reserve = self.window - self.max_output - self.headroom
        return max(1_000, min(by_ratio, by_reserve))

    @property
    def retain_tokens(self) -> int:
        """Size of the newest part of the conversation a summary leaves untouched."""
        return max(500, int((self.window - self.max_output) * self.retain_ratio))

    @property
    def result_cap(self) -> int:
        """Per-result cap: a quarter of the window, and ``result_inline_tokens`` at the most.

        A session does not cap the text of a result below
        ``prune_threshold_tokens`` whatever this says
        (``ContextManager.cap_tool_result``), so in a window of under 8192
        tokens, where a quarter is less than that, a result can be longer
        than this.
        """
        return max(500, min(self.result_inline_tokens, self.window // 4))

    @property
    def summary_cap(self) -> int:
        return max(256, min(self.summary_max_tokens, self.max_output))

    @property
    def user_keep(self) -> int:
        return min(self.user_keep_tokens, self.retain_tokens)


# ---------------------------------------------------------------------------
# Token estimates
# ---------------------------------------------------------------------------


def _count(text: str) -> int:
    if not text:
        return 0
    from utils.tokenize import count_tokens

    return count_tokens(text)


def _message_parts(message: AgentMessage) -> tuple[str, int]:
    """Return the text that is sent for a message and its number of images."""
    if isinstance(message, AssistantMessage):
        parts = [message.text]
        for call in message.tool_calls:
            parts.append(call.name)
            parts.append(str(call.arguments))
        if message.tool_calls:
            parts.append(message.thinking_text)
        return "\n".join(part for part in parts if part), 0
    if isinstance(message, (ToolResultMessage, UserMessage, CustomMessage)):
        content = message.content
        images = (
            sum(1 for block in content if isinstance(block, ImageContent))
            if isinstance(content, list)
            else 0
        )
        return message.text, images
    return message_text(message), 0


def estimate_tokens(message: AgentMessage, image_tokens: int = 1_500) -> int:
    """Estimate the request tokens one message costs."""
    text, images = _message_parts(message)
    return _count(text) + images * image_tokens + _MESSAGE_OVERHEAD_TOKENS


async def estimate_many(messages: Sequence[AgentMessage], image_tokens: int = 1_500) -> int:
    """Estimate several messages without stalling the loop on large ones."""
    total = 0
    heavy: list[AgentMessage] = []
    for message in messages:
        text, _images = _message_parts(message)
        if len(text) > _INLINE_COUNT_BYTES:
            heavy.append(message)
        else:
            total += estimate_tokens(message, image_tokens)
    if heavy:
        total += await asyncio.to_thread(
            lambda: sum(estimate_tokens(message, image_tokens) for message in heavy)
        )
    return total


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Row:
    """One message of the conversation with what persistence and compaction need."""

    entry_id: str
    kind: RowKind
    message: AgentMessage


def kind_of(message: AgentMessage) -> RowKind:
    """Classify a message that came through the loop."""
    if isinstance(message, AssistantMessage):
        return "assistant"
    if isinstance(message, ToolResultMessage):
        return "tool"
    if isinstance(message, UserMessage):
        return "human"
    return "notice"


class Transcript:
    """The loop's message list plus per-message bookkeeping.

    ``messages`` is the very list the loop appends to, so a rewrite has to
    happen in place. Bookkeeping is keyed by object identity; each row keeps
    its message alive, so an identity is never reused while it is registered.
    """

    def __init__(self, messages: list[AgentMessage], new_entry_id: Callable[[], str]) -> None:
        self.messages = messages
        self._new_entry_id = new_entry_id
        self._rows: dict[int, Row] = {}

    def register(self, message: AgentMessage, entry_id: str, kind: RowKind) -> Row:
        row = Row(entry_id, kind, message)
        self._rows[id(message)] = row
        return row

    def is_registered(self, message: AgentMessage) -> bool:
        row = self._rows.get(id(message))
        return row is not None and row.message is message

    def rows(self) -> list[Row]:
        rows: list[Row] = []
        for message in self.messages:
            row = self._rows.get(id(message))
            if row is None or row.message is not message:
                row = self.register(message, self._new_entry_id(), kind_of(message))
            rows.append(row)
        return rows

    def rewrite(self, rows: Sequence[Row]) -> None:
        """Replace the whole conversation, keeping the list object itself."""
        self.messages[:] = [row.message for row in rows]
        self._rows = {id(row.message): row for row in rows}


# ---------------------------------------------------------------------------
# Layer 1: result cap
# ---------------------------------------------------------------------------


def _split_head_tail(text: str, head_tokens: int, tail_tokens: int, gap: str) -> str:
    from utils.tokenize import truncate_head_tail_tokens

    return truncate_head_tail_tokens(text, head_tokens, tail_tokens, separator=gap)


def _fits(text: str, max_tokens: int) -> bool:
    # A token is at least one byte, so a text this short needs no tokenizer.
    if len(text.encode("utf-8", errors="replace")) <= max_tokens:
        return True
    return _count(text) <= max_tokens


def cap_result_text(text: str, cap: int, spill: SpillStore, hint: str) -> str | None:
    """Return the capped form of an oversized result, or ``None`` if it fits.

    Blocking: tokenizes and writes the spill file.
    """
    if _fits(text, cap):
        return None
    path = spill.save(hint, text)
    # Leave room for the notice inside the cap.
    budget = max(64, cap - 96)
    kept = _split_head_tail(text, budget // 2, budget - budget // 2, texts.SPILL_GAP)
    omitted = max(
        0,
        len(text.encode("utf-8", errors="replace"))
        - len(kept.replace(texts.SPILL_GAP, "", 1).encode("utf-8", errors="replace")),
    )
    if path is not None:
        notice = texts.spill_notice(omitted, str(path))
    else:
        notice = f"(Omitted {omitted} bytes. The complete result could not be saved.)"
    return f"{kept}\n\n{notice}"


# What is kept back of the cap for the text after a result's first block,
# as far as that text needs it: a line or two said after a long text (a note
# that a picture could not be passed on) is not cut for the text's sake.
_REST_RESERVE_TOKENS = 256


def first_block_and_rest(content: Sequence[TextContent | ImageContent]) -> tuple[str, str] | None:
    """Return a result's first block of text and the rest of its text as one; ``None`` if it has one block or none.

    A result can come as several blocks of text: the host's gateway hands a
    group of tools over in front of what the call that started the group
    returned. Capped or pruned as one text, such a result would lose the
    end of the group to a long page behind it. So the first block is told
    apart from what follows it, and only the first: however many blocks
    there are, a result is two texts at most, which keeps what either limit
    lets through bounded.
    """
    blocks = [block.text for block in content if isinstance(block, TextContent)]
    if len(blocks) < 2:
        return None
    return blocks[0], "".join(blocks[1:])


def cap_result_blocks(
    first: str, rest: str, cap: int, spill: SpillStore, hint: str, *, first_whole_up_to: int = 0
) -> tuple[str, str] | None:
    """Return the capped form of an oversized result of a first block and the rest, or ``None`` if together they fit.

    The two share the cap. The first block gets all of it but what the
    rest needs of ``_REST_RESERVE_TOKENS``, and the rest gets what the first
    block left: a first block that fits stays whole, and whichever does not
    fit is cut down and saved whole, like a result of one block.

    ``first_whole_up_to`` is a size up to which the first block is never
    cut, whatever the rest needs of the cap: a block the runtime would
    never prune must not arrive cut either. A session passes that size
    and a cap that is no less, the cap a result of one block has: what
    follows a first block gets what the block left of the same length one
    text may have, not less. So a result is over the cap only when its
    first block took more than the cap less what is kept back for the
    rest, and then by what is kept back at the most.

    Blocking: tokenizes and writes the spill files.
    """
    if _fits(first + rest, cap):
        return None
    reserve = min(_count(rest), _REST_RESERVE_TOKENS)
    # (The store numbers what it saves, so the two never share a file.)
    kept_first = cap_result_text(first, max(cap - reserve, first_whole_up_to), spill, hint)
    if kept_first is None:
        kept_first = first
    elif first.endswith("\n"):
        # It still ends where the rest begins.
        kept_first += "\n"
    kept_rest = cap_result_text(rest, max(cap - _count(kept_first), reserve), spill, hint)
    return kept_first, rest if kept_rest is None else kept_rest


# ---------------------------------------------------------------------------
# Layer 2: pruning
# ---------------------------------------------------------------------------


def prune_text(text: str, policy: ContextPolicy) -> str | None:
    """Cut a large tool result to head and tail; ``None`` when it is small enough."""
    if _fits(text, policy.prune_threshold_tokens):
        return None
    return _split_head_tail(
        text, policy.prune_head_tokens, policy.prune_tail_tokens, texts.PRUNED_MIDDLE_MARKER
    )


def prune_result(message: ToolResultMessage, policy: ContextPolicy) -> ToolResultMessage | None:
    """Cut a large tool result to head and tail; ``None`` when it is small enough.

    A result of several blocks of text is cut as two texts, its first block
    and the rest (``first_block_and_rest``): a first block that is small
    enough stays whole however long what follows it. Cut as one text, a
    group of tools handed over in front of a long page would lose the tool
    definitions in its middle.

    The price is that such a result is left longer than one of a single
    block: up to ``prune_threshold_tokens`` of a first block that was not
    cut, and the head and tail of the rest besides. It is bounded, and
    only the results that come in more than one block pay it.
    """
    images = [block for block in message.content if isinstance(block, ImageContent)]
    parts = first_block_and_rest(message.content)
    if parts is None:
        pruned = prune_text(message.text, policy)
        if pruned is None:
            return None
        return message.model_copy(update={"content": [TextContent(text=pruned), *images]})
    cut = [prune_text(part, policy) for part in parts]
    if cut == [None, None]:
        return None
    kept = [TextContent(text=part if pruned is None else pruned) for part, pruned in zip(parts, cut, strict=True)]
    return message.model_copy(update={"content": [*kept, *images]})


def prune_rows(rows: Sequence[Row], policy: ContextPolicy, *, include_latest: bool) -> list[Row] | None:
    """Prune old tool results; returns the new rows, or ``None`` if nothing changed.

    Results after the last assistant message have not been shown to the model
    yet and are left whole unless ``include_latest`` is set.
    """
    last_assistant = max(
        (index for index, row in enumerate(rows) if row.kind == "assistant"), default=-1
    )
    changed = False
    out: list[Row] = []
    for index, row in enumerate(rows):
        message = row.message
        if (
            isinstance(message, ToolResultMessage)
            and (include_latest or index < last_assistant)
        ):
            pruned = prune_result(message, policy)
            if pruned is not None:
                out.append(Row(row.entry_id, row.kind, pruned))
                changed = True
                continue
        out.append(row)
    return out if changed else None


def cap_parallel_batch_results(
    rows: Sequence[Row],
    *,
    budget: int,
    spill: SpillStore,
    floor_tokens: int = 512,
) -> list[Row] | None:
    """Cap combined tokens of a parallel batch of fresh tool results after the last assistant turn.

    Single-tool turns are handled by Layer 1 (``cap_tool_result``). When two or
    more fresh tool results arrive together and their combined token estimate
    exceeds ``budget``, spill the largest results down to an equal share so a
    parallel fan-out cannot burst the context window in one step. Blocking.
    """
    if budget <= 0 or not rows:
        return None
    last_assistant = max((i for i, row in enumerate(rows) if row.kind == "assistant"), default=-1)
    batch_indices = [
        i
        for i in range(last_assistant + 1, len(rows))
        if isinstance(rows[i].message, ToolResultMessage)
    ]
    if len(batch_indices) < 2:
        return None
    sizes = {i: estimate_tokens(rows[i].message, image_tokens=0) for i in batch_indices}
    if sum(sizes.values()) <= budget:
        return None
    # Water-filling: keep small results untouched and divide the remaining
    # aggregate budget evenly across oversized siblings. Aggregate ``budget``
    # takes precedence over ``floor_tokens`` when ``n * floor_tokens > budget``,
    # floored only at the minimal cap below which the spill notice itself would
    # not shrink further.
    min_cap = min(floor_tokens, 160)
    remaining_budget = budget
    uncapped = sorted(batch_indices, key=lambda idx: sizes[idx])
    while uncapped:
        fair = remaining_budget // len(uncapped)
        if sizes[uncapped[0]] <= fair:
            remaining_budget -= sizes[uncapped[0]]
            uncapped.pop(0)
        else:
            break
    if not uncapped:
        return None
    fair = remaining_budget // len(uncapped)
    # Reserve message-wrapper overhead (``estimate_tokens`` adds 6 per message)
    # so the combined post-cap estimate stays within ``budget``.
    item_cap = max(min_cap, fair - 16)
    out = list(rows)
    changed = False
    for index in uncapped:
        row = out[index]
        message = row.message
        assert isinstance(message, ToolResultMessage)
        images = [block for block in message.content if isinstance(block, ImageContent)]
        hint = f"{message.tool_name}-{message.tool_call_id}-batch"
        parts = first_block_and_rest(message.content)
        if parts is not None:
            fitted = cap_result_blocks(*parts, item_cap, spill, hint)
            if fitted is None:
                continue
            updated = message.model_copy(
                update={"content": [*(TextContent(text=part) for part in fitted), *images]}
            )
        else:
            capped = cap_result_text(message.text, item_cap, spill, hint)
            if capped is None:
                continue
            updated = message.model_copy(update={"content": [TextContent(text=capped), *images]})
        out[index] = Row(row.entry_id, row.kind, updated)
        changed = True
    return out if changed else None


# ---------------------------------------------------------------------------
# Image retention
# ---------------------------------------------------------------------------


def retain_recent_images(
    rows: Sequence[Row],
    retention: dict[str, int],
    pools: dict[str, str] | None = None,
    *,
    evict_at: dict[str, int] | None = None,
    include_user_images: bool = False,
) -> list[Row] | None:
    """Keep only the newest image results of the listed tools.

    ``retention`` maps a tool name to how many of its results keep their
    images. Older ones get a text placeholder. ``pools`` maps a tool name to
    a pool; the tools of one pool are counted together, and the pool keeps
    the largest number given for any of them.

    When ``evict_at`` is given, each named tool/pool uses hysteresis: eviction
    for that pool is skipped (leaving older messages byte-for-byte intact for
    prompt cache reuse) until the pool holds at least ``evict_at`` image rows,
    at which point it batch-evicts down to ``retention``. Images evicted before
    the model ever saw them (after the last assistant turn) receive
    ``IMAGE_OMITTED_UNSEEN_PLACEHOLDER``. Returns ``None`` if unchanged.
    """
    if not retention:
        return None
    pools = pools or {}
    remaining: dict[str, int] = {}
    for name, count in retention.items():
        counter = pools.get(name, name)
        remaining[counter] = max(remaining.get(counter, 0), count)

    def _row_counter(message: AgentMessage) -> str | None:
        if isinstance(message, ToolResultMessage) and message.tool_name in retention:
            if any(isinstance(block, ImageContent) for block in message.content):
                return pools.get(message.tool_name, message.tool_name)
        elif (
            include_user_images
            and "__user__" in retention
            and isinstance(message, UserMessage)
            and isinstance(message.content, list)
            and any(isinstance(block, ImageContent) for block in message.content)
        ):
            return pools.get("__user__", "__user__")
        return None

    active_pools: set[str] | None = None
    if evict_at:
        pool_thresholds: dict[str, int] = {}
        for name, threshold in evict_at.items():
            if name in retention:
                counter = pools.get(name, name)
                pool_thresholds[counter] = max(pool_thresholds.get(counter, 0), int(threshold))
        if pool_thresholds:
            counts: dict[str, int] = {}
            for row in rows:
                counter = _row_counter(row.message)
                if counter is not None:
                    counts[counter] = counts.get(counter, 0) + 1
            active_pools = {
                counter
                for counter in remaining
                if counter not in pool_thresholds or counts.get(counter, 0) >= pool_thresholds[counter]
            }
            if not active_pools:
                return None

    last_assistant = max((i for i, row in enumerate(rows) if row.kind == "assistant"), default=-1)
    changed = False
    out: list[Row] = list(rows)
    for index in range(len(out) - 1, -1, -1):
        message = out[index].message
        counter = _row_counter(message)
        if counter is None:
            continue
        if active_pools is not None and counter not in active_pools:
            continue
        if remaining[counter] > 0:
            remaining[counter] -= 1
            continue
        unseen = index > last_assistant if (last_assistant >= 0 or isinstance(message, UserMessage)) else False
        placeholder = (
            texts.IMAGE_OMITTED_UNSEEN_PLACEHOLDER
            if unseen
            else texts.IMAGE_OMITTED_PLACEHOLDER
        )
        content = [
            block if isinstance(block, TextContent) else TextContent(text=placeholder)
            for block in message.content  # type: ignore[union-attr]
        ]
        out[index] = Row(
            out[index].entry_id, out[index].kind, message.model_copy(update={"content": content})
        )
        changed = True
    return out if changed else None


# ---------------------------------------------------------------------------
# Layer 3: summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Compaction:
    """Outcome of one summary: the new conversation and what to persist."""

    rows: list[Row]
    summary_row: Row
    # Text stored in the transcript's compaction entry (without the replay prefix).
    stored_summary: str
    replaced_entry_ids: list[str]
    tokens_before: int
    usage: Usage | None = None
    # A forced fit may have dropped the old span after a failed/truncated
    # summary. The transcript still advances, but device tools stay put.
    complete: bool = True


def frame_summary(
    summary: str,
    *,
    head_truncated: bool = False,
    has_retained_tail: bool = False,
) -> str:
    """Wrap a model-written summary as the checkpoint the conversation continues from."""
    header_parts = [texts.CHECKPOINT_PREAMBLE]
    if has_retained_tail:
        header_parts.append(texts.CHECKPOINT_RETAINED_TAIL_NOTE)
    if head_truncated:
        header_parts.append(texts.CHECKPOINT_HEAD_TRUNCATED_NOTE)
    header = "\n".join(header_parts)
    return f"{header}\n\n{texts.SUMMARY_OPEN_TAG}\n{summary.strip()}\n{texts.SUMMARY_CLOSE_TAG}"


def summary_message(stored_summary: str) -> UserMessage:
    """Build the message a stored summary becomes, identical to transcript replay."""
    return UserMessage(content=f"{_SUMMARY_PREFIX}{stored_summary}")


def choose_cut(rows: Sequence[Row], sizes: Sequence[int], retain_tokens: int) -> int:
    """Return the index where the part kept verbatim begins.

    The newest messages are kept up to ``retain_tokens``. The kept part always
    contains everything from the last assistant message on (its tool results
    may not have been seen yet) and never starts with a tool result, which
    would separate it from the call it answers.
    """
    total = len(rows)
    budget_cut = total
    used = 0
    for index in range(total - 1, -1, -1):
        used += sizes[index]
        if used > retain_tokens:
            break
        budget_cut = index
    last_assistant = max(
        (index for index, row in enumerate(rows) if row.kind == "assistant"), default=total
    )
    last_human = max(
        (index for index, row in enumerate(rows) if row.kind == "human"), default=total
    )
    cut = min(budget_cut, last_assistant if last_assistant < total else last_human)
    while cut < total and rows[cut].kind == "tool":
        cut += 1
    return cut


def select_user_rows(
    region: Sequence[Row], sizes: Sequence[int], budget: int, pinned: Collection[str] = ()
) -> set[str]:
    """Pick the most recent user messages of ``region`` that fit ``budget`` tokens.

    A row named in ``pinned`` is kept whatever its size and on top of the
    budget: what the user said since keeps the room it had.
    """
    kept: set[str] = {row.entry_id for row in region if row.entry_id in pinned}
    used = 0
    for index in range(len(region) - 1, -1, -1):
        row = region[index]
        if row.kind != "human" or row.entry_id in kept:
            continue
        if used + sizes[index] > budget:
            break
        used += sizes[index]
        kept.add(row.entry_id)
    return kept


Summarizer = Callable[[list[AgentMessage], int], Awaitable["tuple[str, Usage | None]"]]


@dataclass(slots=True)
class ContextManager:
    """Keeps one session's conversation within its model's window."""

    policy: ContextPolicy
    spill: SpillStore
    # Called with the messages to condense and an output cap; returns the summary.
    summarize: Summarizer
    new_entry_id: Callable[[], str]
    # Tokens of the system prompt and tool definitions, estimated once.
    fixed_tokens: int = 0
    image_retention: dict[str, int] = field(default_factory=dict)
    # Tool name -> pool; see ``retain_recent_images``.
    image_pools: dict[str, str] = field(default_factory=dict)
    # Tool name -> hysteresis trigger count; see ``retain_recent_images``.
    image_evict_at: dict[str, int] = field(default_factory=dict)
    # Optional callback invoked if a summary request itself hits provider overflow.
    on_summary_overflow: Callable[[str, int], None] | None = None
    _anchor_tokens: int | None = None
    _anchor_count: int = 0

    # -- measurement ----------------------------------------------------------

    def note_usage(self, usage: Usage, message_count: int) -> None:
        """Anchor the size on a provider-reported usage.

        ``message_count`` is the number of messages in the conversation right
        after that response was appended.
        """
        prompt = usage.input + usage.cache_read + usage.cache_write
        if prompt <= 0:
            return
        self._anchor_tokens = prompt + usage.output
        self._anchor_count = message_count

    def invalidate(self) -> None:
        """Forget the anchor after the conversation was rewritten."""
        self._anchor_tokens = None
        self._anchor_count = 0

    async def measure(self, messages: Sequence[AgentMessage]) -> int:
        """Return the conversation size: reported usage plus an estimate of the rest."""
        if self._anchor_tokens is not None and self._anchor_count <= len(messages):
            added = await estimate_many(messages[self._anchor_count:], self.policy.image_tokens)
            return self._anchor_tokens + added
        return self.fixed_tokens + await estimate_many(messages, self.policy.image_tokens)

    # -- layer 1 --------------------------------------------------------------

    async def cap_tool_result(
        self, tool_name: str, call_id: str, result: AgentToolResult
    ) -> AgentToolResult:
        if tool_name in _UNCAPPED_TOOLS:
            return result
        images = [block for block in result.content if isinstance(block, ImageContent)]
        # What the text of one result may be, in one block or several. A
        # text small enough never to be pruned is not cut here either,
        # however small the window (below 8192 tokens a quarter of it is
        # less than that): a group of tools loaded by its name, or handed
        # over in place of a call, is one text and has to arrive whole.
        cap = max(self.policy.result_cap, self.policy.prune_threshold_tokens)
        hint = f"{tool_name}-{call_id}"
        parts = first_block_and_rest(result.content)
        if parts is not None:
            # Its first block and the rest share that cap, each cut by itself.
            fitted = await asyncio.to_thread(
                partial(
                    cap_result_blocks,
                    *parts,
                    cap,
                    self.spill,
                    hint,
                    first_whole_up_to=self.policy.prune_threshold_tokens,
                )
            )
            if fitted is None:
                return result
            return result.model_copy(update={"content": [*(TextContent(text=part) for part in fitted), *images]})
        capped = await asyncio.to_thread(cap_result_text, result.text, cap, self.spill, hint)
        if capped is None:
            return result
        return result.model_copy(update={"content": [TextContent(text=capped), *images]})

    # -- layers 2 and 3 -------------------------------------------------------

    async def fit(
        self,
        transcript: Transcript,
        *,
        force: bool = False,
        tight: bool | None = None,
        on_compaction: Callable[[Compaction], None] | None = None,
        pinned: Collection[str] = (),
        or_drop: bool = True,
        reserve_tokens: int = 0,
        summarize: bool = True,
    ) -> list[Compaction]:
        """Bring the conversation under the threshold; rewrites ``transcript`` in place.

        ``force`` is the overflow path: prune everything and summarize even if
        the estimate says the conversation fits. Returns the summaries made.

        ``tight`` says how much the first summary keeps word for word: a
        quarter of the usual when True. Left out, a forced summary is
        tight (the conversation was refused as too long) and any other is
        not. A conversation that is summed up with nothing wrong with its
        length (``force`` with ``tight`` False) keeps the usual.

        ``pinned`` names rows (by entry id) that a summary leaves as they
        are, word for word: the text that opened the commission the session
        is working on.

        ``or_drop`` says what becomes of the older part when no whole
        summary of it can be had (the request failed, came back empty, or
        ended at its length limit). True: the conversation has to get
        shorter, so the part is replaced all the same, by a note that it
        was dropped, or by the summary as far as it got with a note that
        it ends early. False, for a conversation with nothing wrong with
        its length: it is left as it is, and no summary is returned.

        ``on_compaction`` is how the caller persists them. It is called for each
        summary as soon as that summary has replaced the older messages, with
        no await in between: what comes next (measuring, a second summary) can
        be cancelled, and by then the conversation already rests on the summary.

        Only summaries are persisted. Cutting old tool results and dropping old
        images change the conversation in memory and nothing else: the saved
        transcript keeps every result whole, and a cut row keeps its entry id,
        so the ids a summary refers to stay valid. A session opened from the
        transcript therefore starts from the whole results; it is fitted again
        before its first request, like before every request.
        """
        rows = transcript.rows()
        trimmed = retain_recent_images(
            rows,
            self.image_retention,
            self.image_pools,
            evict_at=self.image_evict_at or None,
        )
        if trimmed is not None:
            transcript.rewrite(trimmed)
            self.invalidate()
        threshold = self.policy.threshold - reserve_tokens
        if not force and await self.measure(transcript.messages) <= threshold:
            return []

        # As it is before anything is cut for the summary: put back when
        # the conversation is to stay as it is and no summary is made.
        as_it_was = None if or_drop else transcript.rows()
        pruned = await asyncio.to_thread(
            prune_rows, transcript.rows(), self.policy, include_latest=force
        )
        if pruned is not None:
            transcript.rewrite(pruned)
            self.invalidate()
            if not force and await self.measure(transcript.messages) <= threshold:
                if as_it_was is not None:
                    transcript.rewrite(as_it_was)
                    self.invalidate()
                return []
        if not summarize:
            if as_it_was is not None and pruned is not None:
                transcript.rewrite(as_it_was)
                self.invalidate()
            return []

        done: list[Compaction] = []
        # Once where the conversation could stay as it is: it is summed up
        # because it was told to, and whoever told it to counted on one
        # request.
        try:
            for attempt in range(2 if or_drop else 1):
                tokens_before = await self.measure(transcript.messages)
                compaction = await self._compact(
                    transcript.rows(),
                    tokens_before,
                    tight=(force if tight is None else tight) or attempt > 0,
                    pinned=pinned,
                    or_drop=or_drop,
                )
                if compaction is None:
                    break
                transcript.rewrite(compaction.rows)
                self.invalidate()
                done.append(compaction)
                if on_compaction is not None:
                    # Before the next await: a cancellation from here on must not
                    # leave the conversation ahead of what the caller has saved.
                    on_compaction(compaction)
                if await self.measure(transcript.messages) <= threshold:
                    break
        finally:
            # Also when this is cancelled, or fails, while the summary is
            # asked for. (One that was made stays, whatever comes after.)
            if as_it_was is not None and not done and pruned is not None:
                # Nothing was summed up, and nothing has to go: the tool
                # results that were cut for it are whole again.
                transcript.rewrite(as_it_was)
                self.invalidate()
        return done

    async def _compact(
        self, rows: list[Row], tokens_before: int, *, tight: bool, pinned: Collection[str] = (), or_drop: bool = True
    ) -> Compaction | None:
        policy = self.policy
        sizes = [estimate_tokens(row.message, policy.image_tokens) for row in rows]
        # The provider's count is authoritative. When it is larger than the local
        # estimate, scale the per-message sizes up to it so the cut is chosen
        # against real numbers.
        estimated = sum(sizes)
        measured = tokens_before - self.fixed_tokens
        if estimated > 0 and measured > estimated:
            factor = measured / estimated
            sizes = [int(size * factor) + 1 for size in sizes]
        retain = policy.retain_tokens // 4 if tight else policy.retain_tokens
        cut = choose_cut(rows, sizes, retain)
        region = rows[:cut]
        kept_ids = select_user_rows(region, sizes[:cut], policy.user_keep // (2 if tight else 1), pinned)
        replaced = [row for row in region if row.entry_id not in kept_ids]
        if not any(row.kind in ("assistant", "tool", "notice") for row in replaced):
            # Nothing but earlier summaries or oversized user text: no gain.
            return None

        summary, usage, complete, head_truncated = await self._summarize_region(replaced, or_drop=or_drop)
        if summary is None:
            # No summary to be had, and nothing that has to go: left as it is.
            return None
        stored = frame_summary(
            summary,
            head_truncated=head_truncated,
            has_retained_tail=cut < len(rows),
        )
        summary_row = Row(self.new_entry_id(), "summary", summary_message(stored))
        replaced_ids = {row.entry_id for row in replaced}
        new_rows: list[Row] = []
        inserted = False
        for row in rows:
            if row.entry_id in replaced_ids:
                if not inserted:
                    new_rows.append(summary_row)
                    inserted = True
                continue
            new_rows.append(row)
        return Compaction(
            rows=new_rows,
            summary_row=summary_row,
            stored_summary=stored,
            replaced_entry_ids=[row.entry_id for row in replaced],
            tokens_before=tokens_before,
            usage=usage,
            complete=complete,
        )

    def _summarizer_supports_retry_options(self) -> bool:
        try:
            params = inspect.signature(self.summarize).parameters
        except (TypeError, ValueError):
            return False
        return "no_tools" in params and "retry_note" in params

    async def _call_summarizer(
        self,
        prepared: list[AgentMessage],
        max_tokens: int,
        *,
        no_tools: bool = False,
        retry_note: str | None = None,
    ) -> tuple[str, Usage | None]:
        if (no_tools or retry_note is not None) and self._summarizer_supports_retry_options():
            return await self.summarize(prepared, max_tokens, no_tools=no_tools, retry_note=retry_note)  # type: ignore[call-arg]
        return await self.summarize(prepared, max_tokens)

    async def _summarize_region(
        self, region: Sequence[Row], *, or_drop: bool = True
    ) -> tuple[str | None, Usage | None, bool, bool]:
        """Ask the model for a checkpoint of ``region`` and say whether it is complete.

        Implements a 3-tier retry ladder when ``or_drop=True``:
        1. Normal summary call (preserving prompt cache prefix with tools).
        2. On tool-call-instead-of-summary or empty reply, retry once with tools
           removed and an explicit plain-text-only prompt.
        3. On provider context overflow / refusal, notify the window learner,
           drop the oldest slice of ``prepared``, and retry once before falling
           back to ``SUMMARY_FALLBACK``.

        When ``or_drop=False`` (e.g. idle compaction), makes at most one request
        and leaves the conversation unchanged if no complete summary comes back.
        """
        from nekopower.runtime.model import ContextOverflowError, RequestRefusedError

        policy = self.policy
        # The request must fit the window itself: shrink tool results first,
        # then drop the oldest messages of the part being condensed.
        room = policy.window - policy.summary_cap - self.fixed_tokens - 2_000
        prepared, size = await asyncio.to_thread(_shrink_for_summary, list(region), policy, room)
        head_truncated = len(prepared) < len(region)
        if not or_drop and (head_truncated or size > room or _leaves_results_out(prepared)):
            # The oldest messages do not fit into the request, so a summary
            # would stand for a part it was never shown; or what is left
            # (one message, which is never left out) is too long for the
            # request by itself, and the request would be refused; or a
            # request would not be sent some tool result there (one with
            # no call, or the second of two of one call), and the
            # summary would stand for it all the same.
            logger.warning("the part to be summed up does not fit one request; the conversation is left as it is")
            return None, None, False, False

        def _handle_cut_short(cut: SummaryCutShort) -> tuple[str | None, Usage | None, bool, bool]:
            cleaned = extract_compaction_summary(cut.text)
            if not or_drop or not cleaned:
                logger.warning("context summary was cut short at its length limit")
                return (SUMMARY_FALLBACK if or_drop else None), None, False, head_truncated
            logger.warning("context summary was cut short at its length limit; kept as far as it got")
            return f"{cleaned}\n\n{SUMMARY_CUT_SHORT_NOTE}", None, False, head_truncated

        supports_retry_opts = self._summarizer_supports_retry_options()
        retry_no_tools = False
        retry_overflow: Exception | None = None
        summary = ""
        usage: Usage | None = None

        try:
            summary, usage = await self._call_summarizer(prepared, policy.summary_cap)
            cleaned = extract_compaction_summary(summary)
            if cleaned:
                return cleaned, usage, not head_truncated, head_truncated
            if or_drop and supports_retry_opts:
                retry_no_tools = True
            else:
                return (SUMMARY_FALLBACK if or_drop else None), usage, False, head_truncated
        except asyncio.CancelledError:
            raise
        except SummaryCutShort as cut:
            return _handle_cut_short(cut)
        except SummaryUsedToolError as exc:
            if or_drop and supports_retry_opts:
                logger.warning("context summary called a tool; retrying once without tools: %s", exc)
                retry_no_tools = True
            elif not or_drop:
                logger.warning("context summary failed; the conversation is left as it is: %s", exc)
                return None, None, False, False
            else:
                logger.warning("context summary failed, dropping the older span instead: %s", exc)
                return SUMMARY_FALLBACK, None, False, head_truncated
        except (ContextOverflowError, RequestRefusedError) as exc:
            if not or_drop:
                logger.warning("context summary refused; the conversation is left as it is: %s", exc)
                return None, None, False, False
            retry_overflow = exc
        except Exception as exc:  # noqa: BLE001 - a failed summary must not fail the run
            if not or_drop:
                logger.warning("context summary failed; the conversation is left as it is: %s", exc)
                return None, None, False, False
            logger.warning("context summary failed, dropping the older span instead: %s", exc)
            return SUMMARY_FALLBACK, None, False, head_truncated

        # Tier 2: retry once with tools disabled when model invoked a tool or returned empty text.
        if retry_no_tools:
            try:
                summary, usage = await self._call_summarizer(
                    prepared,
                    policy.summary_cap,
                    no_tools=True,
                    retry_note=texts.SUMMARY_NO_TOOLS_RETRY_PROMPT,
                )
                cleaned = extract_compaction_summary(summary)
                if cleaned:
                    return cleaned, usage, not head_truncated, head_truncated
                return SUMMARY_FALLBACK, usage, False, head_truncated
            except asyncio.CancelledError:
                raise
            except SummaryCutShort as cut:
                return _handle_cut_short(cut)
            except (ContextOverflowError, RequestRefusedError) as exc:
                retry_overflow = exc
            except Exception as exc:  # noqa: BLE001 - fall back cleanly
                logger.warning("no-tools context summary retry failed: %s", exc)
                return SUMMARY_FALLBACK, None, False, head_truncated

        # Tier 3: summary request itself overflowed; drop oldest slice and retry once.
        if retry_overflow is not None:
            if self.on_summary_overflow is not None and isinstance(retry_overflow, ContextOverflowError):
                try:
                    self.on_summary_overflow(str(retry_overflow), size + self.fixed_tokens)
                except Exception:  # noqa: BLE001 - learning the window is best effort
                    logger.debug("reporting summary overflow window failed", exc_info=True)
            if len(prepared) > 1:
                drop = max(1, (len(prepared) * 2) // 5)
                start = min(drop, len(prepared) - 1)
                while start < len(prepared) - 1 and isinstance(prepared[start], ToolResultMessage):
                    start += 1
                trimmed = prepared[start:]
                head_truncated = True
                logger.warning(
                    "context summary request overflowed (%s); dropping %d oldest messages and retrying once",
                    retry_overflow,
                    start,
                )
                try:
                    summary, usage = await self._call_summarizer(
                        trimmed,
                        policy.summary_cap,
                        no_tools=supports_retry_opts,
                    )
                    cleaned = extract_compaction_summary(summary)
                    if cleaned:
                        return cleaned, usage, False, head_truncated
                except asyncio.CancelledError:
                    raise
                except SummaryCutShort as cut:
                    return _handle_cut_short(cut)
                except Exception as exc:  # noqa: BLE001 - fall back cleanly
                    logger.warning("trimmed context summary retry failed: %s", exc)
            else:
                logger.warning("context summary failed, dropping the older span instead: %s", retry_overflow)
            return SUMMARY_FALLBACK, None, False, head_truncated

        return SUMMARY_FALLBACK, usage, False, head_truncated


def _leaves_results_out(messages: Sequence[AgentMessage]) -> bool:
    """Whether a request made of ``messages`` would not be sent some tool result among them.

    What a request is sent is put right first (``repair_tool_history``,
    as the session does before it asks for the summary): a result
    whose call is not there is left out, and so is one more than a
    call has. Asked of that, and not of the ids alone: two calls of
    one id, each with its own result, lose nothing.
    """
    repair = repair_tool_history(tuple(messages))
    return bool(repair.dropped_orphan_results or repair.dropped_duplicate_results)


def _shrink_for_summary(region: list[Row], policy: ContextPolicy, room: int) -> tuple[list[AgentMessage], int]:
    """Return the messages handed to the summarizer, cut to fit ``room`` tokens, and how long they are.

    The last message is never left out, whatever its length: the
    length returned says whether what is handed over fits.
    """
    messages: list[AgentMessage] = []
    for row in region:
        message = row.message
        if isinstance(message, ToolResultMessage):
            message = prune_result(message, policy) or message
        messages.append(message)
    sizes = [estimate_tokens(message, policy.image_tokens) for message in messages]
    start = 0
    total = sum(sizes)
    while total > room and start < len(messages) - 1:
        total -= sizes[start]
        start += 1
    # Do not open with a tool result whose call was dropped.
    while start < len(messages) - 1 and isinstance(messages[start], ToolResultMessage):
        start += 1
    return messages[start:], sum(sizes[start:])

