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


def test_universal_tutorial_manager_releases_startup_greeting_without_manager_or_auto_round():
    source = _read_manager()

    assert "function dispatchStartupGreetingReleaseWithoutManager(reason, detail = {})" in source
    assert "window.__NEKO_STARTUP_GREETING_RELEASED__ = releaseDetail;" in source
    assert "window.dispatchEvent(new CustomEvent(STARTUP_GREETING_RELEASE_EVENT" in source
    assert "dispatchStartupGreetingReleaseWithoutManager('mobile-tutorial-disabled'" in source
    assert "viewportWidth: window.innerWidth" in source

    auto_round_block = source.split("this.maybeStartAvatarFloatingGuideAutoRound(1200).then((started) => {", 1)[1].split(
        "            });",
        1,
    )[0]
    assert "this.dispatchStartupGreetingRelease('no-avatar-floating-round');" in auto_round_block
    assert "}).catch((error) => {" in auto_round_block
    assert "this.dispatchStartupGreetingRelease('avatar-floating-auto-round-check-failed');" in auto_round_block


def test_universal_tutorial_manager_resets_and_delays_startup_greeting_release():
    source = _read_manager()

    assert "clearStartupGreetingRelease(reason = 'tutorial-started')" in source
    assert "delete window.__NEKO_STARTUP_GREETING_RELEASED__;" in source
    emit_block = source.split("    emitTutorialStarted(page = this.currentPage, source = this.currentTutorialStartSource) {", 1)[1].split(
        "    /**",
        1,
    )[0]
    assert "this.clearStartupGreetingRelease('tutorial-started');" in emit_block
    assert emit_block.index("this.clearStartupGreetingRelease('tutorial-started');") < emit_block.index(
        "window.dispatchEvent(new CustomEvent('neko:tutorial-started'"
    )

    end_block = source.split("    onTutorialEnd() {", 1)[1].split(
        "    restoreYuiGuideChatInputState",
        1,
    )[0]
    assert "const startupGreetingReleasePromise = Promise.resolve(teardownPromise).finally(() => {" in end_block
    assert "this.dispatchStartupGreetingRelease(startupGreetingReleaseReason, {" in end_block
    assert end_block.index("Promise.resolve(teardownPromise).finally") < end_block.index(
        "this.dispatchStartupGreetingRelease(startupGreetingReleaseReason"
    )
    assert "return startupGreetingReleasePromise;" in end_block


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
    assert "deferRevealPrepared: true" in prelude_block
    assert "const deferRevealPrepared = options && options.deferRevealPrepared === true;" in ensure_visible_block
    assert "if (!deferRevealPrepared) {" in ensure_visible_block
    assert "deferRevealPrepared" in render_active_block
    assert "preservePreparingOpacity: deferRevealPrepared" in render_active_block


def test_tutorial_live2d_preparing_hides_model_side_controls():
    repo_root = Path(__file__).resolve().parents[2]
    css_source = (repo_root / "static/css/yui-guide.css").read_text(encoding="utf-8")
    app_ui_source = (repo_root / "static/app-ui.js").read_text(encoding="utf-8")
    live2d_buttons_source = (repo_root / "static/live2d-ui-buttons.js").read_text(encoding="utf-8")
    manager_source = _read_manager()
    reload_controller_source = (repo_root / "static/tutorial/avatar/reload-controller.js").read_text(encoding="utf-8")

    assert "body.yui-guide-live2d-preparing #live2d-floating-buttons" in css_source
    assert "body.yui-guide-live2d-preparing #live2d-lock-icon" in css_source
    assert "body.yui-guide-live2d-preparing #live2d-return-button-container" in css_source

    assert "hideYuiGuideLive2DPreparingControls()" in app_ui_source
    restore_controls_block = app_ui_source.split("function restoreYuiGuideLive2DPreparingControls()", 1)[1].split(
        "function restoreLive2DDisplaySurface",
        1,
    )[0]
    assert "'live2d-floating-buttons'" in restore_controls_block
    assert "'live2d-lock-icon'" in restore_controls_block
    assert "'live2d-return-button-container'" not in restore_controls_block
    assert "if (!preserveYuiGuidePreparing && floatingButtons) {" in app_ui_source
    assert "if (!preserveYuiGuidePreparing && lockIcon) {" in app_ui_source

    assert "function isYuiGuideLive2DPreparing()" in live2d_buttons_source
    assert "if (isYuiGuideLive2DPreparing()) {" in live2d_buttons_source
    assert "hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer)" in live2d_buttons_source
    assert "buttonsContainer.style.setProperty('display', 'flex', 'important');" in live2d_buttons_source
    protection_timer_block = live2d_buttons_source.split(
        "this.tutorialProtectionTimer = setInterval(() => {",
        1,
    )[1].split("}, 300);", 1)[0]
    assert "if (isYuiGuideLive2DPreparing()) {" in protection_timer_block
    assert "hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer);" in protection_timer_block
    assert protection_timer_block.index(
        "if (isYuiGuideLive2DPreparing()) {"
    ) < protection_timer_block.index("const style = window.getComputedStyle(buttonsContainer);")

    assert "hideTutorialLive2dPreparingControls()" in manager_source
    assert "fadeOutCurrentModel: () => this.fadeOutCurrentTutorialSourceModel()" in manager_source
    assert "async fadeOutCurrentTutorialSourceModel()" in manager_source
    assert "const fadeOutMs = 900;" in manager_source
    assert "opacity 900ms ease-in-out" in manager_source
    assert "'live2d-floating-buttons'," in manager_source
    clear_prepare_block = manager_source.split("clearTutorialLive2dPreparingStyles() {", 1)[1].split(
        "restoreTutorialLive2dDisplayState",
        1,
    )[0]
    display_restore_block = clear_prepare_block.split("element.style.removeProperty('display');", 1)[0]
    assert "id === 'live2d-floating-buttons'" in display_restore_block
    assert "id === 'live2d-lock-icon'" in display_restore_block
    assert "id === 'live2d-return-button-container'" not in display_restore_block
    assert "if (preparing === true) {" in manager_source

    assert "this.fadeOutCurrentModel = normalizedOptions.fadeOutCurrentModel || noop;" in reload_controller_source
    assert "await Promise.resolve(this.fadeOutCurrentModel({" in reload_controller_source


def test_tutorial_live2d_preparing_does_not_use_intro_loading_mask():
    repo_root = Path(__file__).resolve().parents[2]
    css_source = (repo_root / "static/css/yui-guide.css").read_text(encoding="utf-8")
    manager_source = _read_manager()

    assert "YUI_GUIDE_LIVE2D_LOADING_MASK" not in manager_source
    assert "showTutorialLive2dPreparingMask" not in manager_source
    assert "hideTutorialLive2dPreparingMask" not in manager_source
    assert "cat_model_change_slow_" not in manager_source
    assert "yui-guide-live2d-loading" not in manager_source

    assert "#yui-guide-live2d-loading-mask" not in css_source
    assert "body.yui-guide-live2d-loading" not in css_source
    assert "cat_model_change_slow_" not in css_source


def test_home_tutorial_teardown_restores_chat_input_lock_before_early_return():
    source = _read_manager()

    teardown_prefix = source.split("    _teardownTutorialUI() {", 1)[1].split(
        "        if (this._teardownPromise) {",
        1,
    )[0]
    assert "this.restoreYuiGuideChatInputState(" in teardown_prefix
    assert "this.clearYuiGuideCompactChatFixedLayout(" in teardown_prefix

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


def test_avatar_floating_guide_lifecycle_toggles_compact_chat_fixed_layout_class():
    source = _read_manager()

    start_round_block = source.split("    async startAvatarFloatingGuideRound(day, options = {}) {", 1)[1].split(
        "            const director = this.ensureYuiGuideDirector();",
        1,
    )[0]
    restore_block = source.split("    restoreYuiGuideChatInputState(reason = 'tutorial-ended') {", 1)[1].split(
        "    _teardownTutorialUI() {",
        1,
    )[0]
    lifecycle_block = source.split("    clearAllTutorialLifecycles(reason = 'destroy') {", 1)[1].split(
        "    normalizeTutorialEndRawReason(reason) {",
        1,
    )[0]
    clear_method_block = source.split("    clearYuiGuideCompactChatFixedLayout(reason = 'tutorial-ended') {", 1)[1].split(
        "    restoreYuiGuideChatInputState(reason = 'tutorial-ended') {",
        1,
    )[0]

    assert "document.body.classList.add('yui-guide-compact-chat-fixed')" in start_round_block
    assert "this.syncYuiGuideCompactChatFixedLayout(true, 'avatar-floating-guide-start')" in start_round_block
    assert "this.clearYuiGuideCompactChatFixedLayout(restoreReason)" in restore_block
    assert "this.clearYuiGuideCompactChatFixedLayout(rawReason)" in lifecycle_block
    assert "document.body.classList.remove('yui-guide-compact-chat-fixed')" in clear_method_block
    assert "this.syncYuiGuideCompactChatFixedLayout(false, reason)" in clear_method_block
    assert "action: 'yui_guide_set_compact_chat_fixed_layout'" in source
    assert "window.nekoTutorialOverlay.relayToChat(message)" in source
