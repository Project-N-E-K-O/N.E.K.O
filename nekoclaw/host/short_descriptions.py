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

"""Short descriptions for plugins whose manifest has none.

The coarse screen lists every plugin by its id and its short description;
a plugin that ships none is listed by the start of its description. When that
description is longer than the coarse screen reads, the small model writes a
short one, once per plugin and description, and it is kept in
``plugin_short_descriptions.json`` next to the ledger.

Nothing waits for it. Listing the plugins applies what is already written and
starts writing the missing ones in the background; until one is there, or
when writing it fails, the plugin is listed by its description as before. It
is never written at startup: only once something lists the plugins.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILE_VERSION = 1


def description_key(description: str) -> str:
    """Return what a written short description is kept under: a hash of the whole description."""
    return hashlib.sha256((description or "").encode("utf-8")).hexdigest()


class ShortDescriptions:
    """Writes, keeps and applies short descriptions of plugins.

    ``llm`` is a ``DecisionLLM``; without one, only what is on disk is
    applied. ``path`` is called in a worker thread (resolving the data root
    reads configuration).
    """

    def __init__(self, path: Callable[[], Path], llm: Any = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._path = path
        self._llm = llm
        self._clock = clock
        # plugin id -> (description key, short description)
        self._written: dict[str, tuple[str, str]] | None = None
        self._loading: asyncio.Lock | None = None
        self._writing_file: asyncio.Lock | None = None
        # plugin id -> when it failed; tried again after a while.
        self._failed_at: dict[str, float] = {}
        self._in_flight: set[str] = set()
        # description key -> whether it is longer than the coarse screen reads.
        self._long: dict[str, bool] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def apply(self, plugins: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the plugins, each without a short description given the one written for it.

        Starts writing, in the background, the ones still missing. The
        plugins themselves are not changed: a plugin given a short
        description is a copy.
        """
        written = await self._load()
        result: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for plugin in plugins:
            if str(plugin.get("short_description") or "").strip():
                result.append(plugin)
                continue
            plugin_id = str(plugin.get("id") or "")
            description = str(plugin.get("description") or "").strip()
            known = written.get(plugin_id)
            if known is not None and known[0] == description_key(description):
                result.append({**plugin, "short_description": known[1]})
                continue
            result.append(plugin)
            if plugin_id and self._worth_writing(description):
                missing.append(plugin)
        self._start_writing(missing)
        return result

    def _worth_writing(self, description: str) -> bool:
        """Whether a short description would say it in fewer words than the coarse screen reads.

        The coarse screen reads up to ``AGENT_PLUGIN_SHORTDESC_MAX_TOKENS`` of
        a plugin's text. A description within that is read whole already; one
        written for it would cost a request and could come out longer.
        """
        from config import AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
        from utils.tokenize import count_tokens

        if not description:
            return False
        key = description_key(description)
        long = self._long.get(key)
        if long is None:
            long = count_tokens(description) > AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
            self._long[key] = long
        return long

    async def aclose(self) -> None:
        """Stop writing; what was written is already on disk."""
        self._closed = True
        tasks, self._tasks = list(self._tasks), set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- keeping ------------------------------------------------------------

    async def _load(self) -> dict[str, tuple[str, str]]:
        if self._written is not None:
            return self._written
        if self._loading is None:
            self._loading = asyncio.Lock()
        async with self._loading:
            if self._written is None:
                self._written = await asyncio.to_thread(self._read)
        return self._written

    def _read(self) -> dict[str, tuple[str, str]]:
        import json

        try:
            path = self._path()
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as exc:  # noqa: BLE001 - an unreadable file is written anew
            logger.warning("reading the plugin short descriptions failed (exc_type=%s)", type(exc).__name__)
            return {}
        entries = data.get("entries") if isinstance(data, dict) else None
        written: dict[str, tuple[str, str]] = {}
        for plugin_id, item in (entries or {}).items() if isinstance(entries, dict) else ():
            if isinstance(plugin_id, str) and isinstance(item, dict):
                key, short = item.get("key"), item.get("short")
                if isinstance(key, str) and isinstance(short, str) and short:
                    written[plugin_id] = (key, short)
        return written

    def _write(self, written: dict[str, tuple[str, str]]) -> None:
        from utils.file_utils import atomic_write_json

        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            path,
            {
                "version": _FILE_VERSION,
                "entries": {plugin_id: {"key": key, "short": short} for plugin_id, (key, short) in written.items()},
            },
            ensure_ascii=False,
            indent=2,
        )

    # -- writing ------------------------------------------------------------

    def _start_writing(self, missing: list[dict[str, Any]]) -> None:
        from config.nekoclaw_settings import PLUGIN_SHORT_DESCRIPTION_RETRY_SECONDS

        if self._llm is None or self._closed or not missing:
            return
        now = self._clock()
        due = []
        for plugin in missing:
            plugin_id = str(plugin.get("id"))
            failed_at = self._failed_at.get(plugin_id)
            if plugin_id in self._in_flight:
                continue
            if failed_at is not None and now - failed_at < PLUGIN_SHORT_DESCRIPTION_RETRY_SECONDS:
                continue
            due.append(plugin)
        if not due:
            return
        self._in_flight |= {str(plugin.get("id")) for plugin in due}
        task = asyncio.ensure_future(self._write_missing(due))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _write_missing(self, plugins: list[dict[str, Any]]) -> None:
        """Write the short descriptions of ``plugins``, one request each, and keep each on disk."""
        try:
            for plugin in plugins:
                plugin_id = str(plugin.get("id"))
                description = str(plugin.get("description") or "").strip()
                try:
                    short = await self._ask(plugin_id, description)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the plugin is listed by its description meanwhile
                    logger.debug("writing a short description for %s failed (exc_type=%s)", plugin_id, type(exc).__name__)
                    short = ""
                finally:
                    self._in_flight.discard(plugin_id)
                if not short:
                    self._failed_at[plugin_id] = self._clock()
                    continue
                self._failed_at.pop(plugin_id, None)
                written = await self._load()
                written[plugin_id] = (description_key(description), short)
                await self._keep(dict(written))
                # Never the text itself in the log: it is the model's.
                logger.info("wrote a short description for plugin %s (%d chars)", plugin_id, len(short))
        finally:
            for plugin in plugins:
                self._in_flight.discard(str(plugin.get("id")))

    async def _ask(self, plugin_id: str, description: str) -> str:
        """Return the model's short description, or empty when it is unusable."""
        from config import AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
        from config.nekoclaw_settings import PLUGIN_SHORT_DESCRIPTION_INPUT_MAX_TOKENS
        from config.prompts.prompts_nekoclaw_brain import (
            PLUGIN_SHORT_DESCRIPTION_SYSTEM_PROMPT,
            PLUGIN_SHORT_DESCRIPTION_USER_TEMPLATE,
        )
        from nekoclaw.host.decision import TIER_SHORT_DESCRIPTION
        from utils.tokenize import count_tokens, truncate_to_tokens

        messages = [
            {"role": "system", "content": PLUGIN_SHORT_DESCRIPTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": PLUGIN_SHORT_DESCRIPTION_USER_TEMPLATE.format(
                    plugin_id=plugin_id,
                    description=truncate_to_tokens(description, PLUGIN_SHORT_DESCRIPTION_INPUT_MAX_TOKENS),
                ),
            },
        ]
        text = str(await self._llm.complete(TIER_SHORT_DESCRIPTION, messages) or "").strip()
        if not text or count_tokens(text) > AGENT_PLUGIN_SHORTDESC_MAX_TOKENS:
            return ""
        return text

    async def _keep(self, written: dict[str, tuple[str, str]]) -> None:
        if self._writing_file is None:
            self._writing_file = asyncio.Lock()
        async with self._writing_file:
            try:
                # Shielded: a shutdown that cancels this must not lose what a model call paid for.
                await asyncio.shield(asyncio.to_thread(self._write, written))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - kept in memory; written again with the next one
                logger.warning("keeping the plugin short descriptions failed (exc_type=%s)", type(exc).__name__)
