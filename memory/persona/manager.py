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
from .mentions import MentionsMixin
from .rendering import RenderingMixin


class PersonaManager(
    PersistenceMixin,
    FactsMixin,
    CorrectionsMixin,
    RefinementMixin,
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
        # 独立的 resolve_corrections 串行锁——只为防多入口（IdleMaint subtask 2
        # 与 _run_post_turn_signals）并发触发同名角色的 LLM 重叠
        # 应用导致重复处理同一批 corrections。本锁与 _alocks (data lock)
        # 完全分开，所以 LLM 期间 aadd_fact / arecord_mentions / aapply_signal
        # 仍能正常拿 _alocks 推进（不再卡 /process 路径）。
        self._resolve_alocks: dict[str, asyncio.Lock] = {}
        # memory-evidence-rfc §3.3.3：evidence 写路径必须走 record_and_save，
        # 保证 event↔view 合约。event_log 注入；None 时 aapply_signal 不可用。
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
