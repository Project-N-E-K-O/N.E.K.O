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
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime.loop.messages import (
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
from nekoclaw.runtime.loop.tools import AgentToolResult
from nekoclaw.runtime.tools.base import SpillStore

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


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Token budgets of one session, derived from the model's window."""

    window: int
    max_output: int
    threshold_ratio: float = 0.8
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
        return min(65_536, self.window // 8)

    @property
    def threshold(self) -> int:
        """Conversation size above which pruning and summarizing start."""
        by_ratio = int(self.window * self.threshold_ratio)
        by_reserve = self.window - self.max_output - self.headroom
        return max(1_000, min(by_ratio, by_reserve))

    @property
    def retain_tokens(self) -> int:
        """Size of the newest part of the conversation a summary leaves untouched."""
        return max(500, int((self.window - self.max_output) * self.retain_ratio))

    @property
    def result_cap(self) -> int:
        """Per-result cap, never more than a quarter of the window."""
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


def _with_text(message: ToolResultMessage, text: str) -> ToolResultMessage:
    images = [block for block in message.content if isinstance(block, ImageContent)]
    return message.model_copy(update={"content": [TextContent(text=text), *images]})


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
            pruned = prune_text(message.text, policy)
            if pruned is not None:
                out.append(Row(row.entry_id, row.kind, _with_text(message, pruned)))
                changed = True
                continue
        out.append(row)
    return out if changed else None


# ---------------------------------------------------------------------------
# Image retention
# ---------------------------------------------------------------------------


def retain_recent_images(rows: Sequence[Row], retention: dict[str, int]) -> list[Row] | None:
    """Keep only the newest image results of the listed tools.

    ``retention`` maps a tool name to how many of its results keep their
    images. Older ones get a text placeholder. Returns ``None`` if unchanged.
    """
    if not retention:
        return None
    remaining = dict(retention)
    changed = False
    out: list[Row] = list(rows)
    for index in range(len(out) - 1, -1, -1):
        message = out[index].message
        if not isinstance(message, ToolResultMessage) or message.tool_name not in remaining:
            continue
        if not any(isinstance(block, ImageContent) for block in message.content):
            continue
        if remaining[message.tool_name] > 0:
            remaining[message.tool_name] -= 1
            continue
        content = [
            block if isinstance(block, TextContent) else TextContent(text=texts.IMAGE_OMITTED_PLACEHOLDER)
            for block in message.content
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


def frame_summary(summary: str) -> str:
    """Wrap a model-written summary as the checkpoint the conversation continues from."""
    return (
        f"{texts.CHECKPOINT_PREAMBLE}\n\n{texts.SUMMARY_OPEN_TAG}\n{summary.strip()}\n"
        f"{texts.SUMMARY_CLOSE_TAG}"
    )


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


def select_user_rows(region: Sequence[Row], sizes: Sequence[int], budget: int) -> set[str]:
    """Pick the most recent user messages of ``region`` that fit ``budget`` tokens."""
    kept: set[str] = set()
    used = 0
    for index in range(len(region) - 1, -1, -1):
        row = region[index]
        if row.kind != "human":
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
        text = result.text
        capped = await asyncio.to_thread(
            cap_result_text, text, self.policy.result_cap, self.spill, f"{tool_name}-{call_id}"
        )
        if capped is None:
            return result
        images = [block for block in result.content if isinstance(block, ImageContent)]
        return result.model_copy(update={"content": [TextContent(text=capped), *images]})

    # -- layers 2 and 3 -------------------------------------------------------

    async def fit(
        self,
        transcript: Transcript,
        *,
        force: bool = False,
        on_compaction: Callable[[Compaction], None] | None = None,
    ) -> list[Compaction]:
        """Bring the conversation under the threshold; rewrites ``transcript`` in place.

        ``force`` is the overflow path: prune everything and summarize even if
        the estimate says the conversation fits. Returns the summaries made.

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
        trimmed = retain_recent_images(rows, self.image_retention)
        if trimmed is not None:
            transcript.rewrite(trimmed)
            self.invalidate()
        if not force and await self.measure(transcript.messages) <= self.policy.threshold:
            return []

        pruned = await asyncio.to_thread(
            prune_rows, transcript.rows(), self.policy, include_latest=force
        )
        if pruned is not None:
            transcript.rewrite(pruned)
            self.invalidate()
            if not force and await self.measure(transcript.messages) <= self.policy.threshold:
                return []

        done: list[Compaction] = []
        for attempt in range(2):
            tokens_before = await self.measure(transcript.messages)
            compaction = await self._compact(
                transcript.rows(), tokens_before, tight=force or attempt > 0
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
            if await self.measure(transcript.messages) <= self.policy.threshold:
                break
        return done

    async def _compact(
        self, rows: list[Row], tokens_before: int, *, tight: bool
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
        kept_ids = select_user_rows(region, sizes[:cut], policy.user_keep // (2 if tight else 1))
        replaced = [row for row in region if row.entry_id not in kept_ids]
        if not any(row.kind in ("assistant", "tool", "notice") for row in replaced):
            # Nothing but earlier summaries or oversized user text: no gain.
            return None

        summary, usage = await self._summarize_region(replaced)
        stored = frame_summary(summary)
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
        )

    async def _summarize_region(self, region: Sequence[Row]) -> tuple[str, Usage | None]:
        """Ask the model for a checkpoint of ``region``; never raises."""
        policy = self.policy
        # The request must fit the window itself: shrink tool results first,
        # then drop the oldest messages of the part being condensed.
        room = policy.window - policy.summary_cap - self.fixed_tokens - 2_000
        prepared = await asyncio.to_thread(_shrink_for_summary, list(region), policy, room)
        try:
            summary, usage = await self.summarize(prepared, policy.summary_cap)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed summary must not fail the run
            logger.warning("context summary failed, dropping the older span instead: %s", exc)
            return SUMMARY_FALLBACK, None
        if not summary.strip():
            return SUMMARY_FALLBACK, usage
        return summary, usage


def _shrink_for_summary(region: list[Row], policy: ContextPolicy, room: int) -> list[AgentMessage]:
    """Return the messages handed to the summarizer, cut to fit ``room`` tokens."""
    messages: list[AgentMessage] = []
    for row in region:
        message = row.message
        if isinstance(message, ToolResultMessage):
            pruned = prune_text(message.text, policy)
            if pruned is not None:
                message = _with_text(message, pruned)
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
    return messages[start:]

