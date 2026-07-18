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
import unicodedata
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from plugin.logging_config import get_logger

if TYPE_CHECKING:
    # Imported lazily to avoid a circular dependency: ``scanner.py`` imports
    # :class:`InstallSourceError` and :func:`classify_plugin_path` from this
    # module. The manager only needs the Scanner type for static hints, and
    # :class:`DiscoveredPlugin` for the reconcile loop's diff signature.
    from plugin.server.application.install_source.scanner import DiscoveredPlugin
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


def _normalise_path_for_compare(p: Path) -> tuple[str, Path]:
    """Normalise a path for cross-platform prefix comparison.

    Returns ``(comparable_str, original_resolved_path)`` — the str form is
    lower-cased on case-insensitive platforms (Windows, default macOS) and
    NFC-normalised so that the same directory name produced by different
    filesystems compares equal.

    The returned ``Path`` is the ``resolve(strict=False)``-ed original, kept
    so callers can still pull ``.parts`` from it for the final directory name.
    """

    resolved = p.resolve(strict=False)
    text = str(resolved)
    # Unicode: macOS APFS/HFS+ uses NFD internally; Windows and Linux see
    # whatever the user typed. Normalising to NFC picks a canonical form
    # that survives a round trip through any of the three platforms.
    text = unicodedata.normalize("NFC", text)
    # Case: Windows is case-insensitive; macOS is case-insensitive by
    # default (APFS can be flagged case-sensitive but defaults aren't).
    # ``os.path.normcase`` is the stdlib's cross-platform way to say
    # "compare paths the way the filesystem would".
    text = os.path.normcase(text)
    return text, resolved


def classify_plugin_path(
    p: Path,
    *,
    builtin_root: Path,
    user_root: Path,
) -> tuple[RootId, str]:
    """Reverse-map a plugin directory path to ``(root_id, directory_name)``.

    Paths are compared after :meth:`Path.resolve` + ``os.path.normcase`` +
    ``unicodedata.normalize("NFC", ...)`` so that Windows drive-letter
    casing (``C:\\`` vs ``c:\\``) and macOS Unicode NFD/NFC differences
    don't misclassify a real plugin directory as ``PATH_OUTSIDE_ROOTS``.

    The returned ``directory_name`` is the first path component **under
    the matched root**, taken from the (non-case-folded) resolved path so
    the caller gets the real on-disk casing back.

    On no-match, raises :class:`InstallSourceError` with code
    ``"PATH_OUTSIDE_ROOTS"``. Callers typically bubble this up as an
    ``install_source_warning`` rather than fatal failure.
    """

    p_key, p_resolved = _normalise_path_for_compare(p)
    b_key, b_resolved = _normalise_path_for_compare(builtin_root)
    u_key, u_resolved = _normalise_path_for_compare(user_root)
    sep = os.sep

    # ``startswith`` with a trailing separator so that e.g. ``/foo/bar``
    # does not match ``/foo/barbell``. Equality with the root itself
    # means "the plugin *is* the root" which is never legal — we still
    # want parts[0] under the root.
    def _matches(resolved: Path, key: str, root_key: str, root_resolved: Path) -> str | None:
        if key == root_key:
            return None  # p == root: no directory under root
        prefix = root_key if root_key.endswith(sep) else root_key + sep
        if not key.startswith(prefix):
            return None
        # Pull the on-disk name from the pre-normalised resolved path,
        # aligning via the same component count.
        try:
            rel_parts = resolved.relative_to(root_resolved).parts
        except ValueError:
            # resolve() drift between compare-form and original-form
            # (very rare; only on symlink races). Fall back to splitting
            # the case-normalised form, which is still correct as a
            # directory_name — callers will match it against on-disk
            # scanner output that goes through the same normalisation.
            rel = key[len(prefix):]
            rel_parts = tuple(part for part in rel.split(sep) if part)
        if not rel_parts:
            return None
        return rel_parts[0]

    dir_name = _matches(p_resolved, p_key, b_key, b_resolved)
    if dir_name is not None:
        return ("builtin", dir_name)
    dir_name = _matches(p_resolved, p_key, u_key, u_resolved)
    if dir_name is not None:
        return ("user", dir_name)

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

    1. Ensure the parent directory exists.
    2. Write ``payload`` to ``<parent>/plugins.lock.json.<pid>.<uuid>.tmp``.
    3. ``os.replace`` the temp file over ``lock_path``. POSIX rename is
       atomic by kernel guarantee. Windows is atomic for same-volume
       renames but can transiently fail with WinError 32 when another
       process (antivirus, Explorer preview, OneDrive) has the target
       file open — we retry a few times with a short backoff.
    4. On any exception, unlink the temp file best-effort and re-raise.
    """

    parent = lock_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp_name = f"plugins.lock.json.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp_path = parent / tmp_name

    try:
        tmp_path.write_bytes(payload)
        # Windows AV / Explorer can briefly hold an open handle on the
        # target, causing os.replace → PermissionError (WinError 32).
        # Retry 3 times with 50/100/200 ms backoff before giving up.
        # POSIX doesn't need this but the retry is cheap there too.
        last_exc: BaseException | None = None
        for attempt_ms in (0, 50, 100, 200):
            if attempt_ms:
                time.sleep(attempt_ms / 1000.0)
            try:
                os.replace(tmp_path, lock_path)
                return
            except PermissionError as exc:
                last_exc = exc
                continue
        # Exhausted retries — re-raise the last PermissionError.
        assert last_exc is not None
        raise last_exc
    except BaseException:
        with suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def _sweep_stale_tmp_files(lock_path: Path) -> None:
    """Remove ``plugins.lock.json.*.tmp`` leftovers from previous runs.

    A hard process kill (SIGKILL, taskkill, power loss) between
    ``write_bytes`` and ``os.replace`` leaves the tmp file behind. These
    are harmless (nothing ever reads them) but they accumulate and
    confuse users browsing the config directory. We sweep once at
    startup; never fatal — any error is swallowed.
    """

    parent = lock_path.parent
    if not parent.is_dir():
        return
    pattern = f"{lock_path.name}.*.tmp"
    try:
        stale = list(parent.glob(pattern))
    except OSError:
        return
    for path in stale:
        with suppress(OSError):
            path.unlink()


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
# Parser
# ---------------------------------------------------------------------------

# Legal enum values — kept as plain sets so the Parser can test membership
# without introspecting ``typing.Literal``.
_LEGAL_CHANNELS = frozenset({"builtin", "manual", "imported", "market"})
_LEGAL_REASONS = frozenset({"user_requested", "auto_dependency"})
_LEGAL_ROOT_IDS = frozenset({"builtin", "user"})

# Default ``install_source`` sub-object for the ``/plugins`` response when
# the plugin can't be matched to a lock entry (or the entry is soft-deleted,
# or the manager is unavailable). Callers should ``.copy()`` before use —
# do not mutate the module-level object in place.
_DEFAULT_INSTALL_SOURCE: dict[str, Any] = {
    "source": "unknown",
    "reason": None,
    "installed_at": None,
    "source_detail": None,
}


# ---------------------------------------------------------------------------
# Source-detail v2 coercion helpers (design §3.1.2 / Req 2.2)
# ---------------------------------------------------------------------------
# These four helpers normalize the v2 ``SourceDetailMarket`` fields when
# parsing a lock entry from disk. They are tolerant by contract: a v1 lock
# (or a hand-edited / partially-written v2 lock) may have any of the new
# keys missing, ``None``, wrong type, or invalid in shape. Each helper
# returns a sane default rather than raising — ``_parse_source_detail``
# relies on this to never fail a parse over a single bad sub-field.

_HEX_CHARS = frozenset("0123456789abcdef")


def _coerce_sha256(value: Any) -> str:
    """Pass-through 64-char lowercase hex; ``""`` for anything else (Req 2.2).

    Empty string is a legitimate "unknown" sentinel — v1 lock entries have
    no SHA stored, and the resulting ``""`` can be replaced once the
    package is downloaded (see :class:`InstallSourceManager.record_market_install`).
    """

    if not isinstance(value, str) or len(value) != 64:
        return ""
    if not all(c in _HEX_CHARS for c in value):
        return ""
    return value


def _coerce_optional_str(value: Any) -> str | None:
    """Pass-through non-empty str; ``None`` otherwise (Req 2.2)."""

    if isinstance(value, str) and value:
        return value
    return None


def _coerce_channel(value: Any) -> str:
    """Pass-through ``"stable"`` / ``"beta"``; default ``"stable"`` (Req 2.2)."""

    if isinstance(value, str) and value in ("stable", "beta"):
        return value
    return "stable"


def _coerce_published_at(value: Any, *, fallback: str, now: str) -> str:
    """Normalize a published-at timestamp; on failure use ``fallback``.

    Wraps :func:`_normalize_ts`: if ``value`` is parseable as ISO 8601 the
    result is the normalized canonical form; otherwise the v1 lock had no
    such field and we fall back to the entry's ``installed_at`` (which
    semantically means "I've had this plugin since at least the install
    date" — a reasonable lower-bound for ``published_at``).

    ``now`` is the per-parse anchor used inside :func:`_normalize_ts` for
    its own deepest fallback path; we prefer ``fallback`` (i.e. installed_at)
    when the input is missing or unparseable, so a non-string / empty input
    short-circuits to ``fallback`` without going through ``_normalize_ts``.
    """

    if not isinstance(value, str) or not value:
        return fallback
    normalized = _normalize_ts(value, now=now)
    # _normalize_ts returns ``now`` on parse failure; that's a worse fallback
    # than the entry's own installed_at. Detect that case and prefer the
    # caller-provided ``fallback``.
    if normalized == now and value != now:
        return fallback
    return normalized


def _parse_source_detail(
    channel: str,
    raw: Any,
    *,
    key: tuple[str, str],
    installed_at: str,
    now: str,
) -> SourceDetail:
    """Parse the ``source_detail`` field for a single entry.

    Per Req 3.20 / design §3.3 the interpretation is channel-driven:

    * ``market`` → :class:`SourceDetailMarket`. v2 (design §3.1.2) adds
      four sub-fields — ``package_sha256`` / ``payload_hash`` / ``channel``
      / ``published_at`` — that fall back to safe defaults when missing or
      malformed via :func:`_coerce_sha256` / :func:`_coerce_optional_str`
      / :func:`_coerce_channel` / :func:`_coerce_published_at` (with
      ``fallback=installed_at`` so a v1 row gets ``published_at`` ≈ when
      the plugin was first installed).
    * ``imported`` → :class:`SourceDetailImported`.
    * ``builtin`` / ``manual`` / anything else → ``None``. Any inbound
      ``source_detail`` for these channels is silently dropped because Req
      3.20 pins the on-disk value to JSON ``null`` for them.

    Malformed input (non-dict, missing required keys) falls back to ``None``
    with a WARN — consistent with Req 14's tolerant posture. Unknown sub-keys
    are silently dropped so a forward-compatible writer (a v3 lock with
    extra fields) doesn't fail this parser.
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
                package_sha256=_coerce_sha256(raw.get("package_sha256")),
                payload_hash=_coerce_optional_str(raw.get("payload_hash")),
                channel=_coerce_channel(raw.get("channel")),
                published_at=_coerce_published_at(
                    raw.get("published_at"), fallback=installed_at, now=now,
                ),
                previous_version=raw.get("previous_version"),
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

    # —— channel / source merge ——
    raw_channel = raw.get("channel")
    raw_source = raw.get("source")

    channel_legal = isinstance(raw_channel, str) and raw_channel in _LEGAL_CHANNELS
    source_legal = isinstance(raw_source, str) and raw_source in _LEGAL_CHANNELS

    if raw_channel is None and raw_source is not None and source_legal:
        # Missing channel but legal source → adopt source.
        channel: Channel = raw_source  # type: ignore[assignment]
    elif channel_legal and source_legal and raw_channel != raw_source:
        # Both legal but disagree → channel wins + WARN.
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
        # Channel illegal (or missing & no legal source). Fall back to
        # source if legal, otherwise "manual" (never "builtin").
        if raw_channel is not None:
            logger.warning(
                "install_source: illegal channel key=%s value=%r, falling back",
                key,
                raw_channel,
            )
        if source_legal:
            channel = raw_source  # type: ignore[assignment]
        else:
            channel = "manual"

    if raw_source is not None and not source_legal:
        logger.warning(
            "install_source: illegal source key=%s value=%r, dropping",
            key,
            raw_source,
        )

    # —— reason ——
    raw_reason = raw.get("reason")
    if raw_reason is None:
        reason: Reason = "user_requested"
    elif isinstance(raw_reason, str) and raw_reason in _LEGAL_REASONS:
        reason = raw_reason  # type: ignore[assignment]
    else:
        logger.warning(
            "install_source: illegal reason key=%s value=%r, falling back to user_requested",
            key,
            raw_reason,
        )
        reason = "user_requested"

    # —— plugin_id (may be "") ——
    raw_plugin_id = raw.get("plugin_id", "")
    plugin_id = raw_plugin_id if isinstance(raw_plugin_id, str) else ""
    raw_package_id = raw.get("package_id", "")
    package_id = raw_package_id.strip() if isinstance(raw_package_id, str) else ""

    # —— Timestamps ——
    installed_at = _normalize_ts(raw.get("installed_at"), now=now)
    updated_at = _normalize_ts(raw.get("updated_at"), now=now)
    last_seen_at = _normalize_ts(raw.get("last_seen_at"), now=now)
    raw_removed_at = raw.get("removed_at")
    removed_at: str | None
    if raw_removed_at is None:
        removed_at = None
    else:
        removed_at = _normalize_ts(raw_removed_at, now=now)

    # —— removed flag ——
    removed = bool(raw.get("removed", False))

    # —— source_detail (depends on installed_at as v2 published_at fallback per Req 2.2) ——
    source_detail = _parse_source_detail(
        channel,
        raw.get("source_detail"),
        key=key,
        installed_at=installed_at,
        now=now,
    )

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
        source_detail=source_detail,
        package_id=package_id,
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

    # —— Step 5: schema_version > 2 → WARN, keep going (Req 2.7) ——
    # v1 (default) and v2 are both legitimate inputs to this parser; only
    # truly unknown future versions get the WARN best-effort treatment.
    if schema_version > 2:
        logger.warning(
            "install_source: schema_version=%d is newer than 2, attempting best-effort parse",
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

    # —— Top-level timestamps ——
    updated_at = _normalize_ts(data.get("updated_at"), now=now)
    raw_created_at = data.get("created_at")
    if raw_created_at is None:
        created_at: str | None = None
    else:
        created_at = _normalize_ts(raw_created_at, now=now)

    return LockFile(
        schema_version=schema_version,
        entries=tuple(by_key.values()),
        updated_at=updated_at,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Serializer (design §5.2)
# ---------------------------------------------------------------------------


def _serialize_source_detail_for_json(detail: SourceDetail) -> dict[str, Any] | None:
    """Convert a :class:`SourceDetail` to a JSON-ready dict (or ``None``)."""

    if detail is None:
        return None

    if isinstance(detail, SourceDetailMarket):
        # Field order is contractually fixed by design §3.1.3 / Req 2.3 so
        # P1 round-trip can byte-equal the same logical entry across writes.
        # Python 3.7+ dict literals preserve insertion order, and json.dumps
        # honors that order; do not reach for OrderedDict.
        return {
            "plugin_market_id": detail.plugin_market_id,
            "version": detail.version,
            "channel": detail.channel,
            "package_url": detail.package_url,
            "package_sha256": detail.package_sha256,
            "payload_hash": detail.payload_hash,
            "published_at": detail.published_at,
            "previous_version": detail.previous_version,
        }

    if isinstance(detail, SourceDetailImported):
        return {
            "package_filename": detail.package_filename,
            "package_sha256": detail.package_sha256,
        }

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
        "source": entry.channel,  # source mirrors channel
        "reason": entry.reason,
        "installed_at": entry.installed_at,
        "updated_at": entry.updated_at,
        "last_seen_at": entry.last_seen_at,
        "removed": entry.removed,
    }

    # Optional for backward-compatible lock-file parsing and stable rewrites
    # of legacy rows. New imported/market installs always populate it.
    if entry.package_id:
        out["package_id"] = entry.package_id

    # removed_at only present when removed=True.
    if entry.removed:
        out["removed_at"] = entry.removed_at

    # source_detail always present (null for None).
    out["source_detail"] = _serialize_source_detail_for_json(entry.source_detail)

    return out


def _serialize_lock(lock: LockFile) -> bytes:
    """Serialize a :class:`LockFile` snapshot to UTF-8 JSON bytes.

    Deterministic output, suitable for atomic write via :func:`_atomic_write`.
    Entries are sorted by ``(root_id, directory_name)`` lexicographically
    so identical input produces identical output regardless of in-memory
    iteration order.

    The on-disk ``schema_version`` is pinned to ``2`` regardless of the
    in-memory ``LockFile.schema_version`` (Req 2.4): once we write through
    this function the v2 layout is the truth on disk. Reading a v1 lock
    and immediately serializing it (no other writes) is a no-op for
    structural fields — only ``schema_version`` flips and the market
    ``source_detail`` sub-object gains its v2 fields with default values.
    This is the lazy migration path described in design §3.1.5.

    Pure function; safe to call from property tests without a manager.
    """

    out: dict[str, Any] = {"schema_version": 2}
    # created_at only written when set (First_Startup emits it initially;
    # subsequent writes preserve whatever was parsed).
    if lock.created_at is not None:
        out["created_at"] = lock.created_at
    out["updated_at"] = lock.updated_at

    sorted_entries = sorted(
        lock.entries, key=lambda e: (e.root_id, e.directory_name)
    )
    out["entries"] = [_serialize_entry_for_json(e) for e in sorted_entries]

    return json.dumps(out, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")


# ---------------------------------------------------------------------------
# InstallSourceManager (design §4.4 / §6.4 / §6.5 / Fix 9)
# ---------------------------------------------------------------------------


class InstallSourceManager:
    """In-memory owner of the ``plugins.lock.json`` snapshot.

    Concurrency: writers hold ``self._lock`` for the read-modify-publish
    cycle and replace ``self._current`` with a freshly-built
    :class:`LockFile` in a single attribute assignment. Readers
    (``list_entries`` / ``to_api_view``) dereference ``self._current``
    without the lock; because :class:`LockFile` is frozen and ``entries``
    is a tuple, readers always see a fully consistent snapshot.

    Degrade:

    * ``FileNotFoundError`` on first read → **First_Startup**: seed an
      empty :class:`LockFile` with ``created_at`` set. The on-disk file
      is created on the next ``save()``.
    * ``PermissionError`` / ``OSError`` → **read-only degrade**: in-memory
      snapshot is populated, but ``save()`` becomes a no-op until the
      underlying issue is resolved and ``load()`` is re-run.
    * ``LOCK_FILE_CORRUPT`` → rename the bad file to
      ``plugins.lock.json.bak-<epoch>`` and rebuild via First_Startup.
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

        # Seed an empty snapshot so readers that fire before ``load()``
        # completes (or in tests that skip ``load``) see a consistent
        # object rather than ``None``. ``load()`` replaces this.
        self._current: LockFile = LockFile(
            schema_version=1,
            entries=(),
            updated_at=self._now_iso(),
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
            # Clean up any stale ``plugins.lock.json.<pid>.<uuid>.tmp``
            # leftovers from a previous run that was hard-killed between
            # tmp-write and atomic rename.
            _sweep_stale_tmp_files(self.lock_path)
            try:
                raw = self.lock_path.read_bytes()
            except FileNotFoundError:
                # First_Startup: empty snapshot with created_at stamped.
                self._current = LockFile(
                    schema_version=1,
                    entries=(),
                    updated_at=now,
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

            # —— Branch A: disk exists ——
            for key, d in disc_by_key.items():
                prev = entries_by_key.get(key)
                if prev is None:
                    # Brand-new directory — seed channel from root_id.
                    new_entries[key] = self._seed_entry(d, now)
                elif prev.removed:
                    # Resurrect: preserve channel / source / reason /
                    # installed_at / bundle_ref; clear removed flags and
                    # refresh seen + updated.
                    new_entries[key] = dataclasses.replace(
                        prev,
                        removed=False,
                        removed_at=None,
                        last_seen_at=now,
                        updated_at=now,
                    )
                elif not prev.plugin_id and d.plugin_id:
                    # Scanner just learned the plugin's real id — patch it.
                    new_entries[key] = dataclasses.replace(
                        prev,
                        plugin_id=d.plugin_id,
                        last_seen_at=now,
                        updated_at=now,
                    )
                else:
                    # Stable: refresh last_seen_at so we have a
                    # "still present" heartbeat.
                    new_entries[key] = dataclasses.replace(
                        prev,
                        last_seen_at=now,
                    )

            # —— Branch B: lock has entry, disk doesn't ——
            for key, prev in entries_by_key.items():
                if key in new_entries:
                    continue
                if prev.removed:
                    # Already soft-deleted — preserve as-is (idempotent).
                    new_entries[key] = prev
                else:
                    # Directory just disappeared — soft delete.
                    new_entries[key] = dataclasses.replace(
                        prev,
                        removed=True,
                        removed_at=now,
                        updated_at=now,
                    )

            new_lock = dataclasses.replace(
                old_lock,
                entries=tuple(new_entries.values()),
                updated_at=now,
            )
            self._current = new_lock
            self.save()

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
        package_id: str = "",
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
                    source_detail=detail,
                    package_id=package_id,
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
                    package_id=package_id or existing.package_id,
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
        package_id: str = "",
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

        # Step 4: build the market source_detail. ``previous_version`` is
        # computed inside the lock block below, after we know the existing
        # entry (if any), so an upgrade captures the old version.

        # Step 5 + 6: upsert under the lock and publish.
        with self._lock:
            old_lock = self._current
            now = self._now_iso()
            existing = self._find_entry(old_lock, root_id, directory_name)

            # Compute previous_version: capture the old market version
            # when this is a genuine upgrade (different version string).
            # Imported → market promotion and first install both leave
            # previous_version=None — there's no prior market version to
            # record.
            previous_version: str | None = None
            if (
                existing is not None
                and isinstance(existing.source_detail, SourceDetailMarket)
                and existing.source_detail.version
                and existing.source_detail.version != version
            ):
                previous_version = existing.source_detail.version

            detail = SourceDetailMarket(
                plugin_market_id=plugin_market_id,
                version=version,
                package_url=package_url,
                # v2 defaults: legacy record_market is kept for back-compat
                # with the old test surface; production callers should use
                # record_market_install / record_market_upgrade which carry
                # the full evidence (sha256, payload_hash, channel, published_at).
                package_sha256="",
                payload_hash=None,
                channel="stable",
                published_at=now,
                previous_version=previous_version,
            )

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
                    source_detail=detail,
                    package_id=package_id,
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
                    package_id=package_id or existing.package_id,
                )

            new_lock = self._replace_entry(
                old_lock, new_entry, updated_at=now
            )
            self._current = new_lock
            self.save()

    # ------------------------------------------------------------------
    # Write path: record_market_install / record_market_upgrade (design §3.2)
    # ------------------------------------------------------------------

    def _build_market_detail_with_warnings(
        self,
        market_detail: dict[str, Any],
        *,
        previous_version: str | None,
        now: str,
    ) -> tuple[SourceDetailMarket, list[str]]:
        """Build a ``SourceDetailMarket`` from a v2 ``market_detail`` dict.

        Required keys: ``plugin_market_id`` / ``version`` / ``package_url`` —
        callers should validate these before invoking; we read defensively
        here too. v2 fields that are missing or invalid get the same defaults
        as :func:`_parse_source_detail` (Req 2.2). Returns the dataclass plus
        a list of human-readable warning strings (currently only emitted
        when ``package_sha256`` is missing or invalid; per Req 4.6 the lock
        still writes ``""`` and the caller surfaces an
        ``install_source_warning`` to the user).
        """

        warnings: list[str] = []

        sha = _coerce_sha256(market_detail.get("package_sha256"))
        if not sha:
            # Empty package_sha256 is a legitimate v1 / partial-write case
            # but the user deserves to know we couldn't pin the package.
            warnings.append(
                "lock entry written with empty package_sha256 — "
                "package integrity cannot be verified offline"
            )

        published_at = _coerce_published_at(
            market_detail.get("published_at"),
            fallback=now,
            now=now,
        )

        return (
            SourceDetailMarket(
                plugin_market_id=str(market_detail.get("plugin_market_id", "")),
                version=str(market_detail.get("version", "")),
                package_url=str(market_detail.get("package_url", "")),
                package_sha256=sha,
                payload_hash=_coerce_optional_str(market_detail.get("payload_hash")),
                channel=_coerce_channel(market_detail.get("channel")),
                published_at=published_at,
                previous_version=previous_version,
            ),
            warnings,
        )

    def _record_market_common(
        self,
        *,
        root_id: RootId,
        directory_name: str,
        plugin_id: str,
        market_detail: dict[str, Any],
        is_upgrade: bool,
        package_id: str = "",
    ) -> tuple[LockEntry, list[str]]:
        """Shared body of :meth:`record_market_install` / :meth:`record_market_upgrade`.

        Difference: ``is_upgrade`` controls whether the prior entry's
        ``version`` is captured into the new entry's ``previous_version``
        and whether ``installed_at`` is preserved (upgrade) or refreshed
        (install).
        """

        # Builtin guard mirrors :meth:`record_market` — builtin entries
        # cannot be promoted to ``channel="market"`` (Req 5.2 / Fix 12).
        if root_id == "builtin":
            logger.error(
                "InstallSourceManager.record_market_%s: builtin channel is locked "
                "(directory=%s, plugin_id=%r)",
                "upgrade" if is_upgrade else "install",
                directory_name,
                plugin_id,
            )
            raise InstallSourceError(
                "BUILTIN_CHANNEL_LOCKED",
                f"builtin plugin {directory_name} cannot be set to channel=market",
                details={
                    "directory_name": directory_name,
                    "plugin_id": plugin_id,
                    "target_channel": "market",
                },
            )

        with self._lock:
            old_lock = self._current
            now = self._now_iso()
            existing = self._find_entry(old_lock, root_id, directory_name)

            previous_version: str | None = None
            installed_at = now
            if existing is not None:
                # Per design §3.2.2, upgrade preserves installed_at as
                # "I've had this directory since at least…"; install
                # refreshes it because the semantic is "fresh install".
                # When existing entry has been soft-removed we still
                # treat it as if it never existed for installed_at
                # purposes — a brand-new install gets ``now``.
                if is_upgrade and not existing.removed:
                    installed_at = existing.installed_at
                    if isinstance(existing.source_detail, SourceDetailMarket):
                        previous_version = existing.source_detail.version

            detail, warnings = self._build_market_detail_with_warnings(
                market_detail,
                previous_version=previous_version,
                now=now,
            )

            new_entry = LockEntry(
                root_id=root_id,
                directory_name=directory_name,
                plugin_id=plugin_id,
                channel="market",
                reason="user_requested",
                installed_at=installed_at,
                updated_at=now,
                last_seen_at=now,
                removed=False,
                removed_at=None,
                source_detail=detail,
                package_id=package_id or (existing.package_id if existing is not None else ""),
            )

            try:
                new_lock = self._replace_entry(old_lock, new_entry, updated_at=now)
                self._current = new_lock
                self.save()
            except Exception as exc:
                # Roll back the in-memory snapshot so a failed write
                # doesn't leak a phantom entry to readers.
                self._current = old_lock
                raise InstallSourceError(
                    "lock_write_failed",
                    f"failed to write lock for {plugin_id} "
                    f"{'upgrade' if is_upgrade else 'install'}: {exc}",
                    details={
                        "plugin_id": plugin_id,
                        "root_id": root_id,
                        "directory_name": directory_name,
                    },
                ) from exc

            return new_entry, warnings

    def record_market_install(
        self,
        *,
        root_id: RootId,
        directory_name: str,
        plugin_id: str,
        market_detail: dict[str, Any],
        package_id: str = "",
    ) -> tuple[LockEntry, list[str]]:
        """Record a fresh ``channel="market"`` install (design §3.2 / Req 4).

        Always sets ``previous_version=None`` and refreshes ``installed_at``
        to the current clock value — the semantic is "this directory is
        being installed fresh" even when an old entry exists at the same
        primary key (in which case the old entry is overwritten so it
        doesn't leave a zombie behind, but its version is *not* recorded
        as ``previous_version`` because that's the upgrade story).

        ``market_detail`` schema (design §3.2.1):

        - ``plugin_market_id`` *(required)*
        - ``version`` *(required)*
        - ``package_url`` *(required)*
        - ``channel`` *(optional, defaults to "stable")*
        - ``package_sha256`` *(optional, defaults to "" with warning)*
        - ``payload_hash`` *(optional, defaults to None)*
        - ``published_at`` *(optional, defaults to now)*

        Returns the newly written ``LockEntry`` plus a list of warning
        messages the caller should attach to its API response (currently
        only "missing package_sha256" — Req 4.6).

        Raises :class:`InstallSourceError` with code:

        - ``"BUILTIN_CHANNEL_LOCKED"`` if ``root_id == "builtin"`` (Req 5.2)
        - ``"lock_write_failed"`` if serialization or atomic write fails (Req 10.4)

        This method **does not** start or stop the plugin lifecycle — that
        is the caller's responsibility (R4.8 / design §3.2.3).
        """

        return self._record_market_common(
            root_id=root_id,
            directory_name=directory_name,
            plugin_id=plugin_id,
            market_detail=market_detail,
            is_upgrade=False,
            package_id=package_id,
        )

    def record_market_upgrade(
        self,
        *,
        root_id: RootId,
        directory_name: str,
        plugin_id: str,
        market_detail: dict[str, Any],
        package_id: str = "",
    ) -> tuple[LockEntry, list[str]]:
        """Record a ``channel="market"`` upgrade (design §3.2 / Req 4).

        When an active (non-removed) entry already exists at the same
        primary key:

        - if its ``source_detail`` is :class:`SourceDetailMarket`,
          ``new_entry.source_detail.previous_version`` captures
          ``old.source_detail.version``;
        - regardless of channel, ``new_entry.installed_at`` is preserved
          from the old entry ("I've had this directory since at least…");
        - ``updated_at`` and ``last_seen_at`` are bumped to the current clock.

        When no active old entry exists (no entry, or only a soft-removed
        one), this method is *equivalent* to :meth:`record_market_install`:
        ``previous_version=None`` and ``installed_at=now``.

        ``market_detail`` schema and error codes are identical to
        :meth:`record_market_install`.
        """

        return self._record_market_common(
            root_id=root_id,
            directory_name=directory_name,
            plugin_id=plugin_id,
            market_detail=market_detail,
            is_upgrade=True,
            package_id=package_id,
        )

    def package_id_for_directory(self, directory_path: Path) -> str:
        """Return the recorded profile key for an installed plugin directory."""

        root_id, directory_name = classify_plugin_path(
            directory_path,
            builtin_root=self.builtin_root,
            user_root=self.user_root,
        )
        entry = self._find_entry(self._current, root_id, directory_name)
        if entry is None or entry.removed:
            return ""
        return entry.package_id

    def find_active_market_entry(self, plugin_ref: str) -> LockEntry | None:
        """Return the active (non-removed) market entry for ``plugin_ref``, if any.

        ``plugin_ref`` may be either the unpacked ``plugin.toml`` id **or**
        the Market-side numeric/string ``plugin_market_id`` stored in the
        lock entry's ``source_detail``.
        """

        if not plugin_ref:
            return None
        for entry in self._current.entries:
            if entry.removed:
                continue
            if entry.channel != "market":
                continue
            if entry.plugin_id == plugin_ref:
                return entry
            detail = entry.source_detail
            if isinstance(detail, SourceDetailMarket):
                if detail.plugin_market_id == plugin_ref:
                    return entry
        return None

    def snapshot(self) -> LockFile:
        """Return the current in-memory :class:`LockFile` snapshot.

        Used by Bridge ``/market/installed`` to project a per-plugin
        ``latest_install_source`` view (design §3.5.2). Lock-free; the
        returned object is frozen so the caller can iterate without
        risking a torn read.
        """

        return self._current

    # ------------------------------------------------------------------
    # Write path: mark_removed (delete-hook)
    # ------------------------------------------------------------------

    def mark_removed(
        self,
        *,
        directory_path: Path,
        reason: str = "user_delete",
    ) -> None:
        """Soft-delete the lock entry at ``directory_path``.

        Mirrors :meth:`record_import` / :meth:`record_market` structurally
        but only flips ``removed`` / ``removed_at`` / ``updated_at``; all
        other fields (including ``installed_at`` and ``source_detail``)
        are preserved as audit trail.

        Idempotent: calling twice on an already-removed entry is a no-op
        (no timestamp mutation, no save). Missing entry is a WARN no-op.
        Builtin entries are rejected per Req 5.2 / Fix 12.

        ``reason`` is a log-only hint; it does NOT mutate the entry's
        ``reason`` field (which describes why the plugin was originally
        installed, not why it was removed).
        """

        root_id, directory_name = classify_plugin_path(
            directory_path,
            builtin_root=self.builtin_root,
            user_root=self.user_root,
        )

        if root_id == "builtin":
            logger.error(
                "InstallSourceManager.mark_removed: builtin channel is locked "
                "(directory=%s)",
                directory_name,
            )
            raise InstallSourceError(
                "BUILTIN_CHANNEL_LOCKED",
                f"builtin plugin {directory_name} cannot be soft-deleted",
                details={
                    "directory_name": directory_name,
                    "target_channel": "removed",
                },
            )

        with self._lock:
            old_lock = self._current
            existing = self._find_entry(old_lock, root_id, directory_name)
            if existing is None:
                logger.warning(
                    "InstallSourceManager.mark_removed: no lock entry for "
                    "(root_id=%s, directory=%s), skipping (reason=%s)",
                    root_id, directory_name, reason,
                )
                return
            if existing.removed:
                # Already removed — idempotent no-op.
                return
            now = self._now_iso()
            new_entry = dataclasses.replace(
                existing,
                removed=True,
                removed_at=now,
                updated_at=now,
            )
            new_lock = self._replace_entry(old_lock, new_entry, updated_at=now)
            self._current = new_lock
            self.save()

    # ------------------------------------------------------------------
    # Read path: list_entries / to_api_view (task 2.6 / design §11.3 / §12.3)
    # ------------------------------------------------------------------

    def list_entries(
        self,
        *,
        include_removed: bool = False,
    ) -> list[LockEntry]:
        """Return a snapshot of entries, optionally including soft-deleted ones.

        Reads ``self._current`` without the lock: writers publish new
        :class:`LockFile` snapshots via a single attribute assignment, so
        each reader sees either the pre-publish or post-publish state in
        full — never a torn intermediate.
        """

        snapshot = self._current
        if include_removed:
            return list(snapshot.entries)
        return [e for e in snapshot.entries if not e.removed]

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
