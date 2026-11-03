"""Click shake feedback for Live2D / VRM / MMD models and its animation-settings toggle."""

import json
import re
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOUCH_GESTURES = "/static/avatar/avatar-touch-gestures.js"
CLICK_SHAKE = "/static/avatar/avatar-click-shake.js"


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.unit
def test_model_click_shake_node_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for click shake runtime tests")
    suite = PROJECT_ROOT / "tests" / "frontend" / "model_click_shake.test.cjs"
    result = run_node_script(
        node,
        f"require({json.dumps(str(suite))});",
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
def test_every_model_page_loads_click_shake_next_to_touch_gestures():
    # Every page/loader that wires model interaction must also ship the shake helper,
    # otherwise the optional-chained call silently degrades to no feedback.
    sources = [
        *sorted((PROJECT_ROOT / "templates").glob("*.html")),
        PROJECT_ROOT / "static/js/character_card_manager/model-previews.js",
        PROJECT_ROOT / "static/js/model_manager/runtime-loaders.js",
        PROJECT_ROOT / "static/vrm/vrm-init.js",
        PROJECT_ROOT / "static/mmd/mmd-init.js",
    ]
    checked = 0
    for path in sources:
        text = path.read_text(encoding="utf-8")
        touch_count = text.count(TOUCH_GESTURES)
        if not touch_count:
            continue
        checked += 1
        assert text.count(CLICK_SHAKE) == touch_count, path.name
    assert checked >= 14


@pytest.mark.unit
def test_click_shake_toggle_lives_in_animation_settings_and_persists():
    popup = _read("static/avatar/avatar-ui-popup.js")
    settings = _read("static/app/app-settings.js")

    panel_start = popup.index("function createAnimationSettingsSidePanel(manager, prefix)")
    panel = popup[panel_start:popup.index("\nfunction ", panel_start + 1)]
    assert "settings.toggles.modelClickShake" in panel
    assert "window.modelClickShakeEnabled = enabled;" in panel
    assert "window.modelClickShakeEnabled !== false" in panel
    assert panel.index("trackingRow.appendChild(hoverFadeRow);") < panel.index(
        "trackingRow.appendChild(clickShakeRow);"
    ) < panel.index("trackingRow.appendChild(forgeDropEffectsRow);")

    shared_keys = re.search(r"const _SHARED_SETTINGS_KEYS = \[(.*?)\];", settings, re.S)
    assert shared_keys and "'modelClickShakeEnabled'" in shared_keys.group(1)
    assert "modelClickShakeEnabled: currentModelClickShake" in settings
    assert "window.modelClickShakeEnabled = settings.modelClickShakeEnabled;" in settings
    # Default on for first launch, parse errors and missing saved values.
    assert settings.count("window.modelClickShakeEnabled = true;") == 3

    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        toggles = json.loads(_read(f"static/locales/{locale}.json"))["settings"]["toggles"]
        assert toggles.get("modelClickShake"), locale
