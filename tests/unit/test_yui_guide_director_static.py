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

    input_block = _function_block(source, "getChatInputTarget", "getChatWindowTarget")
    window_block = _function_block(source, "getChatWindowTarget", "shouldNarrateInChat")
    activation_block = _function_block(source, "getChatIntroActivationTarget", "clearSceneTimers")
    allowed_target_block = source.split("if (this.awaitingIntroActivation) {", 1)[1].split(
        "if (this.manualPluginDashboardOpenAllowed",
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

    assert compact_capsule_selector in allowed_target_block
    assert compact_input_selector in allowed_target_block


def test_day6_plugin_dashboard_handoff_closes_at_narration_boundary():
    source = _read_director()

    assert "const DAY6_PLUGIN_DASHBOARD_DONE_GRACE_MS = 900;" in source
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

    assert "this.waitForPluginDashboardPerformanceUntilNarrationBoundary(pluginDashboardWindow" in dashboard_block
    assert "this.waitForPluginDashboardPerformanceUntilNarrationBoundary(pluginDashboardWindow" in side_panel_block
    assert "await this.waitForPluginDashboardPerformance(pluginDashboardWindow" not in dashboard_block
    assert "await this.waitForPluginDashboardPerformance(pluginDashboardWindow" not in side_panel_block


def test_avatar_floating_guides_keep_real_cursor_visible():
    guide_css = YUI_GUIDE_CSS_PATH.read_text(encoding="utf-8")
    overlay_source = YUI_GUIDE_OVERLAY_PATH.read_text(encoding="utf-8")
    director_source = _read_director()
    plugin_runtime_source = PLUGIN_YUI_GUIDE_RUNTIME_PATH.read_text(encoding="utf-8")

    assert not re.search(r"cursor\s*:\s*none\b", guide_css)
    assert not re.search(r"cursor\s*:\s*none\b", plugin_runtime_source)

    taking_over_block = overlay_source.split("        setTakingOver(active) {", 1)[1].split(
        "        setInteractionShieldSuppressed(active) {",
        1,
    )[0]
    assert "active ? 'none'" not in taking_over_block
    assert "style.cursor = '';" in taking_over_block

    resistance_block = director_source.split("        prepareResistanceCursorReveal() {", 1)[1].split(
        "        playLightResistance(x, y, options) {",
        1,
    )[0]
    assert "style.cursor = 'none';" not in resistance_block
    assert "this.restoreHiddenCursorAfterResistance = false;" in resistance_block


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
