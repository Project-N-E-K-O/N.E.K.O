"""Lightweight embedding outcomes shared across the memory and knowledge layers."""

from dataclasses import dataclass
from typing import Literal

EmbedStatus = Literal["ok", "unavailable", "loading", "disabled", "busy", "timeout", "error"]


@dataclass(frozen=True)
class EmbedResult:
    """Separate admission pressure from genuine missing or failed vectors."""

    vectors: list[list[float] | None]
    status: EmbedStatus
    retry_after_ms: int = 0


class EmbedDeferred(RuntimeError):
    """Defer background work without consuming an index attempt."""

    def __init__(self, retry_after_ms: int = 100):
        super().__init__("embedding_busy")
        self.retry_after_ms = max(1, int(retry_after_ms))
