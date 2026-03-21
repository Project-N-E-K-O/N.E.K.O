"""Hook execution contract for SDK v2 shared core."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from plugin.sdk.shared.models import Result
from plugin.sdk.shared.models.exceptions import HookErrorLike
from .types import JsonObject, JsonValue

HookResult = JsonObject | JsonValue | None
CallNext = Callable[[JsonObject], Awaitable[Result[HookResult, HookErrorLike]]]


class HookExecutorMixin:
    """Contract-only async hook executor."""

    def __init_hook_executor__(self) -> None:
        raise NotImplementedError("sdk contract-only facade: shared.core.hook_executor not implemented")

    async def run_before_hooks(self, target: str, payload: JsonObject) -> Result[JsonObject, HookErrorLike]:
        raise NotImplementedError

    async def run_after_hooks(
        self,
        target: str,
        payload: JsonObject,
        result: HookResult,
    ) -> Result[HookResult, HookErrorLike]:
        raise NotImplementedError

    async def run_around_hooks(
        self,
        target: str,
        payload: JsonObject,
        call_next: CallNext,
    ) -> Result[HookResult, HookErrorLike]:
        raise NotImplementedError


__all__ = ["HookExecutorMixin"]
