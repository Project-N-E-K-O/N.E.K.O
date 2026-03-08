from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class ServerDomainError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

