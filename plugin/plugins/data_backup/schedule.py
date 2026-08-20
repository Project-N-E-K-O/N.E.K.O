"""Persistent schedule state for automatic backup snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


DEFAULT_INTERVAL_DAYS = 7
MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 365
VALID_GROUPS = ("core", "assets")


def _as_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return (
        current.replace(tzinfo=UTC)
        if current.tzinfo is None
        else current.astimezone(UTC)
    )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _time_text(value: datetime) -> str:
    return _as_utc(value).isoformat()


@dataclass(frozen=True)
class ScheduleState:
    enabled: bool = False
    interval_days: int = DEFAULT_INTERVAL_DAYS
    groups: tuple[str, ...] = ("core",)
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_error: str | None = None
    last_warning: str | None = None

    @classmethod
    def from_config(
        cls, value: object, *, now: datetime | None = None
    ) -> ScheduleState:
        raw = value if isinstance(value, dict) else {}
        enabled = raw.get("enabled") is True
        interval = raw.get("interval_days")
        if (
            not isinstance(interval, int)
            or isinstance(interval, bool)
            or not MIN_INTERVAL_DAYS <= interval <= MAX_INTERVAL_DAYS
        ):
            interval = DEFAULT_INTERVAL_DAYS
        raw_groups = raw.get("groups")
        groups = (
            tuple(
                dict.fromkeys(
                    group
                    for group in raw_groups
                    if isinstance(group, str) and group in VALID_GROUPS
                )
            )
            if isinstance(raw_groups, list)
            else ()
        )
        groups = groups or ("core",)
        last_run = _parse_time(raw.get("last_run_at"))
        next_run = _parse_time(raw.get("next_run_at")) if enabled else None
        error = raw.get("last_error")
        if not isinstance(error, str) or not error:
            error = None
        warning = raw.get("last_warning")
        if not isinstance(warning, str) or not warning:
            warning = None
        if enabled and next_run is None:
            next_run = _as_utc(now) + timedelta(days=interval)
        return cls(
            enabled=enabled,
            interval_days=interval,
            groups=groups,
            last_run_at=_time_text(last_run) if last_run else None,
            next_run_at=_time_text(next_run) if next_run else None,
            last_error=error,
            last_warning=warning,
        )

    def reconfigured(
        self,
        *,
        enabled: bool,
        interval_days: int,
        groups: list[str],
        now: datetime | None = None,
    ) -> ScheduleState:
        if (
            not isinstance(interval_days, int)
            or isinstance(interval_days, bool)
            or not MIN_INTERVAL_DAYS <= interval_days <= MAX_INTERVAL_DAYS
        ):
            raise ValueError("interval_days must be between 1 and 365")
        normalized = tuple(dict.fromkeys(groups))
        if not normalized or any(group not in VALID_GROUPS for group in normalized):
            raise ValueError("groups must contain core or assets")
        next_run = _as_utc(now) + timedelta(days=interval_days) if enabled else None
        return ScheduleState(
            enabled=enabled,
            interval_days=interval_days,
            groups=normalized,
            last_run_at=self.last_run_at,
            next_run_at=_time_text(next_run) if next_run else None,
        )

    def is_due(self, *, now: datetime | None = None) -> bool:
        next_run = _parse_time(self.next_run_at)
        return self.enabled and next_run is not None and _as_utc(now) >= next_run

    def succeeded(
        self, *, now: datetime | None = None, warning: str | None = None
    ) -> ScheduleState:
        completed = _as_utc(now)
        return ScheduleState(
            enabled=self.enabled,
            interval_days=self.interval_days,
            groups=self.groups,
            last_run_at=_time_text(completed),
            next_run_at=_time_text(completed + timedelta(days=self.interval_days)),
            last_warning=warning[:500] if warning else None,
        )

    def failed(self, error: str, *, now: datetime | None = None) -> ScheduleState:
        failed_at = _as_utc(now)
        return ScheduleState(
            enabled=self.enabled,
            interval_days=self.interval_days,
            groups=self.groups,
            last_run_at=self.last_run_at,
            next_run_at=_time_text(failed_at + timedelta(days=1)),
            last_error=error[:500],
        )

    def to_dict(self, *, running: bool | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": self.enabled,
            "interval_days": self.interval_days,
            "groups": list(self.groups),
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "last_error": self.last_error,
            "last_warning": self.last_warning,
        }
        if running is not None:
            result["running"] = running
        return result
