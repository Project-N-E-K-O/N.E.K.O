"""Lifecycle isolation helpers for feature modules."""

from __future__ import annotations

from typing import Any


async def setup_all_modules(
    modules: dict[str, Any],
    degraded: dict[str, str],
    ctx: Any,
) -> None:
    degraded.clear()
    for module in modules.values():
        try:
            await module.setup(ctx)
        except Exception as exc:  # noqa: BLE001
            message = _error_message(exc)
            degraded[module.id] = message
            record_failure(ctx, "module_setup_failed", module.id, message)


async def teardown_all_modules(modules: dict[str, Any]) -> None:
    for module in reversed(list(modules.values())):
        try:
            await module.teardown()
        except Exception:  # noqa: BLE001
            pass


async def toggle_module(
    modules: dict[str, Any],
    degraded: dict[str, str],
    module_id: str,
    enabled: bool,
    ctx: Any,
) -> bool:
    module = modules.get(module_id)
    if module is None:
        return False

    previous_enabled = bool(getattr(module, "enabled", False))
    hook = getattr(module, "on_enable" if enabled else "on_disable", None)
    if not callable(hook):
        module.enabled = enabled
        degraded.pop(module_id, None)
        return True

    try:
        await (hook(ctx) if enabled else hook())
        module.enabled = enabled
        degraded.pop(module_id, None)
        return True
    except Exception as exc:  # noqa: BLE001
        module.enabled = previous_enabled
        message = _error_message(exc)
        degraded[module_id] = message
        record_failure(
            ctx,
            "module_enable_failed" if enabled else "module_disable_failed",
            module_id,
            message,
        )
        return False


def record_failure(ctx: Any, op: str, module_id: str, message: str) -> None:
    audit = getattr(ctx, "audit", None)
    record = getattr(audit, "record", None)
    if not callable(record):
        return
    try:
        record(
            op,
            f"module {module_id}: {message}",
            level="error",
            detail={"module": module_id},
        )
    except Exception:  # noqa: BLE001
        pass


def _error_message(exc: Exception) -> str:
    return str(exc).strip() or type(exc).__name__
