from __future__ import annotations

from typing import Any

from main_routers.shared_state import get_session_manager
from plugin.logging_config import get_logger

logger = get_logger("server.application.messages.activity_snapshot_service")


def _clean_lanlan_name(value: object) -> str:
    return str(value or "").strip()


def _resolve_lanlan_name(session_manager: Any, lanlan_name: object) -> str | None:
    requested = _clean_lanlan_name(lanlan_name)
    if requested:
        return requested
    try:
        names = [
            str(name).strip()
            for name in session_manager.keys()
            if str(name).strip()
        ]
    except Exception:
        return None
    if len(names) == 1:
        return names[0]
    return None


def _serialize_active_window(window: object, *, private: bool) -> dict[str, object] | None:
    if window is None:
        return None
    category = str(getattr(window, "category", "") or "")
    if private or category == "private":
        return {
            "category": "private",
            "subcategory": None,
            "canonical": "[private]",
            "is_browser": False,
        }
    return {
        "category": category,
        "subcategory": getattr(window, "subcategory", None),
        "canonical": getattr(window, "canonical", None),
        "is_browser": bool(getattr(window, "is_browser", False)),
    }


def _serialize_snapshot(snapshot: object, *, lanlan_name: str) -> dict[str, object]:
    state = str(getattr(snapshot, "state", "") or "")
    private = state == "private"
    return {
        "available": True,
        "source": "host_activity_tracker",
        "lanlan_name": lanlan_name,
        "state": state,
        "propensity": str(getattr(snapshot, "propensity", "") or ""),
        "os_signals_available": bool(
            getattr(snapshot, "os_signals_available", False)
        ),
        "system_idle_seconds": float(
            getattr(snapshot, "system_idle_seconds", 0.0) or 0.0
        ),
        "active_window": _serialize_active_window(
            getattr(snapshot, "active_window", None),
            private=private,
        ),
    }


class ActivitySnapshotService:
    async def get_activity_snapshot(
        self,
        *,
        lanlan_name: object = None,
        include_enrichment: object = False,
    ) -> dict[str, object]:
        try:
            session_manager = get_session_manager()
        except Exception as exc:
            logger.debug("activity snapshot unavailable: session manager: {}", exc)
            return {"available": False, "reason": "session_manager_unavailable"}

        target = _resolve_lanlan_name(session_manager, lanlan_name)
        if target is None:
            return {"available": False, "reason": "lanlan_required"}

        try:
            manager = session_manager.get(target)
        except Exception as exc:
            logger.debug("activity snapshot unavailable for {}: {}", target, exc)
            manager = None
        if manager is None:
            return {
                "available": False,
                "reason": "session_not_found",
                "lanlan_name": target,
            }

        tracker = getattr(manager, "_activity_tracker", None)
        if tracker is None:
            return {
                "available": False,
                "reason": "activity_tracker_unavailable",
                "lanlan_name": target,
            }

        try:
            snapshot = await tracker.get_snapshot(
                include_enrichment=bool(include_enrichment),
                tick_followups=False,
            )
        except Exception as exc:
            logger.debug("activity snapshot read failed for {}: {}", target, exc)
            return {
                "available": False,
                "reason": "snapshot_failed",
                "lanlan_name": target,
            }

        return _serialize_snapshot(snapshot, lanlan_name=target)
