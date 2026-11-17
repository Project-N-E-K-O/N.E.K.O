"""Host compiler interface and immutable publish candidate."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class PackageError(RuntimeError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class PackageWarning:
    code: str
    path: str
    message: str


class PackageGateway(Protocol):
    def compile(self, story: Mapping[str, Any]) -> Any: ...
    def validate(self, json_bytes: bytes) -> Any: ...


@dataclass(frozen=True)
class PublishCandidate:
    project_id: str
    revision: int
    story_id: str
    package_hash: str
    json_bytes: bytes
