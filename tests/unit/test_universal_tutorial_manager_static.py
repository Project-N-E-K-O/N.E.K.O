import re
from pathlib import Path
from tests.static_app_parts import read_js_parts


UNIVERSAL_TUTORIAL_MANAGER_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "tutorial/core/universal-manager.js"
)
PAGE_TUTORIAL_MANAGER_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "tutorial/core/page-tutorial-manager.js"
)
DRIVER_PATH = Path(__file__).resolve().parents[2] / "static" / "libs/driver.min.js"
DRIVER_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "libs/driver.min.css"
TUTORIAL_STYLES_PATH = Path(__file__).resolve().parents[2] / "static" / "css/tutorial-styles.css"
ROUND_PRELUDE_CONTROLLER_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "tutorial/core/round-prelude-controller.js"
)
YUI_GUIDE_COMMON_PATH = Path(__file__).resolve().parents[2] / "static" / "tutorial/yui-guide/common.js"
COMMON_UI_PATH = Path(__file__).resolve().parents[2] / "static" / "common_ui.js"
APP_AUDIO_CAPTURE_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-audio-capture.js"
CHAT_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "chat.html"
APP_PROMPT_PATH = Path(__file__).resolve().parents[2] / "static" / "tutorial/core/app-prompt.js"
AVATAR_FLOATING_BOOT_PREDICTOR_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "tutorial/core/avatar-floating-boot-predictor.js"
)
FLOATING_GUIDE_RESET_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "tutorial/avatar/floating-guide-reset.js"
)
CHARACTER_PERSONALITY_ONBOARDING_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "js/character_personality_onboarding.js"
)
APP_INTERPAGE_PARTS_PATH = Path(__file__).resolve().parents[2] / "static" / "app" / "app-interpage"


def _read_manager() -> str:
    return UNIVERSAL_TUTORIAL_MANAGER_PATH.read_text(encoding="utf-8")


def _read_page_manager() -> str:
    return PAGE_TUTORIAL_MANAGER_PATH.read_text(encoding="utf-8")


def _read_driver() -> str:
    return DRIVER_PATH.read_text(encoding="utf-8")


def _read_driver_css() -> str:
    return DRIVER_CSS_PATH.read_text(encoding="utf-8")


def _read_tutorial_styles() -> str:
    return TUTORIAL_STYLES_PATH.read_text(encoding="utf-8")


def _read_round_prelude() -> str:
    return ROUND_PRELUDE_CONTROLLER_PATH.read_text(encoding="utf-8")


def _read_yui_guide_common() -> str:
    return YUI_GUIDE_COMMON_PATH.read_text(encoding="utf-8")


def _read_common_ui() -> str:
    return COMMON_UI_PATH.read_text(encoding="utf-8")


def _read_app_audio_capture() -> str:
    return APP_AUDIO_CAPTURE_PATH.read_text(encoding="utf-8")


def _read_chat_template() -> str:
    return CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")


def _read_app_prompt() -> str:
    return APP_PROMPT_PATH.read_text(encoding="utf-8")


def _read_avatar_floating_boot_predictor() -> str:
    return AVATAR_FLOATING_BOOT_PREDICTOR_PATH.read_text(encoding="utf-8")


def _read_floating_guide_reset() -> str:
    return FLOATING_GUIDE_RESET_PATH.read_text(encoding="utf-8")


def _read_character_personality_onboarding() -> str:
    return CHARACTER_PERSONALITY_ONBOARDING_PATH.read_text(encoding="utf-8")


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


def test_home_tutorial_runtime_no_longer_uses_legacy_home_storage_key():
    for source in (
        _read_manager(),
        _read_app_prompt(),
        _read_avatar_floating_boot_predictor(),
        _read_floating_guide_reset(),
        _read_character_personality_onboarding(),
    ):
        assert "'neko_tutorial_home'" not in source
        assert '"neko_tutorial_home"' not in source


def test_non_home_page_tutorials_are_restored_in_separate_driver_runtime():
    universal_source = _read_manager()
    page_source = _read_page_manager()

    page_tutorial_page_keys = (
        "model_manager",
        "parameter_editor",
        "emotion_manager",
        "chara_manager",
        "settings",
        "voice_clone",
        "memory_browser",
        "steam_workshop",
    )
    universal_storage_page_keys = page_tutorial_page_keys + (
        "model_manager_live2d",
        "model_manager_vrm",
        "model_manager_mmd",
    )
    for page_key in universal_storage_page_keys:
        assert f"'{page_key}'" in universal_source

    supported_pages_block = page_source.split("const SUPPORTED_PAGES = Object.freeze([", 1)[1].split(
        "    ]);",
        1,
    )[0]
    supported_pages = tuple(re.findall(r"'([^']+)'", supported_pages_block))
    assert supported_pages == page_tutorial_page_keys

    fallback_block = universal_source.split("function getTutorialStorageKeysForPageFallback(pageKey) {", 1)[1].split(
        "    if (pageKey === 'home')",
        1,
    )[0]
    assert "pageKey === 'model_manager'" in fallback_block
    assert "'model_manager_live2d'" in fallback_block
    assert "'model_manager_vrm'" in fallback_block
    assert "'model_manager_mmd'" in fallback_block
    assert "'model_manager_common'" in fallback_block

    assert "class PageTutorialManager" in page_source
    assert "window.initPageTutorialManager = initPageTutorialManager;" in page_source
    assert "window.resetPageTutorialStorage = resetPageTutorialStorage;" in page_source
    assert "if (path === '/' || path === '/index.html' || path === '/chat')" in page_source
    assert "return 'home';" in page_source
    assert "if (!SUPPORTED_PAGES.includes(this.currentPage)) return false;" in page_source
    assert "const DriverClass = window.driver;" in page_source
    assert "this.driver = new DriverClass({" in page_source
    assert "getModelManagerSteps()" in page_source
    assert "getSettingsSteps()" in page_source
    assert "getVoiceCloneSteps()" in page_source
    assert "getMemoryBrowserSteps()" in page_source
    assert "'steam_workshop': window.t ? window.t('steam.workshop', 'Steam创意工坊')" in universal_source

    detect_block = page_source.split("        static detectPage() {", 1)[1].split(
        "        static getModelManagerDisplayMode() {",
        1,
    )[0]
    assert detect_block.index("path.includes('parameter_editor')") < detect_block.index("path === '/l2d'")
    assert detect_block.index("path.includes('emotion_manager')") < detect_block.index("path === '/l2d'")
    assert "path.includes('l2d')" not in detect_block

    character_wait_block = page_source.split("        waitForCharacterCards(maxWaitTime = 5000) {", 1)[1].split(
        "    function resetPageTutorialStorage(pageKey) {",
        1,
    )[0]
    assert "const hasCard = !!document.querySelector('.chara-card-item, .chara-list-item');" in character_wait_block
    assert "hasCard || Date.now() - start >= maxWaitTime" in character_wait_block
    assert "hasCard || hasContainer" not in character_wait_block


def test_page_tutorial_manager_ignores_stale_yui_handoff_tokens():
    page_source = _read_page_manager()

    assert "const YUI_HANDOFF_STORAGE_KEY = 'neko_yui_guide_handoff_token';" in page_source
    assert "function parseYuiHandoffToken(rawToken)" in page_source
    assert "function getYuiHandoffTargetPagesForPage(pageKey)" in page_source
    assert "return ['settings', 'api_key'];" in page_source
    assert "function isActiveYuiHandoffTokenForPage(rawToken, pageKey)" in page_source
    assert "if (token.consumed) return false;" in page_source
    assert "Date.now() > expiresAt" in page_source
    assert "return !!targetPage && getYuiHandoffTargetPagesForPage(pageKey).includes(targetPage);" in page_source

    handoff_block = page_source.split("hasActiveYuiHandoff() {", 1)[1].split(
        "        checkAndStartTutorial() {",
        1,
    )[0]
    assert "window.universalTutorialManager._yuiGuideHandoffToken" in handoff_block
    assert "localStorage.getItem(YUI_HANDOFF_STORAGE_KEY)" in handoff_block
    assert "return isActiveYuiHandoffTokenForPage(token, this.currentPage);" in handoff_block


def test_page_tutorial_manager_honors_mobile_viewport_bailout():
    page_source = _read_page_manager()

    manage_block = page_source.split("        shouldManageCurrentPage() {", 1)[1].split(
        "        }",
        1,
    )[0]
    # Mirror initUniversalTutorialManager()'s mobile disable guard so page
    # tutorials don't re-enable the Driver overlay at mobile widths. The check
    # must gate before the SUPPORTED_PAGES return, since both checkAndStartTutorial()
    # and startTutorial() funnel through this method.
    assert "window.innerWidth <= 768" in manage_block
    assert manage_block.index("window.innerWidth <= 768") < manage_block.index(
        "return true;"
    )
    assert "!this.shouldAllowCompactDesktopTutorial()" in manage_block


def test_page_tutorial_manager_allows_voice_clone_desktop_popup_width():
    page_source = _read_page_manager()

    compact_block = page_source.split("        shouldAllowCompactDesktopTutorial() {", 1)[1].split(
        "        }",
        1,
    )[0]

    assert "this.currentPage !== 'voice_clone'" in compact_block
    assert "viewportWidth >= 640" in compact_block
    assert "screenWidth > 768" in compact_block


def test_voice_clone_tutorial_targets_visible_dropdown_triggers():
    page_source = _read_page_manager()

    voice_clone_block = page_source.split("        getVoiceCloneSteps() {", 1)[1].split(
        "        getSteamWorkshopSteps() {",
        1,
    )[0]

    assert "#voiceProvider-dropdown-trigger" in voice_clone_block
    assert "#refLanguage-dropdown-trigger" in voice_clone_block
    assert voice_clone_block.index("#refLanguage-dropdown-trigger") < voice_clone_block.index("#refLanguage'")


def test_page_tutorial_skip_button_restores_pointer_events_inside_fixed_portal():
    page_source = _read_page_manager()
    show_block = page_source.split("        showSkipButton() {", 1)[1].split(
        "        hideSkipButton() {",
        1,
    )[0]
    hide_block = page_source.split("        hideSkipButton() {", 1)[1].split(
        "        handleTutorialEnd",
        1,
    )[0]

    assert "let skipHandled = false;" in show_block
    assert "const absorbSkipEvent = (event) => {" in show_block
    assert "event.preventDefault();" in show_block
    assert "event.stopImmediatePropagation();" in show_block
    assert "event.stopPropagation();" in show_block
    assert "const completeSkipRequest = () => {" in show_block
    assert "const handleSkipPress = (event) => {" in show_block
    assert "const handleSkipRequest = (event, delayMs = 0) => {" in show_block
    assert "if (skipHandled) {" in show_block
    assert "skipHandled = true;" in show_block
    assert "window.setTimeout(completeSkipRequest, delayMs);" in show_block
    assert "const controller = this.ensureSkipSafeAreaController();" in show_block
    assert "const host = controller && typeof controller.getButtonHost === 'function'" in show_block
    assert "button.className = 'neko-page-tutorial-skip-btn';" in show_block
    for event_name in ("pointerdown", "mousedown", "touchstart"):
        assert f"button.addEventListener('{event_name}', handleSkipPress" in show_block
    assert "button.addEventListener('pointerup', (event) => handleSkipRequest(event, 80));" in show_block
    assert "button.addEventListener('touchend', (event) => handleSkipRequest(event, 80), { passive: false });" in show_block
    assert "button.addEventListener('click', handleSkipRequest);" in show_block
    assert "button.style.setProperty('pointer-events', 'auto', 'important');" in show_block
    assert "button.style.setProperty('z-index', '2147483647', 'important');" in show_block
    assert "button.style.touchAction = 'manipulation';" in show_block
    assert "this._skipSafeAreaController.hide();" in hide_block


def test_page_tutorial_manager_waits_for_api_settings_loading_overlay():
    page_source = _read_page_manager()

    assert "isApiSettingsPage()" in page_source
    assert "waitForApiSettingsReady(maxWaitTime = 5000)" in page_source

    check_start_block = page_source.split("        checkAndStartTutorial() {", 1)[1].split(
        "        startTutorialWhenI18nReady",
        1,
    )[0]
    assert "if (this.isApiSettingsPage()) {" in check_start_block
    assert "this.waitForApiSettingsReady().then(() => {" in check_start_block
    assert "this.startTutorialWhenI18nReady(300, manual ? 'manual' : 'auto');" in check_start_block
    assert check_start_block.index("this.waitForApiSettingsReady().then(() => {") < check_start_block.index(
        "this.startTutorialWhenI18nReady(1200, manual ? 'manual' : 'auto');"
    )

    wait_block = page_source.split("        waitForApiSettingsReady(maxWaitTime = 5000) {", 1)[1].split(
        "        t(key, fallback) {",
        1,
    )[0]
    assert "const loadingOverlay = document.getElementById('loading-overlay');" in wait_block
    assert "loadingOverlay.hidden" in wait_block
    assert "style.display === 'none' || style.visibility === 'hidden'" in wait_block
    assert "Date.now() - start >= maxWaitTime" in wait_block
    assert "window.setTimeout(check, 120);" in wait_block


def test_emotion_manager_config_steps_survive_until_model_selected():
    page_source = _read_page_manager()

    emotion_block = page_source.split("        getEmotionManagerSteps() {", 1)[1].split(
        "        getCharaManagerSteps() {",
        1,
    )[0]
    # #emotion-config / #reset-btn start display:none until a model is picked, so
    # they must not be visibility-filtered out before the tutorial begins; otherwise
    # the restored tutorial ends right after the model picker and never covers the
    # config area. requiresVisible:false keeps them (the elements exist in the DOM).
    config_step = emotion_block.split("element: '#emotion-config',", 1)[1].split("popover:", 1)[0]
    reset_step = emotion_block.split("element: '#reset-btn',", 1)[1].split("popover:", 1)[0]
    assert "requiresVisible: false" in config_step
    assert "requiresVisible: false" in reset_step
    # Constrain only the two restored steps; a global negative on emotion_block
    # would also break on unrelated, legitimately visibility-gated steps added later.
    assert "requiresVisible: true" not in config_step
    assert "requiresVisible: true" not in reset_step


def test_restored_driver_cleans_drag_handlers_between_steps_and_stays_quiet():
    source = _read_driver()

    assert "console.log(" not in source
    assert "cleanupDragHandlers()" in source
    assert "this.cleanupDragHandlers();" in source

    remove_popover_block = source.split("        removePopover() {", 1)[1].split(
        "        destroy() {",
        1,
    )[0]
    assert "this.cleanupDragHandlers();" in remove_popover_block
    assert "this.popover.classList.remove('dragging');" in remove_popover_block

    cleanup_block = source.split("        cleanupDragHandlers() {", 1)[1].split(
        "        removePopover() {",
        1,
    )[0]
    assert "dragHandle.removeEventListener('mousedown', handleDragStart);" in cleanup_block
    assert "dragHandle.removeEventListener('touchstart', handleDragStart, { passive: false });" in cleanup_block
    assert "document.removeEventListener('touchmove', handleDragMove, { passive: false });" in cleanup_block
    assert "document.removeEventListener('touchend', handleDragEnd, { passive: false });" in cleanup_block

    bind_drag_block = source.split("        bindDragEvents(dragHandle, popover) {", 1)[1].split(
        "    // 暴露到全局",
        1,
    )[0]
    assert "this.cleanupDragHandlers();" in bind_drag_block
    assert "dragHandle.addEventListener('mousedown', handleDragStart);" in bind_drag_block
    assert "document.addEventListener('mouseup', handleDragEnd);" in bind_drag_block
    assert "dragHandle.addEventListener('touchstart', handleDragStart, { passive: false });" in bind_drag_block
    assert "document.addEventListener('touchmove', handleDragMove, { passive: false });" in bind_drag_block
    assert "document.addEventListener('touchend', handleDragEnd, { passive: false });" in bind_drag_block
    assert "dragHandle," in bind_drag_block
    assert "handleDragStart," in bind_drag_block


def test_restored_driver_cancels_delayed_scroll_callbacks_after_destroy():
    source = _read_driver()

    assert "this.isDestroyed = false;" in source
    assert "this.pendingTimers = new Set();" in source
    assert "this.pendingScrollCleanups = new Set();" in source
    assert "setManagedTimeout(callback, delay)" in source
    assert "clearDelayedCallbacks()" in source

    show_step_block = source.split("        showStep(index) {", 1)[1].split(
        "        scrollToElementAndHighlight",
        1,
    )[0]
    assert "if (this.isDestroyed) return;" in show_step_block
    assert "this.clearDelayedCallbacks();" in show_step_block

    scroll_block = source.split("scrollToElementAndHighlight(element, popover, stepIndex) {", 1)[1].split(
        "        createHighlight(element) {",
        1,
    )[0]
    assert "if (this.isDestroyed || !this.isActive) return;" in scroll_block
    assert "if (created || this.isDestroyed || !this.isActive) return;" in scroll_block
    assert "cleanupScroll();" in scroll_block
    assert "this.pendingScrollCleanups.add(cleanupScroll);" in scroll_block
    assert "scrollTimer = this.setManagedTimeout(createAfterScroll, 150);" in scroll_block
    assert "fallbackTimer = this.setManagedTimeout(createAfterScroll, 1200);" in scroll_block
    assert "this.setManagedTimeout(poll, 50);" in scroll_block

    destroy_block = source.split("        destroy() {", 1)[1].split(
        "        /**",
        1,
    )[0]
    assert "if (this.isDestroyed) return;" in destroy_block
    assert "this.isDestroyed = true;" in destroy_block
    assert "this.clearDelayedCallbacks();" in destroy_block


def test_restored_driver_animation_keyframes_are_namespaced():
    driver_css = _read_driver_css()
    tutorial_styles = _read_tutorial_styles()
    combined = driver_css + "\n" + tutorial_styles
    animation_declarations = re.findall(r"\banimation(?:-name)?\s*:\s*([^;}]+)", combined)

    for obsolete_name in ("fadeIn", "slideIn", "pulse"):
        assert f"@keyframes {obsolete_name}" not in combined
        assert f"animation: {obsolete_name}" not in combined
        obsolete_keyframe_ref = re.compile(rf"(?<![-\w]){re.escape(obsolete_name)}(?![-\w])")
        assert all(not obsolete_keyframe_ref.search(declaration) for declaration in animation_declarations)

    for keyframe_name in (
        "neko-page-tutorial-fade-in",
        "neko-page-tutorial-slide-in",
        "neko-page-tutorial-pulse",
    ):
        assert keyframe_name in combined


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
    assert "const round = this.getHomeAvatarFloatingGuideLaunchRound();" in start_block
    assert start_block.index("const round = this.getHomeAvatarFloatingGuideLaunchRound();") < start_block.index(
        "if (!round) {"
    )
    assert start_block.index("if (!round) {") < start_block.index(
        "this.snapshotAvatarFloatingModelInteractionState('tutorial-start');"
    )
    assert start_block.index("this.snapshotAvatarFloatingModelInteractionState('tutorial-start');") < start_block.index(
        "this.startAvatarFloatingGuideRound(round, {"
    )
    assert "this.startAvatarFloatingGuideRound(round, {" in start_block
    assert "const round = this.getHomeAvatarFloatingGuideLaunchRound();" in i18n_block
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
    emit_block = source.split("    emitTutorialStarted(page = this.currentPage, source = this.currentTutorialStartSource, options = {}) {", 1)[1].split(
        "    /**",
        1,
    )[0]
    assert "this.clearStartupGreetingRelease('tutorial-started');" in emit_block
    assert "options.lifecycleAlreadyStarted !== true" in emit_block
    assert "this.relayYuiGuideTutorialLifecycleStarted(page, source);" in emit_block
    assert emit_block.index("this.clearStartupGreetingRelease('tutorial-started');") < emit_block.index(
        "this.relayYuiGuideTutorialLifecycleStarted(page, source);"
    )
    assert emit_block.index("this.relayYuiGuideTutorialLifecycleStarted(page, source);") < emit_block.index(
        "window.dispatchEvent(new CustomEvent('neko:tutorial-started'"
    )
    lifecycle_started_block = source.split(
        "    relayYuiGuideTutorialLifecycleStarted(page, source) {",
        1,
    )[1].split(
        "    syncYuiGuideCompactChatFixedLayout",
        1,
    )[0]
    assert "this.ensurePcTutorialGlobalOverlayStarted('tutorial-lifecycle-started')" in lifecycle_started_block
    assert lifecycle_started_block.index(
        "this.ensurePcTutorialGlobalOverlayStarted('tutorial-lifecycle-started')"
    ) < lifecycle_started_block.index("const startedMessage = {")

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
    assert "isTutorialYuiLive2dVisualReady()" in source
    visual_ready_block = source.split(
        "    isTutorialYuiLive2dVisualReady() {",
        1,
    )[1].split(
        "    waitForTutorialYuiLive2dVisualReady(reason = '', maxWaitTime = 12000) {",
        1,
    )[0]
    assert "this.isTutorialYuiLive2dActive()" in visual_ready_block
    assert "this.hasTutorialYuiLive2dRenderableModel(manager)" in visual_ready_block
    assert "manager._isLoadingModel === true" in visual_ready_block
    assert "state !== 'ready'" in visual_ready_block
    assert "manager._isModelReadyForInteraction !== true" in visual_ready_block

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


def test_round_prelude_waits_for_yui_visual_ready_before_taking_over():
    source = _read_round_prelude()
    play_block = source.split("        async play(day, options) {", 1)[1].split(
        "    return {",
        1,
    )[0]

    assert "this.waitForAvatarReady = normalizedOptions.waitForAvatarReady || noop;" in source
    assert "await toPromise(() => this.ensureVisible(sceneId, {" in play_block
    assert "await toPromise(() => this.waitForAvatarReady(sceneId, {" in play_block
    assert play_block.index("await toPromise(() => this.ensureVisible(sceneId, {") < play_block.index(
        "await toPromise(() => this.waitForAvatarReady(sceneId, {"
    )
    assert play_block.index("await toPromise(() => this.waitForAvatarReady(sceneId, {") < play_block.index(
        "this.beginTakingOver({"
    )


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
        "    async playAvatarFloatingRoundPrelude(round, source, director, options = {}) {",
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
    assert "waitForAvatarReady: (sceneId, _options) => this.waitForTutorialYuiLive2dVisualReady(sceneId, 12000)" in source
    assert "if (!this.hasTutorialYuiLive2dRenderableModel(manager)) {" in placement_block
    assert "deferRevealPrepared: true" in prelude_block
    assert "const deferRevealPrepared = options && options.deferRevealPrepared === true;" in ensure_visible_block
    assert "if (!deferRevealPrepared) {" in ensure_visible_block
    assert "deferRevealPrepared" in render_active_block
    assert "preservePreparingOpacity: deferRevealPrepared" in render_active_block


def test_tutorial_live2d_preparing_hides_model_side_controls():
    repo_root = Path(__file__).resolve().parents[2]
    css_source = (repo_root / "static/css/yui-guide.css").read_text(encoding="utf-8")
    app_ui_source = read_js_parts(repo_root / "static/app/app-ui")
    live2d_buttons_source = (repo_root / "static/live2d/live2d-ui-buttons.js").read_text(encoding="utf-8")
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
    assert "if (isYuiGuideLive2DPreparing() || isYuiGuideFloatingToolbarSuppressed()) {" in live2d_buttons_source
    assert "hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer)" in live2d_buttons_source
    assert "buttonsContainer.style.setProperty('display', 'flex', 'important');" in live2d_buttons_source
    protection_timer_block = live2d_buttons_source.split(
        "this.tutorialProtectionTimer = setInterval(() => {",
        1,
    )[1].split("}, 300);", 1)[0]
    assert "if (isYuiGuideLive2DPreparing() || isYuiGuideFloatingToolbarSuppressed()) {" in protection_timer_block
    assert "hideYuiGuideLive2DPreparingButtonStyles(buttonsContainer);" in protection_timer_block
    assert protection_timer_block.index(
        "if (isYuiGuideLive2DPreparing() || isYuiGuideFloatingToolbarSuppressed()) {"
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


def test_avatar_floating_guide_waits_for_compact_chat_before_fixing_layout_and_restores_ball():
    source = _read_manager()
    start_round_block = source.split("    async startAvatarFloatingGuideRound(day, options = {}) {", 1)[1].split(
        "            const director = this.ensureYuiGuideDirector();",
        1,
    )[0]
    lifecycle_block = source.split("    clearAllTutorialLifecycles(reason = 'destroy') {", 1)[1].split(
        "    normalizeTutorialEndRawReason(reason) {",
        1,
    )[0]

    # 重启教程时聊天桥仍是 closed；只提前打开本轮 lifecycle，用户可见 started 仍等准备成功。
    assert start_round_block.index("this.relayYuiGuideTutorialLifecycleStarted('home', source)") < start_round_block.index(
        "await this.prepareYuiGuideCompactChatForTutorial()"
    )
    assert start_round_block.index("await this.prepareYuiGuideCompactChatForTutorial()") < start_round_block.index(
        "this.syncYuiGuideCompactChatFixedLayout(true, 'avatar-floating-guide-start')"
    )
    assert start_round_block.index("this.syncYuiGuideCompactChatFixedLayout(true, 'avatar-floating-guide-start')") < start_round_block.index(
        "this.emitTutorialStarted('home', source, { lifecycleAlreadyStarted: true })"
    )
    assert "action: 'yui_guide_prepare_compact_chat'" in source
    assert "tutorialRunId: tutorialRunId" in source
    assert "neko:yui-guide:compact-chat-ready" in source
    assert "wasCollapsed: response.wasCollapsed === true" in source
    prepare_block = source.split("    prepareYuiGuideCompactChatForTutorial() {", 1)[1].split(
        "    restoreYuiGuideCompactChatSurface(reason = 'tutorial-ended') {",
        1,
    )[0]
    restore_block = source.split("    restoreYuiGuideCompactChatSurface(reason = 'tutorial-ended') {", 1)[1].split(
        "    clearPcTutorialGlobalOverlay(reason = 'destroy') {",
        1,
    )[0]
    # 单窗口浏览器虽然有 BroadcastChannel，却没有独立聊天页回执；必须先走本地 host。
    assert "const hasNativeChatRelay" in prepare_block
    assert prepare_block.index("if (!hasNativeChatRelay") < prepare_block.index("channel.postMessage(message)")
    # 原生请求超时后不能猜用户原本是毛球；交给聊天窗按同 request 的 prepare 快照恢复。
    assert "finish(false, { restoreFromPrepareSnapshot: posted })" in prepare_block
    assert "restoreFromPrepareSnapshot: response.restoreFromPrepareSnapshot === true" in prepare_block
    assert "snapshot.restoreFromPrepareSnapshot !== true" in restore_block
    assert "restoreFromPrepareSnapshot: snapshot.restoreFromPrepareSnapshot === true" in restore_block
    assert "if (!posted && snapshot.wasCollapsed === true)" in restore_block
    # 所有结束方式都经过 clearAllTutorialLifecycles，因此形态恢复不能只挂在 skip 或 complete 分支。
    assert lifecycle_block.index("this.clearYuiGuideCompactChatFixedLayout(rawReason)") < lifecycle_block.index(
        "this.restoreYuiGuideCompactChatSurface(rawReason)"
    )
    assert "action: 'yui_guide_restore_compact_chat'" in source


def test_avatar_floating_guide_restores_goodbye_business_state_after_model_reload():
    source = _read_manager()
    repo_root = Path(__file__).resolve().parents[2]
    app_ui_source = read_js_parts(repo_root / "static/app/app-ui")
    teardown_block = source.split("    _teardownTutorialUI() {", 1)[1].split(
        "    /**\n     * 恢复所有在引导中修改过的元素",
        1,
    )[0]
    restore_state_block = source.split("    restoreAvatarFloatingModelStateAfterTutorial() {", 1)[1].split(
        "    applyTutorialChatIdentityOverride(detail) {",
        1,
    )[0]

    # 用户模型先恢复，随后才重新进入教程前的猫咪形态，避免点击穿透状态套到普通模型上。
    assert teardown_block.index("this.restoreTutorialAvatarOverride()") < teardown_block.index(
        "this.restoreAvatarFloatingModelStateAfterTutorial()"
    )
    assert "snapshot.goodbyeActive !== true" in restore_state_block
    # 直启教程没有用户模型可供读取，待执行的 PC 默认猫咪请求也必须成为恢复目标。
    assert "autoGoodbyeState.startupDefaultCatRequested === true" in source
    assert "startupDefaultCatPending ? 'startup-default-cat'" in source
    assert "new CustomEvent('live2d-goodbye-click'" in restore_state_block
    consume_block = source.split("    consumePendingStartupDefaultCatRestoreRequest() {", 1)[1].split(
        "    applyTutorialChatIdentityOverride(detail) {",
        1,
    )[0]
    assert "snapshot.goodbyeMeta.reason !== 'startup-default-cat'" in consume_block
    assert "autoGoodbye.consumeStartupDefaultCatRequest()" in consume_block
    assert teardown_block.index("this.consumePendingStartupDefaultCatRestoreRequest()") < teardown_block.index(
        "window.isInTutorial = false"
    )
    assert "restoreSavedGoodbyeRect: snapshot.goodbyeRect || null" in restore_state_block
    assert "this._avatarFloatingModelLockSnapshot = null" in restore_state_block
    assert "const requestedRestoreRect = event && event.detail && event.detail.restoreSavedGoodbyeRect" in app_ui_source


def test_external_chat_prepares_native_capsule_and_restores_previous_ball_state():
    source = (APP_INTERPAGE_PARTS_PATH / "guide-message-relay.js").read_text(encoding="utf-8")
    native_relay_source = (APP_INTERPAGE_PARTS_PATH / "cross-window-broadcast-and-bridge.js").read_text(
        encoding="utf-8"
    )

    # 两种跨窗口传输路径必须落到同一实现，重复 relay 也只能启动一次原生展开动画。
    assert "case 'yui_guide_prepare_compact_chat':" in source
    assert "case 'yui_guide_prepare_compact_chat':" in native_relay_source
    assert "I.yuiGuideCompactChatPrepareRequestId === requestId" in source
    assert "I.yuiGuideCompactChatPrepareRequestId !== requestId" in source
    assert "I.yuiGuideCompactChatPrepareWasCollapsed = wasCollapsed" in source
    assert "nativeBridge.prepareExpandedForTutorial()" in source
    assert "I.postYuiGuideMessageToPet('yui_guide_compact_chat_ready'" in source
    # 新旧原生桥同步抛错时都必须回执失败，不能让 null 被当成准备成功。
    assert source.count("nativePreparation = { ready: false };") == 2
    # 明确回执或同 request 的本地 prepare 快照确认是毛球时才折叠，原本胶囊保持不变。
    assert "if (!shouldRestoreCollapsed) return false" in source
    assert "message.restoreFromPrepareSnapshot === true" in source
    assert "I.yuiGuideCompactChatPrepareWasCollapsed === true" in source
    assert "nativeBridge.restoreCollapsedAfterTutorial()" in source


def test_electron_shortcut_bridges_are_blocked_during_tutorial():
    yui_guide_common = _read_yui_guide_common()
    common_ui = _read_common_ui()
    audio_capture = _read_app_audio_capture()
    chat_template = _read_chat_template()

    assert "root.isNekoShortcutBlockedByTutorial = function ()" in yui_guide_common
    assert "host.isInTutorial === true" in yui_guide_common
    assert "yui-guide-standalone-input-shield-active" in yui_guide_common
    assert "yui-guide-chat-buttons-disabled" in yui_guide_common
    assert "yui-guide-compact-chat-fixed" in yui_guide_common
    assert "isNekoShortcutBlockedByTutorial," in yui_guide_common
    assert "/static/tutorial/yui-guide/common.js" in chat_template
    assert '<script src="/static/common_ui.js' not in chat_template

    for action in ("toggleVoiceSession", "toggleScreenShare", "triggerScreenshot"):
        block = common_ui.split(f"window.{action} = function", 1)[1].split("};", 1)[0]
        guard = f"if (blockNekoShortcutDuringTutorial('{action}')) return;"
        assert guard in block
        if action == "triggerScreenshot":
            assert block.index(guard) < block.index("screenshotButton.click()")
        else:
            assert block.index(guard) < block.index("window.dispatchEvent(event)")

    mic_guard_block = audio_capture.split("function isTutorialShortcutBlockedForMicMute()", 1)[1].split(
        "window.toggleMicMute = function",
        1,
    )[0]
    assert "window.isNekoShortcutBlockedByTutorial" in mic_guard_block
    assert "window.isInTutorial === true" in mic_guard_block

    mute_block = audio_capture.split("window.toggleMicMute = function", 1)[1].split("window.setMicMuted", 1)[0]
    assert "isTutorialShortcutBlockedForMicMute()" in mute_block
    assert "blocked - tutorial active" in mute_block
    assert "return S.isMicMuted;" in mute_block
    assert mute_block.index("return S.isMicMuted;") < mute_block.index("S.isMicMuted = !S.isMicMuted;")
