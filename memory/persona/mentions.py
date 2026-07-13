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
"""Mentions methods for the memory manager."""

from __future__ import annotations








from datetime import datetime, timedelta










from ._shared import (
    SUPPRESS_MENTION_LIMIT,
    SUPPRESS_WINDOW_HOURS,
    SUPPRESS_COOLDOWN_HOURS,
    _is_mentioned,
)

class MentionsMixin:
    def _apply_record_mentions(
        self,
        persona: dict,
        response_text: str,
        stop_names: list[str] | None = None,
    ) -> bool:
        now_str = datetime.now().isoformat()
        now = datetime.now()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False

        for entry in self._collect_all_entries(persona):
            if not isinstance(entry, dict):
                continue
            if entry.get('protected'):
                continue
            if not _is_mentioned(entry.get('text', ''), response_text, stop_names=stop_names):
                continue

            mentions = entry.get('recent_mentions', [])
            mentions.append(now_str)
            mentions = [t for t in mentions if self._in_window(t, cutoff)]
            entry['recent_mentions'] = mentions

            if not entry.get('suppress') and len(mentions) > SUPPRESS_MENTION_LIMIT:
                entry['suppress'] = True
                entry['suppressed_at'] = now_str
            changed = True
        return changed

    def record_mentions(self, name: str, response_text: str) -> None:
        """After a proactive delivery, scan which persona entries the response mentioned.

        Core logic: mentioned > SUPPRESS_MENTION_LIMIT times within 5 hours → suppress.
        Stop-names are stripped before ``_is_mentioned``, so master/lanlan being
        called once per turn doesn't mark every unrelated fact as mentioned.
        """
        persona = self.ensure_persona(name)
        stop_names = self._get_entity_stop_names(name)
        if self._apply_record_mentions(persona, response_text, stop_names=stop_names):
            self.save_persona(name, persona)

    async def arecord_mentions(self, name: str, response_text: str) -> None:
        stop_names = await self._aget_entity_stop_names(name)
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            if self._apply_record_mentions(persona, response_text, stop_names=stop_names):
                await self.asave_persona(name, persona)

    def _apply_update_suppressions(self, persona: dict) -> bool:
        now = datetime.now()
        cutoff = now - timedelta(hours=SUPPRESS_WINDOW_HOURS)
        changed = False

        for entry in self._collect_all_entries(persona):
            if not isinstance(entry, dict):
                continue

            mentions = entry.get('recent_mentions', [])
            cleaned = [t for t in mentions if self._in_window(t, cutoff)]
            if len(cleaned) != len(mentions):
                entry['recent_mentions'] = cleaned
                changed = True

            if entry.get('suppress'):
                suppressed_str = entry.get('suppressed_at')
                if suppressed_str:
                    try:
                        hours_since = (now - datetime.fromisoformat(suppressed_str)).total_seconds() / 3600
                        if hours_since >= SUPPRESS_COOLDOWN_HOURS:
                            entry['suppress'] = False
                            entry['suppressed_at'] = None
                            entry['recent_mentions'] = []
                            changed = True
                    except (ValueError, TypeError):
                        pass
        return changed

    def update_suppressions(self, name: str) -> None:
        """Refresh suppress states: cooldown elapsed → lift; prune recent_mentions outside the window."""
        persona = self.ensure_persona(name)
        if self._apply_update_suppressions(persona):
            self.save_persona(name, persona)

    async def aupdate_suppressions(self, name: str) -> None:
        """P2.a.2: persona.json write-back must happen under the character lock,
        avoiding races with aadd_fact / arecord_mentions / aresolve_corrections."""
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            if self._apply_update_suppressions(persona):
                await self.asave_persona(name, persona)

    @staticmethod
    def _in_window(ts_str: str, cutoff: datetime) -> bool:
        try:
            return datetime.fromisoformat(ts_str) >= cutoff
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _collect_all_entries(persona: dict) -> list[dict]:
        """Collect references to the facts entries of every entity section in the persona."""
        entries = []
        for section in persona.values():
            if isinstance(section, dict):
                entries.extend(section.get('facts', []))
        return entries
