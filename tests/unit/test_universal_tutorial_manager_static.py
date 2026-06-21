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
    assert "restoreTutorialLive2dDisplayState(reason = '', options = {})" in source
    assert "throw new Error('tutorial_yui_live2d_model_missing_after_load');" in source

    renderable_block = source.split(
        "    hasTutorialYuiLive2dRenderableModel(manager = window.live2dManager || null) {",
        1,
    )[1].split(
        "    async ensureTutorialYuiLive2dVisible(reason = '', options = {}) {",
        1,
    )[0]
    visible_block = source.split(
        "    async ensureTutorialYuiLive2dVisible(reason = '', options = {}) {",
        1,
    )[1].split(
        "    isLive2dModelLoadBusy() {",
        1,
    )[0]

    assert "const model = this.getTutorialLive2dCurrentModel(manager);" in renderable_block
    assert "isTutorialLive2dModelAttachedToStage(stage, model)" in source
    assert "isTutorialLive2dRendererViewReady(app, renderer)" in source
    assert "&& !model.destroyed" in renderable_block
    assert "&& internalModel.coreModel" in renderable_block
    assert "&& !stage.destroyed" in renderable_block
    assert "&& !renderer.destroyed" in renderable_block
    assert "&& this.isTutorialLive2dModelAttachedToStage(stage, model)" in renderable_block
    assert "&& this.isTutorialLive2dRendererViewReady(app, renderer)" in renderable_block
    assert "const activeByPath = this.isTutorialYuiLive2dActive();" in visible_block
    assert "if (activeByPath && this.hasTutorialYuiLive2dRenderableModel()) {" in visible_block
    assert "this.ensureTutorialLive2dRenderActive('ensure-visible-active-yui', {" in visible_block
    assert "deferRevealPrepared" in visible_block
    assert "const placementReady = await this.applyTutorialLive2dViewportPlacement();" in visible_block
    assert "if (placementReady) {" in visible_block
    assert "YUI 临时模型路径已激活但视觉对象不可用" in visible_block
    assert "YUI 临时模型需要重新加载以恢复视觉对象" in visible_block
    assert "&& this.hasTutorialYuiLive2dRenderableModel()" in visible_block
    assert "&& placementReady === true;" in visible_block

    restore_block = source.split(
        "    restoreTutorialLive2dDisplayState(reason = '', options = {}) {",
        1,
    )[1].split(
        "    revealTutorialLive2dPrepared() {",
        1,
    )[0]
    assert "document.body.classList.remove('yui-guide-return-petal-fade');" in restore_block
    assert "document.body.style.removeProperty('--yui-guide-return-avatar-opacity');" in restore_block
    assert "const preservePreparingOpacity = options && options.preservePreparingOpacity === true;" in restore_block
    assert "if (!preservePreparingOpacity) {" in restore_block
    assert "live2dContainer.style.removeProperty('opacity');" in restore_block
    assert "live2dContainer.style.setProperty('opacity', '1', 'important');" in restore_block
    assert "live2dCanvas.style.removeProperty('opacity');" in restore_block
    assert "live2dCanvas.style.setProperty('opacity', '1', 'important');" in restore_block


def test_tutorial_yui_teardown_clears_non_live2d_runtime_residue_before_replay():
    source = _read_manager()

    assert "async clearTutorialYuiLive2dRuntimeResidue(reason = '')" in source
    residue_block = source.split(
        "    async clearTutorialYuiLive2dRuntimeResidue(reason = '') {",
        1,
    )[1].split(
        "    snapshotAvatarFloatingModelInteractionState",
        1,
    )[0]
    teardown_block = source.split("    _teardownTutorialUI() {", 1)[1].split(
        "    hasSeenTutorial(",
        1,
    )[0]
    start_round_block = source.split(
        "    async startAvatarFloatingGuideRound(day, options = {}) {",
        1,
    )[1].split(
        "    async playAvatarFloatingRoundPrelude",
        1,
    )[0]
    placement_block = source.split(
        "    async applyTutorialLive2dViewportPlacement() {",
        1,
    )[1].split(
        "    ensureTutorialLive2dViewportPlacementWatcher() {",
        1,
    )[0]
    prelude_block = source.split(
        "    async playAvatarFloatingRoundPrelude(round, source, director) {",
        1,
    )[1].split(
        "    async checkAndStartTutorial() {",
        1,
    )[0]
    ensure_visible_block = source.split(
        "    async ensureTutorialYuiLive2dVisible(reason = '', options = {}) {",
        1,
    )[1].split(
        "    isLive2dModelLoadBusy() {",
        1,
    )[0]
    render_active_block = source.split(
        "    ensureTutorialLive2dRenderActive(reason = '', options = {}) {",
        1,
    )[1].split(
        "    getTutorialLive2dScreenBounds(manager, model) {",
        1,
    )[0]

    assert ".then(() => this.restoreTutorialAvatarOverride())" in teardown_block
    assert ".then(() => this.clearTutorialYuiLive2dRuntimeResidue('tutorial-avatar-restored'))" in teardown_block
    assert "this.isCurrentRuntimeModelLive2d()" in residue_block
    assert "await manager.removeModel({ skipCloseWindows: true });" in residue_block
    assert "this.clearTutorialLive2dManagerMetadata(manager, staleModel);" in residue_block
    assert "manager._lastLoadedModelPath = null;" in source
    assert "manager.modelRootPath = null;" in source
    assert "manager.modelName = null;" in source
    assert "manager.pauseRendering();" in residue_block
    assert "manager.pixi_app.renderer.clear();" in residue_block
    assert "hideTutorialLive2dRuntimeSurfaceAfterResidueClear()" in source
    assert "await this.waitForTutorialTeardownSettled('avatar-floating-guide-start');" in start_round_block
    assert "async waitForTutorialTeardownSettled(reason = '')" in source
    assert "if (!this.hasTutorialYuiLive2dRenderableModel(manager)) {" in placement_block
    assert "deferRevealPrepared: Number(round) === 1" in prelude_block
    assert "const deferRevealPrepared = options && options.deferRevealPrepared === true;" in ensure_visible_block
    assert "if (!deferRevealPrepared) {" in ensure_visible_block
    assert "deferRevealPrepared" in render_active_block
    assert "preservePreparingOpacity: deferRevealPrepared" in render_active_block


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
