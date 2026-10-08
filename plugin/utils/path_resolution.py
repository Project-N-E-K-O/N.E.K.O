"""Path resolution reused explicitly within one synchronous discovery operation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading


@dataclass
class PathResolutionCache:
    paths: dict[tuple[Path, bool], Path] = field(default_factory=dict)
    runtime_data_root: Path | None = None
    owner_thread: int = field(default_factory=threading.get_ident)

    def resolve(self, path: Path, *, strict: bool = False) -> Path:
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError("A discovery path cache cannot be shared across threads")
        absolute = path if path.is_absolute() else Path.cwd() / path
        key = (absolute, strict)
        if key not in self.paths:
            self.paths[key] = path.resolve(strict=strict)
        return self.paths[key]


def canonical_read_path(
    path: Path, *, strict: bool = False, cache: PathResolutionCache | None = None
) -> Path:
    return (
        path.resolve(strict=strict)
        if cache is None
        else cache.resolve(path, strict=strict)
    )
