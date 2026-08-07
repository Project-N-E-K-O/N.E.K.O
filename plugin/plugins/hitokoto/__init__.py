"""N.E.K.O Hitokoto plugin.

Fetches quotes from the public Hitokoto HTTPS API, exposes both plugin-manager
entries and direct conversation-LLM tools, maintains a defensive daily cache,
and can ask the active character to share the daily quote on the first chat
message of the local calendar day.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import quote as url_quote

import httpx

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    message,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

API_URL = "https://v1.hitokoto.cn/"
USER_AGENT = (
    "N.E.K.O-Hitokoto-Plugin/0.1 "
    "(+https://github.com/Project-N-E-K-O/N.E.K.O)"
)

CATEGORIES: dict[str, str] = {
    "a": "动画",
    "b": "漫画",
    "c": "游戏",
    "d": "文学",
    "e": "原创",
    "f": "来自网络",
    "g": "其他",
    "h": "影视",
    "i": "诗词",
    "j": "网易云",
    "k": "哲学",
    "l": "抖机灵",
}

_DEFAULT_SETTINGS: dict[str, Any] = {
    "timeout_seconds": 10.0,
    "default_category": "",
    "max_length": 80,
    "daily_cache": True,
    "daily_greeting": True,
}

_STORE_KEY_SETTINGS = "settings_overrides"
_STORE_KEY_DAILY = "daily_quote"
_STORE_KEY_GREETING_DATE = "greeting_attempted_date"

_USER_CONTEXT_BUCKET = "default"
_USER_CONTEXT_LIMIT = 200
_USER_CONTEXT_TIMEOUT_SECONDS = 1.0

_QUOTE_AFFECTING_SETTINGS = (
    "default_category",
    "timeout_seconds",
    "max_length",
    "daily_cache",
)


def _quote_affecting_settings_identity(
    settings: Mapping[str, Any],
) -> str:
    snapshot = {
        key: settings[key]
        for key in _QUOTE_AFFECTING_SETTINGS
    }
    return json.dumps(
        snapshot,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


_EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

RANDOM_QUOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["", *CATEGORIES.keys()],
            "description": (
                "可选类型码：a 动画、b 漫画、c 游戏、d 文学、e 原创、"
                "f 来自网络、g 其他、h 影视、i 诗词、j 网易云、"
                "k 哲学、l 抖机灵。省略时使用面板默认类型，传空字符串时全类型随机。"
            ),
        }
    },
    "additionalProperties": False,
}

SAVE_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "default_category": {
            "type": "string",
            "enum": ["", *CATEGORIES.keys()],
            "description": "默认类型码；空字符串表示全类型随机。",
        },
        "timeout_seconds": {
            "type": "number",
            "minimum": 1,
            "maximum": 30,
            "description": "API 请求超时秒数（1-30）。",
        },
        "max_length": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": "请求的一言最大长度（1-200）。",
        },
        "daily_cache": {
            "type": "boolean",
            "description": "是否启用每日一句缓存。",
        },
        "daily_greeting": {
            "type": "boolean",
            "description": "是否在每天第一条聊天消息时分享每日一句。",
        },
    },
    "required": [
        "default_category",
        "timeout_seconds",
        "max_length",
        "daily_cache",
        "daily_greeting",
    ],
    "additionalProperties": False,
}


def _local_date() -> str:
    """Return the host's current local calendar date."""

    return date.today().isoformat()


def _local_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _valid_local_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    normalized = parsed.isoformat()
    return normalized if value == normalized else None


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _source_text(author: str, work: str) -> str:
    parts: list[str] = []
    if author:
        parts.append(author)
    if work:
        parts.append(f"《{work}》")
    return " ".join(parts) if parts else "佚名"


def _format_quote(payload: Mapping[str, Any]) -> str:
    sentence = _clean_text(payload.get("sentence"))
    source = _clean_text(payload.get("source")) or "佚名"
    trace_url = _clean_text(payload.get("url"))
    lines = [sentence, f"—— {source}"]
    if trace_url:
        lines.append(trace_url)
    return "\n".join(lines)


def _parse_hitokoto_payload(payload: Any) -> dict[str, Any]:
    """Normalize an API payload without trusting optional field types."""

    if not isinstance(payload, Mapping):
        raise SdkError("一言 API 返回的数据格式无效")

    sentence = _clean_text(payload.get("hitokoto"))
    if not sentence:
        raise SdkError("一言 API 返回了空句子")

    author = _clean_text(payload.get("from_who"))
    work = _clean_text(payload.get("from"))
    type_code = _clean_text(payload.get("type")).lower()
    type_label = CATEGORIES.get(type_code, "未知")
    uuid = _clean_text(payload.get("uuid"))
    if not uuid:
        raise SdkError("一言 API 返回的数据缺少 UUID")
    trace_url = f"https://hitokoto.cn?uuid={url_quote(uuid, safe='')}"

    quote_id = payload.get("id")
    if isinstance(quote_id, bool) or not isinstance(quote_id, (int, str)):
        raise SdkError("一言 API 返回的数据缺少 ID")
    if isinstance(quote_id, str) and not quote_id.strip():
        raise SdkError("一言 API 返回的数据缺少 ID")

    normalized = {
        "sentence": sentence,
        "author": author,
        "work": work,
        "source": _source_text(author, work),
        "type_code": type_code,
        "type_label": type_label,
        # Compatibility aliases make the payload pleasant for existing
        # Hitokoto integrations while keeping the explicit type fields.
        "category_code": type_code,
        "category": type_label,
        "id": quote_id,
        "uuid": uuid,
        "url": trace_url,
    }
    normalized["formatted"] = _format_quote(normalized)
    return normalized


def _quote_from_cache(value: Any) -> dict[str, Any] | None:
    """Validate and normalize a persisted quote record."""

    if not isinstance(value, Mapping):
        return None

    required_fields = {
        "sentence",
        "source",
        "type_code",
        "type_label",
        "id",
        "uuid",
        "url",
    }
    if not required_fields.issubset(value):
        return None

    sentence = _clean_text(value.get("sentence"))
    if not sentence:
        return None

    author = _clean_text(value.get("author"))
    work = _clean_text(value.get("work"))
    source = _clean_text(value.get("source")) or _source_text(author, work)
    type_code = _clean_text(
        value.get("type_code", value.get("category_code"))
    ).lower()
    type_label = _clean_text(
        value.get("type_label", value.get("category"))
    ) or CATEGORIES.get(type_code, "未知")
    uuid = _clean_text(value.get("uuid"))
    if not uuid or not type_label:
        return None
    trace_url = f"https://hitokoto.cn?uuid={url_quote(uuid, safe='')}"

    quote_id = value.get("id")
    if isinstance(quote_id, bool) or not isinstance(quote_id, (int, str)):
        return None
    if isinstance(quote_id, str) and not quote_id.strip():
        return None

    normalized = {
        "sentence": sentence,
        "author": author,
        "work": work,
        "source": source,
        "type_code": type_code,
        "type_label": type_label,
        "category_code": type_code,
        "category": type_label,
        "id": quote_id,
        "uuid": uuid,
        "url": trace_url,
    }
    normalized["formatted"] = _format_quote(normalized)
    return normalized


def _memory_event_payload(value: Any, *, _depth: int = 0) -> dict[str, Any] | None:
    """Unwrap one public SDK memory record without retaining its content."""

    if _depth > 4:
        return None

    if isinstance(value, Mapping):
        raw = value.get("raw")
        if isinstance(raw, Mapping):
            return _memory_event_payload(raw, _depth=_depth + 1)
        if "value" in value:
            nested = _memory_event_payload(
                value.get("value"),
                _depth=_depth + 1,
            )
            if nested is not None:
                return nested
        return {str(key): item for key, item in value.items()}

    for attribute in ("payload", "raw"):
        nested_value = getattr(value, attribute, None)
        if nested_value is not None:
            nested = _memory_event_payload(
                nested_value,
                _depth=_depth + 1,
            )
            if nested is not None:
                return nested
    return None


def _memory_event_timestamp(value: Mapping[str, Any]) -> float | None:
    timestamp = value.get("_ts")
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(float(timestamp))
    ):
        return None
    return float(timestamp)


def _memory_event_identity(value: Mapping[str, Any]) -> str:
    """Return a bounded-cursor identity without keeping verbatim user text."""

    digest = hashlib.sha256()
    for key in (
        "_ts",
        "type",
        "content",
        "source",
        "lanlan",
        "is_voice",
    ):
        item = value.get(key)
        if not isinstance(item, (str, int, float, bool, type(None))):
            item = type(item).__name__
        digest.update(type(item).__name__.encode("utf-8"))
        digest.update(b":")
        digest.update(repr(item).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def _is_user_message_event(value: Mapping[str, Any]) -> bool:
    content = value.get("content")
    return (
        value.get("type") == "user_message"
        and isinstance(content, str)
        and bool(content.strip())
        and value.get("source") == "main_logic.core"
        and isinstance(value.get("is_voice"), bool)
        and _memory_event_timestamp(value) is not None
    )


def _snapshot_overlap(
    previous: tuple[str, ...],
    current: tuple[str, ...],
) -> int | None:
    """Find the largest previous suffix reused as the current prefix."""

    for size in range(min(len(previous), len(current)), 0, -1):
        if previous[-size:] == current[:size]:
            return size
    if not previous or not current:
        return 0
    return None


async def _fetch_hitokoto(
    client: httpx.AsyncClient,
    category: str = "",
    timeout: float = 10.0,
    max_length: int = 80,
) -> dict[str, Any]:
    """Fetch and normalize exactly one Hitokoto quote."""

    if category and category not in CATEGORIES:
        raise SdkError(f"未知的一言类型码：{category}")

    params: dict[str, Any] = {
        "encode": "json",
        "charset": "utf-8",
        "max_length": max_length,
    }
    if category:
        params["c"] = category

    response = await client.get(API_URL, params=params, timeout=timeout)
    response.raise_for_status()
    return _parse_hitokoto_payload(response.json())


def _new_http_client() -> httpx.AsyncClient:
    """Create the real client configuration used by every API request."""

    return httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def _normalize_manifest_settings(raw: Any) -> dict[str, Any]:
    """Validate immutable TOML defaults, falling back field by field."""

    settings = dict(_DEFAULT_SETTINGS)
    if not isinstance(raw, Mapping):
        return settings

    category = raw.get("default_category")
    if isinstance(category, str):
        category = category.strip().lower()
        if category in {"", *CATEGORIES.keys()}:
            settings["default_category"] = category

    timeout = raw.get("timeout_seconds")
    if (
        isinstance(timeout, (int, float))
        and not isinstance(timeout, bool)
        and math.isfinite(float(timeout))
    ):
        settings["timeout_seconds"] = max(1.0, min(float(timeout), 30.0))

    max_length = raw.get("max_length")
    if isinstance(max_length, int) and not isinstance(max_length, bool):
        settings["max_length"] = max(1, min(max_length, 200))

    for key in ("daily_cache", "daily_greeting"):
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]

    return settings


def _validate_settings_overlay(
    raw: Any,
    *,
    require_all: bool,
) -> dict[str, Any]:
    """Validate mutable settings and clamp supported numeric values."""

    if not isinstance(raw, Mapping):
        raise SdkError("设置必须是对象")

    required = set(_DEFAULT_SETTINGS)
    if require_all:
        missing = sorted(required.difference(raw))
        if missing:
            raise SdkError(f"缺少设置字段：{', '.join(missing)}")

    result: dict[str, Any] = {}
    if "default_category" in raw:
        category = raw["default_category"]
        if not isinstance(category, str):
            raise SdkError("默认类型必须是字符串")
        category = category.strip().lower()
        if category not in {"", *CATEGORIES.keys()}:
            raise SdkError(
                "默认类型无效；请选择空值或 a、b、c、d、e、f、g、h、i、j、k、l"
            )
        result["default_category"] = category

    if "timeout_seconds" in raw:
        timeout = raw["timeout_seconds"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
        ):
            raise SdkError("超时秒数必须是有效数字")
        result["timeout_seconds"] = max(1.0, min(float(timeout), 30.0))

    if "max_length" in raw:
        max_length = raw["max_length"]
        if isinstance(max_length, bool) or not isinstance(max_length, int):
            raise SdkError("最大句长必须是整数")
        result["max_length"] = max(1, min(max_length, 200))

    for key, label in (
        ("daily_cache", "每日缓存"),
        ("daily_greeting", "每日首次聊天问候"),
    ):
        if key in raw:
            value = raw[key]
            if not isinstance(value, bool):
                raise SdkError(f"{label}开关必须是布尔值")
            result[key] = value

    unknown = sorted(str(key) for key in raw if key not in required)
    if unknown:
        raise SdkError(f"未知设置字段：{', '.join(unknown)}")
    return result


class _DailyFlightAborted(Exception):
    """Internal signal that lets unaffected waiters elect a new leader."""


@dataclass
class _DailyFlight:
    """Cross-event-loop single-flight state.

    ``threading.Event`` is deliberately used instead of an asyncio primitive:
    the host can invoke startup, commands, and shutdown through different
    ``asyncio.run()`` loops.
    """

    local_date: str
    generation: int
    cache_enabled: bool
    done: threading.Event = field(default_factory=threading.Event)
    quote: dict[str, Any] | None = None
    error: BaseException | None = None


@neko_plugin
class HitokotoPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._state_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._cache_store_lock = threading.Lock()
        self._greeting_store_lock = threading.Lock()
        self._user_context_poll_lock = threading.Lock()
        self._manifest_settings = dict(_DEFAULT_SETTINGS)
        self._settings_overrides: dict[str, Any] = {}
        self._runtime_started = False
        self._api_state = "idle"
        self._last_request: dict[str, Any] = {
            "status": "not_requested",
            "time": None,
            "action": None,
            "failure_class": None,
        }
        self._recent_quote: dict[str, Any] | None = None
        self._memory_daily: dict[str, Any] | None = None
        self._ignore_persisted_daily = False
        self._greeting_attempted_date: str | None = None
        self._greeting_attempted_dates: set[str] = set()
        self._daily_flights: dict[str, _DailyFlight] = {}
        self._cache_generation = 0
        self._user_context_baselined = False
        self._user_context_snapshot: tuple[str, ...] = ()
        self._user_context_latest_timestamp: float | None = None
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._retired_clients: list[httpx.AsyncClient] = []

    # ------------------------------------------------------------------
    # Lifecycle and loop-local HTTP client
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_: Any):
        try:
            config = await self.config.dump(timeout=5.0)
        except Exception as exc:
            self.logger.warning(
                "Hitokoto config load failed: failure_class={}",
                type(exc).__name__,
            )
            config = {}

        raw_section = (
            config.get("hitokoto")
            if isinstance(config, Mapping)
            else None
        )
        manifest_settings = _normalize_manifest_settings(raw_section)
        plugin_section = (
            config.get("plugin")
            if isinstance(config, Mapping)
            else None
        )
        store_section = (
            plugin_section.get("store")
            if isinstance(plugin_section, Mapping)
            else None
        )
        if (
            isinstance(store_section, Mapping)
            and store_section.get("enabled") is True
            and not bool(getattr(self.store, "enabled", False))
        ):
            self.store.enabled = True
            self.logger.info(
                "Hitokoto store enabled from effective plugin config"
            )

        overrides: dict[str, Any] = {}
        stored_overrides = await self._store_get(_STORE_KEY_SETTINGS, None)
        if stored_overrides is not None:
            try:
                overrides = _validate_settings_overlay(
                    stored_overrides,
                    require_all=False,
                )
            except SdkError:
                self.logger.warning(
                    "Hitokoto settings override ignored: failure_class=ValidationError"
                )

        with self._state_lock:
            self._manifest_settings = manifest_settings
            self._settings_overrides = overrides
            self._runtime_started = True

        ui_registered = self.register_static_ui(
            "static",
            cache_control="no-cache",
        )
        settings = self._settings_snapshot()
        self.logger.info(
            "Hitokoto started: store_enabled={} daily_cache={} daily_greeting={} ui_registered={}",
            bool(getattr(self.store, "enabled", False)),
            settings["daily_cache"],
            settings["daily_greeting"],
            ui_registered,
        )
        await self._poll_user_context_once(force_baseline=True)
        return Ok(
            {
                "status": "running",
                "store_enabled": bool(getattr(self.store, "enabled", False)),
                "ui_registered": ui_registered,
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any):
        with self._client_lock:
            active = self._client
            retired = list(self._retired_clients)
            self._client = None
            self._client_loop = None
            self._retired_clients = []

        clients: list[httpx.AsyncClient] = []
        if active is not None:
            clients.append(active)
        clients.extend(retired)
        close_failures = 0
        for client in clients:
            if getattr(client, "is_closed", False):
                continue
            try:
                await client.aclose()
            except Exception:
                # A pool created on a previous, already-closed event loop can
                # reject cross-loop cleanup. The process is shutting down, so
                # cleanup is best effort and only the failure class is logged.
                close_failures += 1

        with self._state_lock:
            self._runtime_started = False
        if close_failures:
            self.logger.warning(
                "Hitokoto client cleanup incomplete: failure_count={}",
                close_failures,
            )
        self.logger.info("Hitokoto shutdown")
        return Ok(
            {
                "status": "shutdown",
                "clients_seen": len(clients),
                "close_failures": close_failures,
            }
        )

    def _get_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        with self._client_lock:
            current = self._client
            if (
                current is None
                or getattr(current, "is_closed", False)
                or self._client_loop is not loop
            ):
                if current is not None and not getattr(current, "is_closed", False):
                    self._retired_clients.append(current)
                current = _new_http_client()
                self._client = current
                self._client_loop = loop
            return current

    # ------------------------------------------------------------------
    # Defensive state and persistence helpers
    # ------------------------------------------------------------------

    def _settings_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            settings = dict(self._manifest_settings)
            settings.update(self._settings_overrides)
            return settings

    @staticmethod
    async def _acquire_without_blocking_loop(lock: threading.Lock) -> None:
        """Acquire a cross-loop lock without ever blocking an event loop."""

        while not lock.acquire(blocking=False):
            await asyncio.sleep(0.01)

    async def _store_get(self, key: str, default: Any = None) -> Any:
        if not bool(getattr(self.store, "enabled", False)):
            return default
        try:
            result = await self.store.get(key, default)
        except Exception as exc:
            self.logger.warning(
                "Hitokoto store read failed: key={} failure_class={}",
                key,
                type(exc).__name__,
            )
            return default
        if isinstance(result, Err):
            self.logger.warning(
                "Hitokoto store read failed: key={} failure_class=StoreError",
                key,
            )
            return default
        if isinstance(result, Ok):
            return result.value
        self.logger.warning(
            "Hitokoto store read failed: key={} failure_class=InvalidResult",
            key,
        )
        return default

    async def _store_set(self, key: str, value: Any) -> bool:
        if not bool(getattr(self.store, "enabled", False)):
            return False
        try:
            result = await self.store.set(key, value)
        except Exception as exc:
            self.logger.warning(
                "Hitokoto store write failed: key={} failure_class={}",
                key,
                type(exc).__name__,
            )
            return False
        if isinstance(result, Err):
            self.logger.warning(
                "Hitokoto store write failed: key={} failure_class=StoreError",
                key,
            )
            return False
        if not isinstance(result, Ok):
            self.logger.warning(
                "Hitokoto store write failed: key={} failure_class=InvalidResult",
                key,
            )
            return False
        return True

    async def _store_delete(self, key: str) -> bool:
        if not bool(getattr(self.store, "enabled", False)):
            return False
        try:
            result = await self.store.delete(key)
        except Exception as exc:
            self.logger.warning(
                "Hitokoto store delete failed: key={} failure_class={}",
                key,
                type(exc).__name__,
            )
            return False
        if isinstance(result, Err):
            self.logger.warning(
                "Hitokoto store delete failed: key={} failure_class=StoreError",
                key,
            )
            return False
        if not isinstance(result, Ok):
            self.logger.warning(
                "Hitokoto store delete failed: key={} failure_class=InvalidResult",
                key,
            )
            return False
        return True

    async def _read_user_context_snapshot(
        self,
    ) -> list[dict[str, Any]]:
        bus = self.bus
        memory = getattr(bus, "memory", None)
        getter = getattr(memory, "get", None)
        if not callable(getter):
            raise RuntimeError("user-context memory bus is unavailable")

        result = await asyncio.to_thread(
            getter,
            bucket_id=_USER_CONTEXT_BUCKET,
            limit=_USER_CONTEXT_LIMIT,
            timeout=_USER_CONTEXT_TIMEOUT_SECONDS,
        )
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Err):
            raise SdkError("user-context memory read failed")
        if isinstance(result, Ok):
            result = result.value

        if isinstance(result, Mapping):
            history = result.get("history")
            records: Any = history if isinstance(history, list) else []
        else:
            records = result
        if (
            not isinstance(records, Iterable)
            or isinstance(records, (str, bytes, bytearray))
        ):
            raise SdkError("user-context memory returned invalid records")

        payloads: list[dict[str, Any]] = []
        for record in records:
            payload = _memory_event_payload(record)
            if payload is not None:
                payloads.append(payload)
        return payloads

    async def _poll_user_context_once(
        self,
        *,
        force_baseline: bool = False,
    ):
        await self._acquire_without_blocking_loop(
            self._user_context_poll_lock
        )
        should_greet = False
        try:
            if force_baseline:
                self._user_context_baselined = False
                self._user_context_snapshot = ()
                self._user_context_latest_timestamp = None

            try:
                payloads = await self._read_user_context_snapshot()
            except Exception as exc:
                self.logger.warning(
                    "Hitokoto user-context poll failed: failure_class={}",
                    type(exc).__name__,
                )
                return Ok(
                    {
                        "status": "read_failed",
                        "observed": False,
                        "failure_class": type(exc).__name__,
                    }
                )

            identities = tuple(
                _memory_event_identity(payload)
                for payload in payloads
            )
            timestamps = [
                timestamp
                for payload in payloads
                if (
                    timestamp := _memory_event_timestamp(payload)
                ) is not None
            ]

            if not self._user_context_baselined:
                self._user_context_baselined = True
                self._user_context_snapshot = identities
                if timestamps:
                    self._user_context_latest_timestamp = max(timestamps)
                return Ok(
                    {
                        "status": "baseline",
                        "observed": False,
                    }
                )

            previous = self._user_context_snapshot
            overlap = _snapshot_overlap(previous, identities)
            if overlap is None:
                cursor = self._user_context_latest_timestamp
                candidates = [
                    payload
                    for payload in payloads
                    if (
                        (timestamp := _memory_event_timestamp(payload))
                        is not None
                        and cursor is not None
                        and timestamp > cursor
                    )
                ]
            else:
                candidates = payloads[overlap:]

            known_identities = set(previous)
            should_greet = any(
                _is_user_message_event(payload)
                and _memory_event_identity(payload)
                not in known_identities
                for payload in candidates
            )
            self._user_context_snapshot = identities
            if timestamps:
                latest = max(timestamps)
                previous_latest = self._user_context_latest_timestamp
                self._user_context_latest_timestamp = (
                    latest
                    if previous_latest is None
                    else max(previous_latest, latest)
                )
        finally:
            self._user_context_poll_lock.release()

        if not should_greet:
            return Ok({"status": "unchanged", "observed": False})
        return await self.on_chat_message()

    def _record_request(
        self,
        *,
        action: str,
        status: str,
        quote: Mapping[str, Any] | None = None,
        cache_hit: bool = False,
        failure_class: str | None = None,
        api_checked: bool = False,
        api_succeeded: bool | None = None,
    ) -> None:
        metadata: dict[str, Any] = {
            "status": status,
            "time": _local_timestamp(),
            "action": action,
            "cache_hit": cache_hit,
            "failure_class": failure_class,
        }
        if quote is not None:
            metadata.update(
                {
                    "id": quote.get("id"),
                    "uuid": quote.get("uuid"),
                    "type_code": quote.get("type_code"),
                    "length": len(_clean_text(quote.get("sentence"))),
                }
            )
        with self._state_lock:
            if api_succeeded is not None:
                self._api_state = "ok" if api_succeeded else "error"
            elif api_checked:
                self._api_state = "ok" if failure_class is None else "error"
            self._last_request = metadata
            if quote is not None:
                self._recent_quote = dict(quote)

    @staticmethod
    def _friendly_fetch_error(prefix: str, exc: BaseException) -> SdkError:
        return SdkError(f"{prefix}（{type(exc).__name__}），请稍后再试")

    def _resolve_category(self, category: Any) -> str:
        if category is None:
            return str(self._settings_snapshot()["default_category"])
        if not isinstance(category, str):
            raise SdkError("一言类型码必须是字符串")
        if category not in {"", *CATEGORIES.keys()}:
            raise SdkError(
                "未知一言类型码；可选空值或 "
                + "、".join(CATEGORIES.keys())
            )
        return category

    async def _fetch_remote(self, category: str) -> dict[str, Any]:
        settings = self._settings_snapshot()
        return await _fetch_hitokoto(
            self._get_client(),
            category=category,
            timeout=float(settings["timeout_seconds"]),
            max_length=int(settings["max_length"]),
        )

    # ------------------------------------------------------------------
    # Daily cache and cross-loop single-flight
    # ------------------------------------------------------------------

    async def _read_daily_cache(
        self,
        local_date: str,
    ) -> dict[str, Any] | None:
        with self._state_lock:
            generation = self._cache_generation
            settings = self._settings_snapshot_unlocked()
            settings_identity = _quote_affecting_settings_identity(settings)
            memory = self._memory_daily
            if (
                isinstance(memory, Mapping)
                and memory.get("date") == local_date
                and memory.get("generation") == generation
            ):
                cached_quote = _quote_from_cache(memory.get("quote"))
                if cached_quote is not None:
                    return cached_quote
            ignore_persisted = self._ignore_persisted_daily

        if ignore_persisted:
            return None

        stored = await self._store_get(_STORE_KEY_DAILY, None)
        if not isinstance(stored, Mapping) or stored.get("date") != local_date:
            return None
        stored_identity = stored.get("settings_identity")
        if stored_identity != settings_identity:
            return None
        cached_quote = _quote_from_cache(stored.get("quote"))
        if cached_quote is None:
            return None
        with self._state_lock:
            settings = self._settings_snapshot_unlocked()
            if (
                generation != self._cache_generation
                or self._ignore_persisted_daily
                or not bool(settings["daily_cache"])
                or stored_identity
                != _quote_affecting_settings_identity(settings)
            ):
                return None
            memory_date = (
                self._memory_daily.get("date")
                if isinstance(self._memory_daily, Mapping)
                else None
            )
            if (
                not isinstance(memory_date, str)
                or memory_date <= local_date
            ):
                self._memory_daily = {
                    "date": local_date,
                    "quote": dict(cached_quote),
                    "generation": generation,
                }
        return cached_quote

    async def _commit_daily_cache(
        self,
        *,
        local_date: str,
        quote: Mapping[str, Any],
        generation: int,
    ) -> None:
        await self._acquire_without_blocking_loop(self._cache_store_lock)
        try:
            with self._state_lock:
                settings = self._settings_snapshot_unlocked()
                if (
                    generation != self._cache_generation
                    or not bool(settings["daily_cache"])
                ):
                    return
                settings_identity = _quote_affecting_settings_identity(
                    settings
                )
                memory_date = (
                    self._memory_daily.get("date")
                    if isinstance(self._memory_daily, Mapping)
                    else None
                )
                if (
                    isinstance(memory_date, str)
                    and memory_date > local_date
                ):
                    return

            record = {
                "date": local_date,
                "quote": dict(quote),
                "generation": generation,
                "settings_identity": settings_identity,
            }
            persisted = await self._store_set(_STORE_KEY_DAILY, record)

            with self._state_lock:
                settings = self._settings_snapshot_unlocked()
                still_current = (
                    generation == self._cache_generation
                    and bool(settings["daily_cache"])
                    and settings_identity
                    == _quote_affecting_settings_identity(settings)
                )
                memory_date = (
                    self._memory_daily.get("date")
                    if isinstance(self._memory_daily, Mapping)
                    else None
                )
                memory_not_newer = (
                    not isinstance(memory_date, str)
                    or memory_date <= local_date
                )
                if still_current and memory_not_newer:
                    self._memory_daily = {
                        "date": local_date,
                        "quote": dict(quote),
                        "generation": generation,
                    }
                    if persisted:
                        self._ignore_persisted_daily = False

            if persisted and not still_current:
                current = await self._store_get(_STORE_KEY_DAILY, None)
                if (
                    isinstance(current, Mapping)
                    and current.get("generation") == generation
                    and current.get("date") == local_date
                    and current.get("settings_identity")
                    == settings_identity
                ):
                    await self._store_delete(_STORE_KEY_DAILY)
        finally:
            self._cache_store_lock.release()

    def _settings_snapshot_unlocked(self) -> dict[str, Any]:
        settings = dict(self._manifest_settings)
        settings.update(self._settings_overrides)
        return settings

    async def _wait_for_flight(self, flight: _DailyFlight) -> dict[str, Any]:
        while not flight.done.is_set():
            await asyncio.sleep(0.01)
        with self._state_lock:
            if flight.generation != self._cache_generation:
                raise _DailyFlightAborted()
            flight_error = flight.error
            flight_quote = (
                dict(flight.quote) if flight.quote is not None else None
            )
        if flight_error is not None:
            raise flight_error
        if flight_quote is None:
            raise SdkError("每日一句请求未返回结果")
        return flight_quote

    async def _daily_quote_data(
        self,
        *,
        local_date: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        today = local_date or _local_date()
        with self._state_lock:
            settings = self._settings_snapshot_unlocked()
            generation = self._cache_generation
        cache_enabled = bool(settings["daily_cache"])

        if cache_enabled:
            cached = await self._read_daily_cache(today)
            if cached is not None:
                return cached, True

        retry_with_current_settings = False
        with self._state_lock:
            if generation != self._cache_generation:
                retry_with_current_settings = True
                flight = None
                leader = False
            else:
                if cache_enabled:
                    memory = self._memory_daily
                    if (
                        isinstance(memory, Mapping)
                        and memory.get("date") == today
                        and memory.get("generation") == generation
                    ):
                        cached = _quote_from_cache(memory.get("quote"))
                        if cached is not None:
                            return cached, True

                flight = self._daily_flights.get(today)
                if (
                    flight is not None
                    and flight.generation != generation
                ):
                    # A cache clear or quote-affecting setting change starts
                    # a new generation. Later calls cannot join stale work.
                    flight = None
                leader = flight is None
                if flight is None:
                    flight = _DailyFlight(
                        local_date=today,
                        generation=generation,
                        cache_enabled=cache_enabled,
                    )
                    self._daily_flights[today] = flight

        if retry_with_current_settings:
            return await self._daily_quote_data(local_date=today)

        if flight is None:
            raise SdkError("每日一句请求状态无效")

        if not leader:
            try:
                joined_quote = await self._wait_for_flight(flight)
            except _DailyFlightAborted:
                return await self._daily_quote_data(local_date=today)
            return joined_quote, bool(flight.cache_enabled)

        retry_with_current_settings = False
        try:
            category = str(settings["default_category"])
            fetched = await self._fetch_remote(category)
            if flight.cache_enabled:
                await self._commit_daily_cache(
                    local_date=today,
                    quote=fetched,
                    generation=flight.generation,
                )
            with self._state_lock:
                if flight.generation != self._cache_generation:
                    flight.error = _DailyFlightAborted()
                    retry_with_current_settings = True
                else:
                    flight.quote = dict(fetched)
        except asyncio.CancelledError:
            flight.error = _DailyFlightAborted()
            raise
        except BaseException as exc:
            with self._state_lock:
                retry_with_current_settings = (
                    flight.generation != self._cache_generation
                    and isinstance(exc, Exception)
                )
                flight.error = (
                    _DailyFlightAborted()
                    if retry_with_current_settings
                    else exc
                )
            if not retry_with_current_settings:
                raise
        finally:
            with self._state_lock:
                if self._daily_flights.get(today) is flight:
                    self._daily_flights.pop(today, None)
            flight.done.set()

        if retry_with_current_settings:
            return await self._daily_quote_data(local_date=today)
        return dict(fetched), False

    # ------------------------------------------------------------------
    # User-facing dual-registered quote capabilities
    # ------------------------------------------------------------------

    @llm_tool(
        name="hitokoto_random_quote",
        description=(
            "获取一句随机一言。用户说“来一句”“来句诗”“今日毒鸡汤”、"
            "“给我一句动漫台词”“来点哲学”或想要语录时调用。"
            "category 可按一言官方类型筛选；省略使用面板默认值，空字符串全类型随机。"
        ),
        parameters=RANDOM_QUOTE_SCHEMA,
        timeout=35.0,
    )
    @plugin_entry(
        id="random_quote",
        name="随机一言",
        description=(
            "获取一句随机一言。适用于“来一句”、诗词、动漫台词、哲学、"
            "毒鸡汤等自然语言请求。category 省略时使用面板默认类型，"
            "传空字符串时全类型随机。"
        ),
        input_schema=RANDOM_QUOTE_SCHEMA,
        llm_result_fields=[
            "formatted",
            "type_code",
            "type_label",
            "id",
            "uuid",
            "url",
        ],
        timeout=35.0,
    )
    async def random_quote(self, category: str | None = None, **_: Any):
        try:
            resolved_category = self._resolve_category(category)
        except SdkError as exc:
            return Err(exc)

        try:
            fetched = await self._fetch_remote(resolved_category)
        except Exception as exc:
            failure_class = type(exc).__name__
            self._record_request(
                action="random_quote",
                status="error",
                failure_class=failure_class,
                api_checked=True,
            )
            self.logger.warning(
                "Hitokoto random request failed: failure_class={}",
                failure_class,
            )
            return Err(self._friendly_fetch_error("获取一言失败", exc))

        self._record_request(
            action="random_quote",
            status="success",
            quote=fetched,
            api_checked=True,
        )
        self.logger.info(
            "Hitokoto random request succeeded: id={} uuid={} type={} length={}",
            fetched.get("id"),
            fetched.get("uuid"),
            fetched.get("type_code"),
            len(fetched["sentence"]),
        )
        return Ok({"cached": False, **fetched})

    @llm_tool(
        name="hitokoto_daily_quote",
        description=(
            "获取今日一言。缓存启用时，同一个本地日历日内始终返回同一句。"
            "用户说“今日一言”“今日句子”或想要每日一句时调用。"
        ),
        parameters=_EMPTY_SCHEMA,
        timeout=35.0,
    )
    @plugin_entry(
        id="daily_quote",
        name="今日一言",
        description=(
            "获取今日一言。缓存启用时同一个本地日历日内复用同一句，"
            "并包含作者、作品、类型、ID、UUID 与可追溯链接。"
        ),
        input_schema=_EMPTY_SCHEMA,
        llm_result_fields=[
            "formatted",
            "type_code",
            "type_label",
            "id",
            "uuid",
            "url",
        ],
        timeout=35.0,
    )
    async def daily_quote(self, **_: Any):
        today = _local_date()
        try:
            quote, cache_hit = await self._daily_quote_data(
                local_date=today,
            )
        except Exception as exc:
            failure_class = type(exc).__name__
            self._record_request(
                action="daily_quote",
                status="error",
                failure_class=failure_class,
                api_checked=True,
            )
            self.logger.warning(
                "Hitokoto daily request failed: failure_class={}",
                failure_class,
            )
            return Err(self._friendly_fetch_error("获取今日一言失败", exc))

        self._record_request(
            action="daily_quote",
            status="cache_hit" if cache_hit else "success",
            quote=quote,
            cache_hit=cache_hit,
            api_checked=not cache_hit,
        )
        self.logger.info(
            "Hitokoto daily request succeeded: id={} uuid={} type={} length={} cache_hit={}",
            quote.get("id"),
            quote.get("uuid"),
            quote.get("type_code"),
            len(quote["sentence"]),
            cache_hit,
        )
        return Ok({"cached": cache_hit, "date": today, **quote})

    # ------------------------------------------------------------------
    # Daily first-chat greeting
    # ------------------------------------------------------------------

    @timer_interval(
        id="hitokoto_user_context_poll",
        seconds=2,
        auto_start=True,
        name="聊天消息观察器",
        description="观察新的文本或语音用户消息并触发每日首次聊天一言。",
    )
    async def poll_user_context(self, **_: Any):
        return await self._poll_user_context_once()

    async def _claim_daily_greeting(self, today: str) -> tuple[bool, bool]:
        with self._state_lock:
            if today in self._greeting_attempted_dates:
                return False, bool(getattr(self.store, "enabled", False))

        stored_date = _valid_local_date(
            await self._store_get(
                _STORE_KEY_GREETING_DATE,
                None,
            )
        )
        with self._state_lock:
            if stored_date is not None:
                self._greeting_attempted_dates.add(stored_date)
            if today in self._greeting_attempted_dates:
                self._greeting_attempted_date = max(
                    self._greeting_attempted_dates
                )
                return False, bool(getattr(self.store, "enabled", False))
            newest_attempt = (
                max(self._greeting_attempted_dates)
                if self._greeting_attempted_dates
                else None
            )
            self._greeting_attempted_dates.add(today)
            self._greeting_attempted_date = max(
                self._greeting_attempted_dates
            )
            if (
                isinstance(newest_attempt, str)
                and newest_attempt > today
            ):
                # A delayed older message must not produce an out-of-order
                # greeting or regress the persisted latest attempt.
                return False, False
            # Mark in memory before any further await. This is the atomic
            # cross-loop claim that suppresses near-simultaneous messages.

        await self._acquire_without_blocking_loop(
            self._greeting_store_lock
        )
        try:
            with self._state_lock:
                newest_attempt = max(self._greeting_attempted_dates)
                if newest_attempt > today:
                    return False, False
            current = await self._store_get(
                _STORE_KEY_GREETING_DATE,
                None,
            )
            current_date = _valid_local_date(current)
            if current_date is not None and current_date > today:
                with self._state_lock:
                    self._greeting_attempted_dates.add(current_date)
                    self._greeting_attempted_date = max(
                        self._greeting_attempted_dates
                    )
                return False, True
            persisted = await self._store_set(
                _STORE_KEY_GREETING_DATE,
                today,
            )
            return True, persisted
        finally:
            self._greeting_store_lock.release()

    @message(
        id="hitokoto_daily_greeting",
        source="chat",
        name="每日首次聊天一言",
        description="每天观察到第一条聊天消息时，让角色自然分享今日一言。",
    )
    async def on_chat_message(self, **_: Any):
        settings = self._settings_snapshot()
        if not bool(settings["daily_greeting"]):
            return Ok({"status": "disabled", "pushed": False})

        today = _local_date()
        claimed, persisted = await self._claim_daily_greeting(today)
        if not claimed:
            return Ok(
                {
                    "status": "already_attempted",
                    "date": today,
                    "pushed": False,
                }
            )

        try:
            quote, cache_hit = await self._daily_quote_data(local_date=today)
        except Exception as exc:
            failure_class = type(exc).__name__
            self._record_request(
                action="daily_greeting",
                status="error",
                failure_class=failure_class,
                api_checked=True,
            )
            self.logger.warning(
                "Hitokoto greeting quote failed: failure_class={} attempt_persisted={}",
                failure_class,
                persisted,
            )
            return Ok(
                {
                    "status": "attempted_fetch_failed",
                    "date": today,
                    "pushed": False,
                    "attempt_persisted": persisted,
                    "failure_class": failure_class,
                }
            )

        instruction = (
            "这是今天第一次观察到 {MASTER_NAME} 的聊天消息。"
            "请用你自己的角色口吻，自然地向 {MASTER_NAME} 分享下面这句今日一言；"
            "可以顺带说一句简短感想，但不要复述这段指令，也不要把它说成插件通知。\n\n"
            f"今日一言：\n{quote['formatted']}"
        )
        try:
            self.push_message(
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": instruction}],
                source="hitokoto",
                priority=2,
                metadata={
                    "event_type": "hitokoto_daily_greeting",
                    "date": today,
                    "quote_id": quote.get("id"),
                    "quote_uuid": quote.get("uuid"),
                    "type_code": quote.get("type_code"),
                    "cache_hit": cache_hit,
                },
            )
        except Exception as exc:
            failure_class = type(exc).__name__
            self._record_request(
                action="daily_greeting",
                status="push_error",
                quote=quote,
                cache_hit=cache_hit,
                failure_class=failure_class,
                api_succeeded=True if not cache_hit else None,
            )
            self.logger.warning(
                "Hitokoto greeting push failed: failure_class={} attempt_persisted={}",
                failure_class,
                persisted,
            )
            return Ok(
                {
                    "status": "attempted_push_failed",
                    "date": today,
                    "pushed": False,
                    "attempt_persisted": persisted,
                    "failure_class": failure_class,
                }
            )

        self._record_request(
            action="daily_greeting",
            status="pushed",
            quote=quote,
            cache_hit=cache_hit,
            api_checked=not cache_hit,
        )
        self.logger.info(
            "Hitokoto greeting pushed: id={} uuid={} type={} length={} cache_hit={} attempt_persisted={}",
            quote.get("id"),
            quote.get("uuid"),
            quote.get("type_code"),
            len(quote["sentence"]),
            cache_hit,
            persisted,
        )
        return Ok(
            {
                "status": "pushed",
                "date": today,
                "pushed": True,
                "cached": cache_hit,
                "attempt_persisted": persisted,
            }
        )

    # ------------------------------------------------------------------
    # Panel-only entries (intentionally not @llm_tool)
    # ------------------------------------------------------------------

    async def _panel_cache_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            generation = self._cache_generation
            settings = self._settings_snapshot_unlocked()
            settings_identity = _quote_affecting_settings_identity(settings)
            memory = (
                dict(self._memory_daily)
                if isinstance(self._memory_daily, Mapping)
                else None
            )
            ignore_persisted = self._ignore_persisted_daily
        record: Any = memory
        if record is None and not ignore_persisted:
            persisted_record = await self._store_get(
                _STORE_KEY_DAILY,
                None,
            )
            persisted_matches_settings = (
                isinstance(persisted_record, Mapping)
                and persisted_record.get("settings_identity")
                == settings_identity
            )
            with self._state_lock:
                settings = self._settings_snapshot_unlocked()
                current_memory = (
                    dict(self._memory_daily)
                    if isinstance(self._memory_daily, Mapping)
                    else None
                )
                if current_memory is not None:
                    record = current_memory
                elif (
                    generation != self._cache_generation
                    or self._ignore_persisted_daily
                    or not bool(settings["daily_cache"])
                    or not persisted_matches_settings
                    or settings_identity
                    != _quote_affecting_settings_identity(settings)
                ):
                    record = None
                else:
                    record = persisted_record
        if not isinstance(record, Mapping):
            return {"date": None, "quote": None}
        cache_date = _valid_local_date(record.get("date"))
        cached_quote = _quote_from_cache(record.get("quote"))
        return {
            "date": cache_date,
            "quote": cached_quote,
        }

    @plugin_entry(
        id="get_panel_state",
        name="读取一言面板状态",
        description="读取 Hitokoto 插件运行状态、设置、缓存与最近请求摘要。",
        input_schema=_EMPTY_SCHEMA,
    )
    async def get_panel_state(self, **_: Any):
        settings = self._settings_snapshot()
        cache = (
            await self._panel_cache_snapshot()
            if bool(settings["daily_cache"])
            else {"date": None, "quote": None}
        )
        with self._state_lock:
            runtime_started = self._runtime_started
            api_state = self._api_state
            last_request = dict(self._last_request)
            recent_quote = (
                dict(self._recent_quote)
                if self._recent_quote is not None
                else None
            )
            override_keys = sorted(self._settings_overrides)
        if recent_quote is None and isinstance(cache.get("quote"), Mapping):
            recent_quote = dict(cache["quote"])

        return Ok(
            {
                "running": runtime_started,
                "api_state": api_state,
                "store_enabled": bool(getattr(self.store, "enabled", False)),
                "settings": settings,
                "defaults": dict(self._manifest_settings),
                "override_keys": override_keys,
                "categories": [
                    {"code": "", "label": "随机 / 全部"},
                    *[
                        {"code": code, "label": label}
                        for code, label in CATEGORIES.items()
                    ],
                ],
                "daily_cache": {
                    "enabled": bool(settings["daily_cache"]),
                    "date": cache.get("date"),
                },
                "latest_request": last_request,
                "recent_quote": recent_quote,
            }
        )

    @plugin_entry(
        id="save_settings",
        name="保存一言设置",
        description="校验并保存 Hitokoto 运行时设置，保存后立即生效。",
        input_schema=SAVE_SETTINGS_SCHEMA,
    )
    async def save_settings(
        self,
        default_category: str,
        timeout_seconds: float,
        max_length: int,
        daily_cache: bool,
        daily_greeting: bool,
        **_: Any,
    ):
        try:
            validated = _validate_settings_overlay(
                {
                    "default_category": default_category,
                    "timeout_seconds": timeout_seconds,
                    "max_length": max_length,
                    "daily_cache": daily_cache,
                    "daily_greeting": daily_greeting,
                },
                require_all=True,
            )
        except SdkError as exc:
            return Err(exc)

        if not bool(getattr(self.store, "enabled", False)):
            return Err(SdkError("插件存储已禁用，无法保存设置"))
        await self._acquire_without_blocking_loop(self._cache_store_lock)
        try:
            if not await self._store_set(_STORE_KEY_SETTINGS, validated):
                return Err(
                    SdkError("保存设置失败（StoreError），请稍后再试")
                )

            with self._state_lock:
                previous = self._settings_snapshot_unlocked()
                self._settings_overrides = dict(validated)
                current = self._settings_snapshot_unlocked()
                cache_invalidated = any(
                    previous[key] != current[key]
                    for key in _QUOTE_AFFECTING_SETTINGS
                )
                if cache_invalidated:
                    self._cache_generation += 1
                    self._memory_daily = None
                    self._ignore_persisted_daily = True

            if (
                cache_invalidated
                and not await self._store_delete(_STORE_KEY_DAILY)
            ):
                self.logger.warning(
                    "Hitokoto daily cache persistence invalidation failed "
                    "after settings save: failure_class=StoreError"
                )
        finally:
            self._cache_store_lock.release()
        self.logger.info(
            "Hitokoto settings saved: default_category={} timeout={} max_length={} daily_cache={} daily_greeting={}",
            validated["default_category"] or "all",
            validated["timeout_seconds"],
            validated["max_length"],
            validated["daily_cache"],
            validated["daily_greeting"],
        )
        return Ok(
            {
                "saved": True,
                "settings": self._settings_snapshot(),
            }
        )

    @plugin_entry(
        id="reset_settings",
        name="恢复一言默认设置",
        description="删除运行时设置覆盖并立即恢复 plugin.toml 默认值。",
        input_schema=_EMPTY_SCHEMA,
    )
    async def reset_settings(self, **_: Any):
        store_enabled = bool(getattr(self.store, "enabled", False))
        await self._acquire_without_blocking_loop(self._cache_store_lock)
        try:
            if (
                store_enabled
                and not await self._store_delete(_STORE_KEY_SETTINGS)
            ):
                return Err(
                    SdkError("恢复默认设置失败（StoreError），请稍后再试")
                )

            with self._state_lock:
                previous = self._settings_snapshot_unlocked()
                self._settings_overrides = {}
                current = self._settings_snapshot_unlocked()
                cache_invalidated = any(
                    previous[key] != current[key]
                    for key in _QUOTE_AFFECTING_SETTINGS
                )
                if cache_invalidated:
                    self._cache_generation += 1
                    self._memory_daily = None
                    self._ignore_persisted_daily = True

            if (
                cache_invalidated
                and store_enabled
                and not await self._store_delete(_STORE_KEY_DAILY)
            ):
                self.logger.warning(
                    "Hitokoto daily cache persistence invalidation failed "
                    "after settings reset: failure_class=StoreError"
                )
        finally:
            self._cache_store_lock.release()
        self.logger.info("Hitokoto settings reset")
        return Ok(
            {
                "reset": True,
                "persisted": store_enabled,
                "settings": self._settings_snapshot(),
            }
        )

    @plugin_entry(
        id="test_api",
        name="测试一言 API",
        description="使用当前设置请求一次 Hitokoto API，不读写每日缓存。",
        input_schema=_EMPTY_SCHEMA,
        timeout=35.0,
    )
    async def test_api(self, **_: Any):
        category = str(self._settings_snapshot()["default_category"])
        try:
            fetched = await self._fetch_remote(category)
        except Exception as exc:
            failure_class = type(exc).__name__
            self._record_request(
                action="test_api",
                status="error",
                failure_class=failure_class,
                api_checked=True,
            )
            self.logger.warning(
                "Hitokoto API test failed: failure_class={}",
                failure_class,
            )
            return Err(self._friendly_fetch_error("一言 API 测试失败", exc))

        self._record_request(
            action="test_api",
            status="success",
            quote=fetched,
            api_checked=True,
        )
        self.logger.info(
            "Hitokoto API test succeeded: id={} uuid={} type={} length={}",
            fetched.get("id"),
            fetched.get("uuid"),
            fetched.get("type_code"),
            len(fetched["sentence"]),
        )
        return Ok({"ok": True, "quote": fetched})

    @plugin_entry(
        id="clear_daily_cache",
        name="清除每日一言缓存",
        description="清除当前每日一句缓存；不会重置今天的问候尝试记录。",
        input_schema=_EMPTY_SCHEMA,
    )
    async def clear_daily_cache(self, **_: Any):
        await self._acquire_without_blocking_loop(self._cache_store_lock)
        try:
            with self._state_lock:
                previous_date = (
                    self._memory_daily.get("date")
                    if isinstance(self._memory_daily, Mapping)
                    else None
                )
                self._cache_generation += 1
                self._memory_daily = None
                self._ignore_persisted_daily = True

            persisted = False
            if bool(getattr(self.store, "enabled", False)):
                persisted = await self._store_delete(_STORE_KEY_DAILY)
                if not persisted:
                    self.logger.warning(
                        "Hitokoto daily cache persistence clear failed: "
                        "previous_date={} failure_class=StoreError",
                        previous_date,
                    )
        finally:
            self._cache_store_lock.release()

        self.logger.info(
            "Hitokoto daily cache cleared: previous_date={} store_enabled={}",
            previous_date,
            bool(getattr(self.store, "enabled", False)),
        )
        return Ok(
            {
                "cleared": True,
                "previous_date": previous_date,
                "persisted": persisted,
            }
        )


__all__ = [
    "API_URL",
    "CATEGORIES",
    "HitokotoPlugin",
    "RANDOM_QUOTE_SCHEMA",
    "SAVE_SETTINGS_SCHEMA",
    "USER_AGENT",
    "_fetch_hitokoto",
    "_format_quote",
    "_new_http_client",
    "_parse_hitokoto_payload",
]
