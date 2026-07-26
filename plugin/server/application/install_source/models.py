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
    """``source_detail`` for ``channel="market"`` entries.

    v2 schema (design §3.1.1): the lock entry carries the same evidence
    Market is already distributing — channel, sha256, payload hash, and
    publish timestamp — so a lock file alone is enough to identify which
    package is on disk and on which release channel it was installed.

    Field order intentionally matches the on-disk serialization order
    defined in design §3.1.3 (``plugin_market_id`` → ``version`` →
    ``channel`` → ``package_url`` → ``package_sha256`` → ``payload_hash``
    → ``published_at`` → ``previous_version``). The serializer writes
    fields in this declaration order via Python 3.7+ dict insertion
    ordering, so reordering this dataclass changes the on-disk byte
    layout — keep it stable.
    """

    plugin_market_id: str
    version: str
    package_url: str
    # v2 (R2.1, R2.9): Market-distributed evidence baked into the lock.
    # ``package_sha256``: 64-char lowercase hex; ``""`` when unknown
    # (legacy v1 row promoted via _parse_source_detail).
    package_sha256: str
    # ``payload_hash``: SHA-256 of unpacked metadata.toml [payload].hash;
    # may be None when the package omits it.
    payload_hash: str | None
    # ``channel``: "stable" | "beta"; default "stable" when missing.
    channel: str
    # ``published_at``: Market-side ``latest_version.created_at``;
    # falls back to ``LockEntry.installed_at`` on legacy rows.
    published_at: str
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
    # Package-profile directory key. Empty on legacy rows that predate
    # package identity tracking; callers may then fall back to plugin_id.
    package_id: str = ""

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
