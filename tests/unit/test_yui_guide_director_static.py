from pathlib import Path
import json
import re


YUI_GUIDE_DIRECTOR_PATH = Path(__file__).resolve().parents[2] / "static" / "yui-guide-director.js"
YUI_GUIDE_STEPS_PATH = Path(__file__).resolve().parents[2] / "static" / "yui-guide-steps.js"
APP_INTERPAGE_PATH = Path(__file__).resolve().parents[2] / "static" / "app-interpage.js"
STATIC_LOCALES_DIR = Path(__file__).resolve().parents[2] / "static" / "locales"


def _read_director() -> str:
    return YUI_GUIDE_DIRECTOR_PATH.read_text(encoding="utf-8")


def _read_steps() -> str:
    return YUI_GUIDE_STEPS_PATH.read_text(encoding="utf-8")


def _read_interpage() -> str:
    return APP_INTERPAGE_PATH.read_text(encoding="utf-8")


def _read_static_locale(locale_name: str) -> dict:
    return json.loads((STATIC_LOCALES_DIR / f"{locale_name}.json").read_text(encoding="utf-8"))


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
        "en": ("gear icon", "replace me"),
        "es": ("icono de engranaje", "reemplazarme"),
        "ja": ("歯車", "取り替える"),
        "ko": ("톱니바퀴", "바꾸려는"),
        "pt": ("ícone de engrenagem", "substituir"),
        "ru": ("шестеренке", "заменить"),
        "zh-CN": ("齿轮", "把我换掉吧？啊啊啊不行"),
        "zh-TW": ("齒輪", "把我換掉吧？啊啊啊不行"),
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
    steps_source = _read_steps()
    match = re.search(r"steps\.intro_basic\.performance\.bubbleText = '([^']+)';", steps_source)
    assert match is not None
    fallback_text = match.group(1)
    static_intro = _read_static_locale("zh-CN")["tutorial"]["yuiGuide"]["lines"]["introBasic"]

    assert "神奇的小按钮" in fallback_text
    assert static_intro == fallback_text
