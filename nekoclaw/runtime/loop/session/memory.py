# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/session/memory.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed in nekoclaw/runtime/loop/__init__.py.

"""In-memory session state reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from nekoclaw.runtime.loop.messages import AgentMessage, CustomMessage, UserMessage
from nekoclaw.runtime.loop.session.entries import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    SessionEntry,
    SessionInfoEntry,
)
from nekoclaw.runtime.loop.session.tree import path_to_entry

_UNSET_LEAF_ID: Final[object] = object()


@dataclass(frozen=True, slots=True)
class SessionState:
    """Current session state derived from append-only entries."""

    messages: tuple[AgentMessage, ...]
    model: str | None
    provider: str | None
    thinking_level: str | None
    labels_by_id: dict[str, str]
    label_timestamps_by_id: dict[str, float]
    active_leaf_id: str | None
    session_info: SessionInfoEntry | None
    custom_entries: tuple[CustomEntry, ...]
    compaction_entries: tuple[CompactionEntry, ...]
    context_entry_ids: tuple[str, ...]
    entries: tuple[SessionEntry, ...]

    @classmethod
    def from_entries(
        cls,
        entries: list[SessionEntry],
        *,
        leaf_id: str | None | object = _UNSET_LEAF_ID,
    ) -> SessionState:
        """Replay the branch ending at the active entry.

        By default the active entry is the last non-leaf entry in file order.
        Historical ``leaf`` records remain readable but never select the tip.
        An explicit `leaf_id` supports in-memory tree navigation; passing
        ``None`` selects the empty path before the first root entry.
        """
        resolved_leaf_id = (
            _last_non_leaf_id(entries) if leaf_id is _UNSET_LEAF_ID else cast(str | None, leaf_id)
        )
        replay_entries = (
            path_to_entry(entries, resolved_leaf_id) if resolved_leaf_id is not None else []
        )

        message_rows: list[tuple[str, AgentMessage]] = []
        model: str | None = None
        provider: str | None = None
        thinking_level: str | None = None
        labels_by_id, label_timestamps_by_id = _resolve_labels(entries)
        active_leaf_id: str | None = resolved_leaf_id
        session_info: SessionInfoEntry | None = None
        custom_entries: list[CustomEntry] = []
        compaction_entries: list[CompactionEntry] = []

        for entry_index, entry in enumerate(replay_entries):
            match entry.type:
                case "message":
                    message_rows.append((entry.id, entry.message))
                case "custom_message":
                    message_rows.append(
                        (
                            entry.id,
                            CustomMessage(
                                custom_type=entry.custom_type,
                                content=entry.content,
                                display=entry.display,
                                details=entry.details,
                                timestamp=round(entry.timestamp * 1000),
                            ),
                        )
                    )
                case "model_change":
                    model = entry.model
                    if entry.provider is not None:
                        provider = entry.provider
                case "thinking_level_change":
                    thinking_level = entry.thinking_level
                case "label":
                    pass  # Resolved globally above so off-path bookmarks remain visible.
                case "leaf":
                    pass  # Backward-compatible historical record; never selects the tip.
                case "session_info":
                    session_info = entry
                case "custom":
                    custom_entries.append(entry)
                case "compaction":
                    compaction_entries.append(entry)
                    message_rows = _apply_compaction(
                        message_rows,
                        entry,
                        path_before=replay_entries[:entry_index],
                    )
                case "branch_summary":
                    message_rows.append(
                        (entry.id, UserMessage(content=_format_branch_summary(entry)))
                    )

        return cls(
            messages=tuple(message for _entry_id, message in message_rows),
            model=model,
            provider=provider,
            thinking_level=thinking_level,
            labels_by_id=labels_by_id,
            label_timestamps_by_id=label_timestamps_by_id,
            active_leaf_id=active_leaf_id,
            session_info=session_info,
            custom_entries=tuple(custom_entries),
            compaction_entries=tuple(compaction_entries),
            context_entry_ids=tuple(entry_id for entry_id, _message in message_rows),
            entries=tuple(replay_entries),
        )


def _resolve_labels(entries: list[SessionEntry]) -> tuple[dict[str, str], dict[str, float]]:
    labels: dict[str, str] = {}
    timestamps: dict[str, float] = {}
    for entry in entries:
        if entry.type != "label":
            continue
        label = entry.label.strip() if entry.label is not None else ""
        if label:
            labels[entry.target_id] = label
            timestamps[entry.target_id] = entry.timestamp
        else:
            labels.pop(entry.target_id, None)
            timestamps.pop(entry.target_id, None)
    return labels, timestamps


def _last_non_leaf_id(entries: list[SessionEntry]) -> str | None:
    for entry in reversed(entries):
        if entry.type != "leaf":
            return entry.id
    return None


def _apply_compaction(
    message_rows: list[tuple[str, AgentMessage]],
    entry: CompactionEntry,
    *,
    path_before: list[SessionEntry],
) -> list[tuple[str, AgentMessage]]:
    summary_row = (entry.id, UserMessage(content=_format_compaction_summary(entry.summary)))

    # Tau originally persisted arbitrary replacement-id sets. Explicit legacy
    # fields take precedence, including an empty list, so old sessions retain
    # their exact replay behavior.
    if "replaces_entry_ids" in entry.model_fields_set:
        replaced_ids = set(entry.replaces_entry_ids)
        retained: list[tuple[str, AgentMessage]] = []
        inserted_summary = False
        for entry_id, message in message_rows:
            if entry_id not in replaced_ids:
                retained.append((entry_id, message))
                continue
            if not inserted_summary:
                retained.append(summary_row)
                inserted_summary = True
        if not inserted_summary:
            retained.append(summary_row)
        return retained

    # Pi resolves the boundary against the complete active path, not only
    # message-producing entries. Reorder retained context by that path as well:
    # a previous compaction can itself occur after the kept boundary.
    first_kept_index = next(
        (
            index
            for index, path_entry in enumerate(path_before)
            if path_entry.id == entry.first_kept_entry_id
        ),
        None,
    )
    if first_kept_index is None:
        return [summary_row]

    retained_by_id = dict(message_rows)
    retained = [
        (path_entry.id, retained_by_id[path_entry.id])
        for path_entry in path_before[first_kept_index:]
        if path_entry.id in retained_by_id
    ]
    return [summary_row, *retained]


def _format_compaction_summary(summary: str) -> str:
    return f"Previous conversation summary:\n{summary}"


def _format_branch_summary(entry: BranchSummaryEntry) -> str:
    return (
        "The following is a summary of a branch that this conversation came back from:\n"
        f"<summary>\n{entry.summary}\n</summary>"
    )
