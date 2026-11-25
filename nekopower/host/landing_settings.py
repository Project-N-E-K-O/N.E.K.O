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

"""What the user set about landing the changes task agents make in git worktrees.

Two settings, kept in a file next to the ledger:

* ``mode``: ``auto`` (what a task agent changed in its worktree is merged
  into the project's directory as soon as its commission is done) or ``ask``
  (it stays in the worktree until the user says that it lands, or that it
  is not wanted). ``auto`` unless the user set otherwise.
* ``inspect``: whether an inspector looks at what was changed before every
  landing. Off unless the user set otherwise.

Read once and kept, with what is set since. A file that cannot be read, or
holds something else, is read as nothing set.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILE_VERSION = 1

LANDING_AUTO = "auto"
LANDING_ASK = "ask"
LANDING_MODES: tuple[str, ...] = (LANDING_AUTO, LANDING_ASK)

_DEFAULTS: dict[str, Any] = {"mode": LANDING_AUTO, "inspect": False}


def _note_a_write_that_failed(writing: "asyncio.Future[dict[str, Any]]") -> None:
    if not writing.cancelled() and writing.exception() is not None:
        logger.warning("writing the landing settings failed: %s", type(writing.exception()).__name__)


class LandingStore:
    """The landing settings the user made, kept in a file next to the ledger."""

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path_source = path
        self._lock = asyncio.Lock()
        self._kept: dict[str, Any] | None = None

    def _path(self) -> Path:
        source = self._path_source
        return Path(source() if callable(source) else source)

    def _read(self) -> dict[str, Any]:
        import json

        found = dict(_DEFAULTS)
        try:
            raw = json.loads(self._path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return found
        except (OSError, ValueError) as exc:
            logger.warning("the landing settings cannot be read (%s); the defaults are used", type(exc).__name__)
            return found
        if isinstance(raw, dict):
            if raw.get("mode") in LANDING_MODES:
                found["mode"] = raw["mode"]
            if isinstance(raw.get("inspect"), bool):
                found["inspect"] = raw["inspect"]
        return found

    def _write(self, values: dict[str, Any], path: Path) -> None:
        from utils.file_utils import atomic_write_json

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": _FILE_VERSION, **values})

    async def get(self) -> dict[str, Any]:
        """Return the settings: ``{"mode": "auto" | "ask", "inspect": bool}``."""
        if self._kept is None:
            # Not beside a write: what it writes is what holds afterwards.
            async with self._lock:
                if self._kept is None:
                    self._kept = await asyncio.to_thread(self._read)
        return dict(self._kept)

    async def put(self, *, mode: Any = None, inspect: Any = None) -> dict[str, Any]:
        """Set what is given and return the settings as they stand; raises ``ValueError`` with a code for what cannot be set.

        ``bad_mode`` for a mode that is none of ``LANDING_MODES``,
        ``bad_inspect`` for what is no boolean. Done to the end once
        begun, the caller cancelled or not.
        """
        if mode is not None and mode not in LANDING_MODES:
            raise ValueError("bad_mode")
        if inspect is not None and not isinstance(inspect, bool):
            raise ValueError("bad_inspect")
        writing = asyncio.ensure_future(self._put(mode, inspect))
        writing.add_done_callback(_note_a_write_that_failed)
        return await asyncio.shield(writing)

    async def _put(self, mode: str | None, inspect: bool | None) -> dict[str, Any]:
        async with self._lock:
            values = dict(self._kept) if self._kept is not None else await asyncio.to_thread(self._read)
            if mode is not None:
                values["mode"] = mode
            if inspect is not None:
                values["inspect"] = inspect
            await asyncio.to_thread(self._write, values, self._path())
            self._kept = values
            return dict(values)
