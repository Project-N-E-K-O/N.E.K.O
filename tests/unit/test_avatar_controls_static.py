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


def test_desktop_avatar_toolbar_exposes_screen_share_quick_control_for_every_renderer():
    state_source = _read(
        "static/avatar/avatar-ui-buttons/methods-state-and-cleanup.js"
    )
    setup_source = _read("static/avatar/avatar-ui-buttons/methods-setup.js")
    screen_source = _read("static/app/app-screen.js")
    bootstrap_source = _read("static/app/app-ui/bootstrap-goodbye-and-toasts.js")

    assert "ManagerPrototype.createScreenShareQuickButton = function(btnWrapper)" in state_source
    assert "ManagerPrototype.createVoiceSessionQuickControls = function(btnWrapper)" in state_source
    assert "createVoiceSessionQuickControlSlot(screenShareBtn)" in state_source
    assert "createVoiceSessionQuickControlSlot(muteBtn)" in state_source
    assert "`${opts.buttonClassPrefix} ${prefix}-screen-share-quick-btn neko-screen-share-quick-btn`" in state_source
    assert "screenShareBtn.setAttribute('role', 'button');" in state_source
    assert "screenShareBtn.setAttribute('aria-pressed'" in state_source
    assert "!event.repeat && (event.key === 'Enter' || event.key === ' ')" in state_source
    assert "window.toggleScreenShare();" in state_source
    assert "new MutationObserver" in state_source
    assert "screenStateObserver.disconnect();" in state_source
    assert "previousQuickButton.cleanup();" in state_source
    assert "screenShareBtn.remove();" in state_source
    assert "nekoScreenShareQuickHint" not in state_source
    assert "nekoScreenShareQuickBreathe" not in state_source
    assert "conic-gradient(" not in state_source
    assert "radial-gradient(" not in state_source
    assert "linear-gradient(145deg" not in state_source
    assert "? '#44b7fe'" in state_source
    assert "screenShareActive ? '#fff' : '#4a90d9'" in state_source
    assert "const visible = voiceSessionActive || screenShareActive;" in state_source
    assert "setVoiceSessionQuickButtonVisible(screenShareBtn, visible, 45);" in state_source
    assert "setVoiceSessionQuickButtonVisible(muteBtn, Boolean(visible));" in state_source
    assert "muteButtonData.updateVisibility(active);" in state_source
    visibility_animation = state_source.split(
        "const setVoiceSessionQuickButtonVisible =", 1
    )[1].split("ManagerPrototype.createMicMuteButton", 1)[0]
    assert "slot.animate([" in visibility_animation
    assert "{ height: '0px', flexBasis: '0px' }" in visibility_animation
    assert "{ height: '22px', flexBasis: '22px' }" in visibility_animation
    assert "button.animate" not in visibility_animation
    assert "opacity:" not in visibility_animation
    assert "transform:" not in visibility_animation
    assert "_nekoQuickControlVisibilityGeneration" in visibility_animation
    assert "animation.finished.then" in visibility_animation
    assert "slot.style.overflow = 'visible';" in visibility_animation
    assert "duration: 220" in state_source
    assert "fill: 'backwards'" in state_source
    assert "prefers-reduced-motion: reduce" in state_source
    quick_button_styles = state_source.split("Object.assign(screenShareBtn.style, {", 1)[1].split(
        "let screenShareActive = false;", 1
    )[0]
    assert "display: 'none'" in quick_button_styles
    assert "transform:" not in quick_button_styles
    assert "transformOrigin" not in quick_button_styles
    assert "saturate(180%) blur(20px)" in quick_button_styles
    mute_button_styles = state_source.split("Object.assign(muteBtn.style, {", 1)[1].split(
        "const stopMuteEvent", 1
    )[0]
    assert "transform:" not in mute_button_styles
    assert "transition: 'all" not in mute_button_styles
    assert "saturate(180%) blur(20px)" in mute_button_styles
    assert "muteSvg.style.transform = 'scale(1.08)'" in state_source
    assert "monitorSvg.style.transform = 'scale(1.08)'" in state_source
    assert "if (slot) slot.remove();" in state_source
    assert "#8a5ce8" not in state_source
    assert "linear-gradient(135deg, #4a90d9" not in state_source
    assert "screenShareBtn.setAttribute('data-i18n-aria', titleKey);" in state_source

    rebuild_cleanup = setup_source.index("Object.values(this._floatingButtons).forEach")
    rebuild_reset = setup_source.index("this._floatingButtons = {};")
    old_dom_removal = setup_source.index("document.querySelectorAll(`#${options.containerElementId}")
    assert rebuild_cleanup < rebuild_reset < old_dom_removal

    renderer_sources = [
        _read("static/live2d/live2d-ui-buttons.js"),
        _read("static/vrm/vrm-ui-buttons.js"),
        _read("static/mmd/mmd-ui-buttons.js"),
        _read("static/pngtuber-core.js"),
    ]
    for source in renderer_sources:
        assert "this.createVoiceSessionQuickControls(btnWrapper)" in source

    for source in (screen_source, bootstrap_source):
        assert "window.pngtuberManager" in source
        assert "['screen-share-quick']" in source


def test_live2d_click_actions_are_not_dispatched_by_the_generic_listener_twice():
    source = _read("static/live2d/live2d-ui-buttons.js")

    assert "window.dispatchEvent(new CustomEvent('live2d-social-click'))" in source
    assert "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'))" in source
    assert "} else if (config.id !== 'social' && config.id !== 'goodbye') {" in source
