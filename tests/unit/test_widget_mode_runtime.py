from pathlib import Path

import pytest

from main_logic.widget_mode_runtime import WidgetModeCoordinator


def test_defaults_are_disabled_and_legacy_file_is_not_read(tmp_path: Path) -> None:
    (tmp_path / "widget_mode_settings.json").write_text(
        '{"enabled": true, "suppressed_until": 9999999999}',
        encoding="utf-8",
    )

    coordinator = WidgetModeCoordinator()

    assert coordinator.snapshot() == {"enabled": False, "stealthEnabled": False}


@pytest.mark.asyncio
async def test_enabled_state_switches_with_an_exact_minimal_snapshot() -> None:
    coordinator = WidgetModeCoordinator()

    assert await coordinator.set_enabled(True) == {
        "enabled": True,
        "stealthEnabled": False,
    }
    assert await coordinator.set_stealth_enabled(True) == {
        "enabled": True,
        "stealthEnabled": True,
    }
    assert await coordinator.set_enabled(False) == {
        "enabled": False,
        "stealthEnabled": True,
    }
    assert coordinator.snapshot() == {"enabled": False, "stealthEnabled": True}


@pytest.mark.asyncio
async def test_only_literal_true_enables_the_runtime() -> None:
    coordinator = WidgetModeCoordinator()

    assert await coordinator.set_enabled(1) == {
        "enabled": False,
        "stealthEnabled": False,
    }
    assert await coordinator.set_enabled("true") == {
        "enabled": False,
        "stealthEnabled": False,
    }
    assert await coordinator.set_stealth_enabled(1) == {
        "enabled": False,
        "stealthEnabled": False,
    }
