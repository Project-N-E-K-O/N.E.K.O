"""Frozen dataclass types for the plugin install source lock.

See design.md §3.2 for the authoritative definitions. All dataclasses are
``frozen=True`` to support the "immutable snapshot + atomic reference
replacement" concurrency strategy described in design §3.1 / Fix 2:

* Readers dereference ``InstallSourceManager._current`` without a lock and
  always observe a fully consistent ``LockFile``.
* Writers construct a new snapshot with :func:`dataclasses.replace` and
  publish it via a single attribute assignment under the manager's
  ``RLock``.

Every time-valued field is carried as an already-normalized string in the
format ``%Y-%m-%dT%H:%M:%S.%fZ`` (UTC). Normalization happens in the Parser
(see design §3.6 / Fix 7); models themselves do not validate or coerce
timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Enumerated string types
# ---------------------------------------------------------------------------

#: Which PLUGIN_CONFIG_ROOT a plugin directory sits under.
RootId = Literal["builtin", "user"]

#: The install channel an entry came from. Mirrored into the JSON ``source``
#: field at serialization time (see Req 3.6 / design §3.3).
Channel = Literal["builtin", "manual", "imported", "market"]

#: Why an entry exists. v1 always writes ``"user_requested"``; the
#: ``"auto_dependency"`` value is reserved for future dependency
#: auto-install work (Req 3.5 glossary).
Reason = Literal["user_requested", "auto_dependency"]


# ---------------------------------------------------------------------------
# Source detail payloads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceDetailMarket:
    """``source_detail`` payload for ``channel="market"`` entries (Req 3.17).

    ``previous_version`` is v1-fixed as ``None`` on write, but the Parser
    preserves any non-null value round-trip (Req 3.18).
    """

    plugin_market_id: str
    version: str
    package_url: str
    # v1 always writes None; Parser preserves future string values as-is.
    previous_version: str | None = None
    # Carries unknown future fields for round-trip (Req 2.6). Exposed as a
    # read-only MappingProxyType so the frozen dataclass remains immutable.
    extra_fields: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class SourceDetailImported:
    """``source_detail`` payload for ``channel="imported"`` entries (Req 3.19).

    ``package_sha256`` is a 64-character lowercase hex string per Req 3.19.
    """

    package_filename: str
    package_sha256: str  # 64-char lowercase hex
    extra_fields: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


#: For ``channel`` in {"builtin", "manual"} the ``source_detail`` is ``None``
#: (Req 3.20 — Serializer writes JSON ``null`` or omits the field).
SourceDetail = SourceDetailMarket | SourceDetailImported | None


# ---------------------------------------------------------------------------
# Lock entry / lock file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LockEntry:
    """A single plugin's install-source record.

    The primary key is ``(root_id, directory_name)`` (Req 4.1). ``plugin_id``
    may be the empty string when the directory's metadata was temporarily
    unreadable (Req 3.3).
    """

    root_id: RootId
    directory_name: str
    plugin_id: str  # may be "" per Req 3.3
    channel: Channel  # in-memory authoritative field (design §3.3)
    reason: Reason  # v1: always "user_requested" (Req 3.5)
    installed_at: str  # normalized %Y-%m-%dT%H:%M:%S.%fZ (Req 3.10)
    updated_at: str  # Req 3.11
    last_seen_at: str  # Req 3.12
    removed: bool = False  # Req 3.13
    # Only written out when removed=True (Req 3.14).
    removed_at: str | None = None
    # v1 always None; non-null values are passed through verbatim per Fix 5
    # (Req 3.15 / 3.16). Typed ``Any`` because future bundle_ref structures
    # are intentionally opaque at this layer.
    bundle_ref: Any = None
    source_detail: SourceDetail = None
    # Entry-level unknown fields captured by the Parser (Req 2.6 / Fix 10).
    extra_fields: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def primary_key(self) -> tuple[str, str]:
        """Return the ``(root_id, directory_name)`` primary key (Req 4.1)."""
        return (self.root_id, self.directory_name)


@dataclass(frozen=True)
class LockFile:
    """Top-level lock file snapshot.

    ``entries`` is a ``tuple`` so the whole structure is hashable and safe
    to share across threads without copying (design §3.1 Fix 2). The write
    path rebuilds a new snapshot with
    ``dataclasses.replace(lock, entries=tuple(new_entries), ...)``.
    """

    schema_version: int  # current writers emit 1 (Req 2.1)
    entries: tuple[LockEntry, ...]  # Req 2.2
    updated_at: str  # Req 2.3 — normalized RFC 3339 UTC
    bundles: tuple[Any, ...] = ()  # v1 writes []; round-trip unknown members (Req 2.4 / 2.5)
    # Written only on First_Startup migration (Req 6.4); preserved thereafter.
    created_at: str | None = None
    # Top-level unknown fields captured by the Parser (Req 2.6).
    extra_fields: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
