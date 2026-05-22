from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


def _log_plugin_noncritical(logger: Any, level: str, message: str, *args: Any) -> None:
    log_fn = getattr(logger, level, None)
    if not callable(log_fn):
        return
    try:
        log_fn(message, *args)
    except Exception:
        return


def _package_public_attr(name: str, fallback: Any) -> Any:
    package = sys.modules.get("plugin.plugins.galgame_plugin")
    if package is None:
        return fallback
    return getattr(package, name, fallback)


def _public_context_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    summary_seed = str(snapshot.get("summary_seed") or "")
    try:
        saved_at = float(snapshot.get("saved_at") or 0.0)
    except (TypeError, ValueError):
        saved_at = 0.0
    return {
        "scene_id": str(snapshot.get("scene_id") or ""),
        "game_id": str(snapshot.get("game_id") or ""),
        "route_id": str(snapshot.get("route_id") or ""),
        "stable_line_count": len(snapshot.get("stable_line_ids") or [])
        if isinstance(snapshot.get("stable_line_ids"), list)
        else 0,
        "summary_seed_chars": len(summary_seed),
        "saved_at": saved_at,
    }


def _migrate_legacy_capture_backend(value: object) -> object:
    """Rewrite legacy "imagegrab" stored value to "mss" at every entry point.

    Old configs saved before the MSS rename keep "imagegrab" verbatim; this
    helper normalizes them at storage / API boundaries so the runtime never
    sees the legacy name and `_OCR_CAPTURE_BACKEND_SELECTIONS` can shrink.
    """
    if isinstance(value, str) and value.strip().lower() == "imagegrab":
        return "mss"
    return value


def _duration_percentile(samples: list[float], percentile: float) -> float:
    values = sorted(float(item) for item in samples if float(item) >= 0.0)
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * max(0.0, min(1.0, percentile))
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _duration_summary(samples: list[float]) -> dict[str, Any]:
    values = [float(item) for item in samples if float(item) >= 0.0]
    return {
        "sample_count": len(values),
        "p50_seconds": _duration_percentile(values, 0.50),
        "p95_seconds": _duration_percentile(values, 0.95),
    }


def _open_url_in_browser(url: str) -> None:
    if sys.platform == "win32":
        os.startfile(url)
    elif sys.platform == "darwin":
        subprocess.run(["open", url], check=True)
    else:
        subprocess.run(["xdg-open", url], check=True)
