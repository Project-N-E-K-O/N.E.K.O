from __future__ import annotations

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain import IO_RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.bus_subscriptions import new_sub_id
from plugin.settings import PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS

logger = get_logger("server.application.bus.subscription")

_SUPPORTED_BUSES = frozenset({"messages", "events", "lifecycle", "runs", "export"})


def _normalize_bus_name(value: object) -> str:
    if not isinstance(value, str):
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="bus is required",
            status_code=400,
            details={},
        )
    bus = value.strip()
    if bus not in _SUPPORTED_BUSES:
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="bus is required",
            status_code=400,
            details={"supported": sorted(_SUPPORTED_BUSES)},
        )
    return bus


def _normalize_deliver(value: object) -> str:
    deliver = str(value or "delta").strip()
    if deliver != "delta":
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="Only deliver=delta is supported",
            status_code=400,
            details={},
        )
    return deliver


def _normalize_rules(value: object) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else ["add"]
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            stripped = str(item).strip()
            if stripped:
                normalized.append(stripped)
        return normalized if normalized else ["add"]
    return ["add"]


def _normalize_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 5.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        return timeout if timeout > 0 else 5.0
    return 5.0


class BusSubscriptionService:
    def subscribe(
        self,
        *,
        from_plugin: str,
        bus: object,
        deliver: object,
        rules: object,
        plan: object,
        debounce_ms: object,
        timeout: object,
    ) -> dict[str, object]:
        if not isinstance(from_plugin, str) or not from_plugin:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="from_plugin is required",
                status_code=400,
                details={},
            )

        normalized_bus = _normalize_bus_name(bus)
        normalized_deliver = _normalize_deliver(deliver)
        normalized_rules = _normalize_rules(rules)
        normalized_timeout = _normalize_timeout(timeout)
        sub_id = new_sub_id()

        info: dict[str, object] = {
            "from_plugin": from_plugin,
            "bus": normalized_bus,
            "rules": normalized_rules,
            "deliver": normalized_deliver,
            "plan": plan,
            "debounce_ms": debounce_ms,
            "timeout": normalized_timeout,
        }

        try:
            state.add_bus_subscription(normalized_bus, sub_id, info)
            current_rev = int(state.get_bus_rev(normalized_bus))
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "subscribe failed: from_plugin={}, bus={}, err_type={}, err={}",
                from_plugin,
                normalized_bus,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="BUS_SUBSCRIBE_FAILED",
                message="Failed to subscribe bus events",
                status_code=500,
                details={"bus": normalized_bus, "error_type": type(exc).__name__},
            ) from exc

        if PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS:
            logger.info(
                "BUS_SUBSCRIBE ok: from_plugin={}, bus={}, sub_id={}",
                from_plugin,
                normalized_bus,
                sub_id,
            )
        return {"ok": True, "sub_id": sub_id, "bus": normalized_bus, "rev": current_rev}

    def unsubscribe(
        self,
        *,
        from_plugin: str,
        bus: object,
        sub_id: object,
    ) -> dict[str, object]:
        if not isinstance(from_plugin, str) or not from_plugin:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="from_plugin is required",
                status_code=400,
                details={},
            )

        normalized_bus = _normalize_bus_name(bus)
        if not isinstance(sub_id, str) or not sub_id:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="bus and sub_id are required",
                status_code=400,
                details={},
            )

        try:
            removed = bool(state.remove_bus_subscription(normalized_bus, sub_id))
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "unsubscribe failed: from_plugin={}, bus={}, sub_id={}, err_type={}, err={}",
                from_plugin,
                normalized_bus,
                sub_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="BUS_UNSUBSCRIBE_FAILED",
                message="Failed to unsubscribe bus events",
                status_code=500,
                details={"bus": normalized_bus, "sub_id": sub_id, "error_type": type(exc).__name__},
            ) from exc

        if PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS:
            logger.info(
                "BUS_UNSUBSCRIBE: from_plugin={}, bus={}, sub_id={}, ok={}",
                from_plugin,
                normalized_bus,
                sub_id,
                removed,
            )
        return {"ok": removed, "sub_id": sub_id, "bus": normalized_bus}
