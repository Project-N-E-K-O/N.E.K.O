# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/session/tree.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed in nekoclaw/runtime/loop/__init__.py.

"""Session tree traversal helpers."""

from __future__ import annotations

from nekoclaw.runtime.loop.session.entries import SessionEntry


class SessionTreeError(ValueError):
    """Raised when session entries do not form a valid traversable tree."""


def entries_by_id(entries: list[SessionEntry]) -> dict[str, SessionEntry]:
    """Return entries keyed by id, rejecting duplicates."""
    result: dict[str, SessionEntry] = {}
    for entry in entries:
        if entry.id in result:
            raise SessionTreeError(f"Duplicate session entry id: {entry.id}")
        result[entry.id] = entry
    return result


def path_to_entry(entries: list[SessionEntry], leaf_id: str) -> list[SessionEntry]:
    """Return the root-to-leaf path for `leaf_id`."""
    by_id = entries_by_id(entries)
    path: list[SessionEntry] = []
    seen: set[str] = set()
    current_id: str | None = leaf_id

    while current_id is not None:
        if current_id in seen:
            raise SessionTreeError(f"Cycle detected at session entry: {current_id}")
        seen.add(current_id)
        entry = by_id.get(current_id)
        if entry is None:
            raise SessionTreeError(f"Missing session entry: {current_id}")
        path.append(entry)
        current_id = entry.parent_id

    path.reverse()
    return path
