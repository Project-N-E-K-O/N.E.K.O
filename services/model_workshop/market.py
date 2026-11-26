"""Bounded market selections over the existing installer, never a second registry.

The configuration-file witness deliberately expires on any persisted character
edit. This also catches A -> B -> A switches without a new global switch epoch
or changes to the normal character-switching lifecycle.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from utils.config_manager import get_reserved

from .bundle import BundleLimits
from .paths import WorkshopError, identity, read_json

HASH = re.compile(r"[0-9a-f]{64}\Z")
REQUEST = re.compile(r"[A-Za-z0-9_-]{32}\Z")
OPERATION = re.compile(r"[0-9a-f]{32}\Z")
TTL = 30 * 60
MAX_LAUNCHES = 32
MAX_OPERATIONS = 64
ACTIVE = {"downloading", "installing", "applying"}
_ACCOUNT_FILE = Path.home() / ".neko" / "market_auth.json"


def _account_witness():
    """Read the login session, excluding token refresh and pending subject discovery."""
    for attempt in range(3):
        try:
            data = read_json(_ACCOUNT_FILE, 64 * 1024)
        except (OSError, WorkshopError, ValueError) as error:
            # OAuth truncates then rewrites this file. Retry that short window,
            # retaining a soft failure if the bounded reads cannot obtain it.
            if attempt < 2:
                time.sleep(0.02)
                continue
            raise WorkshopError("market_auth_pending") from error
        if data is None:
            return None
        keys = ("session_id", "client_id", "auth_url")
        if any(data.get(key) is not None and not isinstance(data[key], str) for key in keys):
            raise WorkshopError("market_auth_required")
        return tuple(data.get(key) for key in keys)


def _witness(config):
    """Read only the authoritative runtime file; never follow a seed fallback."""
    try:
        path = Path(config.get_runtime_config_path("characters.json"))
        object_id = identity(path)
        info = path.stat()
        return (str(Path(config.app_docs_dir).resolve()), *object_id,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except (OSError, WorkshopError) as error:
        # Missing/unreadable or redirected metadata invalidates permission; it
        # must not leak local paths or fall back to a different role snapshot.
        raise WorkshopError("model_market_target_changed") from error


@dataclass
class Connection:
    """One bounded pairing; retain receipts so old commands cannot become new work."""

    operations: dict[str, Launch] = field(default_factory=dict)
    current: str | None = None
    ready: bool = True
    error: str | None = None
    running: bool = False
    account: tuple | None = None


@dataclass
class Launch:
    request_id: str
    name: str
    character_id: str
    witness: tuple
    expires: float
    phase: str = "browsing"
    selection: tuple | None = None
    task_id: str | None = None
    result: dict | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    operation_id: str | None = None
    connection: Connection = field(default_factory=Connection)

    def check(self, config, characters=None):
        """Called again inside the original role-save lock, before any mutation."""
        if self.cancel.is_set():
            raise WorkshopError("model_market_cancelled")
        self.check_connection(config, characters)

    def check_connection(self, config, characters=None):
        """Cancellation ends an operation; role/edit conflicts revoke its whole pairing."""
        if self.connection.error:
            raise WorkshopError(self.connection.error)
        account = _account_witness()  # Unreadable is transient, never proof of logout.
        if self.connection.account is None:
            self.connection.account = account  # Bind the first login across all operations.
        elif account != self.connection.account:
            self.connection.error = "market_auth_required"
            raise WorkshopError("market_auth_required")
        if time.monotonic() >= self.expires:
            raise WorkshopError("model_market_expired")
        if self.operation_id != self.connection.current:
            # A stale local ready request must not revoke a newer valid operation.
            raise WorkshopError("model_market_selection_changed")
        if _witness(config) != self.witness:
            self.connection.error = "model_market_target_changed"
            raise WorkshopError("model_market_target_changed")
        if characters is not None:
            profile = characters.get("猫娘", {}).get(self.name, {})
            if (characters.get("当前猫娘") != self.name
                    or get_reserved(profile, "character_id", default="") != self.character_id):
                self.connection.error = "model_market_target_changed"
                raise WorkshopError("model_market_target_changed")

    def saved(self, config):
        """Advance only this save's exact file version, inside the original save lock."""
        self.phase = "saved"
        self.witness = _witness(config)

    def public(self):
        """No filesystem roots, configuration snapshots or credentials leave Main."""
        return {"request_id": self.request_id, "character_name": self.name,
                "phase": self.phase, "task_id": self.task_id,
                "error_code": self.error, "result": self.result,
                "operation_id": self.operation_id,
                "model_id": self.selection[0] if self.selection else None,
                "connection_error": self.connection.error,
                "can_select": (self.connection.ready and not self.connection.error
                               and not self.connection.running
                               and len(self.connection.operations) < MAX_OPERATIONS
                               and self.operation_id == self.connection.current
                               and time.monotonic() < self.expires)}


class Launches:
    """Retain a pairing while each explicit selection owns an immutable operation."""

    def __init__(self):
        self.items: dict[str, Launch] = {}

    def create(self, config):
        now = time.monotonic()
        self.items = {key: row for key, row in self.items.items()
                      if row.phase in ACTIVE or row.connection.running
                      or (row.expires > now and not row.connection.error)}
        if len(self.items) >= MAX_LAUNCHES:
            raise WorkshopError("model_market_busy")
        # Authoritative loading refuses unpersisted IDs; a same-name replacement
        # must never inherit a previous launch's permission to change its avatar.
        characters = config.load_characters(require_authoritative=True)
        name = characters.get("当前猫娘", "")
        profile = characters.get("猫娘", {}).get(name, {})
        character_id = get_reserved(profile, "character_id", default="")
        if not name or not character_id:
            raise WorkshopError("model_market_target_changed")
        row = Launch(secrets.token_urlsafe(24), name, character_id,
                     _witness(config), now + TTL, connection=Connection(account=_account_witness()))
        self.items[row.request_id] = row
        return row

    def operation(self, request_id, operation_id):
        """Writes require an explicit valid ID; null must never mean 'whatever is current'."""
        if not isinstance(operation_id, str) or not OPERATION.fullmatch(operation_id):
            raise WorkshopError("model_market_invalid_selection")
        return self.get(request_id, operation_id)

    def get(self, request_id, operation_id=None):
        if not isinstance(request_id, str) or not REQUEST.fullmatch(request_id):
            raise WorkshopError("model_market_expired")
        row = self.items.get(request_id)
        if row is None:
            raise WorkshopError("model_market_expired")
        if operation_id is not None:
            if not isinstance(operation_id, str) or not OPERATION.fullmatch(operation_id):
                raise WorkshopError("model_market_invalid_selection")
            row = row.connection.operations.get(operation_id)
            if row is None:
                raise WorkshopError("model_market_operation_unknown")
        return row

    def select(self, config, request_id, model_id, package_hash, size, operation_id):
        """Deduplicate a choice, then serialize new choices without deleting old receipts."""
        row = self.get(request_id)
        if (not isinstance(operation_id, str) or not OPERATION.fullmatch(operation_id)
                or type(model_id) is not int or not 0 < model_id < 2**63
                or not isinstance(package_hash, str) or not HASH.fullmatch(package_hash)
                or type(size) is not int or not 0 < size <= BundleLimits().archive_bytes):
            raise WorkshopError("model_market_invalid_selection")
        selection = (model_id, package_hash, size)
        previous = row.connection.operations.get(operation_id)
        if previous is not None:
            if previous.selection != selection:
                raise WorkshopError("model_market_selection_changed")
            return previous, False
        row.check_connection(config)
        if not row.connection.ready or len(row.connection.operations) >= MAX_OPERATIONS:
            raise WorkshopError("model_market_busy")
        # This synchronous admission runs inside Main's character mutation lock.
        # Other tabs/pairings cannot race an installed or applying choice for this role.
        now = time.monotonic()
        if any(other.character_id == row.character_id and other.witness[0] == row.witness[0]
               and (other.phase in ACTIVE or other.connection.running
                    or other.phase == "installed" and other.expires > now) for other in self.items.values()):
            raise WorkshopError("model_market_busy")
        if sum(item.phase in {"downloading", "installing", "applying"}
               for item in self.items.values()) >= 2:
            raise WorkshopError("model_market_busy")
        if row.operation_id is not None:
            # Each worker keeps its own object; late completion cannot overwrite its successor.
            row = Launch(row.request_id, row.name, row.character_id, row.witness,
                         row.expires, connection=row.connection)
        row.operation_id = operation_id
        row.connection.current = operation_id
        row.connection.ready = False
        row.connection.operations[operation_id] = row
        self.items[request_id] = row
        row.selection, row.phase = selection, "downloading"
        return row, True


class PackageStream:
    """Adapt a bounded network stream to N2.receive(), with one disk copy only.

    Hash and size must match before EOF is returned: receive() queues an install
    only after EOF, so a mismatching download can never race the installer.
    """

    def __init__(self, chunks, package_hash, size, cancelled, *, seconds=600):
        self.chunks = iter(chunks)
        self.expected_hash, self.expected_size = package_hash, size
        self.cancelled = cancelled
        self.digest, self.size = hashlib.sha256(), 0
        self.deadline = time.monotonic() + seconds
        self.finished = False

    def read(self, maximum):
        if self.cancelled.is_set():
            raise WorkshopError("model_market_cancelled")
        if time.monotonic() >= self.deadline:
            raise WorkshopError("model_market_timeout")
        if self.finished:
            return b""
        chunk = next(self.chunks, None)
        # A network read can block past the deadline or a concurrent cancel,
        # including the final EOF that lets receive() enqueue installation.
        if self.cancelled.is_set():
            raise WorkshopError("model_market_cancelled")
        if time.monotonic() >= self.deadline:
            raise WorkshopError("model_market_timeout")
        if chunk is None:
            if self.size != self.expected_size or self.digest.hexdigest() != self.expected_hash:
                raise WorkshopError("model_market_package_changed")
            self.finished = True
            return b""
        if not isinstance(chunk, bytes) or not chunk or len(chunk) > min(maximum, 1024 * 1024):
            raise WorkshopError("model_market_package_changed")
        self.size += len(chunk)
        if self.size > self.expected_size:
            raise WorkshopError("model_market_package_changed")
        self.digest.update(chunk)
        return chunk
