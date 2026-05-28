from pathlib import Path


UI_BUTTON_FILES = (
    Path("static/live2d-ui-buttons.js"),
    Path("static/vrm-ui-buttons.js"),
    Path("static/mmd-ui-buttons.js"),
)


def test_voice_control_main_button_opens_existing_popup_trigger_before_toggle():
    for path in UI_BUTTON_FILES:
        source = path.read_text(encoding="utf-8")

        popup_guard = "config.id === 'mic'"
        popup_trigger_click = "buttonData.triggerButton.click();"
        session_toggle = "new CustomEvent(`live2d-${config.id}-toggle`"

        assert popup_guard in source, path
        assert "config.hasPopup && config.separatePopupTrigger" in source, path
        assert popup_trigger_click in source, path
        assert session_toggle in source, path
        assert source.index(popup_trigger_click) < source.index(session_toggle), path
