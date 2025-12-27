from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, Generic, Iterator, List, Optional, Sequence, Tuple, TypeVar, Union


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


class BusFilterError(ValueError):
    pass


@dataclass(frozen=True)
class BusFilterResult(Generic[TRecord]):
    ok: bool
    value: Optional["BusList[TRecord]"] = None
    error: Optional[Exception] = None


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

    def _dedupe_key(self, item: TRecord) -> Tuple[str, Any]:
        for attr in ("message_id", "event_id", "lifecycle_id", "trace_id"):
            try:
                v = getattr(item, attr, None)
            except Exception:
                v = None
            if isinstance(v, str) and v:
                return (attr, v)

        raw = None
        try:
            raw = getattr(item, "raw", None)
        except Exception:
            raw = None
        if isinstance(raw, dict):
            for k in ("message_id", "event_id", "lifecycle_id", "trace_id"):
                v = raw.get(k)
                if isinstance(v, str) and v:
                    return (k, v)

        try:
            dumped = item.dump()
            fp = tuple(sorted((str(k), repr(v)) for k, v in dumped.items()))
            return ("dump", fp)
        except Exception:
            return ("object", id(item))

    def _sort_value(self, v: Any) -> Tuple[int, Any]:
        if v is None:
            return (2, "")
        if isinstance(v, (int, float)):
            return (0, v)
        return (1, str(v))

    def _get_sort_field(self, item: TRecord, field: str) -> Any:
        try:
            return getattr(item, field)
        except Exception:
            pass

        raw = None
        try:
            raw = getattr(item, "raw", None)
        except Exception:
            raw = None
        if isinstance(raw, dict) and field in raw:
            return raw.get(field)

        try:
            dumped = item.dump()
            return dumped.get(field)
        except Exception:
            return None

    def merge(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        if type(self) is not type(other):
            raise TypeError(f"Cannot merge different bus list types: {type(self).__name__} + {type(other).__name__}")

        merged: List[TRecord] = []
        seen: set[Tuple[str, Any]] = set()
        for item in list(self._items) + list(other._items):
            key = self._dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        return self.__class__(merged)  # type: ignore[call-arg]

    def __add__(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.merge(other)

    def sort(
        self,
        *,
        by: Optional[Union[str, Sequence[str]]] = None,
        key: Optional[Callable[[TRecord], Any]] = None,
        reverse: bool = False,
    ) -> "BusList[TRecord]":
        if key is not None and by is not None:
            raise ValueError("Specify only one of 'key' or 'by'")

        if key is None:
            if by is None:
                by_fields: List[str] = ["timestamp", "created_at", "time"]
            elif isinstance(by, str):
                by_fields = [by]
            else:
                by_fields = list(by)

            def key_func(x: TRecord) -> Tuple[Tuple[int, Any], ...]:
                return tuple(self._sort_value(self._get_sort_field(x, f)) for f in by_fields)

            sort_key: Callable[[TRecord], Any] = key_func
        else:
            sort_key = key

        items = sorted(self._items, key=sort_key, reverse=reverse)
        return self.__class__(items)  # type: ignore[call-arg]

    def sorted(
        self,
        *,
        by: Optional[Union[str, Sequence[str]]] = None,
        key: Optional[Callable[[TRecord], Any]] = None,
        reverse: bool = False,
    ) -> "BusList[TRecord]":
        return self.sort(by=by, key=key, reverse=reverse)

    def intersection(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        if type(self) is not type(other):
            raise TypeError(
                f"Cannot intersect different bus list types: {type(self).__name__} & {type(other).__name__}"
            )

        other_keys = {other._dedupe_key(x) for x in other._items}
        kept: List[TRecord] = []
        seen: set[Tuple[str, Any]] = set()
        for item in self._items:
            key = self._dedupe_key(item)
            if key not in other_keys:
                continue
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)

        return self.__class__(kept)  # type: ignore[call-arg]

    def intersect(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.intersection(other)

    def __and__(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.intersection(other)

    def difference(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        if type(self) is not type(other):
            raise TypeError(
                f"Cannot diff different bus list types: {type(self).__name__} - {type(other).__name__}"
            )

        other_keys = {other._dedupe_key(x) for x in other._items}
        kept: List[TRecord] = []
        seen: set[Tuple[str, Any]] = set()
        for item in self._items:
            key = self._dedupe_key(item)
            if key in other_keys:
                continue
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)

        return self.__class__(kept)  # type: ignore[call-arg]

    def subtract(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.difference(other)

    def __sub__(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.difference(other)

    def __eq__(self, other: object) -> bool:
        if other is self:
            return True
        if not isinstance(other, BusList):
            return False
        if type(self) is not type(other):
            return False
        if len(self._items) != len(other._items):
            return False
        return [self._dedupe_key(x) for x in self._items] == [other._dedupe_key(x) for x in other._items]

    def filter(
        self,
        flt: Optional[BusFilter] = None,
        *,
        strict: bool = True,
        **kwargs: Any,
    ) -> "BusList[TRecord]":
        if flt is None:
            flt = BusFilter(**kwargs)

        def _re_ok(field: str, pattern: Optional[str], value: Optional[str]) -> bool:
            if pattern is None:
                return True
            if value is None:
                return False
            try:
                return re.search(pattern, value) is not None
            except re.error:
                if strict:
                    raise BusFilterError(f"Invalid regex for {field}: {pattern!r}")
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
            if not _re_ok("kind_re", flt.kind_re, x.kind):
                return False
            if not _re_ok("type_re", flt.type_re, x.type):
                return False
            if not _re_ok("plugin_id_re", flt.plugin_id_re, x.plugin_id):
                return False
            if not _re_ok("source_re", flt.source_re, x.source):
                return False
            if not _re_ok("content_re", flt.content_re, x.content):
                return False
            if flt.priority_min is not None:
                try:
                    if int(x.priority) < int(flt.priority_min):
                        return False
                except Exception as e:
                    if strict:
                        raise BusFilterError(f"Invalid priority_min: {flt.priority_min!r}") from e
                    return False
            if flt.since_ts is not None:
                ts = x.timestamp
                try:
                    if ts is None or float(ts) < float(flt.since_ts):
                        return False
                except Exception as e:
                    if strict:
                        raise BusFilterError(f"Invalid since_ts: {flt.since_ts!r}") from e
                    return False
            if flt.until_ts is not None:
                ts = x.timestamp
                try:
                    if ts is None or float(ts) > float(flt.until_ts):
                        return False
                except Exception as e:
                    if strict:
                        raise BusFilterError(f"Invalid until_ts: {flt.until_ts!r}") from e
                    return False
            return True

        return self.__class__([item for item in self._items if _match(item)])  # type: ignore[call-arg]

    def try_filter(self, flt: Optional[BusFilter] = None, **kwargs: Any) -> BusFilterResult[TRecord]:
        try:
            value = self.filter(flt, strict=True, **kwargs)
            return BusFilterResult(ok=True, value=value, error=None)
        except BusFilterError as e:
            return BusFilterResult(ok=False, value=None, error=e)

    def where(self, predicate: Callable[[TRecord], bool]) -> "BusList[TRecord]":
        return self.__class__([item for item in self._items if predicate(item)])  # type: ignore[call-arg]

    def limit(self, n: int) -> "BusList[TRecord]":
        nn = int(n)
        if nn <= 0:
            return self.__class__([])  # type: ignore[call-arg]
        return self.__class__(self._items[:nn])  # type: ignore[call-arg]
