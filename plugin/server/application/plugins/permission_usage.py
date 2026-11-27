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
network uses; nothing else from the entries.
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
        self._since: datetime | None = None  # oldest coverage start seen
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
                    yield Use(at, plugin_id, permission, OUTCOME_ALLOWED, origin, count=count)
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

    def _index(self, use: Use) -> None:
        # Caller holds self._lock.
        key = (use.plugin_id, use.permission)
        last = self._last.get(key)
        if last is None or use.at >= last.at:
            self._last[key] = use
        if use.outcome != OUTCOME_BLOCKED:
            last = self._last_effective.get(key)
            if last is None or use.at >= last.at:
                self._last_effective[key] = use
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

    def _files(self) -> list[str]:
        directory = self._directory
        if directory is None:
            from plugin.server.application.plugins.call_audit import BACKUP_COUNT, audit_directory

            directory = str(audit_directory())
            backups = BACKUP_COUNT
        else:
            backups = 9
        base = os.path.join(str(directory), _FILE_NAME)
        # Oldest first.
        return [f"{base}.{index}" for index in range(backups, 0, -1)] + [base]

    def ensure_seeded(self) -> None:
        """Read the audit entries written before the index existed, once."""
        if self._seeded:
            return
        with self._seed_lock:
            if self._seeded:
                return
            try:
                paths = self._files()
            except Exception as exc:
                logger.warning("permission usage: audit files unavailable: error={}", type(exc).__name__)
                paths = []
            for path in paths:
                self._seed_file(path)
            self._seeded = True

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

    def history_since(self) -> datetime | None:
        """Since when the audit covers every use: the oldest coverage start
        still in the audit; None when there is none."""
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

    def refused_uses(self, after=None) -> dict[tuple[str, str], tuple[datetime, int, bool]]:
        """``(plugin, permission) -> (last time, count, count is a lower bound)``
        of uses judged not allowed because that plugin lacked the permission.
        ``after(plugin, permission)``: count only the refusals later than the
        time it returns. A merged batch that spans that time is left out, so
        the count can be short (down to 0): the third value is then True."""
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
                # listed, with a count of at least 0.
                out[key] = (spanning[-1], 0, True)
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


install()
