"""Bounded, session-local facts for recent live-room danmaku."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import math
import re
from typing import Callable

from .recent_chat_relevance import build_relevance_scorer


_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SENSITIVE_EVENT_ID_MARKERS = (
    "authorization",
    "cookie",
    "odin_tt",
    "sessionid",
    "signature",
    "token",
    "ttwid",
    "webcast_sign",
)


@dataclass(slots=True)
class RecentChatRecord:
    seq: int
    uid: str
    nickname: str
    text: str
    observed_at: float
    selected: bool = False
    ambient_used: bool = False


class RecentChatBuffer:
    """Keep exact public danmaku facts without adding them to every prompt."""

    def __init__(
        self,
        *,
        now: Callable[[], float],
        max_entries: int = 12,
        window_seconds: float = 30.0,
        retention_seconds: float = 120.0,
        session_tail_entries: int = 3,
        dedupe_entries: int = 64,
    ) -> None:
        self._now = now
        self._window_seconds = max(1.0, float(window_seconds))
        self._retention_seconds = max(
            self._window_seconds, float(retention_seconds)
        )
        capacity = max(1, int(max_entries))
        self._records: deque[RecentChatRecord] = deque(maxlen=capacity)
        self._session_tail: deque[RecentChatRecord] = deque(
            maxlen=min(capacity, max(1, int(session_tail_entries)))
        )
        self._seen_provider_event_ids: OrderedDict[str, None] = OrderedDict()
        self._dedupe_entries = max(capacity, int(dedupe_entries))
        self._next_seq = 0
        self._last_now = 0.0

    def reset(self) -> None:
        self._records.clear()
        self._session_tail.clear()
        self._seen_provider_event_ids.clear()
        self._next_seq = 0
        self._last_now = 0.0

    def remember(
        self,
        *,
        uid: str,
        nickname: str,
        text: str,
        observed_at: float | None = None,
        provider_event_id: str = "",
    ) -> int:
        clean_text = _clean_text(text, max_length=512)
        if not clean_text:
            return 0
        clean_event_id = _clean_event_id(provider_event_id)
        if clean_event_id and clean_event_id in self._seen_provider_event_ids:
            return 0
        now = self._clock_now()
        timestamp = _finite_float(observed_at, default=now)
        if timestamp < 0.0 or timestamp > now:
            timestamp = now
        self._prune(now)
        self._next_seq += 1
        record = RecentChatRecord(
            seq=self._next_seq,
            uid=_clean_text(uid, max_length=128),
            nickname=_clean_text(nickname, max_length=64),
            text=clean_text,
            observed_at=timestamp,
        )
        self._records.append(record)
        self._session_tail.append(record)
        if clean_event_id:
            self._seen_provider_event_ids[clean_event_id] = None
            self._seen_provider_event_ids.move_to_end(clean_event_id)
            while len(self._seen_provider_event_ids) > self._dedupe_entries:
                self._seen_provider_event_ids.popitem(last=False)
        return self._next_seq

    def mark_selected(self, seq: int) -> bool:
        if seq <= 0:
            return False
        for record in reversed(self._all_records()):
            if record.seq == seq:
                record.selected = True
                return True
        return False

    def mark_ambient_used(self, seq: int) -> bool:
        if seq <= 0:
            return False
        target: RecentChatRecord | None = None
        for record in reversed(self._all_records()):
            if record.seq == seq:
                target = record
                break
        if target is None:
            return False
        key = (target.uid.casefold(), target.text.casefold())
        changed = False
        for record in self._all_records():
            if (record.uid.casefold(), record.text.casefold()) != key:
                continue
            if not record.ambient_used:
                record.ambient_used = True
                changed = True
        return changed

    def snapshot(
        self,
        *,
        limit: int = 1,
        max_age_seconds: float | None = None,
        selected: bool | None = None,
    ) -> list[dict[str, object]]:
        now = self._clock_now()
        self._prune(now)
        cap = _clean_limit(limit)
        max_age = self._clean_max_age(max_age_seconds)
        records = [
            record
            for record in self._records
            if now - record.observed_at <= max_age
            and (selected is None or record.selected is selected)
        ][-cap:]
        return [
            {
                "seq": record.seq,
                "uid": record.uid,
                "nickname": record.nickname,
                "text": record.text,
                "seconds_ago": round(max(0.0, now - record.observed_at), 1),
                "selected": record.selected,
            }
            for record in reversed(records)
        ]

    def ambient_snapshot(
        self,
        *,
        limit: int = 3,
        max_age_seconds: float | None = None,
    ) -> list[dict[str, object]]:
        now = self._clock_now()
        self._prune(now)
        records = self._ambient_records(
            now=now,
            max_age=self._clean_max_age(max_age_seconds),
        )[: _clean_limit(limit)]
        return [self._project(record, now=now) for record in records]

    def session_tail_snapshot(self, *, limit: int = 1) -> list[dict[str, object]]:
        """Return the newest session facts without turning old rows into ambient context."""

        now = self._clock_now()
        self._prune(now)
        cap = min(_clean_limit(limit), int(self._session_tail.maxlen or 0))
        records = list(self._session_tail)[-cap:]
        return [
            {
                **self._project(record, now=now),
                "within_fresh_window": (
                    now - record.observed_at <= self._window_seconds
                ),
            }
            for record in reversed(records)
        ]

    def relevant_snapshot(
        self,
        *,
        query: object,
        limit: int = 1,
        max_age_seconds: float | None = None,
    ) -> list[dict[str, object]]:
        now = self._clock_now()
        self._prune(now)
        scorer = build_relevance_scorer(query)
        if scorer is None:
            return []
        scored = [
            (scorer(record.text), record.observed_at, record)
            for record in self._ambient_records(
                now=now,
                max_age=self._clean_max_age(max_age_seconds),
            )
        ]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [
            self._project(record, now=now)
            for _, _, record in scored[: _clean_limit(limit)]
        ]

    def count(
        self,
        *,
        max_age_seconds: float | None = None,
        selected: bool | None = None,
    ) -> int:
        now = self._clock_now()
        self._prune(now)
        max_age = self._clean_max_age(max_age_seconds)
        return sum(
            1
            for record in self._records
            if now - record.observed_at <= max_age
            and (selected is None or record.selected is selected)
        )

    def status(self) -> dict[str, int | float]:
        self._prune(self._clock_now())
        return {
            "recent_chat_count": self.count(),
            "recent_chat_capacity": int(self._records.maxlen or 0),
            "recent_chat_window_seconds": self._window_seconds,
            "recent_chat_retained_count": len(self._records),
            "recent_chat_retention_seconds": self._retention_seconds,
            "recent_chat_session_tail_count": len(self._session_tail),
            "recent_chat_session_tail_capacity": int(
                self._session_tail.maxlen or 0
            ),
            "recent_chat_delivery_id_count": len(
                self._seen_provider_event_ids
            ),
            "recent_chat_unselected_count": self.count(
                max_age_seconds=self._retention_seconds,
                selected=False,
            ),
        }

    def _prune(self, now: float) -> None:
        cutoff = now - self._retention_seconds
        while self._records and self._records[0].observed_at < cutoff:
            self._records.popleft()

    def _ambient_records(
        self,
        *,
        now: float,
        max_age: float,
    ) -> list[RecentChatRecord]:
        records: list[RecentChatRecord] = []
        seen: set[tuple[str, str]] = set()
        blocked = {
            (record.uid.casefold(), record.text.casefold())
            for record in self._records
            if now - record.observed_at <= max_age
            and (record.selected or record.ambient_used)
        }
        for record in reversed(self._records):
            if now - record.observed_at > max_age:
                continue
            key = (record.uid.casefold(), record.text.casefold())
            if key in blocked or key in seen:
                continue
            seen.add(key)
            records.append(record)
        return records

    def _all_records(self) -> list[RecentChatRecord]:
        records: dict[int, RecentChatRecord] = {
            record.seq: record for record in self._records
        }
        records.update({record.seq: record for record in self._session_tail})
        return list(records.values())

    def _clean_max_age(self, value: float | None) -> float:
        if value is None:
            return self._window_seconds
        return min(
            self._retention_seconds,
            max(1.0, _finite_float(value, default=self._window_seconds)),
        )

    def _clock_now(self) -> float:
        try:
            value = _finite_float(self._now(), default=self._last_now)
        except Exception:
            value = self._last_now
        if value < self._last_now:
            value = self._last_now
        self._last_now = value
        return value

    @staticmethod
    def _project(record: RecentChatRecord, *, now: float) -> dict[str, object]:
        return {
            "seq": record.seq,
            "uid": record.uid,
            "nickname": record.nickname,
            "text": record.text,
            "seconds_ago": round(max(0.0, now - record.observed_at), 1),
            "selected": record.selected,
        }


def _clean_text(value: object, *, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[: max(0, int(max_length))]


def _clean_event_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    token = value.strip()
    lowered = token.casefold()
    if not _EVENT_ID_RE.fullmatch(token) or any(
        marker in lowered for marker in _SENSITIVE_EVENT_ID_MARKERS
    ):
        return ""
    return token


def _finite_float(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _clean_limit(value: object) -> int:
    if isinstance(value, bool):
        return 1
    try:
        return min(5, max(1, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 1
