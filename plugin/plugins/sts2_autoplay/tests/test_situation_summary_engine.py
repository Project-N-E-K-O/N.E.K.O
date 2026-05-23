from __future__ import annotations

from plugin.plugins.sts2_autoplay.situation_summary_engine import STS2SituationSummaryEngine


def test_compute_delta_and_render_text_for_combat_changes() -> None:
    engine = STS2SituationSummaryEngine()
    before = {
        "screen": "combat",
        "in_combat": True,
        "player": {"current_hp": 50, "block": 0, "energy": 3, "gold": 99},
        "hand": {"count": 5, "names": ["Strike", "Defend", "Zap"]},
        "enemies": {"count": 2, "total_hp": 60, "attack_total": 18},
    }
    after = {
        "screen": "combat",
        "in_combat": True,
        "player": {"current_hp": 44, "block": 8, "energy": 1, "gold": 99},
        "hand": {"count": 4, "names": ["Defend", "Zap", "Dualcast"]},
        "enemies": {"count": 2, "total_hp": 42, "attack_total": 10},
    }

    delta = engine.compute_delta(before, after, source="action_paired")

    assert delta["source"] == "action_paired"
    assert delta["player_changes"]["hp_delta"] == -6
    assert delta["player_changes"]["block_delta"] == 8
    assert delta["player_changes"]["energy_delta"] == -2
    assert delta["enemy_changes"]["enemy_total_hp_delta"] == -18
    assert "Dualcast" in delta["hand_changes"]["entered_cards"]
    assert "Strike" in delta["hand_changes"]["left_cards"]
    assert "玩家血量 -6" in delta["text"]
    assert "护盾 +8" in delta["text"]
    assert "敌方总血量 -18" in delta["text"]


def test_compute_delta_tracks_screen_transition_notable_event() -> None:
    engine = STS2SituationSummaryEngine()
    before = {
        "screen": "combat",
        "in_combat": True,
        "player": {"current_hp": 40, "block": 0, "energy": 0, "gold": 50},
        "hand": {"count": 0, "names": []},
        "enemies": {"count": 1, "total_hp": 0, "attack_total": 0},
    }
    after = {
        "screen": "reward",
        "in_combat": False,
        "player": {"current_hp": 40, "block": 0, "energy": 0, "gold": 50},
        "hand": {"count": 0, "names": []},
        "enemies": {"count": 0, "total_hp": 0, "attack_total": 0},
    }

    delta = engine.compute_delta(before, after, source="continuous_snapshot")

    assert delta["screen_change"] == {"from": "combat", "to": "reward"}
    assert "combat_ended" in delta["notable_events"]
    assert "画面从 combat 切换到 reward" in delta["text"]
