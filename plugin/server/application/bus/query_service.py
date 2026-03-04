from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import MESSAGE_QUEUE_DEFAULT_MAX_COUNT

logger = get_logger("server.application.bus.query")


def _normalize_mapping(raw: Mapping[object, object], *, context: str) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} contains non-string key",
                status_code=500,
                details={"key_type": type(key).__name__},
            )
        normalized[key] = value
    return normalized


def _parse_iso_ts(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        if stripped.endswith("Z"):
            dt = datetime.fromisoformat(stripped[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(stripped)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.timestamp()


def _coerce_optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _compile_pattern(*, field: str, pattern_obj: object, strict: bool) -> re.Pattern[str] | None:
    if pattern_obj is None:
        return None
    pattern_text = str(pattern_obj)
    try:
        return re.compile(pattern_text)
    except re.error as exc:
        if strict:
            raise ServerDomainError(
                code="INVALID_FILTER_REGEX",
                message=f"Invalid regex for filter field '{field}'",
                status_code=400,
                details={"field": field, "error": str(exc)},
            ) from exc
        return None


def _regex_matches(pattern: re.Pattern[str] | None, value: object) -> bool:
    if pattern is None:
        return True
    if not isinstance(value, str):
        return False
    return pattern.search(value) is not None


def _coerce_ts_filter_value(*, filter_value: object, strict: bool, field: str) -> float | None:
    parsed = _coerce_optional_float(filter_value)
    if parsed is None and filter_value is not None and strict:
        raise ServerDomainError(
            code="INVALID_FILTER_VALUE",
            message=f"Invalid numeric filter field '{field}'",
            status_code=400,
            details={"field": field, "value_type": type(filter_value).__name__},
        )
    return parsed


def _matches_record_filter(
    *,
    record: Mapping[str, object],
    normalized_filter: Mapping[str, object],
    strict: bool,
    timestamp_field: str,
) -> bool:
    if not normalized_filter:
        return True

    if normalized_filter.get("kind") is not None and record.get("kind") != normalized_filter.get("kind"):
        return False
    if normalized_filter.get("type") is not None and record.get("type") != normalized_filter.get("type"):
        return False
    if normalized_filter.get("plugin_id") is not None and record.get("plugin_id") != normalized_filter.get("plugin_id"):
        return False
    if normalized_filter.get("source") is not None and record.get("source") != normalized_filter.get("source"):
        return False

    kind_re = _compile_pattern(field="kind_re", pattern_obj=normalized_filter.get("kind_re"), strict=strict)
    type_re = _compile_pattern(field="type_re", pattern_obj=normalized_filter.get("type_re"), strict=strict)
    plugin_re = _compile_pattern(field="plugin_id_re", pattern_obj=normalized_filter.get("plugin_id_re"), strict=strict)
    source_re = _compile_pattern(field="source_re", pattern_obj=normalized_filter.get("source_re"), strict=strict)
    content_re = _compile_pattern(field="content_re", pattern_obj=normalized_filter.get("content_re"), strict=strict)

    if not _regex_matches(kind_re, record.get("kind")):
        return False
    if not _regex_matches(type_re, record.get("type")):
        return False
    if not _regex_matches(plugin_re, record.get("plugin_id")):
        return False
    if not _regex_matches(source_re, record.get("source")):
        return False
    if not _regex_matches(content_re, record.get("content")):
        return False

    filter_since = _coerce_ts_filter_value(
        filter_value=normalized_filter.get("since_ts"),
        strict=strict,
        field="since_ts",
    )
    if filter_since is not None:
        ts = _parse_iso_ts(record.get(timestamp_field))
        if ts is None or ts <= filter_since:
            return False

    filter_until = _coerce_ts_filter_value(
        filter_value=normalized_filter.get("until_ts"),
        strict=strict,
        field="until_ts",
    )
    if filter_until is not None:
        ts = _parse_iso_ts(record.get(timestamp_field))
        if ts is None or ts > filter_until:
            return False

    return True


def _coerce_target_count(raw_max_count: int | None) -> int:
    if raw_max_count is None:
        return int(MESSAGE_QUEUE_DEFAULT_MAX_COUNT)
    if raw_max_count <= 0:
        return int(MESSAGE_QUEUE_DEFAULT_MAX_COUNT)
    return int(raw_max_count)


def _compute_scan_limit(*, target_count: int, store_size: int) -> int:
    base = max(target_count * 20, 2000)
    if store_size <= 0:
        return base
    return int(min(store_size, base))


def _query_records_sync(
    *,
    plugin_id: str | None,
    max_count: int | None,
    raw_filter: Mapping[str, object] | None,
    strict: bool,
    since_ts: float | None,
    timestamp_field: str,
    store_len_getter: Callable[[], int],
    list_tail_getter: Callable[[int], list[dict[str, object]]],
    list_all_getter: Callable[[], list[dict[str, object]]],
    context: str,
) -> list[dict[str, object]]:
    target_count = _coerce_target_count(max_count)
    normalized_filter = _normalize_mapping(raw_filter, context=f"{context}.filter") if raw_filter is not None else {}

    resolved_plugin_id = plugin_id
    if resolved_plugin_id is None:
        filter_plugin_id = normalized_filter.get("plugin_id")
        if isinstance(filter_plugin_id, str) and filter_plugin_id:
            resolved_plugin_id = filter_plugin_id

    resolved_since_ts = since_ts
    if resolved_since_ts is None:
        resolved_since_ts = _coerce_ts_filter_value(
            filter_value=normalized_filter.get("since_ts"),
            strict=strict,
            field="since_ts",
        )

    store_size = 0
    try:
        store_size = int(store_len_getter())
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
        store_size = 0
    scan_limit = _compute_scan_limit(target_count=target_count, store_size=store_size)

    try:
        snapshot = list_tail_getter(scan_limit)
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
        snapshot = list_all_getter()

    picked_reversed: list[dict[str, object]] = []
    for raw_record in reversed(snapshot):
        if not isinstance(raw_record, Mapping):
            continue
        normalized_record = _normalize_mapping(raw_record, context=f"{context}.record")

        if resolved_plugin_id is not None and normalized_record.get("plugin_id") != resolved_plugin_id:
            continue
        if resolved_since_ts is not None:
            record_ts = _parse_iso_ts(normalized_record.get(timestamp_field))
            if record_ts is None or record_ts <= resolved_since_ts:
                continue
        if not _matches_record_filter(
            record=normalized_record,
            normalized_filter=normalized_filter,
            strict=strict,
            timestamp_field=timestamp_field,
        ):
            continue

        picked_reversed.append(normalized_record)
        if len(picked_reversed) >= target_count:
            break

    picked_reversed.reverse()
    return picked_reversed


class BusQueryService:
    async def get_events(
        self,
        *,
        plugin_id: str | None,
        max_count: int | None,
        filter_data: Mapping[str, object] | None,
        strict: bool,
        since_ts: float | None,
    ) -> list[dict[str, object]]:
        try:
            return await asyncio.to_thread(
                _query_records_sync,
                plugin_id=plugin_id,
                max_count=max_count,
                raw_filter=filter_data,
                strict=strict,
                since_ts=since_ts,
                timestamp_field="received_at",
                store_len_getter=state.event_store_len,
                list_tail_getter=state.list_event_records_tail,
                list_all_getter=state.list_event_records,
                context="events",
            )
        except ServerDomainError:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "get_events failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="EVENT_QUERY_FAILED",
                message="Failed to query event records",
                status_code=500,
                details={"error_type": type(exc).__name__, "plugin_id": plugin_id or ""},
            ) from exc

    async def get_lifecycle(
        self,
        *,
        plugin_id: str | None,
        max_count: int | None,
        filter_data: Mapping[str, object] | None,
        strict: bool,
        since_ts: float | None,
    ) -> list[dict[str, object]]:
        try:
            return await asyncio.to_thread(
                _query_records_sync,
                plugin_id=plugin_id,
                max_count=max_count,
                raw_filter=filter_data,
                strict=strict,
                since_ts=since_ts,
                timestamp_field="time",
                store_len_getter=state.lifecycle_store_len,
                list_tail_getter=state.list_lifecycle_records_tail,
                list_all_getter=state.list_lifecycle_records,
                context="lifecycle",
            )
        except ServerDomainError:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "get_lifecycle failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="LIFECYCLE_QUERY_FAILED",
                message="Failed to query lifecycle records",
                status_code=500,
                details={"error_type": type(exc).__name__, "plugin_id": plugin_id or ""},
            ) from exc
