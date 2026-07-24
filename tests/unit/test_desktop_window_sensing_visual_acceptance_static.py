from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUTO_GOODBYE = PROJECT_ROOT / "static" / "app" / "app-auto-goodbye.js"


def test_temporary_desktop_window_sensing_acceptance_uses_real_cat_lifecycle():
    source = APP_AUTO_GOODBYE.read_text(encoding="utf-8")
    begin_marker = "// TEMPORARY desktop-window sensing visual acceptance: BEGIN."
    end_marker = "// TEMPORARY desktop-window sensing visual acceptance: END."
    temporary_block = source.split(begin_marker, 1)[1].split(end_marker, 1)[0]

    cat_visible_handler = source.split(
        "window.addEventListener('neko:idle-return-ball-state', (event) => {",
        1,
    )[1].split(
        "window.addEventListener('live2d-goodbye-click', (event) => {",
        1,
    )[0]
    return_handler = source.split("const handleReturnCommit = (event) => {", 1)[1].split(
        "const handleReturnComplete = (event) => {",
        1,
    )[0]

    assert "window.nekoDesktopWindowSensing" in temporary_block
    assert "operation = sensing.start(catScreenRect);" in temporary_block
    assert "Promise.resolve(sensing.stop(sessionId))" in temporary_block
    assert "desktopWindowSensingAcceptanceGeneration" in temporary_block
    assert "setTimeout" not in temporary_block
    assert "setInterval" not in temporary_block
    assert "detail.visible !== true" in cat_visible_handler
    assert "startDesktopWindowSensingAcceptance(detail.screenRect);" in cat_visible_handler
    assert "stopDesktopWindowSensingAcceptance();" in return_handler
