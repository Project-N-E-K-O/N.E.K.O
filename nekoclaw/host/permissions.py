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

"""What familiars are permitted to do (design section 10.1).

The settings live in ``<data root>/nekoclaw/permissions.json``. Each category
has one of four modes:

* ``first_ask``  - ask the user; "always" turns the category into ``allow``.
* ``always_ask`` - ask the user every time.
* ``allow``      - go ahead without asking.
* ``deny``       - refuse without asking.

``DEFAULT_MODES`` gives the mode of a category the file does not mention. Each
MCP server is a category of its own (``mcp_server`` plus the server's name).

``PermissionGate.check`` is the single decision and is called from the two
places the design names: the runtime's ``decide_permission`` callback for the
built-in tools, and the gateway's entry for the capabilities it serves.
Asking means the commission waits for the user, who answers "once", "always"
or "deny"; "once" covers the one request that asked.

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
CHOICES: tuple[str, ...] = ("once", "always", "deny")

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
    # The answers offered. "always" is missing when the category asks every time.
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
        self._lock = asyncio.Lock()

    def _path(self) -> Path:
        source = self._path_source
        return Path(source() if callable(source) else source)

    def _read(self) -> tuple[dict[str, str], dict[str, str]]:
        path = self._path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, {}
        except (OSError, ValueError) as exc:
            # An unreadable file must not open anything: fall back to the defaults.
            logger.warning("permissions file %s cannot be read (%s); using the defaults", path, type(exc).__name__)
            return {}, {}
        if not isinstance(raw, dict):
            return {}, {}

        def modes(section: Any) -> dict[str, str]:
            if not isinstance(section, dict):
                return {}
            return {str(name): mode for name, mode in section.items() if mode in MODES}

        return modes(raw.get("categories")), modes(raw.get("mcp_servers"))

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
                self._categories, self._mcp_servers = await asyncio.to_thread(self._read)
                self._loaded = True

    async def mode(self, category: str, key: str | None = None) -> str:
        """Return the mode of a category; an unknown category is asked about every time."""
        await self._ensure_loaded()
        name, slot = _slot(category, key)
        if name == MCP_SERVER_CATEGORY:
            specific = self._mcp_servers.get(slot or "*")
            if specific is not None:
                return specific
            return self._categories.get(name, DEFAULT_MODES[name])
        return self._categories.get(name, DEFAULT_MODES.get(name, "always_ask"))

    async def set_mode(self, category: str, mode: str, key: str | None = None) -> None:
        """Set the mode of a category and write the file."""
        if mode not in MODES:
            raise ValueError(f"unknown permission mode {mode!r}")
        await self._ensure_loaded()
        name, slot = _slot(category, key)
        async with self._lock:
            if name == MCP_SERVER_CATEGORY and key is not None:
                self._mcp_servers[slot or "*"] = mode
            else:
                self._categories[name] = mode
            await asyncio.to_thread(self._write, dict(self._categories), dict(self._mcp_servers))

    async def snapshot(self) -> dict[str, Any]:
        """Return every category with its current mode, for the console."""
        await self._ensure_loaded()
        categories = {name: self._categories.get(name, default) for name, default in DEFAULT_MODES.items()}
        return {"categories": categories, "mcp_servers": dict(self._mcp_servers)}


class PermissionGate:
    """Decides one request: by the settings, or by asking the user."""

    def __init__(self, store: PermissionStore, asker: Asker | None = None) -> None:
        self._store = store
        self._asker = asker

    def set_asker(self, asker: Asker | None) -> None:
        self._asker = asker

    async def check(
        self,
        commission_id: str,
        category: str,
        *,
        key: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        """Return whether the commission may do this, asking the user if the mode says so."""
        mode = await self._store.mode(category, key)
        if mode == "allow":
            return PermissionDecision(True)
        if mode == "deny":
            return PermissionDecision(False, DENIED_BY_SETTINGS)
        if self._asker is None:
            return PermissionDecision(False, DENIED_NO_ANSWER)
        options = CHOICES if mode == "first_ask" else ("once", "deny")
        ask = PermissionAsk(category=category, key=key, detail=dict(detail or {}), options=options)
        try:
            choice = await self._asker(commission_id, ask)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - no answer is a refusal
            logger.warning("asking for permission failed: %s", type(exc).__name__)
            return PermissionDecision(False, DENIED_NO_ANSWER)
        if choice == "always" and "always" in options:
            await self._store.set_mode(category, "allow", key)
            return PermissionDecision(True)
        if choice in ("once", "always"):
            return PermissionDecision(True)
        return PermissionDecision(False, DENIED_BY_USER)
