"""Hook contracts for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Literal, Protocol

from plugin.sdk_v2.shared.constants import HOOK_META_ATTR
from plugin.sdk_v2.shared.models import Result
from plugin.sdk_v2.shared.models.exceptions import HookErrorLike
from .types import JsonObject, JsonValue

HookTiming = Literal["before", "after", "around", "replace"]


class HookHandler(Protocol):
    def __call__(self, target: str, payload: JsonObject) -> Awaitable[Result[JsonObject | JsonValue, HookErrorLike]]: ...


@dataclass(slots=True)
class HookMeta:
    target: str = "*"
    timing: HookTiming = "before"
    priority: int = 0
    condition: str | None = None


__all__ = ["HOOK_META_ATTR", "HookTiming", "HookHandler", "HookMeta"]
