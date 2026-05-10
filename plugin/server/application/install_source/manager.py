"""InstallSourceManager module-level helpers.

This module implements the path-resolution, error type, atomic-write,
and parser / serializer primitives used by :class:`InstallSourceManager`.
The class itself is added in a later task; for now this file exposes only
the module-level helpers described in design §4.1 / §5.1 / §5.2 / §5.3:

* :func:`resolve_lock_path` — resolves the on-disk lock file path (Req 1.1 / 1.2).
* :func:`classify_plugin_path` — reverse-maps a plugin directory path to its
  ``(root_id, directory_name)`` primary key (design Fix 3).
* :class:`InstallSourceError` — the module's structured exception type.
* :func:`_atomic_write` — POSIX-style atomic file write (Req 1.3 / 1.4 / 12.1 / 12.3).
* :func:`_normalize_ts` — timestamp normalization to ``%Y-%m-%dT%H:%M:%S.%fZ``
  with graceful fallback (design §3.6 / Fix 7).
* :func:`_parse_lock` — tolerant ``bytes → LockFile`` parser following the
  10-step flow of design §5.1.
* :func:`_serialize_lock` — deterministic ``LockFile → bytes`` serializer
  following the field-order rules of design §5.2.

Do NOT import :mod:`plugin.settings` at module top: reading the user plugin
config root eagerly here would fight the test harness, which overrides the
``PLUGIN_CONFIG_ROOT`` environment variable to point into ``tmp_path``.
:func:`resolve_lock_path` performs the settings lookup lazily on each call.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable

from plugin.logging_config import get_logger

if TYPE_CHECKING:
    # Imported lazily to avoid a circular dependency: ``scanner.py`` imports
    # :class:`InstallSourceError` and :func:`classify_plugin_path` from this
    # module. The manager only needs the Scanner type for static hints, and
    # :class:`DiscoveredPlugin` for the reconcile loop's diff signature.
    from plugin.server.application.install_source.scanner import (
        DiscoveredPlugin,
        PluginDirectoryScanner,
    )
from plugin.server.application.install_source.models import (
    Channel,
    LockEntry,
    LockFile,
    Reason,
    RootId,
    SourceDetail,
    SourceDetailImported,
    SourceDetailMarket,
)

logger = get_logger("server.application.install_source")

# Canonical on-disk timestamp format (Fix 7). String comparison on values in
# this format is equivalent to chronological comparison, which Property 4
# (monotonicity) and the primary-key dedup rule (Req 4.2) rely on.
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class InstallSourceError(Exception):
    """Structured error raised by the install-source subsystem.

    Carries a stable ``code`` string (e.g. ``"PATH_OUTSIDE_ROOTS"``,
    ``"BUILTIN_CHANNEL_LOCKED"``, ``"LOCK_FILE_CORRUPT"``) plus a
    human-readable ``message`` and an open-ended ``details`` dict for
    structured context that callers can attach to API responses or logs.

    ``self.args`` is set to ``(code, message)`` so ``str(exc)`` and
    ``repr(exc)`` both include meaningful information without extra work.
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code: str = code
        self.message: str = message
        self.details: dict[str, Any] = dict(details) if details else {}
        super().__init__(code, message)


# ---------------------------------------------------------------------------
# Path resolution (Req 1.1, 1.2)
# ---------------------------------------------------------------------------


def resolve_lock_path() -> Path:
    """Resolve the absolute path of ``plugins.lock.json``.

    Resolution order (design §4.1 / Req 1.1–1.2):

    1. If the environment variable ``NEKO_PLUGIN_INSTALL_LOCK_PATH`` is set to
       a non-empty value, expand ``~`` and return its resolved absolute path.
    2. Otherwise return ``<USER_PLUGIN_CONFIG_ROOT parent>/plugins.lock.json``.

    The user-plugin-config-root lookup is performed lazily (see module
    docstring) so that tests overriding ``PLUGIN_CONFIG_ROOT`` at runtime
    take effect without needing to re-import this module.
    """

    env_val = os.environ.get("NEKO_PLUGIN_INSTALL_LOCK_PATH", "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()

    # Imported lazily to avoid touching plugin.settings at module import time.
    from plugin.settings import get_user_plugin_config_root

    return (get_user_plugin_config_root().parent / "plugins.lock.json").resolve()


# ---------------------------------------------------------------------------
# Plugin path classification (design Fix 3)
# ---------------------------------------------------------------------------


def classify_plugin_path(
    p: Path,
    *,
    builtin_root: Path,
    user_root: Path,
) -> tuple[RootId, str]:
    """Reverse-map a plugin directory path to ``(root_id, directory_name)``.

    All three inputs are normalised via ``Path.resolve(strict=False)`` before
    comparison so that trailing slashes, ``..`` segments, and symlinks do not
    cause spurious mismatches. :meth:`Path.is_relative_to` (Python 3.9+) is
    used instead of string-prefix matching so that e.g. ``/foo/bar`` does not
    incorrectly match ``/foo/bar_other``.

    The returned ``directory_name`` is the first path component *under the
    matched root* (``relative_to(root).parts[0]``), never ``p.name`` — this
    prevents subdirectories of a plugin from being misidentified as separate
    plugins.

    On no-match, raises :class:`InstallSourceError` with code
    ``"PATH_OUTSIDE_ROOTS"``. Callers typically bubble this up as an
    ``install_source_warning`` in API responses rather than fatal failure.
    """

    resolved = p.resolve(strict=False)
    b_root = builtin_root.resolve(strict=False)
    u_root = user_root.resolve(strict=False)

    if resolved.is_relative_to(b_root):
        return ("builtin", resolved.relative_to(b_root).parts[0])
    if resolved.is_relative_to(u_root):
        return ("user", resolved.relative_to(u_root).parts[0])

    raise InstallSourceError(
        "PATH_OUTSIDE_ROOTS",
        f"plugin path {p} is outside PLUGIN_CONFIG_ROOTS",
        details={"path": str(p)},
    )


# ---------------------------------------------------------------------------
# Atomic write (Req 1.3, 1.4, 12.1, 12.3)
# ---------------------------------------------------------------------------


def _atomic_write(lock_path: Path, payload: bytes) -> None:
    """Write ``payload`` to ``lock_path`` atomically via a ``tmp + rename`` dance.

    Steps:

    1. Ensure the parent directory exists (``mkdir(parents=True, exist_ok=True)``)
       per Req 1.3.
    2. Write ``payload`` to ``<parent>/plugins.lock.json.<pid>.<uuid>.tmp``.
    3. ``os.replace`` the temp file over ``lock_path``. On POSIX this is a
       single-syscall atomic rename; on Windows ``os.replace`` is also atomic
       for same-volume renames.
    4. On any exception (including the ``PermissionError`` raised when the
       parent directory exists but is not writable — Req 1.4), unlink the
       temp file best-effort and re-raise so the caller sees the original
       error.

    The ``<pid>.<uuid>`` suffix avoids collisions between concurrent writers
    from different processes and makes temp-file cleanup diagnosable.
    """

    parent = lock_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp_name = f"plugins.lock.json.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp_path = parent / tmp_name

    try:
        tmp_path.write_bytes(payload)
        os.replace(tmp_path, lock_path)
    except BaseException:
        # Best-effort cleanup; the tmp file may not exist if write_bytes
        # failed before creating it (e.g. PermissionError on the parent).
        with suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


# ---------------------------------------------------------------------------
# Timestamp normalization (Fix 7 / design §3.6)
# ---------------------------------------------------------------------------


def _normalize_ts(value: Any, *, now: str) -> str:
    """Normalize an inbound timestamp string to ``%Y-%m-%dT%H:%M:%S.%fZ`` (UTC).

    Called for every timestamp field read by :func:`_parse_lock` (top-level
    ``updated_at`` / ``created_at`` and entry-level ``installed_at`` /
    ``updated_at`` / ``last_seen_at`` / ``removed_at``) so that in-memory
    string comparison on these fields is equivalent to chronological
    comparison. Property 4 (monotonicity) and the primary-key dedup rule
    (Req 4.2, which picks the entry with the max ``last_seen_at``) both rely
    on this invariant.

    Behavior:

    * ``fromisoformat`` is used after rewriting a trailing ``Z`` to
      ``+00:00`` because Python's ``datetime.fromisoformat`` only learned to
      accept the ``Z`` suffix in 3.11; doing the rewrite keeps behavior
      stable across interpreter versions.
    * If the parsed datetime is naive (no ``tzinfo``), it is interpreted as
      UTC rather than raising — historical writers may have emitted naive
      strings by accident.
    * The result is always re-rendered via ``strftime(_TS_FORMAT)`` so the
      lexical form is canonical (fixed-width microseconds, trailing ``Z``).
    * On any failure (non-string input, unparseable string, overflow, etc.)
      a WARNING is logged and ``now`` is returned. This is the tolerant
      recovery posture required by Req 14 and Fix 7 — a hand-edited or
      corrupted timestamp must not take down the whole parse.

    ``now`` must itself already be in ``_TS_FORMAT``; the caller computes
    it once per parse to keep fallback timestamps consistent across the
    file.
    """

    if not isinstance(value, str) or not value:
        logger.warning(
            "install_source: unparseable timestamp value=%r, falling back to now",
            value,
        )
        return now

    try:
        # Python <3.11 fromisoformat rejects the trailing "Z"; rewrite to
        # the equivalent "+00:00" form so we behave identically on 3.10+.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Naive → interpret as UTC (historical writers may have been sloppy).
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
        return dt.strftime(_TS_FORMAT)
    except (ValueError, TypeError, OverflowError) as exc:
        logger.warning(
            "install_source: unparseable timestamp value=%r error=%s, falling back to now",
            value,
            exc,
        )
        return now


# ---------------------------------------------------------------------------
# Parser (design §5.1)
# ---------------------------------------------------------------------------

# Known fields at each structural level. Anything not in these sets is
# preserved into the corresponding ``extra_fields`` MappingProxyType for
# round-trip (Req 2.5 / 2.6 / 13.3).
_TOP_LEVEL_KNOWN_FIELDS = frozenset(
    {"schema_version", "entries", "updated_at", "bundles", "created_at"}
)
_ENTRY_KNOWN_FIELDS = frozenset(
    {
        "root_id",
        "directory_name",
        "plugin_id",
        "channel",
        "source",
        "reason",
        "bundle_ref",
        "installed_at",
        "updated_at",
        "last_seen_at",
        "removed",
        "removed_at",
        "source_detail",
    }
)
_SOURCE_DETAIL_MARKET_KNOWN = frozenset(
    {"plugin_market_id", "version", "package_url", "previous_version"}
)
_SOURCE_DETAIL_IMPORTED_KNOWN = frozenset({"package_filename", "package_sha256"})

# Legal enum values — kept here as plain sets so Parser can test membership
# without depending on runtime introspection of ``typing.Literal``.
_LEGAL_CHANNELS = frozenset({"builtin", "manual", "imported", "market"})
_LEGAL_REASONS = frozenset({"user_requested", "auto_dependency"})
_LEGAL_ROOT_IDS = frozenset({"builtin", "user"})

# Default ``install_source`` sub-object returned by :meth:`InstallSourceManager.to_api_view`
# when the plugin cannot be matched to any lock entry, or when the entry is
# soft-deleted. The ``/plugins`` response injector in
# :mod:`plugin.server.application.plugins.query_service` also uses this
# constant verbatim when the manager is unavailable or degraded
# (Req 15.2 – 15.6 / design §11.2). Kept module-level so callers can
# ``from ... import _DEFAULT_INSTALL_SOURCE`` and copy it with ``.copy()``
# (do not mutate the module-level object in place).
_DEFAULT_INSTALL_SOURCE: dict[str, Any] = {
    "source": "unknown",
    "reason": None,
    "installed_at": None,
    "source_detail": None,
}


def _extract_extras(raw: dict[str, Any], known: frozenset[str]) -> MappingProxyType[str, Any]:
    """Return a read-only view over keys of ``raw`` not in ``known``.

    The wrapped dict is built with a single generator so the original input
    is never mutated (the caller is free to keep using ``raw``).
    """

    extras = {k: v for k, v in raw.items() if k not in known}
    return MappingProxyType(extras)


def _parse_source_detail(
    channel: str,
    raw: Any,
    *,
    key: tuple[str, str],
) -> SourceDetail:
    """Parse the ``source_detail`` field for a single entry.

    Per Req 3.20 / design §3.3 the interpretation is channel-driven:

    * ``market`` → :class:`SourceDetailMarket`, with unknown sub-keys landing
      in its ``extra_fields``.
    * ``imported`` → :class:`SourceDetailImported`, same unknown-field policy.
    * ``builtin`` / ``manual`` / anything else → ``None``. Any inbound
      ``source_detail`` for these channels is silently dropped because Req
      3.20 pins the on-disk value to JSON ``null`` for them.

    Malformed input (non-dict, missing required keys) falls back to ``None``
    with a WARN — consistent with Req 14's tolerant posture.
    """

    if raw is None:
        return None

    if channel == "market":
        if not isinstance(raw, dict):
            logger.warning(
                "install_source: source_detail for market entry is not a dict key=%s",
                key,
            )
            return None
        try:
            return SourceDetailMarket(
                plugin_market_id=str(raw.get("plugin_market_id", "")),
                version=str(raw.get("version", "")),
                package_url=str(raw.get("package_url", "")),
                previous_version=raw.get("previous_version"),
                extra_fields=_extract_extras(raw, _SOURCE_DETAIL_MARKET_KNOWN),
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "install_source: failed to parse market source_detail key=%s error=%s",
                key,
                exc,
            )
            return None

    if channel == "imported":
        if not isinstance(raw, dict):
            logger.warning(
                "install_source: source_detail for imported entry is not a dict key=%s",
                key,
            )
            return None
        try:
            return SourceDetailImported(
                package_filename=str(raw.get("package_filename", "")),
                package_sha256=str(raw.get("package_sha256", "")),
                extra_fields=_extract_extras(raw, _SOURCE_DETAIL_IMPORTED_KNOWN),
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "install_source: failed to parse imported source_detail key=%s error=%s",
                key,
                exc,
            )
            return None

    # builtin / manual / unknown channel → drop any provided source_detail.
    return None


def _parse_entry(  # noqa: C901 — 10-step flow is intentionally explicit
    raw: Any,
    *,
    now: str,
) -> LockEntry | None:
    """Parse a single entry dict into a :class:`LockEntry` or ``None``.

    Returns ``None`` when the entry is missing the primary-key fields
    (``root_id`` / ``directory_name``) — these are required by Req 4.1 and
    without them the entry cannot be placed in the snapshot. We WARN and
    skip the bad entry rather than aborting the whole parse (Req 14
    tolerance).
    """

    if not isinstance(raw, dict):
        logger.warning("install_source: entry is not a dict value=%r, skipping", raw)
        return None

    # —— Primary key (Req 4.1) ——
    root_id_val = raw.get("root_id")
    directory_name_val = raw.get("directory_name")
    if (
        not isinstance(root_id_val, str)
        or root_id_val not in _LEGAL_ROOT_IDS
        or not isinstance(directory_name_val, str)
        or not directory_name_val
    ):
        logger.warning(
            "install_source: entry missing valid primary key root_id=%r directory_name=%r, skipping",
            root_id_val,
            directory_name_val,
        )
        return None

    # Store as a typing.Literal-narrowed string for mypy; value has been
    # validated against _LEGAL_ROOT_IDS above.
    root_id: RootId = root_id_val  # type: ignore[assignment]
    directory_name: str = directory_name_val
    key: tuple[str, str] = (root_id, directory_name)

    # —— channel / source merge (Req 3.6 / 3.7 / 3.8, Fix 6) ——
    raw_channel = raw.get("channel")
    raw_source = raw.get("source")

    extras_mut: dict[str, Any] = {
        k: v for k, v in raw.items() if k not in _ENTRY_KNOWN_FIELDS
    }

    channel_legal = isinstance(raw_channel, str) and raw_channel in _LEGAL_CHANNELS
    source_legal = isinstance(raw_source, str) and raw_source in _LEGAL_CHANNELS

    if raw_channel is None and raw_source is not None and source_legal:
        # Req 3.8: missing channel but legal source → adopt source.
        channel: Channel = raw_source  # type: ignore[assignment]
    elif channel_legal and source_legal and raw_channel != raw_source:
        # Req 3.7: both legal but disagree → channel wins + WARN.
        logger.warning(
            "install_source: channel/source conflict key=%s channel=%s source=%s — taking channel",
            key,
            raw_channel,
            raw_source,
        )
        channel = raw_channel  # type: ignore[assignment]
    elif channel_legal:
        channel = raw_channel  # type: ignore[assignment]
    else:
        # Fix 6: channel illegal (or missing & no legal source). Preserve
        # the original value for forensic round-trip, fall back to source
        # if that's legal, otherwise "manual" (never "builtin" — Req 5.2).
        if raw_channel is not None:
            extras_mut["_original_channel"] = raw_channel
            logger.warning(
                "install_source: illegal channel key=%s value=%r, falling back",
                key,
                raw_channel,
            )
        if source_legal:
            channel = raw_source  # type: ignore[assignment]
        else:
            channel = "manual"

    # Preserve original source verbatim when it's illegal or disagrees and
    # we ignored it above — symmetric with _original_channel (Fix 6).
    if raw_source is not None and not source_legal:
        extras_mut["_original_source"] = raw_source
        logger.warning(
            "install_source: illegal source key=%s value=%r, dropping",
            key,
            raw_source,
        )

    # —— reason (Req 3.9, Fix 6) ——
    raw_reason = raw.get("reason")
    if raw_reason is None:
        reason: Reason = "user_requested"
    elif isinstance(raw_reason, str) and raw_reason in _LEGAL_REASONS:
        reason = raw_reason  # type: ignore[assignment]
    else:
        extras_mut["_original_reason"] = raw_reason
        logger.warning(
            "install_source: illegal reason key=%s value=%r, falling back to user_requested",
            key,
            raw_reason,
        )
        reason = "user_requested"

    # —— plugin_id (Req 3.3: may be "") ——
    raw_plugin_id = raw.get("plugin_id", "")
    plugin_id = raw_plugin_id if isinstance(raw_plugin_id, str) else ""

    # —— Timestamps (Fix 7) ——
    installed_at = _normalize_ts(raw.get("installed_at"), now=now)
    updated_at = _normalize_ts(raw.get("updated_at"), now=now)
    last_seen_at = _normalize_ts(raw.get("last_seen_at"), now=now)
    raw_removed_at = raw.get("removed_at")
    removed_at: str | None
    if raw_removed_at is None:
        removed_at = None
    else:
        removed_at = _normalize_ts(raw_removed_at, now=now)

    # —— removed flag (Req 3.13) ——
    raw_removed = raw.get("removed", False)
    removed = bool(raw_removed)

    # —— bundle_ref (Fix 5: pass-through, v1 None) ——
    bundle_ref = raw.get("bundle_ref")

    # —— source_detail (Req 3.17 / 3.19 / 3.20) ——
    source_detail = _parse_source_detail(channel, raw.get("source_detail"), key=key)

    return LockEntry(
        root_id=root_id,
        directory_name=directory_name,
        plugin_id=plugin_id,
        channel=channel,
        reason=reason,
        installed_at=installed_at,
        updated_at=updated_at,
        last_seen_at=last_seen_at,
        removed=removed,
        removed_at=removed_at,
        bundle_ref=bundle_ref,
        source_detail=source_detail,
        extra_fields=MappingProxyType(extras_mut),
    )


def _parse_lock(raw: bytes) -> LockFile:
    """Parse ``raw`` bytes into a :class:`LockFile`.

    Implements the 10-step flow from design §5.1. The parser is intentionally
    tolerant: most "weird but recoverable" conditions are logged at WARNING
    level and fall back to a sane default rather than aborting. Only two
    conditions are fatal enough to raise :class:`InstallSourceError` with
    code ``"LOCK_FILE_CORRUPT"`` (Req 14.4):

    1. The byte stream is not valid UTF-8 JSON.
    2. The decoded top level is not a dict, or its ``entries`` field is
       present but not a list.

    Both fatal cases cause the caller (``InstallSourceManager.load``) to
    back up the corrupt file and re-seed from disk — see design §6.4.

    The function is a pure transformation (no filesystem / clock access
    beyond ``datetime.now(UTC)`` for the fallback ``now``) so Phase 6
    property tests can call it directly without a manager instance.
    """

    # Single ``now`` computed once so every fallback timestamp lands on the
    # same value within one parse (makes property-test diagnostics easier
    # and keeps string comparisons consistent).
    now = datetime.now(UTC).strftime(_TS_FORMAT)

    # —— Step 1: decode UTF-8 + json.loads ——
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InstallSourceError(
            "LOCK_FILE_CORRUPT",
            f"plugins.lock.json is not valid UTF-8: {exc}",
            details={"reason": "unicode_decode_error"},
        ) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InstallSourceError(
            "LOCK_FILE_CORRUPT",
            f"plugins.lock.json is not valid JSON: {exc}",
            details={"reason": "json_decode_error", "line": exc.lineno, "col": exc.colno},
        ) from exc

    # —— Step 2: top level must be a dict ——
    if not isinstance(data, dict):
        raise InstallSourceError(
            "LOCK_FILE_CORRUPT",
            f"plugins.lock.json top-level is not an object (got {type(data).__name__})",
            details={"reason": "top_level_not_dict"},
        )

    # —— Step 3: schema_version missing → 1 (Req 2.8) ——
    raw_schema_version = data.get("schema_version")
    if raw_schema_version is None:
        schema_version = 1
    elif isinstance(raw_schema_version, bool):
        # bool is a subclass of int; coerce invalid bool to 1.
        schema_version = 1
    elif isinstance(raw_schema_version, int):
        schema_version = raw_schema_version
    else:
        logger.warning(
            "install_source: non-integer schema_version=%r, treating as 1",
            raw_schema_version,
        )
        schema_version = 1

    # —— Step 5: schema_version > 1 → WARN, keep going (Req 2.7) ——
    if schema_version > 1:
        logger.warning(
            "install_source: schema_version=%d is newer than 1, attempting best-effort parse",
            schema_version,
        )

    # —— Step 6: entries must be a list (Req 14.4) ——
    raw_entries = data.get("entries")
    if raw_entries is None:
        raw_entries = []
    elif not isinstance(raw_entries, list):
        raise InstallSourceError(
            "LOCK_FILE_CORRUPT",
            f"plugins.lock.json 'entries' field is not a list (got {type(raw_entries).__name__})",
            details={"reason": "entries_not_list"},
        )

    # —— Step 4: bundles missing → [] (Req 2.9) ——
    raw_bundles = data.get("bundles")
    if raw_bundles is None:
        raw_bundles = []
    elif not isinstance(raw_bundles, list):
        logger.warning(
            "install_source: 'bundles' field is not a list (got %s), treating as []",
            type(raw_bundles).__name__,
        )
        raw_bundles = []

    # —— Step 7 + 8: parse each entry ——
    parsed: list[LockEntry] = []
    for raw_entry in raw_entries:
        entry = _parse_entry(raw_entry, now=now)
        if entry is not None:
            parsed.append(entry)

    # —— Step 10: primary-key dedup, keep max last_seen_at (Req 4.2) ——
    # All last_seen_at values are normalized by _parse_entry, so lex compare
    # ≡ chronological compare. When there's a tie we keep the later-seen
    # entry (stable for deterministic parser output).
    by_key: dict[tuple[str, str], LockEntry] = {}
    for entry in parsed:
        existing = by_key.get(entry.primary_key)
        if existing is None:
            by_key[entry.primary_key] = entry
        elif entry.last_seen_at >= existing.last_seen_at:
            logger.warning(
                "install_source: duplicate primary key=%s discarding older plugin_id=%r installed_at=%s",
                entry.primary_key,
                existing.plugin_id,
                existing.installed_at,
            )
            by_key[entry.primary_key] = entry
        else:
            logger.warning(
                "install_source: duplicate primary key=%s discarding older plugin_id=%r installed_at=%s",
                entry.primary_key,
                entry.plugin_id,
                entry.installed_at,
            )

    # —— Step 7 (top-level timestamps) ——
    updated_at = _normalize_ts(data.get("updated_at"), now=now)
    raw_created_at = data.get("created_at")
    if raw_created_at is None:
        created_at: str | None = None
    else:
        created_at = _normalize_ts(raw_created_at, now=now)

    # —— Step 9: unknown top-level fields → extra_fields ——
    top_extras = _extract_extras(data, _TOP_LEVEL_KNOWN_FIELDS)

    # —— Step 11: build frozen snapshot ——
    return LockFile(
        schema_version=schema_version,
        entries=tuple(by_key.values()),
        updated_at=updated_at,
        bundles=tuple(raw_bundles),
        created_at=created_at,
        extra_fields=top_extras,
    )


# ---------------------------------------------------------------------------
# Serializer (design §5.2)
# ---------------------------------------------------------------------------


def _serialize_source_detail_for_json(detail: SourceDetail) -> dict[str, Any] | None:
    """Convert a :class:`SourceDetail` to a JSON-ready dict (or ``None``).

    Field order is fixed per design §5.2: known fields first in the order
    declared below, ``extra_fields`` appended last. Any known field that
    would duplicate an extra key "wins" (extras cannot shadow known fields
    on the output side).
    """

    if detail is None:
        return None

    out: dict[str, Any] = {}
    if isinstance(detail, SourceDetailMarket):
        out["plugin_market_id"] = detail.plugin_market_id
        out["version"] = detail.version
        out["package_url"] = detail.package_url
        # previous_version is always written (Req 3.18 round-trip); None → null.
        out["previous_version"] = detail.previous_version
        for k, v in detail.extra_fields.items():
            if k not in out:
                out[k] = v
        return out

    if isinstance(detail, SourceDetailImported):
        out["package_filename"] = detail.package_filename
        out["package_sha256"] = detail.package_sha256
        for k, v in detail.extra_fields.items():
            if k not in out:
                out[k] = v
        return out

    # Defensive: an unknown SourceDetail subclass shouldn't exist, but if
    # one shows up we log and drop it rather than raising.
    logger.warning("install_source: unknown SourceDetail type %r, writing null", type(detail))
    return None


def _serialize_entry_for_json(entry: LockEntry) -> dict[str, Any]:
    """Convert a :class:`LockEntry` to a JSON-ready dict with fixed field order.

    Field order (design §5.2):

        root_id, directory_name, plugin_id, channel, source (= channel),
        reason, bundle_ref, installed_at, updated_at, last_seen_at,
        removed, removed_at (only when removed=True), source_detail,
        [...extra_fields appended last]

    ``source`` always mirrors ``channel`` at write time (Req 3.6) — the
    in-memory model carries only ``channel``.
    """

    out: dict[str, Any] = {
        "root_id": entry.root_id,
        "directory_name": entry.directory_name,
        "plugin_id": entry.plugin_id,
        "channel": entry.channel,
        "source": entry.channel,  # Req 3.6: source mirrors channel.
        "reason": entry.reason,
        "bundle_ref": entry.bundle_ref,  # Fix 5: passthrough, v1 always None.
        "installed_at": entry.installed_at,
        "updated_at": entry.updated_at,
        "last_seen_at": entry.last_seen_at,
        "removed": entry.removed,
    }

    # Req 3.14: removed_at only present when removed=True.
    if entry.removed:
        out["removed_at"] = entry.removed_at

    # Req 3.20: source_detail always present (null for None).
    out["source_detail"] = _serialize_source_detail_for_json(entry.source_detail)

    # Append entry-level extras last; known keys take precedence.
    for k, v in entry.extra_fields.items():
        if k not in out:
            out[k] = v

    return out


def _serialize_lock(lock: LockFile) -> bytes:
    """Serialize a :class:`LockFile` snapshot to UTF-8 JSON bytes.

    Deterministic output, suitable for atomic write via :func:`_atomic_write`.
    Key ordering rules (design §5.2):

    * Top level: ``schema_version``, ``created_at`` (only when non-None),
      ``updated_at``, ``entries``, ``bundles``, then ``extra_fields``.
    * Entries are sorted by ``(root_id, directory_name)`` lexicographically
      (Req 13.4) so identical input produces identical output regardless of
      in-memory iteration order.
    * ``json.dumps(..., ensure_ascii=False, indent=2, sort_keys=False)`` is
      used to preserve our explicit key order and keep Unicode readable.

    Pure function; safe to call from property tests without a manager.
    """

    out: dict[str, Any] = {
        "schema_version": lock.schema_version,
    }
    # created_at only written when set (Req 6.4: only First_Startup emits it
    # initially; subsequent writes preserve whatever was parsed).
    if lock.created_at is not None:
        out["created_at"] = lock.created_at

    out["updated_at"] = lock.updated_at

    sorted_entries = sorted(
        lock.entries, key=lambda e: (e.root_id, e.directory_name)
    )
    out["entries"] = [_serialize_entry_for_json(e) for e in sorted_entries]

    # bundles is a tuple in memory; json.dumps handles tuples natively as
    # arrays, but converting to list makes the intent obvious and matches
    # what would round-trip back through the parser.
    out["bundles"] = list(lock.bundles)

    # Append top-level extras last; never shadow known keys.
    for k, v in lock.extra_fields.items():
        if k not in out:
            out[k] = v

    return json.dumps(out, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")


# ---------------------------------------------------------------------------
# InstallSourceManager (design §4.4 / §6.4 / §6.5 / Fix 9)
# ---------------------------------------------------------------------------


class InstallSourceManager:
    """In-memory owner of the ``plugins.lock.json`` snapshot.

    This task (2.2) implements only the lifecycle scaffolding: ``load``,
    ``save``, the degrade/recovery bookkeeping, and the read-only
    properties. The reconcile loop and write-path methods
    (``reconcile`` / ``record_import`` / ``record_market`` / ``list_entries``
    / ``to_api_view``) land in subsequent tasks (2.3 — 2.6).

    Concurrency model (design §3.1 / Fix 2):

    * Writers (``load`` / ``save`` / future ``reconcile`` / ``record_*``)
      hold ``self._lock`` for the duration of the read-modify-publish cycle
      and replace ``self._current`` with a freshly-built :class:`LockFile`
      in a single attribute assignment.
    * Readers (future ``list_entries`` / ``to_api_view``) dereference
      ``self._current`` without a lock. Because :class:`LockFile` is frozen
      and its ``entries`` tuple is immutable, the reader always sees a
      fully consistent snapshot — either before or after any given
      writer publish, never a torn state.

    Degrade semantics (design §6.4):

    * ``FileNotFoundError`` on first read triggers **First_Startup**: we
      seed an empty :class:`LockFile` with ``created_at`` set to the
      current timestamp. This is the one code path that writes
      ``created_at``; subsequent writes preserve whatever was parsed
      from disk.
    * ``PermissionError`` / ``OSError`` on read enters **read-only
      degrade**: ``_current`` is populated with an empty snapshot
      (without ``created_at``, so a later successful read can still
      reconcile against the on-disk file without clobbering its original
      creation timestamp), ``save()`` becomes a no-op, and
      ``is_degraded`` / ``degrade_reason`` surface the failure to
      callers.
    * ``LOCK_FILE_CORRUPT`` (invalid JSON, top-level non-dict,
      ``entries`` not a list) triggers a **back-up + First_Startup
      rebuild**: the corrupt file is renamed to
      ``plugins.lock.json.bak-<epoch>`` (best-effort) and we fall through
      to the First_Startup seed path. The rebuild intentionally clears
      any prior degrade so that a subsequent healthy write can succeed.

    Self-healing (Fix 9):

    * ``try_recover`` re-runs ``load``, stamps
      ``_last_recover_attempt`` so that callers (the module-level
      ``get_install_source_manager()`` scheduler added in task 3.2) can
      rate-limit retries to once per 60s, and returns ``True`` iff the
      manager exited degrade as a result.
    """

    def __init__(
        self,
        *,
        lock_path: Path,
        builtin_root: Path,
        user_root: Path,
        scanner: "PluginDirectoryScanner",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.lock_path: Path = lock_path
        # Normalise both roots up front so downstream callers can pass
        # relative paths in without surprise. ``strict=False`` keeps us
        # happy when e.g. a fresh test tmp_path hasn't materialised the
        # directory yet.
        self.builtin_root: Path = builtin_root.resolve(strict=False)
        self.user_root: Path = user_root.resolve(strict=False)
        self.scanner: "PluginDirectoryScanner" = scanner
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

        self._lock: threading.RLock = threading.RLock()
        self._read_only: bool = False
        self._degrade_reason: str | None = None
        self._last_recover_attempt: datetime | None = None

        # Seed an empty snapshot so readers that fire before ``load()``
        # completes (or in tests that skip ``load``) see a consistent
        # object rather than ``None``. ``load()`` replaces this.
        self._current: LockFile = LockFile(
            schema_version=1,
            entries=(),
            updated_at=self._now_iso(),
            bundles=(),
            created_at=None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now_iso(self) -> str:
        """Return the current clock value normalised to ``_TS_FORMAT``.

        Any ``datetime`` returned by ``self._clock`` is coerced to UTC:
        naive values are interpreted as UTC (mirroring
        :func:`_normalize_ts`) and aware values are ``astimezone``-d to
        UTC. This guarantees string-compare monotonicity even if a
        caller installs a clock that emits local-time values.
        """

        dt = self._clock()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
        return dt.strftime(_TS_FORMAT)

    def _enter_read_only_degrade(self, *, reason: str) -> None:
        """Mark the manager as degraded and log at ERROR level (Req 14.2)."""

        self._read_only = True
        self._degrade_reason = reason
        logger.error("InstallSourceManager degraded: %s", reason)

    def _clear_degrade(self) -> None:
        """Clear degrade state after a successful recovery."""

        self._read_only = False
        self._degrade_reason = None

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def is_degraded(self) -> bool:
        """``True`` iff the manager is in read-only degrade."""

        return self._read_only

    @property
    def degrade_reason(self) -> str | None:
        """Human-readable explanation for the current degrade, or ``None``."""

        return self._degrade_reason

    @property
    def current_updated_at(self) -> str:
        """``updated_at`` of the current in-memory :class:`LockFile` snapshot."""

        return self._current.updated_at

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the lock file from disk (design §6.4).

        Branches:

        * ``FileNotFoundError`` → First_Startup. Seeds an empty
          :class:`LockFile` with ``created_at`` set to the current
          timestamp and clears any prior degrade. The on-disk file is
          NOT created here; the reconciler writes it at the end of the
          first successful reconcile.
        * ``PermissionError`` / ``OSError`` → read-only degrade with an
          empty in-memory snapshot (``created_at`` left as ``None`` so a
          future successful read can still reconcile against the true
          on-disk file without clobbering its original timestamp).
        * ``LOCK_FILE_CORRUPT`` (raised by :func:`_parse_lock` for
          invalid JSON / wrong top-level / non-list ``entries``) →
          rename corrupt file to ``plugins.lock.json.bak-<epoch>`` and
          fall through to the First_Startup seed path. WARN-level log
          so operators can still find the backup.

        Any other :class:`InstallSourceError` (there shouldn't be any
        under normal conditions, but future parser extensions might add
        more codes) is re-raised so the caller — typically
        :class:`~plugin.server.application.install_source.reconciler.StartupReconciler`
        — can decide what to do.
        """

        with self._lock:
            now = self._now_iso()
            try:
                raw = self.lock_path.read_bytes()
            except FileNotFoundError:
                # First_Startup: empty snapshot with created_at stamped.
                self._current = LockFile(
                    schema_version=1,
                    entries=(),
                    updated_at=now,
                    bundles=(),
                    created_at=now,
                )
                self._clear_degrade()
                logger.info(
                    "InstallSourceManager: First_Startup (lock file missing) path=%s",
                    self.lock_path,
                )
                return
            except (PermissionError, OSError) as exc:
                # Read failed — degrade with an empty snapshot. Do NOT
                # stamp created_at so a later recovery can reconcile
                # against the real on-disk file cleanly.
                self._current = LockFile(
                    schema_version=1,
                    entries=(),
                    updated_at=now,
                    bundles=(),
                    created_at=None,
                )
                self._enter_read_only_degrade(reason=f"read_failed: {exc}")
                return

            try:
                self._current = _parse_lock(raw)
                self._clear_degrade()
                return
            except InstallSourceError as exc:
                if exc.code != "LOCK_FILE_CORRUPT":
                    # Unexpected structured error — let the caller see it.
                    raise
                # Back up the corrupt file (best-effort) and rebuild via
                # First_Startup. Use int(time.time()) so the suffix is a
                # plain epoch seconds value that's easy to grep for.
                epoch = int(time.time())
                bak_path = self.lock_path.with_name(
                    f"plugins.lock.json.bak-{epoch}"
                )
                try:
                    self.lock_path.rename(bak_path)
                    logger.warning(
                        "InstallSourceManager: corrupt lock backed up to %s (%s)",
                        bak_path,
                        exc,
                    )
                except OSError as rename_exc:
                    # We can't back up the corrupt file — log and keep
                    # going; the rebuild's save() will overwrite it.
                    logger.error(
                        "InstallSourceManager: failed to back up corrupt lock %s: %s",
                        self.lock_path,
                        rename_exc,
                    )
                # First_Startup rebuild.
                self._current = LockFile(
                    schema_version=1,
                    entries=(),
                    updated_at=now,
                    bundles=(),
                    created_at=now,
                )
                self._clear_degrade()

    def save(self) -> None:
        """Serialize the current snapshot and atomically write to disk.

        No-op when ``is_degraded`` is True (Req 14.2): a degraded manager
        must not overwrite an on-disk file that might still be readable
        by an administrator. The whole body is wrapped in
        ``self._lock`` so that a concurrent writer can't mutate
        ``_current`` mid-serialization — the serializer itself is a
        pure function but we don't want to race against a ``reconcile``
        that's about to publish a new snapshot.
        """

        with self._lock:
            if self._read_only:
                logger.debug("InstallSourceManager: save skipped (degraded)")
                return
            payload = _serialize_lock(self._current)
            _atomic_write(self.lock_path, payload)

    def try_recover(self) -> bool:
        """Attempt to exit degrade by re-running :meth:`load` (Fix 9).

        Always stamps ``_last_recover_attempt`` so that the module-level
        scheduler added in task 3.2 can rate-limit retries to once per
        60s regardless of outcome. Returns ``True`` iff the manager was
        degraded *and* is no longer degraded after ``load()`` completes
        — i.e. this call actually recovered something. A successful
        ``try_recover`` on an already-healthy manager returns ``True``
        too, but a ``False`` result always means "still degraded".

        ``load()`` itself handles the normal degrade / corrupt-file
        branches internally and shouldn't raise under them; we still
        wrap it in a broad ``except Exception`` so that an unexpected
        parser bug can't crash the scheduler that calls us.
        """

        with self._lock:
            attempt_dt = self._clock()
            if attempt_dt.tzinfo is None:
                attempt_dt = attempt_dt.replace(tzinfo=UTC)
            else:
                attempt_dt = attempt_dt.astimezone(UTC)
            self._last_recover_attempt = attempt_dt

            was_degraded = self._read_only
            try:
                self.load()
            except Exception as exc:  # noqa: BLE001 — belt-and-braces
                logger.warning(
                    "InstallSourceManager.try_recover: load raised %s, staying degraded",
                    exc,
                )
                return False

            if self._read_only:
                return False
            if was_degraded:
                logger.info("InstallSourceManager: recovered from degrade")
            return True

    # ------------------------------------------------------------------
    # Reconcile (design §6.2 / §6.3)
    # ------------------------------------------------------------------

    def reconcile(self) -> None:
        """Run one three-way diff pass over scanner ↔ in-memory snapshot.

        Implements the design §6.2 pseudocode. Pure CPU work under
        ``self._lock``: we read the current snapshot, call
        ``self.scanner.scan()`` (which touches the filesystem but is
        independent of the snapshot), diff the two, and publish a fresh
        :class:`LockFile` in a single attribute assignment (Fix 2).

        Three kinds of structural change trigger a write:

        * **Add** (disk dir has no lock entry) — seeded via
          :meth:`_seed_entry` with channel derived from ``root_id``
          (``builtin`` → ``"builtin"`` per Req 5.1, ``user`` →
          ``"manual"`` per Req 11.1).
        * **Resurrect** (lock entry has ``removed=True`` but the dir is
          back) — clears ``removed`` / ``removed_at`` and refreshes
          ``last_seen_at`` + ``updated_at`` while preserving
          ``channel`` / ``reason`` / ``installed_at`` / ``bundle_ref``
          (Req 7.4).
        * **Soft delete** (lock entry is live but the dir disappeared)
          — sets ``removed=True`` + ``removed_at=now`` + ``updated_at=now``
          (Req 8.1).

        Two cases are special:

        * ``plugin_id`` backfill — if the lock entry's ``plugin_id`` is
          ``""`` (Fix 1 placeholder) and the scanner now has a real id,
          we patch it in and count the pass as dirty.
        * **No change** — platform-stable entries are carried over
          verbatim (``new_entries[key] = prev``) without touching
          ``last_seen_at``. This is the Fix 4 dirty-check semantics:
          if the whole pass is all-no-change we skip ``save()`` entirely
          and leave ``self._current.updated_at`` alone so the on-disk
          mtime stays put.

        First_Startup: when ``load()`` seeded an empty snapshot with
        ``created_at = now``, branch A naturally walks every disk
        directory through :meth:`_seed_entry` and the trailing
        ``save()`` drops a fully-populated lock file (Req 6.5).
        """

        with self._lock:
            old_lock = self._current
            disc = self.scanner.scan()
            disc_by_key: dict[tuple[str, str], "DiscoveredPlugin"] = {
                (d.root_id, d.directory_name): d for d in disc
            }
            entries_by_key: dict[tuple[str, str], LockEntry] = {
                e.primary_key: e for e in old_lock.entries
            }
            now = self._now_iso()
            new_entries: dict[tuple[str, str], LockEntry] = {}
            any_structural_change = False

            # —— Branch A: disk exists ——
            for key, d in disc_by_key.items():
                prev = entries_by_key.get(key)
                if prev is None:
                    # Req 7.2 / 11.1 / 5.1: brand-new directory.
                    new_entries[key] = self._seed_entry(d, now)
                    any_structural_change = True
                elif prev.removed:
                    # Req 7.4: resurrect. Preserve channel / source /
                    # reason / installed_at / bundle_ref; clear the
                    # removed flags and refresh both seen and updated.
                    new_entries[key] = dataclasses.replace(
                        prev,
                        removed=False,
                        removed_at=None,
                        last_seen_at=now,
                        updated_at=now,
                    )
                    any_structural_change = True
                elif not prev.plugin_id and d.plugin_id:
                    # Fix 1: scanner just learned the plugin's real id.
                    # Treat it as a real change so the new value gets
                    # persisted immediately.
                    new_entries[key] = dataclasses.replace(
                        prev,
                        plugin_id=d.plugin_id,
                        last_seen_at=now,
                        updated_at=now,
                    )
                    any_structural_change = True
                else:
                    # Fix 4: no change — carry over the existing entry
                    # verbatim, including its original last_seen_at,
                    # so the dirty check below can correctly skip save().
                    new_entries[key] = prev

            # —— Branch B: lock has entry, disk doesn't ——
            for key, prev in entries_by_key.items():
                if key in new_entries:
                    continue
                if prev.removed:
                    # Req 8.2: already soft-deleted — preserve as-is.
                    new_entries[key] = prev
                else:
                    # Req 8.1: directory just disappeared — soft delete.
                    new_entries[key] = dataclasses.replace(
                        prev,
                        removed=True,
                        removed_at=now,
                        updated_at=now,
                    )
                    any_structural_change = True

            # —— Fix 4 dirty check ——
            # If nothing structural changed, don't touch updated_at and
            # don't save; the on-disk file's mtime should stay stable.
            if not any_structural_change:
                return

            new_lock = dataclasses.replace(
                old_lock,
                entries=tuple(new_entries.values()),
                updated_at=now,
            )
            # Fix 2: single-assignment publish. Readers that dereference
            # self._current without the lock see either the old or new
            # snapshot, never a torn intermediate.
            self._current = new_lock
            self.save()  # Req 7.5

    # ------------------------------------------------------------------
    # Write-path helpers (tasks 2.4 / 2.5)
    # ------------------------------------------------------------------

    @staticmethod
    def _find_entry(
        lock: LockFile, root_id: RootId, directory_name: str
    ) -> LockEntry | None:
        """Return the entry with primary key ``(root_id, directory_name)`` or ``None``.

        Linear scan is fine here: ``entries`` is at most a few hundred
        rows in practice, and the write path is cold (user-triggered
        install / market install). A dict index would have to be rebuilt
        every time :attr:`_current` is replaced anyway.
        """

        for e in lock.entries:
            if e.root_id == root_id and e.directory_name == directory_name:
                return e
        return None

    @staticmethod
    def _replace_entry(
        old_lock: LockFile, new_entry: LockEntry, *, updated_at: str
    ) -> LockFile:
        """Return a new :class:`LockFile` with ``new_entry`` upserted at its primary key.

        Any existing entry with the same primary key is dropped; the new
        entry is appended to the end. Serializer (design §5.2) re-sorts by
        ``(root_id, directory_name)`` on write, so callers do not need to
        preserve ordering here. The top-level ``updated_at`` is bumped to
        the provided value.
        """

        key = new_entry.primary_key
        kept = [e for e in old_lock.entries if e.primary_key != key]
        kept.append(new_entry)
        return dataclasses.replace(
            old_lock,
            entries=tuple(kept),
            updated_at=updated_at,
        )

    # ------------------------------------------------------------------
    # Write path: record_import (design §7.4 / task 2.4)
    # ------------------------------------------------------------------

    def record_import(
        self,
        *,
        directory_path: Path,
        package_filename: str,
        package_sha256: str,
    ) -> None:
        """Record an ``imported`` install in the lock snapshot (Req 9.*).

        Flow (design §7.4):

        1. Resolve ``(root_id, directory_name)`` via
           :func:`classify_plugin_path` (Fix 3). Paths outside either
           root raise ``InstallSourceError("PATH_OUTSIDE_ROOTS", ...)``
           and are surfaced to the caller (``PluginCliService`` treats
           them as non-fatal warnings — see design §7.2).
        2. Read ``plugin_id`` eagerly from ``plugin.toml`` via
           :meth:`PluginDirectoryScanner._load_plugin_id` (Fix 1). The
           helper is best-effort and returns ``""`` on any failure, so
           it's safe to call before the builtin guard below.
        3. If the target dir lives under ``builtin_root``, reject with
           ``InstallSourceError("BUILTIN_CHANNEL_LOCKED", ...)`` at
           ERROR log level (Fix 12 / Req 5.2). Builtin entries are
           write-protected from the record paths; they can only be
           mutated by the reconciler's soft-delete/resurrect branches.
        4. Build the new :class:`LockEntry` under ``self._lock``:

           * **New entry**: all three timestamps set to a single ``now``
             (Req 9.4 / 9.5).
           * **Existing entry**: ``installed_at`` is preserved (Req 9.4
             idempotence guarantee); only ``channel="imported"``,
             ``source_detail``, ``plugin_id`` (if newly read is
             non-empty), ``updated_at``, and ``last_seen_at`` change.
             The entry is also un-soft-deleted
             (``removed=False`` / ``removed_at=None``) so a directory
             that disappeared and was re-imported comes back live.

        5. Publish via :meth:`_replace_entry` + single-assignment to
           ``self._current`` (Fix 2) and persist via :meth:`save`.
        """

        # Step 1: classify. PATH_OUTSIDE_ROOTS bubbles up to the caller.
        root_id, directory_name = classify_plugin_path(
            directory_path,
            builtin_root=self.builtin_root,
            user_root=self.user_root,
        )

        # Step 2: read plugin_id eagerly (Fix 1). Imported lazily here
        # to avoid a circular import — ``scanner.py`` imports
        # :class:`InstallSourceError` and :func:`classify_plugin_path`
        # from this module.
        from plugin.server.application.install_source.scanner import (
            PluginDirectoryScanner,
        )

        plugin_id = PluginDirectoryScanner._load_plugin_id(directory_path)

        # Step 3: builtin guard (Fix 12 / Req 5.2).
        if root_id == "builtin":
            logger.error(
                "InstallSourceManager.record_import: builtin channel is locked "
                "(directory=%s, plugin_id=%r)",
                directory_name,
                plugin_id,
            )
            raise InstallSourceError(
                "BUILTIN_CHANNEL_LOCKED",
                (
                    f"builtin plugin {directory_name} cannot be set to "
                    "channel=imported"
                ),
                details={
                    "directory_name": directory_name,
                    "plugin_id": plugin_id,
                    "target_channel": "imported",
                },
            )

        detail = SourceDetailImported(
            package_filename=package_filename,
            package_sha256=package_sha256,
        )

        # Step 4 + 5: build new entry under the lock and publish.
        with self._lock:
            old_lock = self._current
            now = self._now_iso()
            existing = self._find_entry(old_lock, root_id, directory_name)

            if existing is None:
                new_entry = LockEntry(
                    root_id=root_id,
                    directory_name=directory_name,
                    plugin_id=plugin_id,
                    channel="imported",
                    reason="user_requested",
                    installed_at=now,
                    updated_at=now,
                    last_seen_at=now,
                    removed=False,
                    removed_at=None,
                    bundle_ref=None,
                    source_detail=detail,
                )
            else:
                # Idempotent overwrite: preserve installed_at (Req 9.4)
                # and only upgrade plugin_id when we've actually read a
                # non-empty value — never regress a known id back to "".
                new_entry = dataclasses.replace(
                    existing,
                    plugin_id=plugin_id or existing.plugin_id,
                    channel="imported",
                    source_detail=detail,
                    updated_at=now,
                    last_seen_at=now,
                    removed=False,
                    removed_at=None,
                )

            new_lock = self._replace_entry(
                old_lock, new_entry, updated_at=now
            )
            self._current = new_lock
            self.save()

    # ------------------------------------------------------------------
    # Write path: record_market (design §8.2 / task 2.5)
    # ------------------------------------------------------------------

    def record_market(
        self,
        *,
        directory_path: Path,
        plugin_market_id: str,
        version: str,
        package_url: str,
    ) -> None:
        """Record a ``market`` install in the lock snapshot (Req 10.*).

        Structural mirror of :meth:`record_import` — same classification,
        same ``plugin_id`` eager-read (Fix 1), same builtin guard (Fix 12 /
        Req 5.2), same idempotence contract (Req 10.4) — but writes a
        :class:`SourceDetailMarket` and pins ``channel="market"``.

        Flow (design §8.2):

        1. Resolve ``(root_id, directory_name)`` via
           :func:`classify_plugin_path` (Fix 3). ``PATH_OUTSIDE_ROOTS``
           bubbles up to the caller.
        2. Read ``plugin_id`` eagerly from ``plugin.toml`` via
           :meth:`PluginDirectoryScanner._load_plugin_id` (Fix 1).
        3. Reject if the target dir lives under ``builtin_root``
           (``BUILTIN_CHANNEL_LOCKED``, ERROR log level). The details
           dict carries ``target_channel="market"`` so the caller can
           distinguish this from the ``record_import`` variant.
        4. Build :class:`SourceDetailMarket` with
           ``previous_version=None`` — v1 does not track prior versions
           on the write path (Req 10.5); any upgrade history lives in
           the market backend, not here.
        5. Upsert under ``self._lock``:

           * **New entry**: all three timestamps set to a single ``now``
             (Req 10.6 / 10.7).
           * **Existing entry**: ``installed_at`` preserved (Req 10.4
             idempotence); ``channel="market"``, ``source_detail``,
             ``plugin_id`` (when newly-read is non-empty), ``updated_at``,
             and ``last_seen_at`` updated. The entry is also
             un-soft-deleted. This path also covers the legitimate
             ``channel="imported" → "market"`` overwrite: after Fix 8
             the upload-and-install pipeline no longer double-writes,
             but manual admin recovery / legacy lock files can still
             land on a prior ``imported`` row and we promote it
             in-place.
        6. Publish via :meth:`_replace_entry` + single-assignment (Fix 2)
           and persist via :meth:`save`.
        """

        # Step 1: classify. PATH_OUTSIDE_ROOTS bubbles up to the caller.
        root_id, directory_name = classify_plugin_path(
            directory_path,
            builtin_root=self.builtin_root,
            user_root=self.user_root,
        )

        # Step 2: read plugin_id eagerly (Fix 1). Imported lazily here
        # to avoid a circular import with ``scanner.py``.
        from plugin.server.application.install_source.scanner import (
            PluginDirectoryScanner,
        )

        plugin_id = PluginDirectoryScanner._load_plugin_id(directory_path)

        # Step 3: builtin guard (Fix 12 / Req 5.2).
        if root_id == "builtin":
            logger.error(
                "InstallSourceManager.record_market: builtin channel is locked "
                "(directory=%s, plugin_id=%r)",
                directory_name,
                plugin_id,
            )
            raise InstallSourceError(
                "BUILTIN_CHANNEL_LOCKED",
                (
                    f"builtin plugin {directory_name} cannot be set to "
                    "channel=market"
                ),
                details={
                    "directory_name": directory_name,
                    "plugin_id": plugin_id,
                    "target_channel": "market",
                },
            )

        # Step 4: build the market source_detail. Req 10.5: v1 always
        # writes previous_version=None on this path.
        detail = SourceDetailMarket(
            plugin_market_id=plugin_market_id,
            version=version,
            package_url=package_url,
            previous_version=None,
        )

        # Step 5 + 6: upsert under the lock and publish.
        with self._lock:
            old_lock = self._current
            now = self._now_iso()
            existing = self._find_entry(old_lock, root_id, directory_name)

            if existing is None:
                new_entry = LockEntry(
                    root_id=root_id,
                    directory_name=directory_name,
                    plugin_id=plugin_id,
                    channel="market",
                    reason="user_requested",
                    installed_at=now,
                    updated_at=now,
                    last_seen_at=now,
                    removed=False,
                    removed_at=None,
                    bundle_ref=None,
                    source_detail=detail,
                )
            else:
                # Idempotent overwrite: preserve installed_at (Req 10.4)
                # and only upgrade plugin_id when we've actually read a
                # non-empty value — never regress a known id back to "".
                # This branch also covers the imported → market promotion
                # case called out in the docstring.
                new_entry = dataclasses.replace(
                    existing,
                    plugin_id=plugin_id or existing.plugin_id,
                    channel="market",
                    source_detail=detail,
                    updated_at=now,
                    last_seen_at=now,
                    removed=False,
                    removed_at=None,
                )

            new_lock = self._replace_entry(
                old_lock, new_entry, updated_at=now
            )
            self._current = new_lock
            self.save()

    # ------------------------------------------------------------------
    # Read path: list_entries / to_api_view (task 2.6 / design §11.3 / §12.3)
    # ------------------------------------------------------------------

    def list_entries(
        self,
        *,
        include_removed: bool = False,
        channel: str | None = None,
        source: str | None = None,
        reason: str | None = None,
        root_id: str | None = None,
    ) -> list[LockEntry]:
        """Return a filtered list of :class:`LockEntry` from the current snapshot.

        **Fix 2 — lock-free read path.** This method intentionally does
        NOT acquire ``self._lock``: it dereferences ``self._current``
        once into a local ``snapshot`` variable and iterates that frozen
        object. Because writers publish new snapshots via a single
        attribute assignment, every reader observes either the
        pre-publish or post-publish state in full — never a torn
        intermediate (design §3.1 / §12.3).

        Filter semantics (design §12.3):

        * ``include_removed=False`` (default) drops entries whose
          ``removed == True`` (Req 16.1 / 8.3).
        * ``channel`` and ``source`` both refer to
          :attr:`LockEntry.channel` — ``source`` is the on-disk JSON
          alias that mirrors it (Req 3.6). If both are provided and
          disagree, ``channel`` wins and we log a WARN (Req 16.8).
          If only ``source`` is provided it is used as the effective
          channel filter.
        * ``reason`` filters on :attr:`LockEntry.reason`.
        * ``root_id`` filters on :attr:`LockEntry.root_id`.

        Any provided filter value that is not in the legal enumeration
        raises :class:`InstallSourceError` with code
        ``"INVALID_FILTER"`` and ``details={"field": ..., "value": ...}``
        (Req 16.7 — the HTTP layer maps this to a 422). The four
        ``_LEGAL_*`` frozensets defined at module scope are the single
        source of truth for legality, kept in sync with the Parser.
        """

        # —— Validate filters up front (Req 16.7) ——
        if channel is not None and channel not in _LEGAL_CHANNELS:
            raise InstallSourceError(
                "INVALID_FILTER",
                f"invalid channel filter: {channel!r}",
                details={"field": "channel", "value": channel},
            )
        if source is not None and source not in _LEGAL_CHANNELS:
            raise InstallSourceError(
                "INVALID_FILTER",
                f"invalid source filter: {source!r}",
                details={"field": "source", "value": source},
            )
        if reason is not None and reason not in _LEGAL_REASONS:
            raise InstallSourceError(
                "INVALID_FILTER",
                f"invalid reason filter: {reason!r}",
                details={"field": "reason", "value": reason},
            )
        if root_id is not None and root_id not in _LEGAL_ROOT_IDS:
            raise InstallSourceError(
                "INVALID_FILTER",
                f"invalid root_id filter: {root_id!r}",
                details={"field": "root_id", "value": root_id},
            )

        # —— Resolve channel vs source (Req 16.8) ——
        # When both are set and disagree, channel wins + WARN. When only
        # ``source`` is given we promote it to the effective channel
        # filter so callers that use either alias get identical results.
        effective_channel = channel
        if channel is not None and source is not None and channel != source:
            logger.warning(
                "install_source: list_entries channel=%r and source=%r conflict, taking channel",
                channel,
                source,
            )
        elif effective_channel is None and source is not None:
            effective_channel = source

        # —— Iterate the immutable snapshot without locking (Fix 2) ——
        snapshot = self._current
        out: list[LockEntry] = []
        for entry in snapshot.entries:
            if not include_removed and entry.removed:
                continue
            if effective_channel is not None and entry.channel != effective_channel:
                continue
            if reason is not None and entry.reason != reason:
                continue
            if root_id is not None and entry.root_id != root_id:
                continue
            out.append(entry)
        return out

    def to_api_view(
        self,
        plugin_id: str,
        *,
        directory_path: Path | None = None,
    ) -> dict[str, Any]:
        """Build the ``install_source`` sub-object for the ``/plugins`` response.

        **Fix 1 — path-priority matching.** When the caller can supply
        the plugin's directory path we classify it into its
        ``(root_id, directory_name)`` primary key and look up the entry
        directly. This is the only reliable path for plugins that were
        just imported / market-installed and whose ``plugin_id`` hasn't
        been read yet — e.g. a lock entry may carry ``plugin_id = ""``
        (Req 3.3) but the primary-key lookup still succeeds
        (design §11.3).

        **Fix 2 — lock-free read.** Like :meth:`list_entries` we
        snapshot ``self._current`` once and operate on the frozen
        object. The method never mutates manager state, never acquires
        ``self._lock``, and never raises: any
        :class:`InstallSourceError` from :func:`classify_plugin_path`
        (e.g. the path is outside both roots) is swallowed and we fall
        through to the ``plugin_id`` fallback. This keeps the
        ``/plugins`` response path a hard 200 even when the caller
        passed a garbage path (Req 15.6).

        Match order (design §11.3):

        1. ``directory_path`` provided → ``classify_plugin_path`` →
           exact ``(root_id, directory_name)`` lookup.
        2. Fallback by ``plugin_id`` text match. When multiple entries
           share the same ``plugin_id`` (e.g. a soft-deleted row plus a
           resurrected one under a different directory), prefer
           ``removed=False``; break ties by the newest ``updated_at``.
        3. Req 4.3 placeholder semantics — if no entry matches
           ``plugin_id`` directly, accept an entry whose ``plugin_id``
           is ``""`` but whose ``directory_name`` equals the caller's
           ``plugin_id``. This covers the narrow window between import
           and the first scanner pass where the directory's id hasn't
           been read yet and the caller only has the directory name to
           work with.

        Return shape:

        * **No match or** ``entry.removed == True`` → a fresh copy of
          :data:`_DEFAULT_INSTALL_SOURCE` (Req 15.2 – 15.5).
        * **Matched live entry** → ``{"source": entry.channel,
          "reason": entry.reason, "installed_at": entry.installed_at,
          "source_detail": <serialized>}``. ``source_detail`` is
          produced by :func:`_serialize_source_detail_for_json` so it
          mirrors exactly what the on-disk JSON would carry (Req 15.1
          / 15.5 / design §5.2).
        """

        snapshot = self._current  # Fix 2 — no lock.
        entry: LockEntry | None = None

        # —— Step 1: path-priority lookup (Fix 1) ——
        if directory_path is not None:
            try:
                classified_root_id, directory_name = classify_plugin_path(
                    directory_path,
                    builtin_root=self.builtin_root,
                    user_root=self.user_root,
                )
                entry = self._find_entry(
                    snapshot, classified_root_id, directory_name
                )
            except InstallSourceError:
                # PATH_OUTSIDE_ROOTS (or any future classify error) →
                # fall through to plugin_id matching. /plugins must
                # never 5xx on a bad path (Req 15.6).
                entry = None

        # —— Step 2: plugin_id fallback ——
        if entry is None:
            candidates = [
                e for e in snapshot.entries if e.plugin_id == plugin_id
            ]
            # Step 3: Req 4.3 placeholder — directory_name stands in
            # for plugin_id while the real id is still "".
            if not candidates:
                candidates = [
                    e
                    for e in snapshot.entries
                    if e.plugin_id == "" and e.directory_name == plugin_id
                ]
            if candidates:
                # Prefer non-removed rows; break ties by newest
                # updated_at. ``updated_at`` strings are all normalized
                # to ``%Y-%m-%dT%H:%M:%S.%fZ`` by the Parser (Fix 7) so
                # string compare ≡ chronological compare.
                non_removed = [e for e in candidates if not e.removed]
                pool = non_removed or candidates
                entry = max(pool, key=lambda e: e.updated_at)

        # —— Build the view ——
        if entry is None or entry.removed:
            return _DEFAULT_INSTALL_SOURCE.copy()

        return {
            "source": entry.channel,
            "reason": entry.reason,
            "installed_at": entry.installed_at,
            "source_detail": _serialize_source_detail_for_json(entry.source_detail),
        }

    def _seed_entry(self, d: "DiscoveredPlugin", now: str) -> LockEntry:
        """Build a fresh :class:`LockEntry` for a newly-discovered directory.

        Field sourcing:

        * ``root_id`` / ``directory_name`` / ``plugin_id`` — from the
          scanner (``plugin_id`` may be ``""`` per Req 3.3 / Fix 1).
        * ``channel`` — ``"builtin"`` when ``d.root_id == "builtin"``
          (Req 5.1); ``"manual"`` when ``d.root_id == "user"``
          (Req 11.1). No other channel is reachable via the scanner
          path — market / imported entries only ever arrive via the
          ``record_*`` write path.
        * ``reason`` — ``"user_requested"`` per Req 3.5 (v1 only uses
          this value; ``"auto_dependency"`` is reserved for future work).
        * ``installed_at`` / ``updated_at`` / ``last_seen_at`` — all
          three are set to the single ``now`` argument so that a
          First_Startup run produces entries whose timestamps agree
          within a microsecond (Req 6.2 / 6.3).
        * ``bundle_ref`` — ``None`` per Fix 5 (v1 never writes bundle
          references).
        * ``source_detail`` — ``None`` per Req 11.2 / Req 3.20 for
          builtin/manual channels.
        """

        channel: Channel = "builtin" if d.root_id == "builtin" else "manual"
        return LockEntry(
            root_id=d.root_id,
            directory_name=d.directory_name,
            plugin_id=d.plugin_id,
            channel=channel,
            reason="user_requested",
            installed_at=now,
            updated_at=now,
            last_seen_at=now,
            removed=False,
            removed_at=None,
            bundle_ref=None,
            source_detail=None,
        )


__all__ = [
    "InstallSourceError",
    "InstallSourceManager",
    "resolve_lock_path",
    "classify_plugin_path",
    "_atomic_write",
    "_normalize_ts",
    "_parse_lock",
    "_serialize_lock",
    "_DEFAULT_INSTALL_SOURCE",
]
