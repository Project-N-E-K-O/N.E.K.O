from pathlib import Path
import json
import re


YUI_GUIDE_DIRECTOR_PATH = Path(__file__).resolve().parents[2] / "static" / "tutorial/yui-guide/director.js"
YUI_GUIDE_OVERLAY_PATH = Path(__file__).resolve().parents[2] / "static" / "tutorial/yui-guide/overlay.js"
YUI_GUIDE_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "css/yui-guide.css"
YUI_GUIDE_STEPS_PATH = Path(__file__).resolve().parents[2] / "static" / "tutorial/yui-guide/steps.js"
YUI_GUIDE_DAY1_PATH = Path(__file__).resolve().parents[2] / "static" / "tutorial/yui-guide/days/day1-home-guide.js"
SCENE_ORCHESTRATOR_PATH = Path(__file__).resolve().parents[2] / "static" / "tutorial/core/scene-orchestrator.js"
NEW_USER_ICEBREAKER_PATH = Path(__file__).resolve().parents[2] / "static" / "icebreaker/new-user-icebreaker.js"
APP_INTERPAGE_PATH = Path(__file__).resolve().parents[2] / "static" / "app-interpage.js"
PLUGIN_YUI_GUIDE_RUNTIME_PATH = Path(__file__).resolve().parents[2] / "frontend" / "plugin-manager/src/yui-guide-runtime.ts"
YUI_GUIDE_COMMON_PATH = Path(__file__).resolve().parents[2] / "static" / "tutorial/yui-guide/common.js"
STATIC_LOCALES_DIR = Path(__file__).resolve().parents[2] / "static" / "locales"


def _read_director() -> str:
    return YUI_GUIDE_DIRECTOR_PATH.read_text(encoding="utf-8")


def _read_steps() -> str:
    return YUI_GUIDE_STEPS_PATH.read_text(encoding="utf-8")


def _read_day1_guide() -> str:
    return YUI_GUIDE_DAY1_PATH.read_text(encoding="utf-8")


def _read_interpage() -> str:
    return APP_INTERPAGE_PATH.read_text(encoding="utf-8")


def _read_static_locale(locale_name: str) -> dict:
    return json.loads((STATIC_LOCALES_DIR / f"{locale_name}.json").read_text(encoding="utf-8"))


def _extract_deep_freeze_registration_block(source: str) -> str:
    marker = "registerGuide(deepFreeze("
    start = source.index(marker) + len(marker)
    assert source[start] == "{"
    depth = 0
    quote = None
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("registerGuide(deepFreeze(...)) block was not closed")


def _function_block(source: str, name: str, next_name: str) -> str:
    return source.split(f"        {name}() {{", 1)[1].split(f"        {next_name}(", 1)[0]


def test_home_tutorial_chat_targets_prefer_compact_capsule_over_removed_full_window():
    source = _read_director()
    overlay_source = YUI_GUIDE_OVERLAY_PATH.read_text(encoding="utf-8")

    input_block = _function_block(source, "getChatInputTarget", "getChatWindowTarget")
    window_block = _function_block(source, "getChatWindowTarget", "shouldNarrateInChat")
    activation_block = _function_block(source, "getChatIntroActivationTarget", "clearSceneTimers")

    compact_input_selector = (
        '#react-chat-window-root [data-compact-geometry-owner="surface"]'
        '[data-compact-geometry-item="input"]'
    )
    compact_capsule_selector = (
        '#react-chat-window-root [data-compact-geometry-owner="surface"]'
        '[data-compact-geometry-item="capsule"]'
    )
    compact_surface_selector = "#react-chat-window-root .compact-chat-surface-shell"
    legacy_shell_selector = "#react-chat-window-shell"
    legacy_composer_selector = "#react-chat-window-root .composer-input"

    assert compact_input_selector in input_block
    assert compact_surface_selector in input_block
    assert input_block.index(compact_input_selector) < input_block.index(legacy_composer_selector)

    assert compact_input_selector in activation_block
    assert activation_block.index(compact_input_selector) < activation_block.index(legacy_composer_selector)

    assert compact_capsule_selector in window_block
    assert compact_input_selector in window_block
    assert compact_surface_selector in window_block
    assert window_block.index(compact_capsule_selector) < window_block.index(legacy_shell_selector)
    assert window_block.index(compact_input_selector) < window_block.index(legacy_shell_selector)

    assert "isAllowedTutorialInteractionTarget" not in source
    assert "setTutorialInputShieldActive(active)" in overlay_source
    assert "this.overlay.setTutorialInputShieldActive(isActive);" in source
    assert "!this.interactionShieldSuppressed" in overlay_source
    assert "(this.tutorialInputShieldActive || this.takingOverActive)" in overlay_source
    assert "#neko-tutorial-skip-btn, [data-yui-skip-control], [data-yui-emergency-exit]" in overlay_source
    assert "isSystemDialogEventTarget(target)" in overlay_source
    assert "hasOpenSystemDialog()" in overlay_source
    assert "this.isSystemDialogEventTarget(target)" in overlay_source
    assert "#storage-location-overlay:not([hidden])" in overlay_source
    assert "#prominent-notice-overlay" in overlay_source
    assert ".modal-overlay" in overlay_source
    assert "is-interaction-shield-system-dialog-suspended" in overlay_source
    assert "event.stopImmediatePropagation();" in overlay_source
    assert "installGlobalInteractionShieldBlocker()" in overlay_source
    assert "event.isTrusted === false" in overlay_source


def test_day6_plugin_dashboard_handoff_closes_at_narration_boundary():
    source = _read_director()

    assert "const DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS = 1120;" in source
    assert "const DAY6_PLUGIN_SIDE_PANEL_CURSOR_START_DELAY_MS = 500;" in source
    assert "const DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS = 480;" in source
    assert "const DAY6_PLUGIN_DASHBOARD_DONE_GRACE_MS = 120;" in source
    assert "finishPluginDashboardHandoff(reason) {" in source

    boundary_block = source.split(
        "        async waitForPluginDashboardPerformanceUntilNarrationBoundary(windowRef, payload, options) {",
        1,
    )[1].split(
        "        async waitForPluginDashboardPerformance(windowRef, payload) {",
        1,
    )[0]
    dashboard_block = source.split(
        "        async runDay6PluginDashboardHandoffFlow(scene, narrationStartedAt) {",
        1,
    )[1].split(
        "        async cleanupDay6PluginDashboardPostNarration(previewState, homeCursorPosition, sceneRunId) {",
        1,
    )[0]
    dashboard_cleanup_block = source.split(
        "        async cleanupDay6PluginDashboardPostNarration(previewState, homeCursorPosition, sceneRunId) {",
        1,
    )[1].split(
        "        async runDay6PluginSidePanelFlow(scene, narrationStartedAt) {",
        1,
    )[0]
    side_panel_block = source.split(
        "        async runDay6PluginSidePanelFlow(scene, narrationStartedAt) {",
        1,
    )[1].split(
        "        async runDay4AnimationDistanceShowcase(scene, narrationStartedAt) {",
        1,
    )[0]

    assert "const performancePromise = this.waitForPluginDashboardPerformance(windowRef, payload).catch(() => false);" in boundary_block
    assert "this.notifyPluginDashboardNarrationFinished();" in boundary_block
    assert "this.finishPluginDashboardHandoff('plugin_dashboard_done_grace_timeout');" in boundary_block
    assert "return await Promise.race([performancePromise, boundaryPromise]);" in boundary_block

    missing_window_block = dashboard_block.split(
        "if (!pluginDashboardWindow || pluginDashboardWindow.closed) {",
        1,
    )[1].split(
        "if (guardFailed()) {",
        1,
    )[0]
    assert "const cleanupCompleted = await this.cleanupDay6PluginDashboardPostNarration(" in missing_window_block
    assert "this.day6PluginDashboardPreview = null;" in missing_window_block
    assert "return cleanupCompleted && !guardFailed();" in missing_window_block
    assert "this.waitForPluginDashboardPerformanceUntilNarrationBoundary(pluginDashboardWindow" in dashboard_block
    assert "const cleanupCompleted = await this.cleanupDay6PluginDashboardPostNarration(" in dashboard_block
    assert "if (!cleanupCompleted || guardFailed()) {" in dashboard_block
    assert "scheduleDay6PluginDashboardPostNarrationCleanup(" not in source
    assert "await this.closePluginDashboardWindowIfCreatedByGuide('Day 6 插件管理预览完成');" not in dashboard_block
    assert "await this.closeAgentPanel().catch(() => {});" not in dashboard_block
    assert "await this.waitForHomeMainUIReady(3600);" not in dashboard_block
    assert "await this.closePluginDashboardWindowIfCreatedByGuide('Day 6 插件管理预览完成');" in dashboard_cleanup_block
    assert "await this.closeAgentPanel().catch(() => {});" in dashboard_cleanup_block
    assert "const homeReady = await this.waitForHomeMainUIReady(3600);" in dashboard_cleanup_block
    assert "this.waitForPluginDashboardPerformanceUntilNarrationBoundary(pluginDashboardWindow" in side_panel_block
    assert "await this.waitForPluginDashboardPerformance(pluginDashboardWindow" not in dashboard_block
    assert "await this.waitForPluginDashboardPerformance(pluginDashboardWindow" not in side_panel_block


def test_day6_agent_status_cursor_moves_to_cat_paw_and_clicks_during_line():
    source = _read_director()

    assert "const DAY6_PLUGIN_AGENT_PANEL_CURSOR_MOVE_MS = 2800;" in source
    assert "const DAY6_PLUGIN_AGENT_PANEL_CURSOR_START_DELAY_MS = 500;" in source
    assert "const DAY6_PLUGIN_AGENT_PANEL_CLICK_VISIBLE_MS = 620;" in source
    assert "const DAY6_PLUGIN_CAT_PAW_CURSOR_OFFSET_Y = 8;" in source

    status_block = source.split(
        "        async runDay6PluginOpenAgentPanelFlow(scene) {",
        1,
    )[1].split(
        "        async runDay6PluginOpenManagementPanelFlow(scene) {",
        1,
    )[0]

    assert "const scaleSceneMs = this.createSceneScaler(scene && scene.voiceKey);" in status_block
    assert "const catPawButton = await this.waitForVisibleTarget([" in status_block
    assert "], 2200);" in status_block
    assert "const catPawButton = this.getFloatingButtonShell(this.getFallbackFloatingButton('agent'))" not in status_block
    assert "await this.waitForSceneDelay(DAY6_PLUGIN_AGENT_PANEL_CURSOR_START_DELAY_MS)" in status_block
    assert "await this.moveAvatarFloatingCursor(Object.assign({}, scene || {}, {" in status_block
    assert (
        status_block.index("await this.waitForSceneDelay(DAY6_PLUGIN_AGENT_PANEL_CURSOR_START_DELAY_MS)")
        < status_block.index("await this.moveAvatarFloatingCursor(Object.assign({}, scene || {}, {")
    )
    assert "targetPointOffset: { y: DAY6_PLUGIN_CAT_PAW_CURSOR_OFFSET_Y }" in status_block
    assert "clampTargetPointToRect: true" in status_block
    assert "targetPointClampInsetPx: 4" in status_block
    assert "await this.ensureDay6AgentPanelCursorHasStartPoint()" not in source
    assert "async ensureDay6AgentPanelCursorHasStartPoint() {" not in source
    assert "const catPawCursorPoint = await this.moveDay6CursorToCatPawButton(" not in source
    assert "async moveDay6CursorToCatPawButton(element, durationMs) {" not in source
    assert "getDay6CatPawCursorPoint" not in source
    assert "catPawCursorPoint: catPawCursorPoint" not in status_block
    assert "scaleSceneMs(DAY6_PLUGIN_AGENT_PANEL_CURSOR_MOVE_MS, 2100, 5200)" in status_block
    assert "await this.runActionWithCursorClickExact(" in status_block
    assert "scaleSceneMs(DAY6_PLUGIN_AGENT_PANEL_CLICK_VISIBLE_MS, 480, 1200)" in status_block
    assert "() => this.openAgentPanel()" in status_block
    assert "resolveCursorPointFromRect(rect, options)" in source
    assert "this.resolveCursorPointFromRect(rect, normalizedOptions)" in source

    management_block = source.split(
        "        async runDay6PluginOpenManagementPanelFlow(scene) {",
        1,
    )[1].split(
        "        async runDay6PluginDashboardHandoffFlow(scene, narrationStartedAt) {",
        1,
    )[0]
    assert "const scaleSceneMs = this.createSceneScaler(scene && scene.voiceKey);" in management_block
    assert "await this.ensureDay6ManagementCursorStartsFromPreviousCatPaw()" not in source
    assert "async ensureDay6ManagementCursorStartsFromPreviousCatPaw() {" not in source
    assert "previewState.catPawCursorPoint" not in source
    assert "await this.waitForSceneDelay(DAY6_PLUGIN_SIDE_PANEL_CURSOR_START_DELAY_MS)" in management_block
    assert (
        management_block.index("await this.waitForSceneDelay(DAY6_PLUGIN_SIDE_PANEL_CURSOR_START_DELAY_MS)")
        < management_block.index("const userPluginMovePromise = this.moveCursorToTrackedElement(")
    )
    assert "const sidePanelShownPromise = this.ensureAvatarFloatingAgentSidePanel('user-plugin');" not in management_block
    assert "const movedToUserPlugin = await userPluginMovePromise;" in management_block
    assert "const sidePanelShown = await this.runActionWithCursorClickExact(" in management_block
    assert "() => this.ensureAvatarFloatingAgentSidePanel('user-plugin')" in management_block
    assert (
        management_block.index("const movedToUserPlugin = await userPluginMovePromise;")
        < management_block.index("const sidePanelShown = await this.runActionWithCursorClickExact(")
    )
    assert "this.applyGuideHighlights({" in management_block
    assert "key: sceneId + '-clear-user-plugin'," in management_block
    assert "primary: null" in management_block
    assert "scaleSceneMs(DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS, 840, 2100)" in management_block
    assert "scaleSceneMs(DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS, 360, 900)" in management_block


def test_avatar_floating_guides_hide_real_cursor_during_takeover_and_show_banner():
    guide_css = YUI_GUIDE_CSS_PATH.read_text(encoding="utf-8")
    overlay_source = YUI_GUIDE_OVERLAY_PATH.read_text(encoding="utf-8")
    director_source = _read_director()
    plugin_runtime_source = PLUGIN_YUI_GUIDE_RUNTIME_PATH.read_text(encoding="utf-8")
    common_source = YUI_GUIDE_COMMON_PATH.read_text(encoding="utf-8")
    manager_source = (Path(__file__).resolve().parents[2] / "static" / "tutorial/core/universal-manager.js").read_text(
        encoding="utf-8"
    )
    resistance_source = (
        Path(__file__).resolve().parents[2] / "static" / "tutorial/visual/resistance-controllers.js"
    ).read_text(encoding="utf-8")

    assert "Double .yui-taking-over to out-specificity earlier cursor:auto !important rules." in guide_css
    assert "body.yui-taking-over.yui-taking-over," in guide_css
    assert re.search(
        r"body\.yui-taking-over\.yui-taking-over,[\s\S]*?body\.yui-taking-over \[data-yui-cursor-hidden=\"true\"\][\s\S]*?cursor\s*:\s*none\s*!important",
        guide_css,
    )
    assert re.search(
        r"body\.yui-taking-over\.yui-taking-over #neko-tutorial-skip-btn,[\s\S]*?body\.yui-taking-over\.yui-taking-over \[data-yui-emergency-exit\] \*[\s\S]*?cursor\s*:\s*auto\s*!important",
        guide_css,
    )
    assert "body.yui-taking-over.yui-resistance-cursor-reveal" in guide_css
    assert "body.yui-taking-over.yui-user-cursor-revealed" in guide_css

    assert "Double .yui-taking-over to out-specificity earlier cursor:auto !important rules." in plugin_runtime_source
    assert "html.yui-taking-over.yui-taking-over," in plugin_runtime_source
    assert re.search(
        r"html\.yui-taking-over\.yui-taking-over,[\s\S]*?html\.yui-taking-over \[data-yui-cursor-hidden=\"true\"\][\s\S]*?body\.yui-guide-plugin-dashboard-running\.yui-taking-over button[\s\S]*?cursor\s*:\s*none\s*!important",
        plugin_runtime_source,
    )
    assert "DEFAULT_RESISTANCE_CURSOR_REVEAL_MS" not in plugin_runtime_source
    assert "yui-guide-control-banner" in guide_css
    assert ".yui-guide-control-banner.is-visible" in guide_css
    assert ".yui-guide-control-banner.is-interrupt-emphasis" in guide_css
    assert "transform: translate(-50%, 0);" in guide_css
    assert "translate(-50%, -50%) scale(3)" in guide_css
    assert "--yui-guide-control-banner-emphasis-ease: cubic-bezier(0.16, 1, 0.3, 1)" in guide_css
    assert "top 420ms var(--yui-guide-control-banner-emphasis-ease)" in guide_css
    assert "yui-guide-control-banner" in overlay_source
    assert "CONTROL_BANNER_TEXT_KEY = 'tutorial.yuiGuide.controlBanner'" in overlay_source
    assert "syncControlBanner()" in overlay_source
    assert "emphasizeControlBanner(durationMs = CONTROL_BANNER_INTERRUPT_EMPHASIS_MS)" in overlay_source
    assert "CONTROL_BANNER_INTERRUPT_EMPHASIS_MS = 2000" in overlay_source
    assert "controlBannerEmphasisActive" in overlay_source
    assert "this.controlBanner.classList.toggle('is-interrupt-emphasis', isEmphasized);" in overlay_source
    assert "renderedControlBannerText" in overlay_source
    assert "renderedControlBannerVisible" in overlay_source
    assert "renderedControlBannerEmphasis" in overlay_source
    assert "this.renderedControlBannerText === text" in overlay_source
    assert "this.renderedControlBannerVisible === isVisible" in overlay_source
    assert "this.renderedControlBannerEmphasis === isEmphasized" in overlay_source
    assert "yui-guide-plugin-control-banner" in plugin_runtime_source
    assert ".yui-guide-plugin-control-banner.is-visible" in plugin_runtime_source
    assert ".yui-guide-plugin-control-banner.is-interrupt-emphasis" in plugin_runtime_source
    assert "transform: translate(-50%, 0);" in plugin_runtime_source
    assert "translate(-50%, -50%) scale(3)" in plugin_runtime_source
    assert "--yui-guide-plugin-control-banner-emphasis-ease: cubic-bezier(0.16, 1, 0.3, 1)" in plugin_runtime_source
    assert "top 420ms var(--yui-guide-plugin-control-banner-emphasis-ease)" in plugin_runtime_source
    assert "CONTROL_BANNER_TEXT_KEY = 'tutorial.yuiGuide.controlBanner'" in plugin_runtime_source
    assert "CONTROL_BANNER_INTERRUPT_EMPHASIS_MS = 2000" in plugin_runtime_source
    assert "syncControlBanner(active?: boolean)" in plugin_runtime_source
    assert "emphasizeControlBanner(durationMs = CONTROL_BANNER_INTERRUPT_EMPHASIS_MS)" in plugin_runtime_source
    assert "controlBannerEmphasisActive" in plugin_runtime_source
    assert "this.controlBanner.classList.toggle('is-interrupt-emphasis', isEmphasized)" in plugin_runtime_source
    assert "renderedControlBannerText" in plugin_runtime_source
    assert "renderedControlBannerVisible" in plugin_runtime_source
    assert "renderedControlBannerEmphasis" in plugin_runtime_source
    assert "this.renderedControlBannerText === text" in plugin_runtime_source
    assert "this.renderedControlBannerVisible === isVisible" in plugin_runtime_source
    assert "this.renderedControlBannerEmphasis === isEmphasized" in plugin_runtime_source

    for locale_name in ["en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"]:
        locale = _read_static_locale(locale_name)
        assert locale["tutorial"]["yuiGuide"]["controlBanner"].strip()

    plugin_locale_dir = Path(__file__).resolve().parents[2] / "frontend" / "plugin-manager/src/i18n/locales"
    for locale_file in ["en-US.ts", "ja.ts", "ko.ts", "zh-CN.ts", "zh-TW.ts", "ru.ts", "pt.ts", "es.ts"]:
        assert "controlBanner:" in (plugin_locale_dir / locale_file).read_text(encoding="utf-8")

    taking_over_block = overlay_source.split("        setTakingOver(active) {", 1)[1].split(
        "        setInteractionShieldSuppressed(active) {",
        1,
    )[0]
    assert "active ? 'none'" not in taking_over_block
    assert "style.cursor = '';" in taking_over_block
    assert "this.syncControlBanner();" in taking_over_block

    director_takeover_block = director_source.split("        setTutorialTakingOver(active, options) {", 1)[1].split(
        "        getAvatarStandInCue(day, sceneId) {",
        1,
    )[0]
    assert "const shouldSyncCursor = !(options && options.syncSystemCursor === false);" in director_takeover_block
    assert "if (isActive && shouldSyncCursor) {" in director_takeover_block
    assert "this.syncSystemCursorHidden(true, 'taking_over_started');" in director_takeover_block

    resistance_block = director_source.split("        suppressResistanceCursorReveal() {", 1)[1].split(
        "        playLightResistance(x, y, options) {",
        1,
    )[0]
    assert "style.cursor = 'none';" not in resistance_block
    assert "classList.add('yui-resistance-cursor-reveal')" not in resistance_block
    assert "return false;" not in resistance_block
    assert "return true;" not in resistance_block
    assert "prepareResistanceCursorReveal" not in director_source
    assert "restoreHiddenCursorAfterResistance" not in director_source
    assert "this.syncSystemCursorHidden(true, 'resistance_cursor_reveal_suppressed');" in resistance_block
    assert "noteUserCursorRevealSuppressionAttempt" in director_source
    assert "noteUserCursorRevealAttempt" not in director_source
    assert "noteUserCursorRevealSuppressionAttempt" in resistance_source
    assert "noteUserCursorRevealAttempt" not in resistance_source
    assert "noteUserCursorRevealSuppressionAttempt" in plugin_runtime_source
    assert "noteUserCursorRevealAttempt" not in plugin_runtime_source

    reveal_block = director_source.split("        suppressUserCursorReveal() {", 1)[1].split(
        "        clearUserCursorRevealSuppression(resetCursor) {",
        1,
    )[0]
    assert "classList.add('yui-user-cursor-revealed')" not in reveal_block
    assert "classList.add('yui-resistance-cursor-reveal')" not in reveal_block
    assert "this.syncSystemCursorHidden(false, 'user_cursor_revealed');" not in reveal_block
    assert "this.syncSystemCursorHidden(true, 'user_cursor_reveal_suppressed');" in reveal_block
    assert "revealUserCursor()" not in director_source
    assert "userCursorRevealed" not in director_source

    plugin_reveal_block = plugin_runtime_source.split("  suppressUserCursorReveal() {", 1)[1].split(
        "  clearUserCursorRevealSuppression() {",
        1,
    )[0]
    assert "classList.add('yui-user-cursor-revealed')" not in plugin_reveal_block
    assert "classList.add('yui-resistance-cursor-reveal')" not in plugin_reveal_block
    assert "classList.remove('yui-resistance-cursor-reveal')" in plugin_reveal_block

    plugin_resistance_reveal_block = plugin_runtime_source.split("  suppressResistanceCursorReveal() {", 1)[1].split(
        "  async playLightResistance(x: number, y: number) {",
        1,
    )[0]
    assert "classList.add('yui-resistance-cursor-reveal')" not in plugin_resistance_reveal_block
    assert "window.setTimeout" in plugin_resistance_reveal_block
    assert "revealUserCursor()" not in plugin_runtime_source
    assert "revealRealCursorTemporarily" not in plugin_runtime_source
    assert "userCursorRevealed" not in plugin_runtime_source

    assert "syncPcSystemCursorHidden(hidden, reason = 'tutorial')" in manager_source
    assert "syncPcSystemCursorHidden(hidden === true, reason);" in manager_source
    assert "function syncPcSystemCursorHidden(hidden, reason = 'tutorial', options)" in common_source
    assert "function syncPcSystemCursorTemporaryReveal(durationMs = 2000, reason = 'tutorial-temporary-reveal', options)" in common_source
    assert "'yui_guide_system_cursor_visibility'" in common_source
    assert "'yui_guide_system_cursor_temporary_reveal'" in common_source
    assert "syncPcSystemCursorTemporaryReveal(normalizedDurationMs, reason)" in director_source
    assert "action: 'yui_guide_system_cursor_visibility'" not in manager_source
    assert "action: 'yui_guide_system_cursor_visibility'" not in director_source
    assert "ensurePcTutorialGlobalOverlayStarted(reason = 'tutorial-started')" in manager_source
    assert "const overlay = window.nekoTutorialOverlay;" in manager_source
    assert "overlay.begin({" in manager_source
    assert "relayYuiGuideTutorialLifecycleStarted(page, source);" in manager_source
    assert "action: 'yui_guide_tutorial_lifecycle_started'" in manager_source
    assert "window.nekoTutorialOverlay.relayToChat(startedMessage);" in manager_source
    assert "window.nekoTutorialOverlay.relayToPet(startedMessage);" in manager_source
    lifecycle_started_block = manager_source.split(
        "    relayYuiGuideTutorialLifecycleStarted(page, source) {",
        1,
    )[1].split(
        "    syncYuiGuideCompactChatFixedLayout",
        1,
    )[0]
    assert lifecycle_started_block.index(
        "this.ensurePcTutorialGlobalOverlayStarted('tutorial-lifecycle-started')"
    ) < lifecycle_started_block.index("const startedMessage = {")
    emit_started_block = manager_source.split(
        "    emitTutorialStarted(page = this.currentPage, source = this.currentTutorialStartSource) {",
        1,
    )[1].split(
        "    showSkipButton() {",
        1,
    )[0]
    assert emit_started_block.index("relayYuiGuideTutorialLifecycleStarted(page, source);") < emit_started_block.index(
        "this.syncPcSystemCursorHidden(true, 'tutorial-started');"
    )
    assert "this.syncPcSystemCursorHidden(true, 'tutorial-started');" in manager_source
    assert "this.syncPcSystemCursorHidden(false, rawReason);" in manager_source
    assert "syncSystemCursorHidden(hidden, reason = 'tutorial')" in director_source
    assert "syncPcSystemCursorHidden(hidden === true, reason);" in director_source
    assert "syncSystemCursorHidden(false, 'interrupt_resist_light')" not in resistance_source
    assert "syncSystemCursorHidden', null, false, 'interrupt_resist_light')" not in resistance_source
    assert "director.revealSystemCursorTemporarily(2000, 'interrupt_resist_light');" in resistance_source
    assert "director.revealRealCursorForInterruptCount();" not in resistance_source
    assert "syncSystemCursorHidden(true, 'interrupt_resist_light')" not in resistance_source
    assert "director.overlay.emphasizeControlBanner();" in resistance_source
    assert "call(this.overlay, 'emphasizeControlBanner', null);" in resistance_source
    assert "call(this.callbacks, 'suppressResistanceCursorReveal', null, normalizedOptions);" in resistance_source
    assert "director.suppressResistanceCursorReveal(normalizedOptions);" in resistance_source
    assert "shouldRestoreHiddenCursorAfterResistance" not in resistance_source
    assert "prepareResistanceCursorReveal" not in resistance_source
    assert "interrupt_resist_light_done" not in resistance_source
    assert "this.syncSystemCursorHidden(false, 'interrupt_angry_exit');" in resistance_source
    assert "call(this.callbacks, 'setTutorialTakingOver', null, true, {" in resistance_source
    assert "director.setTutorialTakingOver(true, {" in resistance_source
    assert "syncSystemCursor: false" in resistance_source
    assert "this.emphasizeControlBanner()" in plugin_runtime_source
    assert "this.syncSystemCursorHidden(false, 'destroy');" in director_source
    assert "syncSystemCursorHidden: optional callback" in resistance_source


def test_externalized_chat_clear_preserves_pc_cursor_without_stale_cache():
    director_source = _read_director()
    app_interpage_source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    clear_target_block = director_source.split(
        "        clearExternalizedChatGuideTarget(options) {",
        1,
    )[1].split(
        "        createAvatarFloatingUnionTarget(key, elements, options) {",
        1,
    )[0]
    cursor_clear_block = app_interpage_source.split(
        "    function applyYuiGuideChatCursor(kind, options) {",
        1,
    )[1].split(
        "        if (isYuiGuidePcCursorOnlyMode()) {",
        1,
    )[0]

    assert "if (shouldClearCursor && shouldPreservePcOverlayCursor) {" in clear_target_block
    assert "this.setHomePcCursorOutputSuppressedForExternalizedChat(false);" in clear_target_block
    assert (
        clear_target_block.index("this.setHomePcCursorOutputSuppressedForExternalizedChat(false);")
        < clear_target_block.index("this.overlay.syncCursorPosition(currentCursorPoint.x, currentCursorPoint.y, true);")
    )
    assert "preservePcOverlayCursor: shouldPreservePcOverlayCursor" in clear_target_block
    assert "else if (isYuiGuidePcCursorOnlyMode()) {" in cursor_clear_block
    assert "yuiGuidePcOverlayCursor = null;" in cursor_clear_block


def test_day1_intro_activation_copy_matches_auto_advance_behavior():
    source = _read_director()
    zh_cn = _read_static_locale("zh-CN")
    en = _read_static_locale("en")

    assert "const INTRO_ACTIVATION_HINT = '稍等一下，我马上开始说话啦～';" in source
    assert "点一下这里，我就能开始说话啦～" not in source
    assert (
        zh_cn["tutorial"]["yuiGuide"]["lines"]["introActivationHint"]
        == "稍等一下，我马上开始说话啦～"
    )
    assert (
        en["tutorial"]["yuiGuide"]["lines"]["introActivationHint"]
        == "Hang on a moment, I'll start talking soon, nyan~!"
    )


def test_plugin_dashboard_manual_open_temporarily_pauses_input_shield():
    source = _read_director()
    manual_open_block = source.split(
        "        async waitForManualPluginDashboardOpen(",
        1,
    )[1].split(
        "        getPluginDashboardExpectedOrigin() {",
        1,
    )[0]

    assert "const shouldRestoreTutorialInputShield = !!(" in manual_open_block
    assert "this.overlay.tutorialInputShieldActive === true" in manual_open_block
    assert "this.overlay.setInteractionShieldSuppressed(true);" in manual_open_block
    assert "this.overlay.setTutorialInputShieldActive(false);" in manual_open_block
    assert "shouldRestoreTutorialInputShield && runId === this.sceneRunId && !this.isStopping()" in manual_open_block
    assert manual_open_block.index("this.overlay.setTutorialInputShieldActive(false);") > manual_open_block.index(
        "this.overlay.setInteractionShieldSuppressed(true);"
    )
    assert manual_open_block.index("this.overlay.setTutorialInputShieldActive(") < manual_open_block.rindex(
        "this.overlay.setInteractionShieldSuppressed(false);"
    )


def test_steps_keep_default_non_home_page_registrations():
    source = _read_steps()
    page_key_block = source.split("const day1Guide = getDailyGuide(1) || {};", 1)[1].split(
        "const steps = {};",
        1,
    )[0]

    assert "const configuredPageKeys = Array.isArray(day1Guide.pageKeys) ? day1Guide.pageKeys : [];" in page_key_block
    assert "const pageKeys = DEFAULT_PAGE_KEYS.concat(configuredPageKeys).filter" in page_key_block
    assert "list.indexOf(page) === index" in page_key_block


def test_timeline_voice_key_resolution_uses_director_before_normalized_audio():
    source = SCENE_ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    runtime_block = source.split("createTimelineAudioRuntime(scene, timelineScene, context)", 1)[1].split(
        "async runLegacyScene",
        1,
    )[0]

    assert "const resolveTimelineVoiceKey = (voiceKey) => {" in runtime_block
    assert "director.resolveAvatarFloatingSceneVoiceKey(legacyScene)" in runtime_block
    assert "return resolvedSceneVoiceKey || voiceKey || audio.voiceKey || legacyScene.voiceKey || '';" in runtime_block
    assert "const resolvedVoiceKey = resolveTimelineVoiceKey(voiceKey);" in runtime_block
    assert "director.getGuideVoiceDurationMs(\n                            resolveTimelineVoiceKey(voiceKey)," in runtime_block


def test_icebreaker_does_not_restart_completed_current_day():
    source = NEW_USER_ICEBREAKER_PATH.read_text(encoding="utf-8")
    start_block = source.split("async function start(reason)", 1)[1].split(
        "activeSession = {",
        1,
    )[0]

    assert "activeSession || isDayCompleted(DAY) || hasCompletedFinalDay()" in start_block


def test_externalized_tutorial_chat_spotlight_targets_compact_input_not_window_shell():
    source = _read_director()

    assert "setExternalizedChatSpotlight('input')" in source
    assert "setExternalizedChatSpotlight('window')" not in source


def test_return_control_scene_highlights_compact_input_while_final_line_plays():
    source = _read_director()

    scene_target_block = source.split("        getSceneSpotlightTarget(stepId, performance) {", 1)[1].split(
        "        getActionSpotlightTarget",
        1,
    )[0]
    persistent_setup_block = source.split("            const persistentSpotlightTarget = this.getSceneSpotlightTarget(stepId, performance);", 1)[1].split(
        "            const actionSpotlightTarget = this.getActionSpotlightTarget(stepId, performance);",
        1,
    )[0]

    assert "if (stepId === 'takeover_return_control')" in scene_target_block
    assert "return this.getChatInputTarget() || fallbackTarget;" in scene_target_block
    assert "if (stepId === 'takeover_return_control') {\n                this.overlay.clearPersistentSpotlight();" not in persistent_setup_block
    assert "this.overlay.setPersistentSpotlight(persistentSpotlightTarget);" in persistent_setup_block


def test_standalone_chat_spotlight_input_prefers_compact_capsule():
    source = _read_interpage()
    target_block = source.split("function getYuiGuideChatSpotlightTarget(kind)", 1)[1].split(
        "function clearYuiGuideChatSpotlightTracking",
        1,
    )[0]

    compact_input_selector = (
        '#react-chat-window-root [data-compact-geometry-owner="surface"]'
        '[data-compact-geometry-item="input"]'
    )
    compact_capsule_selector = (
        '#react-chat-window-root [data-compact-geometry-owner="surface"]'
        '[data-compact-geometry-item="capsule"]'
    )
    legacy_composer_selector = "#react-chat-window-root .composer-panel"

    assert compact_input_selector in target_block
    assert compact_capsule_selector in target_block
    assert target_block.index(compact_input_selector) < target_block.index(legacy_composer_selector)
    assert target_block.index(compact_capsule_selector) < target_block.index(legacy_composer_selector)


def test_tutorial_chat_messages_match_react_assistant_message_shape():
    source = _read_director()
    append_block = source.split("        appendGuideChatMessage(text, options) {", 1)[1].split(
        "            const streamingMessage =",
        1,
    )[0]

    assert "role: 'assistant'" in append_block
    assert "const author = this.getGuideAssistantName();" in append_block
    assert "author: author" in append_block
    assert "avatarLabel:" in append_block
    assert "avatarUrl: this.getGuideAssistantAvatarUrl()" in append_block
    assert "blocks: [{" in append_block
    assert "type: 'text'" in append_block
    assert "status: 'sent'" in append_block


def test_tutorial_chat_streams_finalize_as_sent_on_termination():
    source = _read_director()

    assert "this.activeGuideChatMessages = new Map();" in source
    assert "finalizeActiveGuideChatMessages()" in source

    stream_block = source.split("        streamGuideChatMessage(message, content, options) {", 1)[1].split(
        "        appendGuideChatMessage(text, options) {",
        1,
    )[0]
    assert "this.activeGuideChatMessages.set(String(message.id), message);" in stream_block
    assert "this.activeGuideChatMessages.delete(String(message.id));" in stream_block

    finalize_block = source.split("        finalizeActiveGuideChatMessages() {", 1)[1].split(
        "        scheduleGuideChatStream(callback, delayMs) {",
        1,
    )[0]
    assert "status: 'sent'" in finalize_block
    assert "blocks: message.blocks" in finalize_block
    assert "actions: message.actions" in finalize_block

    termination_block = source.split("        beginTerminationVisualCleanup() {", 1)[1].split(
        "        async run",
        1,
    )[0]
    destroy_block = source.split("        destroy() {", 2)[2].split(
        "        handleGlobalClick",
        1,
    )[0]
    assert "this.finalizeActiveGuideChatMessages();" in termination_block
    assert "this.finalizeActiveGuideChatMessages();" in destroy_block


def test_tutorial_exit_force_hides_managed_home_surfaces_before_async_panel_close():
    source = _read_director()

    side_panel_block = source.split("        forceHideAvatarFloatingSidePanels() {", 1)[1].split(
        "        forceHideAvatarFloatingGuideManagedSurfaces() {",
        1,
    )[0]
    managed_surface_block = source.split(
        "        forceHideAvatarFloatingGuideManagedSurfaces() {",
        1,
    )[1].split(
        "        hideTemporaryAvatarFloatingGuideHud",
        1,
    )[0]
    temporary_hud_block = source.split(
        "        hideTemporaryAvatarFloatingGuideHud(reason) {",
        1,
    )[1].split(
        "        async expandAvatarFloatingSidePanel",
        1,
    )[0]
    close_panels_block = source.split("        async closeAvatarFloatingGuidePanels(options) {", 1)[1].split(
        "        isDay1AvatarFloatingScene",
        1,
    )[0]
    termination_block = source.split("        beginTerminationVisualCleanup() {", 1)[1].split(
        "        async ensureChatVisible",
        1,
    )[0]
    destroy_block = source.split("        destroy() {\n            if (this.destroyed) {", 1)[1].split(
        "        onKeyDown",
        1,
    )[0]

    assert "popupUi.collapseOtherSidePanels(null);" in side_panel_block
    assert "document.querySelectorAll('[data-neko-sidepanel]')" in side_panel_block
    assert "this.forceHideManagedPanel('settings');" in managed_surface_block
    assert "this.forceHideManagedPanel('agent');" in managed_surface_block
    assert "this.forceHideAvatarFloatingSidePanels();" in managed_surface_block
    assert "this.avatarFloatingGuideTemporaryHudShown" in temporary_hud_block
    assert "!this.avatarFloatingGuideTemporaryHudWasVisible" in temporary_hud_block
    assert "window.AgentHUD.hideAgentTaskHUD();" in temporary_hud_block

    assert close_panels_block.index("this.forceHideAvatarFloatingGuideManagedSurfaces();") < close_panels_block.index(
        "await this.closeManagedPanels().catch"
    )
    assert close_panels_block.index("this.hideTemporaryAvatarFloatingGuideHud('close-panels');") < close_panels_block.index(
        "await this.closeManagedPanels().catch"
    )
    assert termination_block.index("this.forceHideAvatarFloatingGuideManagedSurfaces();") < termination_block.index(
        "this.closeManagedPanels().catch"
    )
    assert termination_block.index("this.hideTemporaryAvatarFloatingGuideHud('termination-cleanup');") < termination_block.index(
        "this.closeManagedPanels().catch"
    )
    assert destroy_block.index("this.forceHideAvatarFloatingGuideManagedSurfaces();") < destroy_block.index(
        "this.closeManagedPanels().catch"
    )
    assert destroy_block.index("this.hideTemporaryAvatarFloatingGuideHud('destroy');") < destroy_block.index(
        "this.closeManagedPanels().catch"
    )


def test_new_tutorial_chat_line_finishes_previous_stream_before_append():
    source = _read_director()

    append_block = source.split("        appendGuideChatMessage(text, options) {", 1)[1].split(
        "        focusAndHighlightChatInput",
        1,
    )[0]
    content_index = append_block.index("const content = formatGuideDebugText(")
    clear_index = append_block.index("this.clearGuideChatStreamTimers();")
    finalize_index = append_block.index("this.finalizeActiveGuideChatMessages();")
    message_index = append_block.index("const message = {")

    assert content_index < clear_index < finalize_index < message_index


def test_guide_audio_playback_state_uses_guide_message_id_for_compact_capsule_clear():
    source = _read_director()

    assert "const GUIDE_SPEECH_PLAYBACK_STATE_KEY = 'neko_speech_playback_state';" in source
    assert "const GUIDE_SPEECH_PLAYBACK_CHANNEL_NAME = 'neko_speech_playback_channel';" in source
    assert "publishGuideSpeechPlaybackState('guide_audio_started'" in source
    assert "publishGuideSpeechPlaybackState(success ? 'guide_audio_ended' : 'guide_audio_failed'" in source

    constructor_block = source.split("    class YuiGuideDirector {", 1)[1].split(
        "        async init()",
        1,
    )[0]
    append_block = source.split("        appendGuideChatMessage(text, options) {", 1)[1].split(
        "            if (Array.isArray(normalizedOptions.buttons)",
        1,
    )[0]
    speak_block = source.split("        async speakGuideLine(text, options) {", 1)[1].split(
        "        resolvePerformanceBubbleText",
        1,
    )[0]
    normalize_block = source.split("        normalizeVoiceQueueSpeakOptions(options) {", 1)[1].split(
        "        async guideChatTypeMessage",
        1,
    )[0]
    run_narration_block = source.split("        async runNarration(narration) {", 1)[1].split(
        "        async speakLineAndWait",
        1,
    )[0]

    assert "this.guideChatVoiceMessageIds = new Map();" in constructor_block
    assert "this.guideChatVoiceMessageIds.set(voiceKey, message.id);" in append_block
    assert "this.normalizeVoiceQueueSpeakOptions(options)" in speak_block
    assert "normalizedOptions.playbackTurnId = guideMessageId;" in normalize_block
    assert "playbackTurnId: narration.playbackTurnId" in run_narration_block


def test_settings_peek_copy_matches_existing_voice_audio_script():
    expected_audio_script_markers = {
        "en": ("little space", "warmth of my words"),
        "es": ("little space", "warmth of my words"),
        "ja": ("小さな空間", "ワガママ"),
        "ko": ("우리만의", "다정함"),
        "pt": ("little space", "warmth of my words"),
        "ru": ("крошечном пространстве", "Теплоту"),
        "zh-CN": ("小空间", "说话的温度"),
        "zh-TW": ("小空間", "說話的溫度"),
    }

    for locale_name, (intro_marker, detail_marker) in expected_audio_script_markers.items():
        static_lines = _read_static_locale(locale_name)["tutorial"]["yuiGuide"]["lines"]
        assert intro_marker in static_lines["takeoverSettingsPeekIntro"]
        assert detail_marker in static_lines["takeoverSettingsPeekDetail"]
        assert detail_marker in (
            static_lines["takeoverSettingsPeekDetailPart1"]
            + static_lines["takeoverSettingsPeekDetailPart2"]
        )


def test_zh_cn_intro_basic_copy_matches_step_fallback_and_voice_script():
    day1_source = _read_day1_guide()
    match = re.search(r"bubbleText: '([^']+)',\n\s+bubbleTextKey: 'tutorial\.yuiGuide\.lines\.introBasic'", day1_source)
    assert match is not None
    fallback_text = match.group(1)
    static_intro = _read_static_locale("zh-CN")["tutorial"]["yuiGuide"]["lines"]["introBasic"]

    assert "神奇的按钮" in fallback_text
    assert static_intro == fallback_text
    assert not fallback_text.endswith("喵！")
    assert fallback_text.endswith("啦！")


def test_day1_audio_files_by_key_preserves_locale_override_map():
    day1_source = _read_day1_guide()
    registration_block = _extract_deep_freeze_registration_block(day1_source)

    assert "audioFilesByKey: audioFilesByKey" in registration_block
    assert "audioFileOverridesByKey: audioFilesByKey" in registration_block
    assert "intro_basic: audioFilesForAllLocales(audioFileNames.intro_basic)" not in registration_block
