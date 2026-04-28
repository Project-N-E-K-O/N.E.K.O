from __future__ import annotations

import importlib.util
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STS2_DIR = PROJECT_ROOT / "plugin" / "plugins" / "sts2_autoplay"


def load_service_class():
    package_name = "sts2_autoplay_live_commentary_pkg"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(STS2_DIR)]
        sys.modules[package_name] = package
    module_name = f"{package_name}.service"
    spec = importlib.util.spec_from_file_location(module_name, STS2_DIR / "service.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.STS2AutoplayService


STS2AutoplayService = load_service_class()


class DummyLogger:
    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        pass


def make_service(**cfg: Any) -> STS2AutoplayService:
    service = STS2AutoplayService(DummyLogger(), lambda status: None)
    service._cfg = {
        "neko_commentary_enabled": True,
        "neko_commentary_probability": 1.0,
        "neko_commentary_min_interval_seconds": 4,
        "neko_critical_commentary_always": True,
        "neko_desperate_hp_threshold": 0.2,
        "neko_auto_low_hp_threshold": 0.3,
        "character_strategy": "defect",
        **cfg,
    }
    return service


def base_report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "step": 1,
        "screen": "combat",
        "floor": 3,
        "act": 1,
        "player_hp": 60,
        "max_hp": 80,
        "in_combat": True,
    }
    report.update(overrides)
    return report


def build_commentary(service: STS2AutoplayService, *, report: dict[str, Any] | None = None, tactical: dict[str, Any] | None = None, action: str = "play_card Strike") -> dict[str, Any]:
    return service._build_neko_live_commentary(
        report=report or base_report(),
        hand_names=["打击", "防御", "电击"],
        enemies_str="史莱姆 12/20 attack8",
        chosen_action=action,
        decision_reason="测试理由",
        tactical_brief=tactical or {"atk": 0, "need_block": 0, "lethal": False, "def": False},
    )


@pytest.mark.unit
def test_live_commentary_low_hp_uses_high_urgency_and_strategy_style() -> None:
    service = make_service(character_strategy="ironclad")

    commentary = build_commentary(service, report=base_report(player_hp=20, max_hp=80))

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "low_hp"
    assert commentary["urgency"] == "high"
    assert commentary["tone"] == "稳健"
    assert commentary["character_strategy"] == "ironclad"
    assert any(token in commentary["text"] for token in {"20/80", "血量", "少掉血"})
    assert any(token in commentary["text"] for token in {"稳住节奏", "血量", "少掉血"})


@pytest.mark.unit
def test_live_commentary_lethal_opportunity_speaks() -> None:
    service = make_service()

    commentary = build_commentary(service, tactical={"atk": 0, "need_block": 0, "lethal": True, "def": False})

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "lethal"
    assert commentary["urgency"] == "high"
    assert "斩杀" in commentary["text"] or "收尾" in commentary["text"]


@pytest.mark.unit
def test_live_commentary_high_incoming_attack_speaks_with_high_priority() -> None:
    service = make_service()

    commentary = build_commentary(service, tactical={"atk": 24, "need_block": 12, "lethal": False, "def": True})

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "incoming_attack"
    assert commentary["urgency"] == "high"
    assert commentary["priority"] == 7
    assert "24" in commentary["text"]
    assert "12" in commentary["text"]


@pytest.mark.unit
def test_live_commentary_normal_combat_uses_hand_and_action_hint() -> None:
    service = make_service(neko_commentary_min_interval_seconds=0)

    commentary = build_commentary(service)

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "combat"
    assert commentary["urgency"] == "low"
    assert "打击" in commentary["text"]
    assert commentary["action_hint"] == "打出关键牌"


@pytest.mark.unit
def test_live_commentary_reward_screen() -> None:
    service = make_service(neko_commentary_min_interval_seconds=0)

    commentary = build_commentary(service, report=base_report(screen="card_reward", in_combat=False))

    assert commentary["should_speak"] is True
    assert commentary["scene"] == "reward"
    assert commentary["mood"] == "开心"
    assert "奖励" in commentary["text"] or "战利品" in commentary["text"]


@pytest.mark.unit
def test_live_commentary_throttles_repeated_low_urgency_scene() -> None:
    service = make_service(neko_commentary_probability=1.0, neko_commentary_min_interval_seconds=60)
    service._last_neko_commentary_at = time.time()
    service._last_neko_commentary_scene = "combat"

    commentary = build_commentary(service)

    assert commentary["should_speak"] is False
    assert commentary["scene"] == "combat"
    assert commentary["text"] == ""
    assert commentary["tts"] is False


@pytest.mark.unit
def test_live_commentary_event_scenes_for_combat_end_key_relic_and_route_chosen() -> None:
    service = make_service(neko_commentary_min_interval_seconds=0)
    service._last_neko_commentary_scene = "combat"

    combat_end = build_commentary(service, report=base_report(screen="reward", in_combat=False, floor=5))
    assert combat_end["scene"] == "combat_end"
    assert "战斗结束" in combat_end["text"] or "这一场" in combat_end["text"]

    relic = build_commentary(service, report=base_report(screen="treasure", in_combat=False, floor=6), action="choose_treasure_relic")
    assert relic["scene"] == "key_relic"
    assert "遗物" in relic["text"]

    route = build_commentary(service, report=base_report(screen="map", in_combat=False, floor=7), action="choose_map_node")
    assert route["scene"] == "route_chosen"
    assert "路线" in route["text"]
