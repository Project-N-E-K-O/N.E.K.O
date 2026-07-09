"""Replaceable Douyin bridge backend specification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..live_bridge.process_supervisor import cleanup_stale_windows_processes


@dataclass(frozen=True, slots=True)
class DouyinBridgeBackend:
    """Spec for one bundled local bridge executable."""

    backend_id: str
    executable_path: Path
    args_factory: Callable[[int], list[str]]
    stale_process_cleaner: Callable[[Path], None] | None = None


def default_douyin_bridge_backend() -> DouyinBridgeBackend:
    return DouyinBridgeBackend(
        backend_id="douyinlive",
        executable_path=_bundled_executable_path("douyinLive.exe"),
        args_factory=lambda port: ["--port", str(port), "--log-level", "warn"],
        stale_process_cleaner=cleanup_stale_windows_processes,
    )


def _bundled_executable_path(filename: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "vendor" / "douyin_bridge" / "windows-amd64" / filename
