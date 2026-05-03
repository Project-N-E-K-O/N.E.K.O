from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.sts2_autoplay.neko_commanding import NekoCommandingMixin


class NekoCommandingService(NekoCommandingMixin):
    def __init__(self) -> None:
        self._step_count = 7

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _first_present(self, *values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None

    def _combat_player_block(self, combat: dict[str, Any]) -> int:
        return self._safe_int(combat.get("player_block"), 0)

    def _enemy_intent_attack_total(self, enemy: dict[str, Any]) -> int:
        return self._safe_int(enemy.get("intent_attack"), 0)


@pytest.mark.unit
def test_review_snapshot_summary_uses_top_level_hp_fallback() -> None:
    service = NekoCommandingService()
    summary = service._build_review_snapshot_summary(
        {
            "screen": "combat",
            "floor": 2,
            "act": 1,
            "in_combat": True,
            "raw_state": {
                "current_hp": 33,
                "max_hp": 70,
                "combat": {"turn": 1, "player_block": 4},
                "run": {},
            },
        },
        timestamp=123.0,
    )

    assert summary["hp"] == 33
    assert summary["max_hp"] == 70


@pytest.mark.unit
def test_review_snapshot_summary_player_hp_takes_priority_over_raw_state() -> None:
    """combat.player.hp 必须优先于 raw_state.current_hp（_first_present 的参数顺序锁）。
    防止有人不小心把 _first_present 调用顺序调换喵。"""
    service = NekoCommandingService()
    summary = service._build_review_snapshot_summary(
        {
            "screen": "combat",
            "floor": 3,
            "act": 1,
            "in_combat": True,
            "raw_state": {
                "current_hp": 99,   # 应当被 player.hp 覆盖
                "max_hp": 100,      # 应当被 player.max_hp 覆盖
                "combat": {
                    "turn": 2,
                    "player_block": 5,
                    "player": {
                        "hp": 45,
                        "max_hp": 80,
                    },
                },
                "run": {},
            },
        },
        timestamp=456.0,
    )

    assert summary["hp"] == 45
    assert summary["max_hp"] == 80
