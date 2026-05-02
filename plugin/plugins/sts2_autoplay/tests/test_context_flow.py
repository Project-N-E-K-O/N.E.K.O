from __future__ import annotations

import asyncio
from typing import Any

import pytest

from plugin.plugins.sts2_autoplay.context_flow import ContextFlowMixin


class ContextFlowService(ContextFlowMixin):
    def __init__(self) -> None:
        self._cfg: dict[str, Any] = {}
        self.latest_context: dict[str, Any] | None = None

    async def _fetch_step_context(self, *, publish: bool = False, record_history: bool = False) -> dict[str, Any]:
        assert self.latest_context is not None
        return self.latest_context

    def _allowed_kwargs_for_action(self, action_type: str, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, list[int]]:
        if action_type != "play_card":
            return {}
        combat = context["snapshot"]["raw_state"]["combat"]
        card_indices = [card["index"] for card in combat["hand"] if card.get("playable")]
        target_indices = sorted({target for card in combat["hand"] for target in card.get("valid_target_indices", [])})
        return {"card_index": card_indices, **({"target_index": target_indices} if target_indices else {})}

    def _normalize_action_kwargs(self, action_type: str, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in raw.items() if key in {"card_index", "target_index"}}

    def _validate_play_card_target_combo(self, normalized_kwargs: dict[str, Any], context: dict[str, Any], decision: dict[str, Any]) -> bool:
        card_index = normalized_kwargs.get("card_index")
        combat = context["snapshot"]["raw_state"]["combat"]
        selected_card = next((card for card in combat["hand"] if card.get("index") == card_index and card.get("playable")), None)
        if selected_card is None:
            return False
        valid_targets = selected_card.get("valid_target_indices", [])
        return not valid_targets or normalized_kwargs.get("target_index") in valid_targets


def run(coro):
    return asyncio.run(coro)


@pytest.mark.unit
def test_action_interval_uses_default_when_config_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ContextFlowService()
    service._cfg = {"action_interval_seconds": "bad"}
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    run(service._await_action_interval())

    assert sleeps == [0.5]


@pytest.mark.unit
def test_stable_step_config_helpers_fallback_on_invalid_values() -> None:
    service = ContextFlowService()
    service._cfg = {
        "stable_state_attempts": "bad",
        "poll_interval_active_seconds": "bad",
        "post_action_settle_attempts": "bad",
        "post_action_delay_seconds": "bad",
    }

    assert service._safe_config_int("stable_state_attempts", 4) == 4
    assert service._safe_config_float("poll_interval_active_seconds", 1.0) == 1.0
    assert service._safe_config_int("post_action_settle_attempts", 6) == 6
    assert service._safe_config_float("post_action_delay_seconds", 0.5) == 0.5


@pytest.mark.unit
def test_revalidate_prepared_play_card_accepts_generic_action_template() -> None:
    service = ContextFlowService()
    action = {"type": "play_card", "raw": {"name": "play_card", "requires_index": True, "requires_target": False}}
    context = {
        "actions": [action],
        "signature": ("combat",),
        "snapshot": {
            "raw_state": {
                "combat": {
                    "hand": [
                        {"index": 1, "name": "打击", "playable": True, "valid_target_indices": []},
                        {"index": 2, "name": "防御+", "playable": True, "valid_target_indices": []},
                    ]
                }
            }
        },
    }
    service.latest_context = {**context, "signature": ("combat", "latest")}
    prepared = {
        "action": {**action, "raw": {**action["raw"], "card_index": 2}},
        "action_type": "play_card",
        "kwargs": {"card_index": 2},
        "fingerprint": ("play_card", None, None, 2, None, "play_card"),
        "kwargs_signature": (("card_index", 2),),
        "context_signature": context["signature"],
        "context": context,
    }

    revalidated = run(service._revalidate_prepared_action(prepared, context))

    assert revalidated is not None
    assert revalidated["action"] == action
    assert revalidated["kwargs"] == {"card_index": 2}
    assert revalidated["context_signature"] == ("combat", "latest")


@pytest.mark.unit
def test_revalidate_prepared_play_card_rejects_unplayable_prepared_card() -> None:
    service = ContextFlowService()
    action = {"type": "play_card", "raw": {"name": "play_card", "requires_index": True, "requires_target": False}}
    context = {
        "actions": [action],
        "signature": ("combat",),
        "snapshot": {
            "raw_state": {
                "combat": {
                    "hand": [
                        {"index": 1, "name": "打击", "playable": True, "valid_target_indices": []},
                        {"index": 2, "name": "防御+", "playable": False, "valid_target_indices": []},
                    ]
                }
            }
        },
    }
    service.latest_context = context
    prepared = {
        "action": {**action, "raw": {**action["raw"], "card_index": 2}},
        "action_type": "play_card",
        "kwargs": {"card_index": 2},
        "fingerprint": ("play_card", None, None, 2, None, "play_card"),
        "kwargs_signature": (("card_index", 2),),
        "context_signature": context["signature"],
        "context": context,
    }

    assert run(service._revalidate_prepared_action(prepared, context)) is None
