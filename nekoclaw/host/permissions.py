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
  "session" allows it until the asking commission is done, has failed or
  is stopped (being stuck and taken up again does not end it).
* ``always_ask`` - ask the user every time; only "once" and "deny" are offered.
* ``allow``      - go ahead without asking.
* ``deny``       - refuse without asking.

``DEFAULT_MODES`` gives the mode of a category the file does not mention. Each
MCP server is a category of its own (``mcp_server`` plus the server's name).
The defaults apply only while there is no file. A file that exists but cannot
be read or parsed makes every category ``deny`` until it is repaired: the
user's own settings are unknown then, and ``deny`` is the one mode every
caller honours.
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
* "once"    - allow the one request that asked. For the keyboard and mouse
  and for the browser (``RUN_SCOPED_CATEGORIES``) it lasts until the task
  agent's current run is over: their tools are single clicks and keys, and
  an approval that covered one click would be asked for again at the next.
* "deny"    - refuse this request; the settings stay as they are.

Task preferences are never read here: guidance for a model cannot open a
permission.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODES: tuple[str, ...] = ("first_ask", "always_ask", "allow", "deny")
CHOICES: tuple[str, ...] = ("always", "session", "once", "deny")
# A lower rank withdraws approval previously issued under a wider setting.
_MODE_RANK = {"deny": 0, "always_ask": 1, "first_ask": 2, "allow": 3}
# What a category that asks every time offers.
CHOICES_ALWAYS_ASK: tuple[str, ...] = ("once", "deny")

MCP_SERVER_CATEGORY = "mcp_server"

DEFAULT_MODES: dict[str, str] = {
    # Built-in tools of the runtime (nekoclaw.runtime.permissions.CATEGORIES).
    "workdir_files": "allow",
    "outside_read": "first_ask",
    "outside_write": "first_ask",
    "run_command": "first_ask",
    "other": "allow",
    # Capabilities behind the gateway.
    "keyboard_mouse": "first_ask",
    "browser": "first_ask",
    "plugin_entry": "allow",
    "managed_delivery": "first_ask",
    MCP_SERVER_CATEGORY: "first_ask",
}

# Categories whose tools are single small steps. "once" covers the rest of
# the run that asked instead of one call, in both asking modes.
RUN_SCOPED_CATEGORIES: frozenset[str] = frozenset({"keyboard_mouse", "browser"})

DENIED_BY_SETTINGS = "permission denied: the permission settings forbid this"
DENIED_BY_USER = "permission denied: the user declined"
DENIED_NO_ANSWER = "permission denied: the request for approval could not be answered"

# Not a category of the settings: asked each time it comes up, whatever the
# settings say of the browser (``PermissionGate.ask_each_time``).
BROWSER_REOPEN = "browser_reopen"

# Not a category of the settings either: a shell command that points at the
# task agent space, the files task agents keep for good, is asked about each
# time, whatever was allowed for commands (``PermissionGate.ask_each_time``).
# Commands the settings forbid stay forbidden.
SPACE_COMMAND = "space_command"

# Nor is this one: the work on a commission has used as many tokens as one
# commission may, and the user is asked whether it is to go on with as many
# again (``TaskAgentManager``). Asked each time that much is used; what is
# allowed is allowed once.
USAGE_LIMIT = "usage_limit"

_FILE_VERSION = 1
# The mode of every category while an existing file cannot be read.
_UNREADABLE_MODE = "deny"


class PermissionFileError(RuntimeError):
    """The permissions file exists but cannot be read, so it is not written over."""


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allow: bool
    reason: str | None = None
    # Whether the user was asked for this very decision and approved.
    asked: bool = False
    # Host-only audit reference. Never returned to the model as a credential.
    grant_id: str | None = None

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
    # Done by whoever puts the question to the user the moment the
    # answer to it is in, before whoever asked gets to go on. Not
    # part of what the user is asked.
    at_answer: Callable[[], None] | None = field(default=None, compare=False, repr=False)

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
ParentLookup = Callable[[str], Awaitable["str | None | tuple[str | None, bool]"]]


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
        self._revisions: dict[tuple[str, str | None], int] = {}
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

    def revision(self, category: str, key: str | None = None) -> int:
        """Generation of the last tightening of the effective rule for this scope."""
        name, slot = _slot(category, key)
        if name == MCP_SERVER_CATEGORY and slot in self._mcp_servers:
            return self._revisions.get((name, slot), 0)
        return self._revisions.get((name, None), 0)

    async def set_mode(
        self, category: str, mode: str, key: str | None = None, *, expected_version: int | None = None,
        expected_revision: int | None = None,
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
            if expected_revision is not None and self.revision(category, key) != expected_revision:
                return False
            fallback = self._categories.get(name, DEFAULT_MODES.get(name, "always_ask"))
            setting = (name, None)
            previous = fallback
            generation = self._revisions.get(setting, 0)
            if name == MCP_SERVER_CATEGORY and key is not None:
                previous = self._mcp_servers.get(slot, fallback)
                generation = self.revision(name, key)
                setting = (name, slot)
                self._mcp_servers[slot] = mode
            else:
                self._categories[name] = mode
            self._version += 1
            # Managed transfers still ask per recipient under an "allow" setting.
            ceiling = _MODE_RANK["first_ask"] if name == "managed_delivery" else _MODE_RANK["allow"]
            tighter = min(_MODE_RANK[mode], ceiling) < min(_MODE_RANK[previous], ceiling)
            self._revisions[setting] = self._version if tighter else generation
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


@dataclass(eq=False, slots=True)
class GrantBinding:
    """One host registration lifetime, compared by identity."""

    active: bool = True


@dataclass(frozen=True, slots=True)
class GrantScope:
    """Verified provider, resource/purpose and recipient for one operation."""

    provider: str
    resource: str = ""
    recipient: str = ""
    bindings: tuple[GrantBinding, ...] = ()

    def registered(self) -> bool:
        return all(binding.active for binding in self.bindings)


class SessionActionScope(GrantScope):
    """Host-constructed scope for intrinsic session operations, never constructed from tool JSON."""


@dataclass(frozen=True, slots=True)
class _Loan:
    id: str
    owner: str
    root: str
    slot: tuple[str, str | None]
    scope: GrantScope
    revision: int
    lifetime: str
    parent_id: str | None = None
    branch: tuple[str, ...] = ()


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
        # commission id -> what the user allowed "once" for the run under way
        # (``RUN_SCOPED_CATEGORIES`` only).
        self._run_grants: dict[str, set[tuple[str, str | None]]] = {}
        self._loans: dict[str, _Loan] = {}
        self._shared: dict[tuple[str, tuple[str, str | None], GrantScope], _Loan] = {}
        self._pending: dict[tuple[str, tuple[str, str | None], GrantScope], asyncio.Future[PermissionDecision]] = {}
        # Lifetimes captured before an await; end_run/session invalidates late answers.
        self._work: dict[str, object] = {}
        self._lives: dict[str, object] = {}
        self._lineage_lookups: dict[object, set[str]] = {}
        self._external_input_roots: set[str] = set()

    def set_asker(self, asker: Asker | None) -> None:
        self._asker = asker

    def set_parent_lookup(self, parent_of: ParentLookup | None) -> None:
        self._parent_of = parent_of

    def end_session(self, commission_id: str) -> None:
        """Forget what was allowed for a commission's session; call it when the commission is done, has failed or is stopped."""
        for ended in self._lineage_lookups.values():
            ended.add(commission_id)
        self._external_input_roots.discard(commission_id)
        self._lives.pop(commission_id, None)
        self._session_grants.pop(commission_id, None)
        self._run_grants.pop(commission_id, None)
        self._work.pop(commission_id, None)
        for loan in tuple(self._loans.values()):
            if loan.owner == commission_id or loan.root == commission_id or commission_id in loan.branch:
                self._discard_loan(loan.id)

    def received_external_input(self, commission_id: str) -> None:
        """Isolate this branch from old team approvals when another session adds text."""
        self.end_session(commission_id)
        self._external_input_roots.add(commission_id)

    def _sharing_lineage(self, lineage: list[str], received: set[str]) -> list[str]:
        for i, actor in enumerate(lineage):
            if actor in received or actor in self._external_input_roots:
                return lineage[: i + 1]
        return lineage

    def current_run(self, commission_id: str) -> Callable[[], bool]:
        """Bind in-flight host work to this run, including while awaiting a ledger write."""
        work = self._work.setdefault(commission_id, object())
        return lambda: self._work.get(commission_id) is work

    def end_run(self, commission_id: str) -> None:
        """Forget what was allowed "once" for a commission's run; call it when that run is over."""
        self._run_grants.pop(commission_id, None)
        self._work.pop(commission_id, None)
        for loan in tuple(self._loans.values()):
            if loan.owner == commission_id and loan.lifetime != "session":
                self._discard_loan(loan.id)

    def run_grants(self, commission_id: str) -> frozenset[tuple[str, str | None]]:
        """Return what the user allowed "once" for this commission's run under way."""
        return frozenset(self._run_grants.get(commission_id, ())) | frozenset(
            loan.slot for loan in self._loans.values() if loan.owner == commission_id and loan.lifetime == 'run'
        )

    def session_grants(self, commission_id: str) -> frozenset[tuple[str, str | None]]:
        """Return what the user allowed for this commission's own session."""
        return frozenset(self._session_grants.get(commission_id, ())) | frozenset(
            loan.slot for loan in self._loans.values() if loan.owner == commission_id and loan.lifetime == 'session'
        )

    async def _lineage(self, commission_id: str, received: set[str], *, verified: bool = False) -> list[str]:
        """Return the commission and the commissions it descends from, nearest first."""
        chain = [commission_id]
        if self._parent_of is None:
            return chain
        current = commission_id
        while True:
            try:
                link = await self._parent_of(current)
                if isinstance(link, tuple):
                    parent, external = link
                    if external:
                        received.add(current)
                else:
                    parent = link
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - without a known parent only its own grants count
                logger.debug("looking up the parent of a commission failed", exc_info=True)
                return [] if verified else chain
            if not parent:
                return chain
            if parent in chain:
                return [] if verified else chain
            chain.append(parent)
            current = parent

    async def check(
        self,
        commission_id: str,
        category: str,
        *,
        key: str | None = None,
        detail: dict[str, Any] | None = None,
        scope: GrantScope | None = None,
        hold_once: bool = False,
    ) -> PermissionDecision:
        """Check host-verified scope when available; older callers retain their category contract."""
        if scope is not None:
            return await self._check_scoped(commission_id, category, key, scope, detail, hold_once)
        if category == "managed_delivery":
            return PermissionDecision(False, DENIED_NO_ANSWER)
        mode = await self._store.mode(category, key)
        if mode == "allow":
            return PermissionDecision(True)
        if mode == "deny":
            return PermissionDecision(False, DENIED_BY_SETTINGS)
        # Only a mode that asks can use what was allowed for a session, so the
        # lineage (one ledger read per level) is looked up only now. The mode
        # is then read again together with its version, with nothing awaited
        # between the two.
        received: set[str] = set()
        lineage = await self._lineage(commission_id, received) if mode == "first_ask" else [commission_id]
        mode = await self._store.mode(category, key)
        version = self._store.version
        if mode == "allow":
            return PermissionDecision(True)
        if mode == "deny":
            return PermissionDecision(False, DENIED_BY_SETTINGS)
        slot = _slot(category, key)
        if mode == "first_ask" and any(slot in self._session_grants.get(item, ()) for item in self._sharing_lineage(lineage, received)):
            # Allowed for this session, or for the session of the commission
            # that started this one. A category that asks every time has no
            # such answer and keeps asking.
            return PermissionDecision(True)
        run_scoped = category in RUN_SCOPED_CATEGORIES
        if run_scoped and slot in self._run_grants.get(commission_id, ()):
            # Allowed "once" earlier in this run. Only the commission that
            # asked is covered: a task agent it started has runs of its own.
            return PermissionDecision(True)
        if self._asker is None:
            return PermissionDecision(False, DENIED_NO_ANSWER)
        options = CHOICES if mode == "first_ask" else CHOICES_ALWAYS_ASK
        ask = PermissionAsk(category=category, key=key, detail=dict(detail or {}), options=options)
        # The session this question is asked in. A "session" answer goes
        # into this very set. ``end_session`` drops it, so an answer that
        # comes in after the commission ended is kept for nobody, instead of
        # starting a new session for a commission that is over. The same
        # holds for a "once" answer and the run it is asked in.
        session = self._session_grants.setdefault(commission_id, set())
        run = self._run_grants.setdefault(commission_id, set()) if run_scoped else None
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
            session.add(slot)
        elif run is not None:
            run.add(slot)
        return PermissionDecision(True, asked=True)

    async def check_builtin(
        self, commission_id: str, category: str, *, detail: dict[str, Any], writes: bool
    ) -> PermissionDecision:
        """Scope files by access and resolved path, commands by tool, cwd and exact command."""
        work = self._work.setdefault(commission_id, object())
        mode = await self._store.mode(category)
        if self._work.get(commission_id) is not work:
            return PermissionDecision(False, DENIED_NO_ANSWER)
        if mode in ("allow", "deny"):
            return PermissionDecision(mode == "allow", DENIED_BY_SETTINGS if mode == "deny" else None)
        tool = str(detail.get("tool") or "")
        resource = tool
        if category == "run_command":
            command = [tool, detail.get("cwd"), detail.get("command")]
            resource = hashlib.sha256(json.dumps(command).encode()).hexdigest()
        elif isinstance(path := detail.get("path"), str) and path:
            actual_path = os.path.normcase(await asyncio.to_thread(os.path.realpath, path))
            resource = json.dumps(["write" if writes else "read", actual_path])
        if self._work.get(commission_id) is not work:
            return PermissionDecision(False, DENIED_NO_ANSWER)
        return await self.check(commission_id, category, detail=detail, scope=GrantScope("builtin-runtime", resource))

    async def check_native(
        self, commission_id: str, category: str, *, detail: dict[str, Any]
    ) -> PermissionDecision:
        """ACP knows the operation, not the native tool owner's registration identity."""
        if category not in {"workdir_files", "outside_read", "outside_write", "run_command", "other"}:
            return PermissionDecision(False, DENIED_BY_SETTINGS)
        live = self.current_run(commission_id)
        mode = await self._store.mode(category)
        if not live():
            return PermissionDecision(False, DENIED_NO_ANSWER)
        if mode in ("allow", "deny"):
            return PermissionDecision(mode == "allow", DENIED_BY_SETTINGS if mode == "deny" else None)
        decision = await self.ask_each_time(commission_id, category, detail=detail)
        if not live():
            return PermissionDecision(False, DENIED_NO_ANSWER)
        if await self._store.mode(category) == "deny":
            return PermissionDecision(False, DENIED_BY_SETTINGS)
        if not live():
            return PermissionDecision(False, DENIED_NO_ANSWER)
        return decision

    def authorization_ref(self, grant_id: str | None) -> str | None:
        """Stable audit ID shared by operations using the same approval."""
        loan = self._loans.get(grant_id) if grant_id else None
        return (loan.parent_id or loan.id) if loan else None

    def grant_active(self, grant_id: str | None) -> bool:
        """Last synchronous check before crossing a host-owned boundary."""
        loan = self._loans.get(grant_id) if grant_id else None
        return bool(
            loan and loan.scope.registered() and self._store.revision(*loan.slot) == loan.revision
            and (loan.parent_id is None or loan.parent_id in self._loans)
        )

    def revoke_grant(self, grant_id: str | None) -> None:
        """Withdraw this loan, including any queued operations relying on it."""
        loan = self._loans.get(grant_id) if grant_id else None
        if loan and loan.parent_id:
            self._discard_loan(loan.parent_id)
        self._discard_loan(grant_id)

    def _discard_loan(self, grant_id: str | None) -> None:
        loan = self._loans.pop(grant_id, None) if grant_id else None
        if loan is not None:
            shared = (loan.owner, loan.slot, loan.scope)
            if self._shared.get(shared) is loan:
                del self._shared[shared]

    def release_grant(self, grant_id: str | None) -> None:
        """Release the temporary receipt used across awaits for one operation."""
        loan = self._loans.get(grant_id) if grant_id else None
        if loan is not None and loan.lifetime == "once":
            self._discard_loan(loan.id)

    async def valid_grant(
        self, commission_id: str, grant_id: str | None, category: str, *, key: str | None = None, scope: GrantScope
    ) -> bool:
        if not self.grant_active(grant_id):
            return False
        received: set[str] = set()
        lineage = await self._lineage(commission_id, received, verified=True)
        loan = self._loans.get(grant_id)
        return bool(
            loan and lineage and loan.scope == scope and loan.slot == _slot(category, key)
            and loan.root == lineage[-1] and loan.owner in self._sharing_lineage(lineage, received) and self.grant_active(grant_id)
        )

    def _record_loan(
        self, owner: str, root: str, slot: tuple[str, str | None], scope: GrantScope, lifetime: str,
        parent_id: str | None = None, branch: tuple[str, ...] = (),
    ) -> str:
        loan = _Loan(uuid.uuid4().hex, owner, root, slot, scope, self._store.revision(*slot), lifetime, parent_id, branch)
        self._loans[loan.id] = loan
        if lifetime != "once":
            self._shared[(owner, slot, scope)] = loan
        return loan.id

    async def _check_scoped(
        self, commission_id: str, category: str, key: str | None, scope: GrantScope,
        detail: dict[str, Any] | None, hold_once: bool,
    ) -> PermissionDecision:
        if not scope.registered() or not scope.provider or (category == "managed_delivery" and not scope.recipient):
            return PermissionDecision(False, DENIED_NO_ANSWER)
        async def effective_mode() -> str:
            if isinstance(scope, SessionActionScope):
                # These actions have always been available without prompting. Reading a peer
                # additionally respects the explicit ban on reads outside the work directory.
                if category == "session_action":
                    return "allow"
                if category == "outside_read":
                    return "deny" if await self.forbidden(category) else "allow"
                return "deny"
            return await self._store.mode(category, key)

        # Capture before any asynchronous settings or parent lookup. A late
        # request from an ended run cannot attach itself to the next run.
        work = self._work.setdefault(commission_id, object())
        # Ancestors are not known until the ledger answers. Record which sessions
        # ended during this lookup, including an ancestor that has since resumed.
        lookup = object()
        ended = self._lineage_lookups[lookup] = set()
        try:
            mode = await effective_mode()
            if not scope.registered() or self._work.get(commission_id) is not work:
                return PermissionDecision(False, DENIED_NO_ANSWER)
            if mode == "deny":
                return PermissionDecision(False, DENIED_BY_SETTINGS)
            delivery = category == "managed_delivery"
            if mode == "allow" and not delivery and not hold_once:
                return PermissionDecision(True)
            received: set[str] = set()
            lineage = await self._lineage(commission_id, received, verified=True)
        finally:
            del self._lineage_lookups[lookup]
        if not lineage or not ended.isdisjoint(lineage) or self._work.get(commission_id) is not work:
            return PermissionDecision(False, DENIED_NO_ANSWER)
        branch = tuple(lineage)
        root = branch[-1]
        sharing_root = self._sharing_lineage(lineage, received)[-1]
        slot = _slot(category, key)
        lives = [(actor, self._lives.setdefault(actor, object())) for actor in branch]

        def live() -> bool:
            return scope.registered() and self._work.get(commission_id) is work and all(
                self._lives.get(actor) is life for actor, life in lives
            )

        def receipt(loan_id: str) -> str:
            if hold_once or delivery:
                return self._record_loan(commission_id, root, slot, scope, "once", parent_id=loan_id, branch=branch)
            return loan_id

        while live():
            mode = await effective_mode()
            if mode == "deny" or not live():
                return PermissionDecision(False, DENIED_BY_SETTINGS)
            if delivery and mode == "allow":
                # A blanket setting is not approval to disclose data to every recipient.
                mode = "first_ask"
            if mode == "allow":
                grant_id = self._record_loan(commission_id, root, slot, scope, "once", branch=branch) if hold_once else None
                return PermissionDecision(True, grant_id=grant_id)
            for owner in (sharing_root, commission_id):
                loan = self._shared.get((owner, slot, scope))
                if loan is None:
                    continue
                if not self.grant_active(loan.id):
                    self.revoke_grant(loan.id)
                elif (loan.lifetime == "session" and mode == "first_ask") or (
                    loan.lifetime == "run" and loan.owner == commission_id
                ):
                    return PermissionDecision(True, grant_id=receipt(loan.id))
            if self._asker is None:
                return PermissionDecision(False, DENIED_NO_ANSWER)
            pending_key = (sharing_root, slot, scope)
            pending = self._pending.get(pending_key)
            if pending is not None:
                # One answer can cover the whole team. A one-time answer only
                # covers the operation that asked, so other callers check again.
                result = await asyncio.shield(pending)
                # A failed question in a different commission is not the user's answer to this one.
                if not result.allow and result.reason != DENIED_NO_ANSWER:
                    return result
                continue
            pending = asyncio.get_running_loop().create_future()
            self._pending[pending_key] = pending
            result = PermissionDecision(False, DENIED_NO_ANSWER)
            try:
                revision = self._store.revision(*slot)
                options = CHOICES_ALWAYS_ASK if mode == "always_ask" else CHOICES
                if delivery and mode != "always_ask":
                    options = ("session", "once", "deny")
                ask = PermissionAsk(category, key, dict(detail or {}), options)
                choice = await self._asker(commission_id, ask)
                if not live():
                    pending.set_result(PermissionDecision(True))
                    return result
                if choice in ("always", "session") and choice not in options:
                    choice = "once"
                if choice not in options or choice == "deny":
                    result = PermissionDecision(False, DENIED_BY_USER)
                    return result
                if choice == "always":
                    # Only this rule can invalidate an approval, not a simultaneous edit elsewhere.
                    await self._store.set_mode(category, "allow", key, expected_revision=revision)
                current = await self._store.mode(category, key)
                if current == "deny":
                    result = PermissionDecision(False, DENIED_BY_SETTINGS)
                    return result
                if not live():
                    pending.set_result(PermissionDecision(True))
                    return result
                tightened = self._store.revision(*slot) != revision
                if choice == "session" and not tightened:
                    ancestors = branch[branch.index(sharing_root):]
                    grant_id = receipt(self._record_loan(sharing_root, root, slot, scope, "session", branch=ancestors))
                elif choice == "once" and category in RUN_SCOPED_CATEGORIES and not tightened:
                    grant_id = receipt(self._record_loan(commission_id, root, slot, scope, "run", branch=branch))
                elif delivery or hold_once:
                    grant_id = self._record_loan(commission_id, root, slot, scope, "once", branch=branch)
                else:
                    grant_id = None
                result = PermissionDecision(True, asked=True, grant_id=grant_id)
                return result
            except asyncio.CancelledError:
                # Peers with a live commission can still ask under their own identity.
                pending.set_result(PermissionDecision(True))
                raise
            except Exception as exc:  # noqa: BLE001 - no answer is a refusal
                logger.warning("asking for scoped permission failed: %s", type(exc).__name__)
                return result
            finally:
                self._pending.pop(pending_key, None)
                if not pending.done():
                    pending.set_result(result)
        return PermissionDecision(False, DENIED_NO_ANSWER)

    async def forbidden(self, category: str, key: str | None = None) -> bool:
        """Whether the settings forbid this as of now; nobody is asked.

        For after a question of another kind that the user may have taken
        long to answer: the settings may have been changed meanwhile, and a
        ``deny`` set since wins, as it does over the answer to a permission
        question.
        """
        return await self._store.mode(category, key) == "deny"

    async def ask_each_time(
        self,
        commission_id: str,
        category: str,
        *,
        detail: dict[str, Any] | None = None,
        at_answer: Callable[[], None] | None = None,
    ) -> PermissionDecision:
        """Ask the user about something no setting answers, and that no earlier answer covers.

        For what is asked about as an event rather than as a kind of action
        (``BROWSER_REOPEN``: the user closed the browser, and a task wants
        it again). The answers on offer are to allow it this once or to
        refuse; nothing is kept of either. ``at_answer`` is done the
        moment the answer to this question is in (``PermissionAsk``).
        """
        if self._asker is None:
            return PermissionDecision(False, DENIED_NO_ANSWER)
        ask = PermissionAsk(
            category=category, key=None, detail=dict(detail or {}), options=CHOICES_ALWAYS_ASK, at_answer=at_answer
        )
        try:
            choice = await self._asker(commission_id, ask)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - no answer is a refusal
            logger.warning("asking the user failed: %s", type(exc).__name__)
            return PermissionDecision(False, DENIED_NO_ANSWER)
        if choice in ("always", "session"):
            # Approved, though more widely than was offered: it covers this once.
            choice = "once"
        if choice != "once":
            return PermissionDecision(False, DENIED_BY_USER)
        return PermissionDecision(True, asked=True)

    async def _decide_again(self, category: str, key: str | None) -> PermissionDecision:
        """Decide a request the user approved after the settings changed under the question.

        The new settings win: ``deny`` refuses, and the approval covers this one
        request only, so it never turns the category into ``allow``.
        """
        if await self._store.mode(category, key) == "deny":
            return PermissionDecision(False, DENIED_BY_SETTINGS)
        return PermissionDecision(True, asked=True)
