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
"""Manager assembly methods for the memory manager."""

from __future__ import annotations

import asyncio





import threading








from utils.config_manager import get_config_manager





from .persistence import PersistenceMixin
from .facts import FactsMixin
from .corrections import CorrectionsMixin
from .refinement import RefinementMixin
from .fusion import ExternalFusionMixin
from .mentions import MentionsMixin
from .rendering import RenderingMixin


class PersonaManager(
    PersistenceMixin,
    FactsMixin,
    CorrectionsMixin,
    RefinementMixin,
    ExternalFusionMixin,
    MentionsMixin,
    RenderingMixin,
):
    """Manages per-character persona files with dynamic entity sections.

    Core entities: 'master', 'neko', 'relationship'.
    Storage is entity-agnostic — any string key is accepted as an entity.
    Each entity section is ``{entity: {'facts': [...]}}``.
    """
    FACT_ADDED = 'added'
    FACT_REJECTED_CARD = 'rejected_card'
    FACT_QUEUED_CORRECTION = 'queued'
    def __init__(self, event_log=None):
        self._config_manager = get_config_manager()
        self._personas: dict[str, dict] = {}
        # Per-character asyncio.Lock (P2.a.2). Protects load→mutate→save
        # sequences in add_fact / resolve_corrections / record_mentions /
        # queue_correction. Lazily created to avoid event-loop binding at
        # module-import time. threading.Lock guards the dict itself
        # (pure-Python block, no await inside).
        self._alocks: dict[str, asyncio.Lock] = {}
        self._alocks_guard = threading.Lock()
        # Separate resolve_corrections serialization lock: prevents multiple entry points (IdleMaint subtask 2
        # and _run_post_turn_signals) from overlapping same-character LLM calls and applications,
        # processing the same corrections twice. Entirely separate from _alocks (data lock),
        # so aadd_fact / arecord_mentions / aapply_signal can still acquire _alocks
        # during LLM work without blocking the /process path.
        self._resolve_alocks: dict[str, asyncio.Lock] = {}
        # memory-evidence-rfc §3.3.3: evidence writes must use record_and_save,
        # preserving the event↔view contract. event_log is injected; None disables aapply_signal.
        self._event_log = event_log

    def _get_alock(self, name: str) -> asyncio.Lock:
        """Per-character asyncio.Lock; lazy + DCL-guarded.

        See reflection.py:_get_alock for full rationale. Thread-safety
        scope: event-loop-only caller. asyncio.Lock binds to the running
        loop at first acquire on CPython 3.10+.
        """
        if name not in self._alocks:
            with self._alocks_guard:
                if name not in self._alocks:
                    self._alocks[name] = asyncio.Lock()
        return self._alocks[name]

    def _get_resolve_alock(self, name: str) -> asyncio.Lock:
        """Per-character asyncio.Lock dedicated to serializing resolve_corrections.

        Only serializes concurrency between resolve_corrections calls; it does
        **not** interlock with the data lock (_get_alock). The LLM call runs
        inside this lock but outside the data lock — the data lock is only
        borrowed for the short critical sections before/after the LLM.
        """
        if name not in self._resolve_alocks:
            with self._alocks_guard:
                if name not in self._resolve_alocks:
                    self._resolve_alocks[name] = asyncio.Lock()
        return self._resolve_alocks[name]
