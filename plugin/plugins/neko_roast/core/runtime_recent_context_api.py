"""Runtime compatibility API for recent-context projections."""

from __future__ import annotations

from typing import Any

from . import recent_context


class RuntimeRecentContextApiMixin:
    def recent_interaction_context(self, *, limit: int = 3) -> list[str]:
        return recent_context.build_recent_interaction_context(
            self.recent_results, limit=limit
        )

    def viewer_session_context(self, uid: str, *, limit: int = 2) -> list[str]:
        return recent_context.build_viewer_session_context(
            self.recent_results, uid, limit=limit
        )

    def recent_room_danmaku_context(
        self, event: Any | None = None, *, limit: int = 6
    ) -> list[str]:
        return recent_context.build_recent_room_danmaku_context(
            self.recent_results,
            event,
            limit=limit,
        )

    @staticmethod
    def _spent_output_text(result: dict[str, Any]) -> str:
        return recent_context.spent_output_text(result)

    @staticmethod
    def _spent_output_families(output: str) -> list[str]:
        return recent_context.spent_output_families(output)

    def _recent_spent_output_families(self, *, limit: int = 12) -> set[str]:
        return recent_context.recent_spent_output_families(
            self.recent_results, limit=limit
        )

    @staticmethod
    def _compact_context_text(value: str, *, limit: int = 80) -> str:
        return recent_context.compact_context_text(value, limit=limit)

    @staticmethod
    def _route_from_result(result: dict[str, Any]) -> str:
        return recent_context.route_from_result(result)

    @staticmethod
    def _signal_route_for_event_type(event_type: str) -> str:
        return recent_context.signal_route_for_event_type(event_type)

    @staticmethod
    def _event_signal_from_result(result: dict[str, Any]) -> str:
        return recent_context.event_signal_from_result(result)

    def _has_recent_response_module(self, module_id: str) -> bool:
        target = str(module_id)
        for result in reversed(self.recent_results):
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "") != "pushed":
                continue
            if self._route_from_result(result) == target:
                return True
        return False

    def _has_recent_hosting_response(self) -> bool:
        for module_id in ("warmup_hosting", "idle_hosting", "active_engagement"):
            if self._has_recent_response_module(module_id):
                return True
        return False

    def _recent_actual_route_streak_since_viewer_activity(self, module_id: str) -> int:
        target = str(module_id)
        streak = 0
        for result in reversed(self.recent_results):
            if not isinstance(result, dict):
                continue
            event = result.get("event") if isinstance(result.get("event"), dict) else {}
            if str(event.get("source") or "") == "live_danmaku":
                return streak
            if str(result.get("status") or "") != "pushed":
                continue
            if self._route_from_result(result) == target:
                streak += 1
                continue
            if streak > 0:
                return streak
        return streak
