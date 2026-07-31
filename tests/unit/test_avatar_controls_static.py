import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_mobile_avatar_layout_keeps_screen_share_control_for_every_renderer():
    methods_source = _read("static/avatar/avatar-ui-buttons/methods-buttons.js")
    assert "id: 'screen'" in methods_source
    assert "titleKey: 'buttons.screenShare'" in methods_source
    assert "mobileOnly: true" in methods_source
    assert "btnWrapper.dataset.mobileOnly = 'true'" in methods_source
    assert "ManagerPrototype.syncResponsiveButtonVisibility" in methods_source
    assert "querySelectorAll('[data-mobile-only=\"true\"]')" in methods_source

    renderer_sources = [
        _read("static/live2d/live2d-ui-buttons.js"),
        _read("static/vrm/vrm-ui-buttons.js"),
        _read("static/mmd/mmd-ui-buttons.js"),
        _read("static/pngtuber-core.js"),
    ]
    for source in renderer_sources:
        assert "syncResponsiveButtonVisibility(buttonsContainer)" in source
        assert "if (config.mobileOnly" not in source
        assert "config.id === 'screen'" in source
    for source in renderer_sources[1:3]:
        assert "{ id: 'screen', mobileOnly: true }" in source
        assert ".filter(c => !(c.mobileOnly && !mobile))" in source


def test_mobile_screen_share_state_reconciles_after_capture_attempts():
    state_source = _read(
        "static/avatar/avatar-ui-buttons/methods-state-and-cleanup.js"
    )
    controls_source = _read("static/app/app-ui/surface-floating-controls.js")

    assert "const screenButton = document.getElementById('screenButton');" in state_source
    assert (
        "this.setButtonActive('screen', screenButton.classList.contains('active'));"
        in state_source
    )
    assert re.search(
        r"window\.addEventListener\('live2d-screen-toggle', async \(e\) => \{.*?"
        r"\} finally \{\s*"
        r"if \(typeof window\.syncFloatingScreenButtonState === 'function'\) \{\s*"
        r"window\.syncFloatingScreenButtonState\(isScreenSharingActive\(\)\);",
        controls_source,
        re.S,
    )


def test_live2d_click_actions_are_not_dispatched_by_the_generic_listener_twice():
    source = _read("static/live2d/live2d-ui-buttons.js")

    assert "window.dispatchEvent(new CustomEvent('live2d-social-click'))" in source
    assert "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'))" in source
    assert "} else if (config.id !== 'social' && config.id !== 'goodbye') {" in source
