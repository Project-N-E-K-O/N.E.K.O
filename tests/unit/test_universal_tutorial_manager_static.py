from pathlib import Path


UNIVERSAL_TUTORIAL_MANAGER_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "tutorial/core/universal-manager.js"
)


def _read_manager() -> str:
    return UNIVERSAL_TUTORIAL_MANAGER_PATH.read_text(encoding="utf-8")


def test_universal_tutorial_manager_excludes_legacy_driver_tutorial_system():
    source = _read_manager()

    for obsolete in (
        "waitForDriver",
        "initDriver",
        "getDriverConfig",
        "recreateDriverWithI18n",
        "startTutorialSteps",
        "onStepChange",
        "getStepsForPage",
        "getModelManagerSteps",
        "getCharaManagerSteps",
        "blockNekoTutorialClickEvent",
        "blockTutorialPointerEvent",
        "driver-popover",
        "driver-overlay",
        "driver-highlight",
        "neko-tutorial-driver",
    ):
        assert obsolete not in source


def test_universal_tutorial_manager_starts_day1_through_yui_round_directly():
    source = _read_manager()
    start_block = source.split("    startTutorial() {", 1)[1].split(
        "    resetTutorialStartState() {",
        1,
    )[0]
    i18n_block = source.split("    startTutorialWhenI18nReady(delayMs = 0) {", 1)[1].split(
        "    shouldSkipAutomaticHomeTutorialStart() {",
        1,
    )[0]

    assert "this.startAvatarFloatingGuideRound(1, {" in start_block
    assert "this.startAvatarFloatingGuideRound(1, { source })" in i18n_block
    assert "this.startYuiGuideSceneSequence(sceneIds" not in source
    assert "getDirectYuiGuideSceneIdsForCurrentPage" not in source
    assert "getPendingYuiGuideResumeScene" not in source
    assert "notifyYuiGuideStepEnter" not in source
    assert "notifyYuiGuideStepLeave" not in source


def test_home_tutorial_teardown_restores_chat_input_lock_before_early_return():
    source = _read_manager()

    teardown_prefix = source.split("    _teardownTutorialUI() {", 1)[1].split(
        "        if (this._teardownPromise) {",
        1,
    )[0]
    assert "this.restoreYuiGuideChatInputState(" in teardown_prefix

    restore_block = source.split("    restoreYuiGuideChatInputState(reason = 'tutorial-ended') {", 1)[1].split(
        "    _teardownTutorialUI() {",
        1,
    )[0]
    assert "document.body.classList.remove('yui-guide-chat-buttons-disabled')" in restore_block
    assert "data-yui-guide-prev-readonly" in restore_block
    assert "data-yui-guide-prev-contenteditable" in restore_block
    assert "action: 'yui_guide_set_chat_buttons_disabled'" in restore_block
    assert "disabled: false" in restore_block
    assert "reactChatWindowHost" in restore_block
    assert "setHomeTutorialInteractionLocked(false" in restore_block
