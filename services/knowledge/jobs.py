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


"""Shared task records and terminal states for knowledge operations.

The service still owns admission, persistence, queueing and shutdown. Export
and indexing helpers consume these records without introducing another job
registry or changing their durable JSON representation.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

from .subscriptions import KnowledgeSubscription

ACTIVE_JOB_STATES = frozenset({"queued", "building"})
TERMINAL_JOB_STATES = frozenset({"active", "failed", "cancelled"})


@dataclass(slots=True)
class ImportJob:
    """One shared import/export record; its gate arbitrates cancel and commit."""

    job_id: str
    pack_id: str
    state: str
    created_at: str
    updated_at: str
    entries_total: int = 0
    chunks_total: int = 0
    reason: str = ""
    cancel_requested: bool = False
    arrived_at: int = 0
    staged_bytes: int = 0
    staged_sha256: str = ""
    # Set once the index write commits; from then on the import is no longer
    # cancellable. The lock makes "cancel" and "commit" exclusive.
    committed: bool = False
    # Registry/artifact publication is the user-visible commit point. The
    # earlier SQLite gate alone does not prove an install became active.
    published: bool = False
    # Market metadata of a subscription install; None for a local import.
    subscription: KnowledgeSubscription | None = None
    kind: str = "pack_import"
    origin: str = ""
    initial_enabled: bool | None = None
    archive_sha256: str = ""
    result: dict[str, Any] | None = None
    seed_vectors: list[dict] = field(default_factory=list, repr=False, compare=False)
    # Keep the persisted format only to reject retries of retired text exports;
    # every newly admitted export is a complete v2 package.
    export_format: int = 2
    export_version: str = "1"
    # Explicitly deleted/discarded jobs cannot be revived by a worker flush.
    # Evicted history is still persisted, so absence from _jobs is not enough.
    export_deleted: bool = field(default=False, repr=False, compare=False)
    frozen_input: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    gate: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "pack_id": self.pack_id,
            "state": self.state,
            "reason": self.reason,
            "entries_total": self.entries_total,
            "chunks_total": self.chunks_total,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "subscription": self.subscription.to_json() if self.subscription else None,
            "kind": self.kind,
            "result": self.result,
        }


def consume_task_result(task: asyncio.Task[Any]) -> None:
    """Drain exceptions of owned tasks whose caller may stop waiting."""
    if not task.cancelled():
        task.exception()
