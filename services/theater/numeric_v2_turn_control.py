"""Request-local model limits and deterministic recovery policy, independent of drafting."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from pathlib import Path


class NumericV2ModelCallLimitError(RuntimeError):
    """No further model call belongs to this turn's allowance."""


@dataclass
class TurnModelCalls:
    limit: int = 4
    stages: list[str] = field(default_factory=list)

    def reserve(self, stage: str):
        if len(self.stages) >= self.limit:
            raise NumericV2ModelCallLimitError("numeric_v2_turn_model_call_limit")
        self.stages.append(stage)


_turn_calls: ContextVar[TurnModelCalls | None] = ContextVar("numeric_v2_turn_calls", default=None)


@contextmanager
def bounded_model_calls():
    calls = TurnModelCalls()
    token = _turn_calls.set(calls)
    try:
        yield calls
    finally:
        _turn_calls.reset(token)


def reserve_model_call(stage: str):
    calls = _turn_calls.get()
    if calls is not None:
        calls.reserve(stage)


def optimized_turn_enabled() -> bool:
    return _turn_calls.get() is not None


def pending_scene_task(plan: dict, pending: list[dict], required_keys: set[str]) -> dict | None:
    """Schedule an issued unfinished result; this does not establish feasibility or completion."""
    required = [row for row in pending if row["key"] in required_keys]
    goals = plan.get("goals") or []
    if not required and not goals:
        return None
    return {**plan, "direction": (required or goals)[0]["description"], "pending_results": required}


def invitation_checkpoint_due(engine, current) -> bool:
    """A budget requests an invitation; it never establishes a missing completion fact."""
    session = current.session
    recommended = engine.nodes[session.current_node_id].get("recommended_turns")
    if type(recommended) is int and session.node_turn_count >= recommended:
        return True
    rejected = 0
    for event in reversed(current.ledger_events):
        if (event.get("from_node_id") != session.current_node_id
                or event.get("to_node_id") != session.current_node_id
                or not event.get("delivery_fallback")):
            break
        rejected += 1
        if rejected >= 3:
            return True
    return False


def safe_pause_performance(dialogue_policy: str) -> dict:
    from utils.language_utils import get_global_language_full

    language = get_global_language_full()
    language = "zh-CN" if language == "zh" else language
    if language not in {"en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"}:
        language = "en"
    root = Path(__file__).resolve().parents[2]
    key = "safePauseAction" if dialogue_policy == "forbidden" else "safePause"
    text = json.loads((root / "static" / "locales" / (language + ".json")).read_text(encoding="utf-8"))["theater"][key]
    return {"performance": text, "suggested_inputs": []}
