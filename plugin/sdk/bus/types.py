from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, Generic, Iterator, List, Optional, Sequence, TypeVar


TRecord = TypeVar("TRecord", bound="BusRecord")


@dataclass(frozen=True)
class BusFilter:
    kind: Optional[str] = None
    type: Optional[str] = None
    plugin_id: Optional[str] = None
    source: Optional[str] = None
    kind_re: Optional[str] = None
    type_re: Optional[str] = None
    plugin_id_re: Optional[str] = None
    source_re: Optional[str] = None
    content_re: Optional[str] = None
    priority_min: Optional[int] = None
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None


@dataclass(frozen=True)
class BusRecord:
    kind: str
    type: str
    timestamp: Optional[float]
    plugin_id: Optional[str] = None
    source: Optional[str] = None
    priority: int = 0
    content: Optional[str] = None
    metadata: Dict[str, Any] = None  # type: ignore[assignment]
    raw: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", {} if self.metadata is None else dict(self.metadata))
        object.__setattr__(self, "raw", {} if self.raw is None else dict(self.raw))

    def dump(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "type": self.type,
            "timestamp": self.timestamp,
            "plugin_id": self.plugin_id,
            "source": self.source,
            "priority": self.priority,
            "content": self.content,
            "metadata": dict(self.metadata or {}),
            "raw": dict(self.raw or {}),
        }


class BusList(Generic[TRecord]):
    def __init__(self, items: Sequence[TRecord]):
        self._items: List[TRecord] = list(items)

    def __iter__(self) -> Iterator[TRecord]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def count(self) -> int:
        return len(self._items)

    def size(self) -> int:
        return len(self._items)

    def __getitem__(self, idx: int) -> TRecord:
        return self._items[idx]

    def dump(self) -> List[Dict[str, Any]]:
        return [x.dump() for x in self._items]

    def dump_records(self) -> List[TRecord]:
        return list(self._items)

    def filter(self, flt: Optional[BusFilter] = None, **kwargs: Any) -> "BusList[TRecord]":
        if flt is None:
            flt = BusFilter(**kwargs)

        def _re_ok(pattern: Optional[str], value: Optional[str]) -> bool:
            if pattern is None:
                return True
            if value is None:
                return False
            try:
                return re.search(pattern, value) is not None
            except re.error:
                return False

        def _match(x: BusRecord) -> bool:
            if flt.kind is not None and x.kind != flt.kind:
                return False
            if flt.type is not None and x.type != flt.type:
                return False
            if flt.plugin_id is not None and x.plugin_id != flt.plugin_id:
                return False
            if flt.source is not None and x.source != flt.source:
                return False
            if not _re_ok(flt.kind_re, x.kind):
                return False
            if not _re_ok(flt.type_re, x.type):
                return False
            if not _re_ok(flt.plugin_id_re, x.plugin_id):
                return False
            if not _re_ok(flt.source_re, x.source):
                return False
            if not _re_ok(flt.content_re, x.content):
                return False
            if flt.priority_min is not None and int(x.priority) < int(flt.priority_min):
                return False
            if flt.since_ts is not None:
                ts = x.timestamp
                if ts is None or float(ts) < float(flt.since_ts):
                    return False
            if flt.until_ts is not None:
                ts = x.timestamp
                if ts is None or float(ts) > float(flt.until_ts):
                    return False
            return True

        return self.__class__([item for item in self._items if _match(item)])  # type: ignore[call-arg]

    def where(self, predicate: Callable[[TRecord], bool]) -> "BusList[TRecord]":
        return self.__class__([item for item in self._items if predicate(item)])  # type: ignore[call-arg]

    def limit(self, n: int) -> "BusList[TRecord]":
        nn = int(n)
        if nn <= 0:
            return self.__class__([])  # type: ignore[call-arg]
        return self.__class__(self._items[:nn])  # type: ignore[call-arg]
