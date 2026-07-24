from pathlib import Path
from tests.static_app_parts import read_js_parts


APP_CHARACTER_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-character.js"
APP_INTERPAGE_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-interpage"
APP_UI_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-ui"
INDEX_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "css" / "index.css"


def test_character_switch_resets_avatar_lock_after_successful_model_load():
    source = APP_CHARACTER_PATH.read_text(encoding="utf-8")

    assert "function resetAvatarLockForCharacterSwitch(modelType)" in source
    assert "window.vrmManager.core.setLocked(false)" in source
    assert "window.mmdManager.core.setLocked(false)" in source
    assert "window.live2dManager.setLocked(false, { updateFloatingButtons: !hiddenByModelManager })" in source
    assert "rehideMainUIIfModelManagerOwnsVisibility('character-switch-lock-reset')" in source

    vrm_load = source.index("await window.vrmManager.loadModel(modelUrl);")
    vrm_reset = source.index("resetAvatarLockForCharacterSwitch('vrm');")
    assert vrm_load < vrm_reset

    mmd_load = source.index("await window.mmdManager.loadModel(mmdModelUrl, { loadingSessionId: mmdLoadingSessionId });")
    mmd_reset = source.index("resetAvatarLockForCharacterSwitch('mmd');")
    assert mmd_load < mmd_reset

    live2d_load = source.index("await window.live2dManager.loadModel(modelConfig, {")
    live2d_reset = source.index("resetAvatarLockForCharacterSwitch('live2d');", live2d_load)
    assert live2d_load < live2d_reset

    fallback_load = source.index("await window.live2dManager.loadModel(defaultConfig, {")
    fallback_reset = source.index("resetAvatarLockForCharacterSwitch('live2d');", fallback_load)
    assert fallback_load < fallback_reset


def test_character_switch_start_event_runs_after_all_deduplication_guards():
    source = APP_CHARACTER_PATH.read_text(encoding="utf-8")
    block = source.split("async function handleCatgirlSwitch", 1)[1].split(
        "window.invalidatePendingMusicSearch();", 1
    )[0]
    event = block.index("neko:character-switch-start")

    assert block.index("if (S.isSwitchingCatgirl)") < event
    assert block.index("if (!newCatgirl)") < event
    assert block.index("if (newCatgirl === oldCatgirl)") < event
    assert block.index("window.lanlan_config.lanlan_name === newCatgirl") < event


def test_character_switch_clears_goodbye_state_only_after_commit():
    source = APP_CHARACTER_PATH.read_text(encoding="utf-8")

    assert "function clearGoodbyeStateForCharacterSwitch()" in source
    assert "window.hideAllNekoReturnBallContainers(reason)" in source
    assert "window.hideNekoReturnBallContainer(container, reason)" in source
    assert "container.removeAttribute('data-neko-return-visible')" in source
    assert "action: 'idle_return_ball_state'" in source
    assert "channel.postMessage(payload)" in source
    assert "manager._goodbyeClicked = false" in source
    assert "manager._isInReturnState = false" in source
    assert "window.__nekoGoodbyeSilentState = {" in source
    assert "action: 'goodbye_state'" in source
    assert "active: false" in source
    assert "window.appInterpage.postGoodbyeChatComposerHiddenState(false, reason)" in source
    assert "window.reactChatWindowHost.setGoodbyeComposerHidden(false, reason)" in source
    assert "react-chat-window:set-goodbye-composer-hidden" in source

    commit = source.index("switchHasCommitted = true;")
    clear = source.index("clearGoodbyeStateForCharacterSwitch();")
    toast = source.index("showStatusToast(window.t ? window.t('app.switchedCatgirl'")
    assert commit < clear < toast


def test_app_ui_exposes_shared_return_ball_hide_helper():
    source = read_js_parts(APP_UI_PATH)

    assert "function hideReturnBallContainer(container, reason = 'return-ball-hide')" in source
    assert "scheduleIdleReturnBallDesktopBridge(reason || 'return-ball-hide', container)" in source
    assert "window.hideNekoReturnBallContainer = hideReturnBallContainer" in source
    assert "window.hideAllNekoReturnBallContainers = function(reason = 'return-ball-hide')" in source
    assert "ensureMultiWindowReturnBallDrag(null)" in source


def test_character_switch_does_not_restore_full_container_pointer_events():
    character_source = APP_CHARACTER_PATH.read_text(encoding="utf-8")
    interpage_source = read_js_parts(APP_INTERPAGE_PATH)
    app_ui_source = read_js_parts(APP_UI_PATH)
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    live2d_css = css_source[
        css_source.index("#live2d-container {"):
        css_source.index("#live2d-container.minimized")
    ]
    assert "pointer-events: none;" in live2d_css
    assert "#live2d-canvas" in css_source

    for source in (character_source, interpage_source, app_ui_source):
        assert "live2dContainer.style.pointerEvents = 'auto';" not in source
        assert 'live2dContainer.style.pointerEvents = "auto";' not in source
        assert "live2dContainer2.style.pointerEvents = 'auto';" not in source
        assert "l2dContainer.style.pointerEvents = 'auto';" not in source
        assert "live2dContainer.style.setProperty('pointer-events', 'auto'" not in source

    assert "live2dContainer2.style.setProperty('pointer-events', 'none', 'important');" in interpage_source
    assert "live2dCanvas2.style.pointerEvents = 'auto';" in interpage_source
    assert "live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');" in app_ui_source


def test_character_model_switch_repeated_paths_keep_only_model_entities_interactive():
    source = APP_CHARACTER_PATH.read_text(encoding="utf-8")
    interpage_source = read_js_parts(APP_INTERPAGE_PATH)

    assert "window.pngtuberManager.image.style.pointerEvents = 'auto';" in source
    assert "live2dCanvas2.style.pointerEvents = 'auto';" in interpage_source
    assert "vrmCanvas.style.pointerEvents = 'auto';" in interpage_source
    assert "vrmContainer.style.removeProperty('pointer-events');" in interpage_source
    assert "mmdContainerShow.style.removeProperty('pointer-events');" in source

    for target in (
        "live2dContainer.style.pointerEvents = 'auto';",
        "live2dContainer2.style.pointerEvents = 'auto';",
        "pngtuberContainer.style.pointerEvents = 'auto';",
        "oldPngtuberContainer.style.pointerEvents = 'auto';",
    ):
        assert target not in source
        assert target not in interpage_source

    assert "oldPngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "clearGoodbyeStateForCharacterSwitch();" in source
