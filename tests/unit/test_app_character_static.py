from pathlib import Path


APP_CHARACTER_PATH = Path(__file__).resolve().parents[2] / "static" / "app-character.js"


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


def test_character_switch_clears_goodbye_state_only_after_commit():
    source = APP_CHARACTER_PATH.read_text(encoding="utf-8")

    assert "function clearGoodbyeStateForCharacterSwitch()" in source
    assert "manager._goodbyeClicked = false" in source
    assert "manager._isInReturnState = false" in source
    assert "manager._returnButtonContainer.style.display = 'none'" in source
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
