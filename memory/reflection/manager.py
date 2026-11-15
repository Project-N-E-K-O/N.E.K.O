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
from typing import TYPE_CHECKING

from utils.config_manager import get_config_manager
from memory.persona import (
    PersonaManager,
)

if TYPE_CHECKING:
    from memory.event_log import EventLog
    from memory.facts import FactStore

from .persistence import PersistenceMixin
from .synthesis import SynthesisMixin
from .refinement import RefinementMixin
from .evidence_flow import EvidenceFlowMixin
from .surfacing import SurfacingMixin
from .promotion import PromotionMixin
from .promotion_merge import PromotionMergeMixin


class ReflectionEngine(
    PersistenceMixin,
    SynthesisMixin,
    RefinementMixin,
    EvidenceFlowMixin,
    SurfacingMixin,
    PromotionMixin,
    PromotionMergeMixin,
):
    """Synthesizes facts into reflections and manages the pending → confirmed lifecycle."""

    _UPGRADABLE_FEEDBACK = {None, 'confirmed', 'auto_confirmed'}

    def __init__(
        self, fact_store: FactStore, persona_manager: PersonaManager,
        event_log: EventLog | None = None,
    ):
        self._config_manager = get_config_manager()
        self._fact_store = fact_store
        self._persona_manager = persona_manager
        # memory-evidence-rfc section 3.3.3: evidence writes must go through record_and_save.
        # Inject event_log; when None, aapply_signal is unavailable (cold-start/pure unit-test paths can
        # still use synthesize, auto_promote, and other methods that do not touch evidence).
        self._event_log = event_log
        # Per-character asyncio.Lock (P2.a.2). ReflectionEngine's async mutating
        # methods span multiple awaits (e.g. aauto_promote_stale calls
        # persona.aadd_fact across an await boundary) — so asyncio.Lock is the
        # right choice per the CLAUDE rule: when threading.Lock spans an await, switch to
        # asyncio.Lock". Lock is lazily created to avoid event-loop binding
        # at module-import time.
        self._alocks: dict[str, asyncio.Lock] = {}
        # threading.Lock guards the dict itself (reads/writes of _alocks are
        # pure Python, no await inside this critical section).
        self._alocks_guard = threading.Lock()
        # In-process synthesis-failure backoff mirror {name: {key: {"n", "at"}}}, the authoritative working copy
        # within the session; disk provides only persistence and restart recovery. Thus even if synth_backoff.json
        # cannot be written (read-only filesystem/permissions), failure counts remain and the dead-letter gate still works
        # (Codex P2). Match review's in-process _maint_state persistence semantics.
        self._synth_backoff_mem: dict[str, dict] = {}
        # Process-local erase generations for scoped subjects. A synthesis
        # captures one before its out-of-lock LLM call and rechecks it before
        # append, so a concurrent scoped_forget can invalidate late results.
        self._subject_forget_epochs: dict[tuple[str, str, str], int] = {}
        self._active_subject_forgets: set[tuple[str, str, str]] = set()

    def _get_alock(self, name: str) -> asyncio.Lock:
        """Get (or lazily create) the per-character asyncio.Lock.

        Thread-safety scope: this method is called from the single
        FastAPI event-loop thread, never from asyncio.to_thread workers.
        The outer `name not in self._alocks` check is therefore single-
        threaded by construction. The inner check inside the guard is
        for multi-loop robustness (e.g. test harnesses that spin up a
        fresh loop per test). Matches the DCL pattern already used in
        facts.py / outbox.py / cursors.py.

        asyncio.Lock binding: on CPython 3.10+ Lock binds to the running
        loop at first `acquire`/`__aenter__`, not at `__init__`. Lazy
        construction here is defensive for 3.9 and cleaner for fresh-
        loop tests; not strictly required on the target 3.11 runtime.
        """
        if name not in self._alocks:
            with self._alocks_guard:
                if name not in self._alocks:
                    self._alocks[name] = asyncio.Lock()
        return self._alocks[name]
