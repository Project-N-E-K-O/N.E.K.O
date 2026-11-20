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
from .paths import WorkshopError, identity

HASH = re.compile(r"[0-9a-f]{64}\Z")
REQUEST = re.compile(r"[A-Za-z0-9_-]{32}\Z")
TTL = 30 * 60
MAX_LAUNCHES = 32


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

    def check(self, config, characters=None):
        """Called again inside the original role-save lock, before any mutation."""
        if self.cancel.is_set():
            raise WorkshopError("model_market_cancelled")
        if time.monotonic() >= self.expires:
            raise WorkshopError("model_market_expired")
        if _witness(config) != self.witness:
            raise WorkshopError("model_market_target_changed")
        if characters is not None:
            profile = characters.get("猫娘", {}).get(self.name, {})
            if (characters.get("当前猫娘") != self.name
                    or get_reserved(profile, "character_id", default="") != self.character_id):
                raise WorkshopError("model_market_target_changed")

    def public(self):
        """No filesystem roots, configuration snapshots or credentials leave Main."""
        return {"request_id": self.request_id, "character_name": self.name,
                "phase": self.phase, "task_id": self.task_id,
                "error_code": self.error, "result": self.result}


class Launches:
    """One short-lived selection per launch; callers own the bounded async job."""

    def __init__(self):
        self.items: dict[str, Launch] = {}

    def create(self, config):
        now = time.monotonic()
        self.items = {key: row for key, row in self.items.items()
                      if row.expires > now or row.phase in {"downloading", "installing", "applying"}}
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
                     _witness(config), now + TTL)
        self.items[row.request_id] = row
        return row

    def get(self, request_id):
        if not isinstance(request_id, str) or not REQUEST.fullmatch(request_id):
            raise WorkshopError("model_market_expired")
        row = self.items.get(request_id)
        if row is None:
            raise WorkshopError("model_market_expired")
        return row

    def select(self, config, request_id, model_id, package_hash, size):
        """Repeat clicks return the same job; a launch cannot change selection."""
        row = self.get(request_id)
        if (type(model_id) is not int or not 0 < model_id < 2**63
                or not isinstance(package_hash, str) or not HASH.fullmatch(package_hash)
                or type(size) is not int or not 0 < size <= BundleLimits().archive_bytes):
            raise WorkshopError("model_market_invalid_selection")
        selection = (model_id, package_hash, size)
        if row.selection is not None:
            if row.selection != selection:
                raise WorkshopError("model_market_selection_changed")
            return row, False
        row.check(config)
        if sum(item.phase in {"downloading", "installing", "applying"}
               for item in self.items.values()) >= 2:
            raise WorkshopError("model_market_busy")
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
