from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker


class AgentDiagnosticsMixin:
    def _peek_summary_debug(self, shared: dict[str, Any]) -> dict[str, Any]:
        session_id = str(shared.get("active_session_id") or "")
        if session_id == self._observed_session_id:
            return {}
        transition_type, transition_reason, transition_fields = self._classify_session_transition(
            self._observed_session_fingerprint,
            self._session_fingerprint(shared),
        )
        return {
            "peek_session_transition": {
                "type": transition_type,
                "reason": transition_reason,
                "fields": json_copy(transition_fields),
                "observed_session_id": self._observed_session_id,
                "shared_session_id": session_id,
                "committed": False,
            }
        }

    @staticmethod
    def _append_bounded(items: list[dict[str, Any]], item: dict[str, Any], *, limit: int) -> None:
        items.append(dict(item))
        if len(items) > limit:
            del items[:-limit]

    def _trace_runtime(self, message: str) -> None:
        if not message:
            return
        if message == self._last_trace_message:
            return
        self._last_trace_message = message
        self._logger.info("galgame_agent {}", message)
