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
"""Passive turn context for ``LLMSessionManager``: the generic hook that
lets providers (knowledge, corpus, memories) add context to one text reply.

A text turn (typed, or transcribed by the independent ASR) starts every
provider as soon as its words are known (``_start_turn_context``) and
collects them right before the reply is sent (``_collect_turn_context``),
under one shared budget. A turn superseded meanwhile (a newer message took
over, the route left Core) drops what came back. Voice turns answered by a
realtime model never come here: only the offline client carries the blocks.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import time

from main_logic.omni_offline_client import OmniOfflineClient
from main_logic import turn_context as _providers
from ._shared import logger

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


class TurnContextMixin:
    """Passive turn context methods (see module docstring)."""

    def _start_turn_context(self, text, *, session=None, ingress_time=None):
        """Start the providers for one text turn answered by ``session``
        (default: the current one). None when that session cannot carry the
        blocks or there is nothing to look up; never raises.

        ``ingress_time`` (wall clock) is when the message reached Main: the
        shared budget counts from there, so time spent preparing the session
        comes out of it.
        """
        session = self.session if session is None else session
        if not isinstance(session, OmniOfflineClient):
            return None
        try:
            from main_logic import public_knowledge

            started_at = (
                getattr(self, "_turn_context_conversation_started_at", None)
                or getattr(self, "session_start_time", None)
            )
            started = None
            if isinstance(ingress_time, (int, float)):
                started = time.monotonic() - max(time.time() - float(ingress_time), 0.0)
            job = _providers.start_turn_context(
                port=self.memory_server_port,
                lanlan_name=self.lanlan_name,
                text=text,
                language=getattr(self, "user_language", None),
                exclude_card_ids=session.resident_turn_context_ids(),
                knowledge_available=public_knowledge.tool_available(),
                session_started_at=started_at.timestamp() if started_at else None,
                settings_loader=_core_facade.aload_global_conversation_settings,
                started=started,
            )
            if job is not None:
                job.session = session
            return job
        except Exception as exc:
            logger.warning(
                "[%s] turn context could not start: %s", self.lanlan_name, type(exc).__name__,
            )
            return None

    async def _collect_turn_context(self, job, *, still_current):
        """What the providers of ``job`` brought back by its deadline, as a
        ``TurnContext`` for ``stream_text``; None when nothing came back, the
        session is no longer the one the lookup was made for, or
        ``still_current()`` says the turn was superseded meanwhile."""
        if job is None:
            return None
        try:
            results = await _providers.collect_turn_context(job)
        except asyncio.CancelledError:
            job.cancel()
            raise
        except Exception as exc:
            job.cancel()
            logger.warning(
                "[%s] turn context collection failed: %s", self.lanlan_name, type(exc).__name__,
            )
            return None
        elapsed_ms = int((time.monotonic() - job.started) * 1000)
        if self.session is not job.session or not still_current():
            # Another turn took over while the providers ran: whatever came
            # back answers a message that is no longer the one being replied to.
            logger.info(
                "[%s] turn context dropped: turn superseded after %dms",
                self.lanlan_name, elapsed_ms,
            )
            return None
        context = _providers.build_turn_context(results, job.language)
        logger.info(
            "[%s] turn context %s (%dms)",
            self.lanlan_name, _providers.describe(results, context), elapsed_ms,
        )
        return context
