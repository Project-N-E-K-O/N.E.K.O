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

"""What task agents are permitted to do (design section 10.1).

The settings live in ``<data root>/nekoclaw/permissions.json``. Each category
has one of four modes:

* ``first_ask``  - ask the user; "always" turns the category into ``allow``,
  "session" allows it until the asking commission ends.
* ``always_ask`` - ask the user every time; only "once" and "deny" are offered.
* ``allow``      - go ahead without asking.
* ``deny``       - refuse without asking.

``DEFAULT_MODES`` gives the mode of a category the file does not mention. Each
MCP server is a category of its own (``mcp_server`` plus the server's name).
The defaults apply only while there is no file. A file that exists but cannot
be read or parsed makes every category ``deny`` until it is repaired: the
user's own settings are unknown then, and ``deny`` is the one mode every
caller honours (the task brain's direct plugin calls check for ``deny`` only).
The file is read again on each use until it can be read, and nothing is
written over it meanwhile.

``PermissionGate.check`` is the single decision and is called from the two
places the design names: the runtime's ``decide_permission`` callback for the
built-in tools, and the gateway's entry for the capabilities it serves.
Asking means the commission waits for the user, who answers one of:

* "always"  - allow from now on; written to the settings file.
* "session" - allow for the rest of this commission's task agent session. Kept
  in memory only: it ends with the commission and with the host process.
  Task agents the commission starts are covered by it as well.
* "once"    - allow the one request that asked.
* "deny"    - refuse this request; the settings stay as they are.

Task preferences are never read here: guidance for a model cannot open a
permission.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODES: tuple[str, ...] = ("first_ask", "always_ask", "allow", "deny")
CHOICES: tuple[str, ...] = ("always", "session", "once", "deny")
# What a category that asks every time offers.
CHOICES_ALWAYS_ASK: tuple[str, ...] = ("once", "deny")

MCP_SERVER_CATEGORY = "mcp_server"

DEFAULT_MODES: dict[str, str] = {
    # Built-in tools of the runtime (nekoclaw.runtime.permissions.CATEGORIES).
    "workdir_files": "allow",
    "outside_read": "allow",
    "outside_write": "first_ask",
    "run_command": "first_ask",
    "other": "allow",
    # Capabilities behind the gateway.
    "keyboard_mouse": "first_ask",
    "browser": "first_ask",
    "plugin_entry": "allow",
    MCP_SERVER_CATEGORY: "first_ask",
}

DENIED_BY_SETTINGS = "permission denied: the permission settings forbid this"
DENIED_BY_USER = "permission denied: the user declined"
DENIED_NO_ANSWER = "permission denied: the request for approval could not be answered"

_FILE_VERSION = 1
# The mode of every category while an existing file cannot be read.
_UNREADABLE_MODE = "deny"


class PermissionFileError(RuntimeError):
    """The permissions file exists but cannot be read, so it is not written over."""


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allow: bool
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"allow": self.allow, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class PermissionAsk:
    """One request for the user's approval."""

    category: str
    # The MCP server's name for ``mcp_server``; otherwise None.
    key: str | None
    # What exactly is asked for: tool, path, command and so on.
    detail: dict[str, Any] = field(default_factory=dict)
    # The answers offered. "always" and "session" are missing when the
    # category asks every time.
    options: tuple[str, ...] = CHOICES

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "permission",
            "category": self.category,
            "key": self.key,
            "detail": self.detail,
            "options": list(self.options),
        }


# Puts the question to the user on behalf of a commission; returns a choice.
Asker = Callable[[str, PermissionAsk], Awaitable[str]]
# Returns the commission that started the given one, or None.
ParentLookup = Callable[[str], Awaitable["str | None"]]


def _slot(category: str, key: str | None) -> tuple[str, str | None]:
    if category == MCP_SERVER_CATEGORY:
        return category, (key or "").strip() or "*"
    return category, None


class PermissionStore:
    """``permissions.json``: read on first use, rewritten on every change."""

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path_source = path
        self._categories: dict[str, str] = {}
        self._mcp_servers: dict[str, str] = {}
        self._loaded = False
        # True while an existing file cannot be read.
        self._unreadable = False
        # Grows with every change, so a decision can tell that the settings
        # it was based on changed meanwhile.
        self._version = 0
        self._lock = asyncio.Lock()

    def _path(self) -> Path:
        source = self._path_source
        return Path(source() if callable(source) else source)

    def _read(self) -> tuple[dict[str, str], dict[str, str]] | None:
        """Return the stored settings; ``({}, {})`` without a file, None if it cannot be read."""
        path = self._path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, {}
        except (OSError, ValueError) as exc:
            if not self._unreadable:
                logger.warning(
                    "permissions file %s cannot be read (%s); everything is denied until it is repaired",
                    path,
                    type(exc).__name__,
                )
            return None
        sections = (raw.get("categories"), raw.get("mcp_servers")) if isinstance(raw, dict) else None
        if sections is None or any(section is not None and not isinstance(section, dict) for section in sections):
            if not self._unreadable:
                logger.warning(
                    "permissions file %s has an unknown layout; everything is denied until it is repaired", path
                )
            return None

        def modes(section: Any) -> dict[str, str]:
            if not isinstance(section, dict):
                return {}
            return {str(name): mode for name, mode in section.items() if mode in MODES}

        # Server names are looked up as ``mode`` writes them: stripped, and
        # "*" for none. A key edited by hand is read the same way.
        servers = {(name.strip() or "*"): mode for name, mode in modes(sections[1]).items()}
        return modes(sections[0]), servers

    def _write(self, categories: dict[str, str], mcp_servers: dict[str, str]) -> None:
        from utils.file_utils import atomic_write_json

        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            path,
            {"version": _FILE_VERSION, "categories": categories, "mcp_servers": mcp_servers},
        )

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if not self._loaded:
                stored = await asyncio.to_thread(self._read)
                if stored is None:
                    # Read again next time; the user may repair the file.
                    self._unreadable = True
                    return
                self._categories, self._mcp_servers = stored
                self._unreadable = False
                self._loaded = True
                self._version += 1

    @property
    def version(self) -> int:
        """A number that changes whenever the settings change."""
        return self._version

    @property
    def unreadable(self) -> bool:
        """True while an existing permissions file cannot be read."""
        return self._unreadable

    async def mode(self, category: str, key: str | None = None) -> str:
        """Return the mode of a category; an unknown category is asked about every time."""
        await self._ensure_loaded()
        if self._unreadable:
            return _UNREADABLE_MODE
        name, slot = _slot(category, key)
        if name == MCP_SERVER_CATEGORY:
            specific = self._mcp_servers.get(slot or "*")
            if specific is not None:
                return specific
            return self._categories.get(name, DEFAULT_MODES[name])
        return self._categories.get(name, DEFAULT_MODES.get(name, "always_ask"))

    async def set_mode(
        self, category: str, mode: str, key: str | None = None, *, expected_version: int | None = None
    ) -> bool:
        """Set the mode of a category and write the file.

        With ``expected_version``, nothing is changed and False is returned
        when the settings changed since that version was read. Raises
        ``PermissionFileError`` while an existing file cannot be read, so the
        user's settings in it are not lost.
        """
        from utils.asyncio_executor import to_thread_through_cancellation

        if mode not in MODES:
            raise ValueError(f"unknown permission mode {mode!r}")
        await self._ensure_loaded()
        name, slot = _slot(category, key)
        async with self._lock:
            if not self._loaded:
                raise PermissionFileError("the permissions file cannot be read; repair or remove it first")
            if expected_version is not None and expected_version != self._version:
                return False
            if name == MCP_SERVER_CATEGORY and key is not None:
                self._mcp_servers[slot or "*"] = mode
            else:
                self._categories[name] = mode
            self._version += 1
            # A cancelled caller still waits for the write, so the lock is not
            # released while an older setting can land on disk after a newer one.
            await to_thread_through_cancellation(self._write, dict(self._categories), dict(self._mcp_servers))
        return True

    async def snapshot(self) -> dict[str, Any]:
        """Return every category with its current mode, for the console."""
        await self._ensure_loaded()
        if self._unreadable:
            categories = {name: _UNREADABLE_MODE for name in DEFAULT_MODES}
            return {"categories": categories, "mcp_servers": {}, "unreadable": True}
        categories = {name: self._categories.get(name, default) for name, default in DEFAULT_MODES.items()}
        return {"categories": categories, "mcp_servers": dict(self._mcp_servers), "unreadable": False}


class PermissionGate:
    """Decides one request: by the settings, or by asking the user."""

    def __init__(
        self, store: PermissionStore, asker: Asker | None = None, parent_of: ParentLookup | None = None
    ) -> None:
        self._store = store
        self._asker = asker
        self._parent_of = parent_of
        # commission id -> what the user allowed for that commission's session.
        self._session_grants: dict[str, set[tuple[str, str | None]]] = {}

    def set_asker(self, asker: Asker | None) -> None:
        self._asker = asker

    def set_parent_lookup(self, parent_of: ParentLookup | None) -> None:
        self._parent_of = parent_of

    def end_session(self, commission_id: str) -> None:
        """Forget what was allowed for a commission's session; call it when the commission ends."""
        self._session_grants.pop(commission_id, None)

    def session_grants(self, commission_id: str) -> frozenset[tuple[str, str | None]]:
        """Return what the user allowed for this commission's own session."""
        return frozenset(self._session_grants.get(commission_id, ()))

    async def _lineage(self, commission_id: str) -> list[str]:
        """Return the commission and the commissions it descends from, nearest first."""
        chain = [commission_id]
        if self._parent_of is None:
            return chain
        current = commission_id
        while True:
            try:
                parent = await self._parent_of(current)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - without a known parent only its own grants count
                logger.debug("looking up the parent of a commission failed", exc_info=True)
                return chain
            if not parent or parent in chain:
                return chain
            chain.append(parent)
            current = parent

    async def check(
        self,
        commission_id: str,
        category: str,
        *,
        key: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        """Return whether the commission may do this, asking the user if the mode says so."""
        # Looked up first, so nothing is awaited between reading the mode and
        # reading the version it belongs to.
        lineage = await self._lineage(commission_id)
        mode = await self._store.mode(category, key)
        # Read together with the mode, with nothing awaited in between.
        version = self._store.version
        if mode == "allow":
            return PermissionDecision(True)
        if mode == "deny":
            return PermissionDecision(False, DENIED_BY_SETTINGS)
        slot = _slot(category, key)
        if mode == "first_ask" and any(slot in self._session_grants.get(item, ()) for item in lineage):
            # Allowed for this session, or for the session of the commission
            # that started this one. A category that asks every time has no
            # such answer and keeps asking.
            return PermissionDecision(True)
        if self._asker is None:
            return PermissionDecision(False, DENIED_NO_ANSWER)
        options = CHOICES if mode == "first_ask" else CHOICES_ALWAYS_ASK
        ask = PermissionAsk(category=category, key=key, detail=dict(detail or {}), options=options)
        try:
            choice = await self._asker(commission_id, ask)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - no answer is a refusal
            logger.warning("asking for permission failed: %s", type(exc).__name__)
            return PermissionDecision(False, DENIED_NO_ANSWER)
        if choice in ("always", "session") and choice not in options:
            # Approved, though more widely than this category offers (it asks
            # every time): the approval covers the one request that asked.
            choice = "once"
        if choice not in options or choice == "deny":
            return PermissionDecision(False, DENIED_BY_USER)
        if self._store.version != version:
            return await self._decide_again(category, key)
        if choice == "always":
            if not await self._store.set_mode(category, "allow", key, expected_version=version):
                return await self._decide_again(category, key)
        elif choice == "session":
            self._session_grants.setdefault(commission_id, set()).add(slot)
        return PermissionDecision(True)

    async def _decide_again(self, category: str, key: str | None) -> PermissionDecision:
        """Decide a request the user approved after the settings changed under the question.

        The new settings win: ``deny`` refuses, and the approval covers this one
        request only, so it never turns the category into ``allow``.
        """
        if await self._store.mode(category, key) == "deny":
            return PermissionDecision(False, DENIED_BY_SETTINGS)
        return PermissionDecision(True)
