from __future__ import annotations

import importlib.util
import sys
from types import ModuleType
from pathlib import Path


def _load_mode_manager() -> ModuleType:
    plugin_root = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    for package_name, package_path in (
        ("plugin", plugin_root.parents[1]),
        ("plugin.plugins", plugin_root.parents[0]),
        ("plugin.plugins.study_companion", plugin_root),
    ):
        package = sys.modules.get(package_name)
        if package is None:
            package = ModuleType(package_name)
            package.__path__ = [str(package_path)]  # type: ignore[attr-defined]
            sys.modules[package_name] = package

    constants_name = "plugin.plugins.study_companion.constants"
    if constants_name not in sys.modules:
        constants_spec = importlib.util.spec_from_file_location(constants_name, plugin_root / "constants.py")
        assert constants_spec is not None
        assert constants_spec.loader is not None
        constants_module = importlib.util.module_from_spec(constants_spec)
        sys.modules[constants_name] = constants_module
        constants_spec.loader.exec_module(constants_module)

    spec = importlib.util.spec_from_file_location("plugin.plugins.study_companion.mode_manager", plugin_root / "mode_manager.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_mode_intent_ignores_learning_text_with_mode_words() -> None:
    mode_manager = _load_mode_manager()

    for text in (
        "a discussion of mitochondria",
        "teaching strategies in biology",
        "companion matrix eigenvalues",
        "photosynthesis companion enzymes",
    ):
        intent = mode_manager.handle_user_intent(text, language="en")
        assert intent["matched"] is False
        assert intent["remaining_text"] == text


def test_mode_intent_still_accepts_explicit_switch_phrases() -> None:
    mode_manager = _load_mode_manager()

    teaching = mode_manager.handle_user_intent("teaching mode photosynthesis", language="en")
    assert teaching["mode"] == mode_manager.MODE_TEACHING
    assert teaching["remaining_text"] == "photosynthesis"

    discussion = mode_manager.handle_user_intent("switch to discussion mode mitochondria", language="en")
    assert discussion["mode"] == mode_manager.MODE_INTERACTIVE
    assert discussion["remaining_text"] == "mitochondria"

    companion = mode_manager.handle_user_intent("switch to companion mode", language="en")
    assert companion["mode"] == mode_manager.MODE_COMPANION
    assert companion["pure_switch"] is True

    direct = mode_manager.handle_user_intent("教我微分", language="zh-CN")
    assert direct["mode"] == mode_manager.MODE_TEACHING
    assert direct["remaining_text"] == "微分"

    cross_mode = mode_manager.handle_user_intent("教我互动模式 光合作用", language="zh-CN")
    assert cross_mode["mode"] == mode_manager.MODE_INTERACTIVE
    assert cross_mode["keyword"] == "互动模式"


def test_failed_rapid_mode_switch_attempts_count_toward_lock() -> None:
    mode_manager = _load_mode_manager()
    manager = mode_manager.ModeManager(current_mode=mode_manager.MODE_COMPANION, mode_started_at=1000.0)

    first = manager.switch_to(mode_manager.MODE_TEACHING, "unit", now=1005.0)
    second = manager.switch_to(mode_manager.MODE_TEACHING, "unit", now=1010.0)
    third = manager.switch_to(mode_manager.MODE_TEACHING, "unit", now=1015.0)

    assert first["changed"] is False
    assert first["lock_reason"] == "minimum_dwell"
    assert len(first["checkpoint"]["recent_mode_switches"]) == 1
    assert second["changed"] is False
    assert second["lock_reason"] == "minimum_dwell"
    assert len(second["checkpoint"]["recent_mode_switches"]) == 2
    assert third["changed"] is False
    assert third["lock_reason"] == "mode_lock"
    assert third["lock_until"] > 1015.0
    assert manager.mode_lock_until == third["lock_until"]
