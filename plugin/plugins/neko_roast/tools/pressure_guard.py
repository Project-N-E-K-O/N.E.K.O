"""Fail-closed helpers shared by the local pressure tools."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol


EXIT_OK = 0
EXIT_PREFLIGHT = 3
EXIT_CONNECTION = 4
EXIT_RUN = 5
EXIT_RESTORE = 6


class PressureClient(Protocol):
    def context(self) -> dict[str, Any]: ...

    def action(
        self, action_id: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class PressureError(RuntimeError):
    message: str
    exit_code: int = EXIT_RUN

    def __str__(self) -> str:
        return self.message


def state_from_context(context: dict[str, Any]) -> dict[str, Any]:
    state = context.get("state")
    return state if isinstance(state, dict) else {}


def connection_from_context(context: dict[str, Any]) -> dict[str, Any]:
    connection = state_from_context(context).get("live_connection")
    return connection if isinstance(connection, dict) else {}


def connection_room(context: dict[str, Any]) -> str:
    state = state_from_context(context)
    connection = connection_from_context(context)
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    return str(
        connection.get("room_ref")
        or connection.get("room_id")
        or config.get("live_room_ref")
        or config.get("live_room_id")
        or ""
    )


def preflight_block_reason(context: dict[str, Any]) -> str:
    """Return a stable reason when pressure must not alter runtime state."""
    state = state_from_context(context)
    safety = state.get("safety") if isinstance(state.get("safety"), dict) else {}
    safety_status = str(safety.get("status") or "").lower()
    if safety_status in {"paused", "tripped", "blocked"}:
        return f"safety_{safety_status}"
    if safety.get("auto_paused") is True:
        return "safety_tripped"
    if safety.get("manual_paused") is True:
        return "safety_paused"

    live_state = state.get("live_state") if isinstance(state.get("live_state"), dict) else {}
    for key in ("state", "status", "summary"):
        if str(live_state.get(key) or "").lower() == "blocked":
            return "live_state_blocked"

    readiness = (
        state.get("solo_test_readiness")
        if isinstance(state.get("solo_test_readiness"), dict)
        else {}
    )
    items = readiness.get("items") if isinstance(readiness.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict) or item.get("id") != "preflight":
            continue
        if str(item.get("status") or "").lower() == "blocked":
            return "readiness_preflight_blocked"
    return ""


def require_safe_preflight(context: dict[str, Any]) -> None:
    reason = preflight_block_reason(context)
    if reason:
        raise PressureError(
            f"pressure preflight refused: {reason}; resolve it manually before retrying",
            EXIT_PREFLIGHT,
        )


def require_real_output_confirmation(*, real_output: bool, confirmed: bool) -> None:
    if real_output and not confirmed:
        raise PressureError(
            "real output requires both --real-output and --confirm-real-output",
            EXIT_PREFLIGHT,
        )


def require_action_success(action_id: str, response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    if not isinstance(result, dict):
        raise PressureError(f"{action_id} returned no plugin-entry result")
    if result.get("success") is False:
        reason = result.get("error") or result.get("message") or "plugin entry reported failure"
        raise PressureError(f"{action_id} failed: {reason}")
    return response


def wait_for_connection(
    client: PressureClient,
    *,
    room: str,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        context = client.context()
        connection = connection_from_context(context)
        if connection.get("connected") is True and (
            not room or not connection_room(context) or connection_room(context) == room
        ):
            return context
        sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    raise PressureError(
        "connect_live_room did not reach the requested connected state before timeout; 0 events submitted",
        EXIT_CONNECTION,
    )


def compare_and_restore_config(
    client: PressureClient,
    *,
    initial_config: dict[str, Any],
    applied_config: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Restore only values that still equal the values written by this process."""
    try:
        latest_state = state_from_context(client.context())
    except Exception as exc:  # noqa: BLE001
        raise PressureError(
            f"config restore comparison failed: {type(exc).__name__}: {exc}",
            EXIT_RESTORE,
        ) from exc
    latest = latest_state.get("config") if isinstance(latest_state.get("config"), dict) else {}
    restore: dict[str, Any] = {}
    skipped: list[str] = []
    for key, applied_value in applied_config.items():
        if key not in initial_config:
            continue
        if latest.get(key) == applied_value:
            restore[key] = initial_config[key]
        else:
            skipped.append(key)
    if restore:
        try:
            require_action_success("update_config", client.action("update_config", restore))
        except Exception as exc:  # noqa: BLE001
            raise PressureError(str(exc), EXIT_RESTORE) from exc
    return restore, skipped


def disconnect_owned_connection(client: PressureClient, *, owned_room: str) -> bool:
    """Disconnect only when the current listener is still the one this process opened."""
    try:
        context = client.context()
    except Exception as exc:  # noqa: BLE001
        raise PressureError(
            f"owned connection comparison failed: {type(exc).__name__}: {exc}",
            EXIT_RESTORE,
        ) from exc
    connection = connection_from_context(context)
    if connection.get("connected") is not True:
        return False
    current_room = connection_room(context)
    if owned_room and current_room and current_room != owned_room:
        return False
    try:
        require_action_success("disconnect_live_room", client.action("disconnect_live_room"))
    except Exception as exc:  # noqa: BLE001
        raise PressureError(str(exc), EXIT_RESTORE) from exc
    return True
