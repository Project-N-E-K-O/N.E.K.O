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

"""How much the work on one commission may use before the user is asked, as the user set it.

The default is a setting of the build (``TASK_AGENT_USAGE_LIMIT_TOKENS``).
A user who wants commissions stopped earlier, or let run longer, sets
another number on the page about the runtime; it is kept in a file next to
the ledger and read every time the limit is looked at, so a change holds
from the next model request on, for commissions under way as well. What a
commission was allowed beyond the limit (the user said "go on") stays as it
was allowed.

The number is within two bounds (``TASK_AGENT_USAGE_LIMIT_MIN_TOKENS``,
``TASK_AGENT_USAGE_LIMIT_MAX_TOKENS``): there is no way to set no limit
here. A file that cannot be read, or holds what is no such number, is read
as nothing set, and the default holds.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILE_VERSION = 1


def bounds() -> tuple[int, int]:
    """Return the least and the most the limit can be set to."""
    from config.nekopower_settings import TASK_AGENT_USAGE_LIMIT_MAX_TOKENS, TASK_AGENT_USAGE_LIMIT_MIN_TOKENS

    return int(TASK_AGENT_USAGE_LIMIT_MIN_TOKENS), int(TASK_AGENT_USAGE_LIMIT_MAX_TOKENS)


def _settable(value: Any) -> bool:
    least, most = bounds()
    return isinstance(value, int) and not isinstance(value, bool) and least <= value <= most


def _note_a_write_that_failed(writing: "asyncio.Future[None]") -> None:
    if not writing.cancelled() and writing.exception() is not None:
        logger.warning("writing the usage limit failed: %s", type(writing.exception()).__name__)


class UsageLimitStore:
    """The limit the user set, kept in a file next to the ledger; nothing in it while the default holds."""

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path_source = path
        self._lock = asyncio.Lock()

    def _path(self) -> Path:
        source = self._path_source
        return Path(source() if callable(source) else source)

    def _read(self, path: Path | None = None) -> int | None:
        import json

        try:
            raw = json.loads((path or self._path()).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("the usage limit cannot be read (%s); the default is used", type(exc).__name__)
            return None
        value = raw.get("tokens") if isinstance(raw, dict) else None
        return value if _settable(value) else None

    def _write(self, value: int | None, path: Path) -> None:
        from utils.file_utils import atomic_write_json

        if value is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": _FILE_VERSION, "tokens": value})

    async def get(self) -> int | None:
        """Return the limit the user set, in tokens; None while the default holds."""
        return await asyncio.to_thread(self._read)

    async def put(self, value: int | None) -> None:
        """Set the limit; None goes back to the default. Raises ``ValueError`` for a number out of bounds.

        Done to the end once begun, the caller cancelled or not: the write
        in its thread cannot be stopped, and the next one must not begin
        before it is over.
        """
        if value is not None and not _settable(value):
            raise ValueError("out_of_range")
        writing = asyncio.ensure_future(self._put(value))
        writing.add_done_callback(_note_a_write_that_failed)
        await asyncio.shield(writing)

    async def _put(self, value: int | None) -> None:
        async with self._lock:
            path = self._path()
            await asyncio.to_thread(self._write, value, path)
