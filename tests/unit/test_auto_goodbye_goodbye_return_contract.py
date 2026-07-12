from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUTO_GOODBYE_PATH = PROJECT_ROOT / "static" / "app" / "app-auto-goodbye.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui.js"
APP_BUTTONS_PATH = PROJECT_ROOT / "static" / "app" / "app-buttons.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_auto_goodbye_reuses_existing_goodbye_base_chain():
    auto_source = _read(APP_AUTO_GOODBYE_PATH)
    ui_source = _read(APP_UI_PATH)
    buttons_source = _read(APP_BUTTONS_PATH)

    assert "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'" in auto_source
    assert "window.__nekoGoodbyeSilentState" in auto_source
    assert "action: 'start_session'" not in auto_source
    assert "resetSessionButton.click();" in ui_source
    assert "function playModelGoodbyeExit(container, rect)" in ui_source
    assert "playModelGoodbyeExit(live2dContainerForGoodbye, savedGoodbyeRect)" in ui_source
    assert "playModelGoodbyeExit(mmdContainer, savedGoodbyeRect)" in ui_source
    assert "playModelGoodbyeExit(vrmContainer, savedGoodbyeRect)" in ui_source
    assert "container.classList.add('minimized');" in ui_source
    assert "resetSessionButton.disabled = false;\n                resetSessionButton.click();" in ui_source

    reset_block = _between(
        buttons_source,
        "resetSessionButton.addEventListener('click', function () {",
        "// ----------------------------------------------------------------\n        // Return session button click",
    )
    assert "window.isNekoGoodbyeModeActive()" in reset_block
    assert "action: 'end_session'" in reset_block
    assert "goodbye_active: !!isGoodbyeMode" in reset_block
    assert "reason: isGoodbyeMode ? 'goodbye' : 'manual'" in reset_block
    assert "window.cancelPendingSessionStart('Voice start cancelled by goodbye');" in reset_block
    assert "S.voiceStartPending = false;" in reset_block
    assert "window.isMicStarting = false;" in reset_block
    assert "textInputArea.classList.add('hidden');" in reset_block
    assert "window.syncVoiceChatComposerHidden(true);" in reset_block
    assert "returnSessionButton.disabled = false;" in reset_block
    assert "window.stopProactiveChatSchedule();" in reset_block


def test_return_ball_keeps_handle_return_click_semantics():
    ui_source = _read(APP_UI_PATH)
    buttons_source = _read(APP_BUTTONS_PATH)

    handle_return_block = _between(
        ui_source,
        "const handleReturnClick = async (event) => {",
        "window.addEventListener('live2d-return-click', handleReturnClick);",
    )
    assert "start_session" not in handle_return_block
    assert "returnSessionButton.click" not in handle_return_block
    assert "window.live2dManager._goodbyeClicked = false;" in handle_return_block
    assert "hideReturnBallContainer(live2dReturnButtonContainer);" in handle_return_block
    assert "hideReturnBallContainer(vrmReturnButtonContainer);" in handle_return_block
    assert "hideReturnBallContainer(mmdReturnButtonContainer);" in handle_return_block
    assert "syncReactChatWindowGoodbyeMinimized" not in handle_return_block

    return_session_block = _between(
        buttons_source,
        "returnSessionButton.addEventListener('click', async function () {",
        "function markFirstUserInputForAchievement() {",
    )
    assert "action: 'start_session'" in return_session_block
    assert "action: 'goodbye_state'" in return_session_block
    assert "active: false" in return_session_block


def test_return_clears_all_model_lock_state_instead_of_replaying_a_snapshot():
    ui_source = _read(APP_UI_PATH)
    handle_return_block = _between(
        ui_source,
        "const handleReturnClick = async (event) => {",
        "window.addEventListener('live2d-return-click', handleReturnClick);",
    )

    assert "_savedLockState" not in ui_source
    assert "window.live2dManager.setLocked(false, { updateFloatingButtons: false });" in handle_return_block
    assert "window.vrmManager.core.setLocked(false);" in handle_return_block
    assert "window.mmdManager.core.setLocked(false);" in handle_return_block
    assert "window.pngtuberManager.setLocked(false, { updateFloatingButtons: false });" in handle_return_block
