from __future__ import annotations

from .tutor_llm_agent_common import *  # noqa: F401, F403



async def summarize_session(
    self,
    history: list[dict[str, Any]] | None = None,
    *,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> TutorReply:
    operation_context = {
        **dict(context or {}),
        "history": list(history or []),
        "language": self._config.language,
        "mode": normalize_mode(mode),
    }
    return await self._invoke_structured_operation(LLM_OPERATION_SUMMARIZE_SESSION, operation_context)

def _normalize_summary(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    summary = _as_str(raw.get("summary")).strip()
    markdown = _as_str(raw.get("markdown")).strip()
    if not summary and markdown:
        summary = next((line.strip("# ").strip() for line in markdown.splitlines() if line.strip()), "")
    if not summary:
        raise SdkError("missing summary")
    if not markdown:
        markdown = self._markdown_from_summary(
            summary,
            _string_list(raw.get("highlights")),
            _string_list(raw.get("weak_points")),
            _string_list(raw.get("next_actions")),
        )
    return {
        "summary": summary,
        "highlights": _string_list(raw.get("highlights")),
        "weak_points": _string_list(raw.get("weak_points")),
        "next_actions": _string_list(raw.get("next_actions")),
        "markdown": markdown,
        "screen_type": self._screen_type_from_context(context),
    }

def _fallback_summary(self, context: dict[str, Any]) -> dict[str, Any]:
    history = [item for item in _as_list(context.get("history")) if isinstance(item, dict)]
    highlights = [
        f"{_as_str(item.get('kind'), 'interaction')}: {_as_str(item.get('output_text')).strip()[:80]}"
        for item in history[:4]
        if _as_str(item.get("output_text")).strip()
    ]
    summary = STUDY_FALLBACK_SUMMARY_EMPTY if not history else STUDY_FALLBACK_SUMMARY_DEFAULT
    weak_points = _string_list(_as_dict(context.get("session_summary_seed")).get("weak_points"), limit=4)
    next_actions = list(STUDY_FALLBACK_SUMMARY_NEXT_ACTIONS)
    markdown = self._markdown_from_summary(summary, highlights, weak_points, next_actions)
    return {
        "summary": summary,
        "highlights": highlights,
        "weak_points": weak_points,
        "next_actions": next_actions,
        "markdown": markdown,
        "screen_type": self._screen_type_from_context(context),
    }
