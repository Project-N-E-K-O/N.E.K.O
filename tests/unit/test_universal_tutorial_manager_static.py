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

    assert "getHomeAvatarFloatingGuideStartRound(options = {})" in source
    assert "candidates.push(state.pendingRound, state.manualResetRound, 1);" in source
    assert "const round = this.getHomeAvatarFloatingGuideStartRound();" in start_block
    assert start_block.index("const round = this.getHomeAvatarFloatingGuideStartRound();") < start_block.index(
        "if (!round) {"
    )
    assert start_block.index("if (!round) {") < start_block.index(
        "this.snapshotAvatarFloatingModelInteractionState('tutorial-start');"
    )
    assert start_block.index("this.snapshotAvatarFloatingModelInteractionState('tutorial-start');") < start_block.index(
        "this.startAvatarFloatingGuideRound(round, {"
    )
    assert "this.startAvatarFloatingGuideRound(round, {" in start_block
    assert "const round = this.getHomeAvatarFloatingGuideStartRound();" in i18n_block
    assert "this.startAvatarFloatingGuideRound(round, { source })" in i18n_block
    assert "this.startAvatarFloatingGuideRound(1, {" not in source
    assert "this.startAvatarFloatingGuideRound(1, { source })" not in source
    assert "this.startYuiGuideSceneSequence(sceneIds" not in source
    assert "getDirectYuiGuideSceneIdsForCurrentPage" not in source
    assert "getPendingYuiGuideResumeScene" not in source
    assert "notifyYuiGuideStepEnter" not in source
    assert "notifyYuiGuideStepLeave" not in source


def test_tutorial_yui_visibility_does_not_trust_stale_live2d_path_without_model():
    source = _read_manager()

    assert "getTutorialLive2dCurrentModel(manager = window.live2dManager || null)" in source
    assert "hasTutorialYuiLive2dRenderableModel(manager = window.live2dManager || null)" in source
    assert "restoreTutorialLive2dDisplayState(reason = '')" in source
    assert "throw new Error('tutorial_yui_live2d_model_missing_after_load');" in source

    renderable_block = source.split(
        "    hasTutorialYuiLive2dRenderableModel(manager = window.live2dManager || null) {",
        1,
    )[1].split(
        "    async ensureTutorialYuiLive2dVisible(reason = '') {",
        1,
    )[0]
    visible_block = source.split(
        "    async ensureTutorialYuiLive2dVisible(reason = '') {",
        1,
    )[1].split(
        "    isLive2dModelLoadBusy() {",
        1,
    )[0]

    assert "const model = this.getTutorialLive2dCurrentModel(manager);" in renderable_block
    assert "return !!(manager && model && app && app.stage && app.renderer);" in renderable_block
    assert "const activeByPath = this.isTutorialYuiLive2dActive();" in visible_block
    assert "if (activeByPath && this.hasTutorialYuiLive2dRenderableModel()) {" in visible_block
    assert "this.ensureTutorialLive2dRenderActive('ensure-visible-active-yui');" in visible_block
    assert "const placementReady = await this.applyTutorialLive2dViewportPlacement();" in visible_block
    assert "if (placementReady) {" in visible_block
    assert "YUI 临时模型路径已激活但视觉对象不可用" in visible_block
    assert "YUI 临时模型需要重新加载以恢复视觉对象" in visible_block
    assert "&& this.hasTutorialYuiLive2dRenderableModel()" in visible_block
    assert "&& placementReady === true;" in visible_block

    restore_block = source.split(
        "    restoreTutorialLive2dDisplayState(reason = '') {",
        1,
    )[1].split(
        "    revealTutorialLive2dPrepared() {",
        1,
    )[0]
    assert "document.body.classList.remove('yui-guide-return-petal-fade');" in restore_block
    assert "document.body.style.removeProperty('--yui-guide-return-avatar-opacity');" in restore_block
    assert "live2dContainer.style.setProperty('opacity', '1', 'important');" in restore_block
    assert "live2dCanvas.style.setProperty('opacity', '1', 'important');" in restore_block


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
