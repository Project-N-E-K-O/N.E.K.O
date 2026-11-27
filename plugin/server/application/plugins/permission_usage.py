"""When each plugin last used each permission, from the call audit.

A small read-only index over the call-audit entries (see ``call_audit``):

* ``decision`` entries: one judged use by ``plugin`` (deduplicated by the gate
  within a minute, so counts are a lower bound);
* ``allow_summary`` entries: per-call counts of allowed uses by the called
  plugin (``target``); the cause comes from the call's ``forward`` entry;
* ``system_use`` entries: ``system.*`` uses the plugin process reported.

Entries recorded since the index was created arrive through the audit
observer; older ones are read once from the audit files on the first query.
Only identifiers, times and outcomes are kept, plus the target host of
network uses and, for the per-plugin audit list, the reported target summary
of a ``system_use`` (already reduced by the plugin process, e.g. a redacted
path or a program name); nothing else from the entries.

The audit files rotate, and entries can be lost on the way to them, so the
last uses are also kept in a small summary file next to them
(``usage_summary.json``, see ``SUMMARY_FILE``): per plugin and permission the
last use and the last one not refused (time, outcome, origin), the first use
seen, plus since when uses are covered and, per plugin, when entries were last
lost. It is read before the audit files and saved from time to time
(``start_summary_saver``) and at shutdown. A missing or unreadable summary
leaves the audit files as the only source, as before.

Lost entries (``note_loss``): what the plugin process could not send or the
host did not take (``system_use_lost``). An entry the host's audit file
could not take (queue full, write failure, closed) is not lost for this
index: observers see every entry before it is queued, so it is indexed and
reaches the next summary save. Within the last 30 days they make
"unused" unknown for the plugin's ``system.*`` rows (see ``lost_since``).
"""

from __future__ import annotations

import bisect
import json
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from plugin.logging_config import get_logger
from plugin.sdk.shared.permissions.catalog import normalize_permission

logger = get_logger("server.plugins.permission_usage")

OUTCOME_ALLOWED = "allowed"
# Judged as not allowed, but only recorded (the use went ahead).
OUTCOME_WOULD_BLOCK = "would_block"
# Judged as not allowed and refused.
OUTCOME_BLOCKED = "blocked"

NETWORK_CONNECT = "system.network:connect"
RECENT_LIMIT = 50
DAYS_KEPT = 31
# Refusals kept per plugin and permission, as (last time, count, first time)
# batches; past this the two neighbouring batches closest in time are merged
# (on a tie, the newer pair). A count after a cut-off takes only batches that
# start after it, so it is exact unless the cut-off falls inside a merged
# batch, which leaves that batch out: refused_uses then says the count is a
# lower bound. Merging by gap keeps a lone refusal from before the cut-off
# (read back after a restart) out of the newer batches; refusals in a burst
# right after the cut-off can still be merged across it.
REFUSED_BATCHES = 32
_ORIGINS_KEPT = 1024
_FILE_NAME = "call_audit.jsonl"
# Written once per host start (see record_coverage_start). Since then every
# use is audited, including allowed host-side uses outside any call; older
# entries may lack those, so history only counts from the oldest such entry.
COVERAGE_START = "usage_coverage_start"
SYSTEM_USE_LOST = "system_use_lost"
# Uses kept per plugin and permission for the audit list (newest).
AUDIT_KEPT = 100
SUMMARY_FILE = "usage_summary.json"
SUMMARY_SCHEMA = 1
SUMMARY_SAVE_SECONDS = 60.0
_SUMMARY_MAX_BYTES = 8 * 1024 * 1024
# Flags shown in the audit list (fixed vocabulary; others are dropped).
AUDIT_FLAGS = frozenset({
    "unattributed", "plugin_thread", "loopback", "possible_laundering", "consent_unavailable",
})
_OUTCOMES = frozenset({"allowed", "would_block", "blocked"})
_INVALID = "<invalid>"

# A network target as the plugin process reports it: "name(address):port",
# "address:port" or a bare name / address (a long name may be cut with "…").
# Only the host name, or without one the address, is kept; a target of any
# other shape (a URL path, a query, spaces) is dropped.
_HOST = r"[\w\-]+(?:\.[\w\-]+)*\.?"
_IP = r"\[[0-9A-Fa-f:.]+(?:%[\w.\-]+)?\]|[0-9]{1,3}(?:\.[0-9]{1,3}){3}"
_PORT = r"(?::[0-9]{1,5})?"
_NAMED_TARGET = re.compile(rf"(?P<host>{_HOST}…?)\((?:{_IP})\){_PORT}")
_BARE_TARGET = re.compile(rf"(?P<host>{_IP}|{_HOST}){_PORT}")


def network_host(target: object) -> str | None:
    """The host name in a reported network target, else its address; None
    when the target is not of a known shape."""
    if not isinstance(target, str) or not target or len(target) > 260:
        return None
    match = _NAMED_TARGET.fullmatch(target) or _BARE_TARGET.fullmatch(target)
    return match.group("host") if match else None


@dataclass(frozen=True, slots=True)
class Use:
    at: datetime
    plugin_id: str
    permission: str
    outcome: str
    origin_kind: str | None = None
    target: str | None = None
    # Plugins the judgement found lacking the permission.
    blocked_by: tuple[str, ...] = ()
    # Whether this use is also counted by its call's allow_summary.
    summarized: bool = False
    count: int = 1
    # For the audit list only: the operation (audit event or RPC operation),
    # the reported target summary, attribution flags and the handler a
    # plugin_thread use was started in.
    operation: str | None = None
    subject: str | None = None
    flags: tuple[str, ...] = ()
    handler: str | None = None

    def audit_dict(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "permission": self.permission,
            "operation": self.operation,
            "target": self.subject,
            "outcome": self.outcome,
            "origin_kind": self.origin_kind,
            "count": self.count,
            "flags": list(self.flags),
            "handler": self.handler,
        }

    def to_dict(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "plugin_id": self.plugin_id,
            "permission": self.permission,
            "outcome": self.outcome,
            "origin_kind": self.origin_kind,
            "target": self.target,
        }

    def last_used(self) -> dict:
        return {"at": self.at.isoformat(), "origin_kind": self.origin_kind, "outcome": self.outcome}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _outcome(entry: dict) -> str:
    if entry.get("verdict") == "would_deny":
        return OUTCOME_BLOCKED if entry.get("enforcing") is True else OUTCOME_WOULD_BLOCK
    return OUTCOME_ALLOWED


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _operation(value: object) -> str | None:
    # Host-written identifiers (call_audit sanitises what plugins send); a
    # value of any other shape (e.g. from an edited file) is dropped.
    if isinstance(value, str) and 0 < len(value) <= 128 and value != _INVALID and value.isprintable():
        return value
    return None


def _subject(permission: str, target: object) -> str | None:
    """The audit list's target: the network host, or the target the process
    reported (a redacted path, a program name), as written to the audit."""
    if permission == NETWORK_CONNECT:
        return network_host(target)
    if not isinstance(target, str) or not target or target == _INVALID or len(target) > 200:
        return None
    return target if target.isprintable() else None


def _flags(flags: Iterable) -> tuple[str, ...]:
    kept = [flag for flag in flags if isinstance(flag, str) and flag in AUDIT_FLAGS]
    return tuple(sorted(set(kept))) if len(kept) > 1 else tuple(kept)


def _audit_order(use: Use) -> tuple:
    # Newest first; ties in a fixed order, so pages do not shuffle them.
    return (use.at, use.permission, use.operation or "", use.subject or "", use.outcome, use.count)


def _summary_use(plugin_id: str, permission: str, value: object) -> Use | None:
    """One last use read back from the summary file."""
    if not isinstance(value, dict):
        return None
    at = _parse_time(value.get("at"))
    outcome = value.get("outcome")
    if at is None or not isinstance(outcome, str) or outcome not in _OUTCOMES:
        return None
    return Use(at, plugin_id, permission, outcome, _text(value.get("origin_kind")))


def _summary_dict(use: Use | None) -> dict | None:
    if use is None:
        return None
    return {"at": use.at.isoformat(), "outcome": use.outcome, "origin_kind": use.origin_kind}


class UsageIndex:
    def __init__(self, *, now=None, directory=None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._directory = directory
        self._lock = threading.Lock()
        self._seed_lock = threading.Lock()
        # Entries from before this moment are read from the files; later
        # ones come through observe(), so none is counted twice.
        self._created = self._now()
        self._seeded = False
        # Oldest coverage start seen in the audit (files and live): counts
        # and lists are complete from then on.
        self._since: datetime | None = None
        # Oldest coverage start the summary file holds: last uses are
        # complete from the earlier of the two (they outlive the files).
        self._summary_since: datetime | None = None
        self._last: dict[tuple[str, str], Use] = {}
        # Last use that was not refused.
        self._last_effective: dict[tuple[str, str], Use] = {}
        self._daily: dict[tuple[str, str], dict[date, int]] = {}
        # Uses judged not allowed, keyed by the plugin that lacked the
        # permission (for a call chain, each blocking member).
        # (plugin, permission) -> [(last time, count, first time)] in time order
        self._refused: dict[tuple[str, str], list[tuple[datetime, int, datetime]]] = {}
        self._recent: dict[str, list[Use]] = {}
        self._origins: OrderedDict[str, str] = OrderedDict()
        # (plugin, permission) -> uses for the audit list (bounded, see AUDIT_KEPT)
        self._audit: dict[tuple[str, str], list[Use]] = {}
        self._first: dict[tuple[str, str], datetime] = {}
        # plugin -> when entries of it were last lost
        self._lost: dict[str, datetime] = {}
        # Whether the summary file has fallen behind the index.
        self._dirty = False
        self._save_lock = threading.Lock()
        # Whether the summary file was read (False: missing or unusable).
        self.summary_loaded = False
        # "missing" / "read" / "corrupt" once read; None until a read worked.
        # A summary that could not be read this time (an OSError, a newer
        # format) is tried again and never overwritten meanwhile.
        self._summary_state: str | None = None
        self._summary_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Feeding
    # ------------------------------------------------------------------

    def observe(self, entry: dict) -> None:
        """Audit observer: index one entry. Never raises."""
        try:
            self._add(entry)
        except Exception:
            # Runs inside call_audit.record: a malformed entry is skipped,
            # it must never fail the recording itself.
            pass

    def _add(self, entry: dict) -> None:
        if not isinstance(entry, dict):
            return
        at = _parse_time(entry.get("ts"))
        if at is None:
            return
        kind = entry.get("kind")
        with self._lock:
            if kind == COVERAGE_START:
                if self._since is None or at < self._since:
                    self._since = at
                    self._dirty = True
                return
            if kind == SYSTEM_USE_LOST:
                plugin_id = _text(entry.get("plugin"))
                if plugin_id:
                    self._note_loss_locked(plugin_id, at)
                return
            if kind == "forward":
                call_id, origin = _text(entry.get("call_id")), _text(entry.get("origin_kind"))
                if call_id and origin:
                    self._origins[call_id] = origin
                    while len(self._origins) > _ORIGINS_KEPT:
                        self._origins.popitem(last=False)
                return
            for use in self._uses(kind, entry, at):
                self._index(use)

    def _uses(self, kind: object, entry: dict, at: datetime):
        if kind == "allow_summary":
            plugin_id, counts = _text(entry.get("target")), entry.get("counts")
            if not plugin_id or not isinstance(counts, dict):
                return
            origin = self._origins.get(_text(entry.get("call_id")) or "")
            for name, count in counts.items():
                permission = normalize_permission(name)
                if permission and isinstance(count, int) and not isinstance(count, bool) and count > 0:
                    yield Use(at, plugin_id, permission, OUTCOME_ALLOWED, origin, count=count,
                              operation="call")
            return
        if kind not in ("decision", "system_use"):
            return
        plugin_id = _text(entry.get("plugin"))
        permission = normalize_permission(entry.get("capability"))
        if not plugin_id or not permission:
            return
        flags = entry.get("flags") if isinstance(entry.get("flags"), list) else []
        origin = _text(entry.get("origin_kind")) if kind == "decision" else None
        if kind == "decision" and "unattributed" in flags:
            origin = None
        target = network_host(entry.get("target")) if permission == NETWORK_CONNECT else None
        outcome = _outcome(entry)
        blocked_by: tuple[str, ...] = ()
        if outcome != OUTCOME_ALLOWED:
            listed = entry.get("blocked_by") if kind == "decision" else None
            blocked_by = tuple(item for item in listed if _text(item)) if isinstance(listed, list) else ()
            blocked_by = blocked_by or (plugin_id,)
        yield Use(
            at, plugin_id, permission, outcome, origin, target,
            operation=_operation(entry.get("operation")),
            subject=_subject(permission, entry.get("target")) if kind == "system_use" else None,
            flags=(shown := _flags(flags)),
            handler=_operation(entry.get("handler")) if "plugin_thread" in shown else None,
            blocked_by=blocked_by,
            # An allowed decision inside a call is also counted by the call's
            # summary, except a possible laundering one (the gate does not
            # note that use on the call).
            summarized=(
                kind == "decision"
                and outcome == OUTCOME_ALLOWED
                and bool(_text(entry.get("call_id")))
                and "possible_laundering" not in flags
            ),
        )

    def _note_loss_locked(self, plugin_id: str, at: datetime) -> None:
        lost = self._lost.get(plugin_id)
        if lost is None or at > lost:
            self._lost[plugin_id] = at
            self._dirty = True

    def note_loss(self, plugin_id: object, at: datetime | None = None) -> None:
        """Entries of ``plugin_id`` were lost (not written to the audit). Never raises."""
        try:
            if _text(plugin_id):
                with self._lock:
                    self._note_loss_locked(plugin_id, at or self._now())
        except Exception:
            # Called while recording an audit entry: noting a loss must
            # never make the recording fail.
            pass

    def _index(self, use: Use) -> None:
        # Caller holds self._lock.
        key = (use.plugin_id, use.permission)
        last = self._last.get(key)
        if last is None or use.at >= last.at:
            self._last[key] = use
            self._dirty = True
        first = self._first.get(key)
        if first is None or use.at < first:
            self._first[key] = use.at
            self._dirty = True
        if use.outcome != OUTCOME_BLOCKED:
            last = self._last_effective.get(key)
            if last is None or use.at >= last.at:
                self._last_effective[key] = use
                self._dirty = True
            day = use.at.date()
            oldest = self._now().date() - timedelta(days=DAYS_KEPT)
            if not use.summarized and day >= oldest:
                days = self._daily.setdefault(key, {})
                days[day] = days.get(day, 0) + use.count
                if len(days) > DAYS_KEPT + 1:
                    for stale in [item for item in days if item < oldest]:
                        del days[stale]
        for blocked in use.blocked_by:
            refused_key = (blocked, use.permission)
            batches = self._refused.setdefault(refused_key, [])
            bisect.insort(batches, (use.at, use.count, use.at))
            if len(batches) > REFUSED_BATCHES:
                # Merge the two neighbours with the smallest gap (the newer
                # pair on a tie). Live uses arrive before the audit files are
                # read, so a merged batch can overlap an older single one:
                # the merged batch spans both.
                i = min(
                    range(len(batches) - 1),
                    key=lambda k: (batches[k + 1][2] - batches[k][0], -k),
                )
                (first_end, first_count, first_start), (second_end, second_count, second_start) = batches[i], batches[i + 1]
                batches[i:i + 2] = [
                    (max(first_end, second_end), first_count + second_count, min(first_start, second_start))
                ]
        recent = self._recent.setdefault(use.permission, [])
        recent.append(use)
        if len(recent) > RECENT_LIMIT * 2:
            recent.sort(key=lambda item: item.at, reverse=True)
            del recent[RECENT_LIMIT:]
        # A use its call's allow_summary also counts is listed once, there.
        listed = self._audit.setdefault(key, []) if not use.summarized else None
        if listed is not None:
            listed.append(use)
        if listed is not None and len(listed) > AUDIT_KEPT * 2:
            listed.sort(key=lambda item: item.at, reverse=True)
            del listed[AUDIT_KEPT:]

    def _audit_directory(self) -> tuple[str, int]:
        directory = self._directory
        if directory is None:
            from plugin.server.application.plugins.call_audit import BACKUP_COUNT, audit_directory

            return str(audit_directory()), BACKUP_COUNT
        return str(directory), 9

    def _files(self) -> list[str]:
        directory, backups = self._audit_directory()
        base = os.path.join(directory, _FILE_NAME)
        # Oldest first.
        return [f"{base}.{index}" for index in range(backups, 0, -1)] + [base]

    def _summary_path(self) -> str:
        return os.path.join(self._audit_directory()[0], SUMMARY_FILE)

    def ensure_seeded(self) -> None:
        """Read the summary and the audit entries written before the index
        existed, once."""
        if self._seeded:
            return
        with self._seed_lock:
            if self._seeded:
                return
            self._ensure_summary()
            try:
                paths = self._files()
            except Exception as exc:
                logger.warning("permission usage: audit files unavailable: error={}", type(exc).__name__)
                paths = []
            for path in paths:
                self._seed_file(path)
            self._seeded = True

    # ------------------------------------------------------------------
    # Summary file
    # ------------------------------------------------------------------

    def _ensure_summary(self) -> str | None:
        """Read the summary file once it can be read; its state (None: not
        readable this time, try again later)."""
        with self._summary_lock:
            if self._summary_state is None:
                self._summary_state = self._load_summary()
            return self._summary_state

    def _load_summary(self) -> str | None:
        """Merge the summary file into the index. Content that is not a
        summary (not JSON, wrong shape, an older or unknown format, too
        large) is "corrupt": the index goes on with the audit files alone
        and the file is moved aside before the next save. A file that could
        not be read now (an OSError) or is from a newer version returns
        None: nothing is merged and it is never overwritten until read."""
        try:
            path = self._summary_path()
            with open(path, "rb") as stream:
                raw = stream.read(_SUMMARY_MAX_BYTES + 1)
        except FileNotFoundError:
            return "missing"
        except Exception as exc:
            logger.warning("permission usage: summary not readable now, kept as it is: error={}",
                           type(exc).__name__)
            return None
        try:
            if len(raw) > _SUMMARY_MAX_BYTES:
                raise ValueError("summary too large")
            document = json.loads(raw.decode("utf-8"))
            version = document.get("schema_version") if isinstance(document, dict) else None
            if isinstance(version, int) and not isinstance(version, bool) and version > SUMMARY_SCHEMA:
                logger.warning("permission usage: summary from a newer version, kept as it is")
                return None
            plugins = document.get("plugins")
            if version != SUMMARY_SCHEMA or not isinstance(plugins, dict):
                raise ValueError("unknown summary format")
        except Exception as exc:
            logger.warning("permission usage: summary not usable, using the audit files only: error={}",
                           type(exc).__name__)
            return "corrupt"
        try:
            self._merge_summary(document, plugins)
        except Exception as exc:
            logger.warning("permission usage: summary partly read: error={}", type(exc).__name__)
        self.summary_loaded = True
        return "read"

    def _merge_summary(self, document: dict, plugins: dict) -> None:
        since = _parse_time(document.get("covered_since"))
        with self._lock:
            if since is not None and (self._summary_since is None or since < self._summary_since):
                self._summary_since = since
            for plugin_id, record in plugins.items():
                if not _text(plugin_id) or not isinstance(record, dict):
                    continue
                lost = _parse_time(record.get("lost_at"))
                if lost is not None:
                    self._note_loss_locked(plugin_id, lost)
                permissions = record.get("permissions")
                if not isinstance(permissions, dict):
                    continue
                for name, item in permissions.items():
                    permission = normalize_permission(name)
                    if not permission or not isinstance(item, dict):
                        continue
                    key = (plugin_id, permission)
                    last = _summary_use(plugin_id, permission, item.get("last"))
                    if last is None:
                        continue
                    if key not in self._last or last.at > self._last[key].at:
                        self._last[key] = last
                    effective = _summary_use(plugin_id, permission, item.get("last_effective"))
                    if effective is not None and effective.outcome != OUTCOME_BLOCKED and (
                        key not in self._last_effective or effective.at > self._last_effective[key].at
                    ):
                        self._last_effective[key] = effective
                    first = _parse_time(item.get("first_at")) or last.at
                    if key not in self._first or first < self._first[key]:
                        self._first[key] = first

    def summary(self) -> dict:
        """The summary document as it would be saved now."""
        with self._lock:
            plugins: dict[str, dict] = {}
            for (plugin_id, permission), last in self._last.items():
                record = plugins.setdefault(plugin_id, {"lost_at": None, "permissions": {}})
                first = self._first.get((plugin_id, permission), last.at)
                record["permissions"][permission] = {
                    "last": _summary_dict(last),
                    "last_effective": _summary_dict(self._last_effective.get((plugin_id, permission))),
                    "first_at": first.isoformat(),
                }
            # Losses older than the 30-day window no longer matter.
            oldest = self._now() - timedelta(days=DAYS_KEPT)
            for plugin_id, lost in self._lost.items():
                if lost >= oldest:
                    plugins.setdefault(plugin_id, {"lost_at": None, "permissions": {}})["lost_at"] = lost.isoformat()
            since = self._last_use_since_locked()
            return {
                "schema_version": SUMMARY_SCHEMA,
                "covered_since": since.isoformat() if since else None,
                "plugins": plugins,
            }

    def _last_use_since_locked(self) -> datetime | None:
        known = [since for since in (self._since, self._summary_since) if since is not None]
        return min(known, default=None)

    def save_summary(self, *, force: bool = False) -> bool:
        """Write the summary file if it fell behind (or ``force``). Merges the
        old summary first, so nothing in it is dropped (the audit files are
        not read for this: they are still there for the next start, and a
        read of them under way is not waited for). A summary that could not
        be read is not written over; a corrupt one is moved aside first
        (``usage_summary.json.bad``). Returns whether a file was written.
        Never raises."""
        try:
            state = self._ensure_summary()
            if state is None:
                return False
            with self._save_lock:
                with self._lock:
                    if not (self._dirty or force):
                        return False
                    self._dirty = False
                document = self.summary()
                try:
                    from utils.file_utils import atomic_write_json

                    path = self._summary_path()
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    if state == "corrupt":
                        try:
                            os.replace(path, path + ".bad")  # kept for a look, never read back
                        except FileNotFoundError:
                            pass
                        with self._summary_lock:
                            self._summary_state = "missing"
                    atomic_write_json(path, document, indent=None)
                except Exception:
                    with self._lock:
                        self._dirty = True  # try again next time
                    raise
                return True
        except Exception as exc:
            logger.warning("permission usage: summary not saved: error={}", type(exc).__name__)
            return False

    def _seed_file(self, path: str) -> None:
        try:
            stream = open(path, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("permission usage: audit file not read: error={}", type(exc).__name__)
            return
        with stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                at = _parse_time(entry.get("ts"))
                if at is None or at >= self._created:
                    continue
                self.observe(entry)

    # ------------------------------------------------------------------
    # Queries (each reads the files first if not done yet)
    # ------------------------------------------------------------------

    def last_use_since(self) -> datetime | None:
        """Since when last uses (``last_use``, ``last_effective_use``) are
        complete: the oldest coverage start in the audit or the summary."""
        self.ensure_seeded()
        with self._lock:
            return self._last_use_since_locked()

    def last_use_covers_days(self, days: int) -> bool:
        since = self.last_use_since()
        return since is not None and since <= self._now() - timedelta(days=days)

    def history_since(self) -> datetime | None:
        """Since when the audit covers every use: the oldest coverage start
        still in the audit files (or recorded since); None when there is
        none. Counts (``count``), recent records and the audit list are
        complete from then on; last uses can reach further back
        (``last_use_since``)."""
        self.ensure_seeded()
        with self._lock:
            return self._since

    def covers_days(self, days: int) -> bool:
        since = self.history_since()
        return since is not None and since <= self._now() - timedelta(days=days)

    def last_use(self, plugin_id: str, permission: str) -> Use | None:
        self.ensure_seeded()
        with self._lock:
            return self._last.get((plugin_id, permission))

    def last_effective_use(self, plugin_id: str, permission: str) -> Use | None:
        self.ensure_seeded()
        with self._lock:
            return self._last_effective.get((plugin_id, permission))

    def first_use(self, plugin_id: str, permission: str) -> datetime | None:
        self.ensure_seeded()
        with self._lock:
            return self._first.get((plugin_id, permission))

    def lost_since(self, plugin_id: str, cutoff: datetime) -> bool:
        """Whether entries of ``plugin_id`` were lost after ``cutoff``."""
        self.ensure_seeded()
        with self._lock:
            lost = self._lost.get(plugin_id)
        return lost is not None and lost > cutoff

    def lost_at(self, plugin_id: str) -> datetime | None:
        """When entries of ``plugin_id`` were last lost, within DAYS_KEPT days."""
        self.ensure_seeded()
        with self._lock:
            lost = self._lost.get(plugin_id)
        return lost if lost is not None and lost >= self._now() - timedelta(days=DAYS_KEPT) else None

    def audit(
        self,
        plugin_id: str,
        *,
        permission: str | None = None,
        outcome: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Use], int]:
        """Uses of ``plugin_id`` newest first, optionally of one permission /
        outcome: ``limit`` of them from ``offset`` on, and how many match in
        all. Only the newest AUDIT_KEPT per permission are kept, whatever
        their outcome; the outcome filter applies to those."""
        self.ensure_seeded()
        with self._lock:
            uses = [
                use
                for (owner, name), listed in self._audit.items()
                if owner == plugin_id and (permission is None or name == permission)
                for use in sorted(listed, key=_audit_order, reverse=True)[:AUDIT_KEPT]
            ]
        uses = [use for use in uses if outcome is None or use.outcome == outcome]
        uses.sort(key=_audit_order, reverse=True)
        return uses[offset:offset + limit], len(uses)

    def count(self, plugin_id: str, permission: str, days: int) -> int:
        """Recorded uses (not refused) on the last ``days`` days, today included."""
        self.ensure_seeded()
        first = (self._now() - timedelta(days=days - 1)).date()
        with self._lock:
            daily = self._daily.get((plugin_id, permission)) or {}
            for day in [day for day in daily if day < self._now().date() - timedelta(days=DAYS_KEPT)]:
                del daily[day]
            return sum(count for day, count in daily.items() if day >= first)

    def latest(self, permissions) -> Use | None:
        """The newest use (not refused) of any of ``permissions`` by any plugin."""
        self.ensure_seeded()
        wanted = set(permissions)
        with self._lock:
            uses = [use for (_, permission), use in self._last_effective.items() if permission in wanted]
        return max(uses, key=lambda use: use.at, default=None)

    def plugins_using(self, permissions) -> set[str]:
        self.ensure_seeded()
        wanted = set(permissions)
        with self._lock:
            return {plugin_id for plugin_id, permission in self._last if permission in wanted}

    def forget_refused(self, plugin_id: str, permission: str) -> None:
        """The user decided ``permission`` for ``plugin_id``: earlier refusals
        no longer ask for a decision. Does not read the audit files: refusals
        seeded later are filtered by the stored decision time."""
        with self._lock:
            self._refused.pop((plugin_id, permission), None)

    def has_refused(self, plugin_id: str, permission: str, *, after: datetime | None = None) -> bool:
        """Whether refusals are held for the pair, later than ``after`` if given
        (a batch merged across it counts: its last refusal is later)."""
        self.ensure_seeded()
        with self._lock:
            batches = list(self._refused.get((plugin_id, permission)) or ())
        return any(after is None or at > after for at, _count, _start in batches)

    def refused_uses(self, after=None) -> dict[tuple[str, str], tuple[datetime, int, bool]]:
        """``(plugin, permission) -> (last time, count, count is a lower bound)``
        of uses judged not allowed because that plugin lacked the permission.
        ``after(plugin, permission)``: count only the refusals later than the
        time it returns. A merged batch that spans that time is left out, so
        the count can be short (down to 1): the third value is then True."""
        self.ensure_seeded()
        with self._lock:
            refused = {key: list(batches) for key, batches in self._refused.items()}
        out = {}
        for key, batches in refused.items():
            cutoff = after(*key) if after is not None else None
            kept = [(at, count) for at, count, start in batches if cutoff is None or start > cutoff]
            spanning = [at for at, _count, start in batches if cutoff is not None and start <= cutoff < at]
            if kept:
                out[key] = (kept[-1][0], sum(count for _at, count in kept), bool(spanning))
            elif spanning:
                # Every refusal after the cut-off was merged across it: still
                # listed. The batch's last refusal is itself after the
                # cut-off, so the count is at least 1.
                out[key] = (spanning[-1], 1, True)
        return out

    def recent(self, permissions, limit: int = RECENT_LIMIT) -> list[Use]:
        """Newest first."""
        self.ensure_seeded()
        with self._lock:
            uses = [use for permission in set(permissions) for use in self._recent.get(permission, ())]
        uses.sort(key=lambda use: use.at, reverse=True)
        return uses[:limit]


usage_index = UsageIndex()


def record_coverage_start() -> None:
    """Note in the audit that complete use recording starts now (host start)."""
    from plugin.server.application.plugins import call_audit

    call_audit.record({"kind": COVERAGE_START})


def install() -> None:
    """Start feeding ``usage_index`` from the call audit (idempotent)."""
    from plugin.server.application.plugins import call_audit

    call_audit.add_observer(usage_index.observe)
    # The files written before this index existed are read before the first
    # rotation can delete one (once per run; a seed under way is waited for).
    call_audit.add_rollover_observer(usage_index.ensure_seeded)


_saver: tuple[threading.Thread, threading.Event] | None = None
_saver_lock = threading.Lock()


def start_summary_saver(interval: float = SUMMARY_SAVE_SECONDS) -> None:
    """Read the audit files into ``usage_index`` on a background thread,
    then save its summary every ``interval`` seconds when it fell behind
    (host start; idempotent)."""
    global _saver
    with _saver_lock:
        if _saver is not None and _saver[0].is_alive():
            return
        stop = threading.Event()

        def run() -> None:
            # Read the audit files now, before they can rotate: uses newer
            # than the summary (e.g. after a crash) must reach it.
            try:
                usage_index.ensure_seeded()
            except Exception as exc:
                logger.warning("permission usage: audit files not read: error={}", type(exc).__name__)
            usage_index.save_summary()
            while not stop.wait(interval):
                usage_index.save_summary()

        thread = threading.Thread(target=run, name="neko-usage-summary", daemon=True)
        thread.start()
        _saver = (thread, stop)


def stop_summary_saver() -> None:
    """Stop the saver and save what is left (host shutdown). Never raises."""
    global _saver
    with _saver_lock:
        saver, _saver = _saver, None
    if saver is not None:
        saver[1].set()
    usage_index.save_summary()


install()
