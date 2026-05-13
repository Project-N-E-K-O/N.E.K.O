"""Frozen dataclass types for the plugin install source lock.

All timestamps are carried as already-normalized strings in the format
``%Y-%m-%dT%H:%M:%S.%fZ`` (UTC). Normalization happens in the Parser; the
models themselves do not validate or coerce timestamps.

``frozen=True`` lets writers publish new :class:`LockFile` snapshots via
a single attribute assignment on the manager — readers always observe a
fully consistent :class:`LockFile` whether they take the pre- or
post-publish state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RootId = Literal["builtin", "user"]
Channel = Literal["builtin", "manual", "imported", "market"]
Reason = Literal["user_requested", "auto_dependency"]


@dataclass(frozen=True)
class SourceDetailMarket:
    """``source_detail`` for ``channel="market"`` entries."""

    plugin_market_id: str
    version: str
    package_url: str
    # Captured on upgrade (old version before the current write); None on
    # first install and on no-op same-version re-calls.
    previous_version: str | None = None


@dataclass(frozen=True)
class SourceDetailImported:
    """``source_detail`` for ``channel="imported"`` entries."""

    package_filename: str
    package_sha256: str  # 64-char lowercase hex


# builtin / manual channels carry source_detail=None.
SourceDetail = SourceDetailMarket | SourceDetailImported | None


@dataclass(frozen=True)
class LockEntry:
    """One plugin's install-source record.

    Primary key is ``(root_id, directory_name)``. ``plugin_id`` may be ``""``
    when the directory's metadata was temporarily unreadable.
    """

    root_id: RootId
    directory_name: str
    plugin_id: str
    channel: Channel
    reason: Reason
    installed_at: str
    updated_at: str
    last_seen_at: str
    removed: bool = False
    removed_at: str | None = None
    source_detail: SourceDetail = None

    @property
    def primary_key(self) -> tuple[str, str]:
        return (self.root_id, self.directory_name)


@dataclass(frozen=True)
class LockFile:
    """Top-level lock file snapshot."""

    schema_version: int
    entries: tuple[LockEntry, ...]
    updated_at: str
    # Written only on First_Startup migration; preserved thereafter.
    created_at: str | None = None
