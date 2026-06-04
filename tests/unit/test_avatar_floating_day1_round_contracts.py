from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DAY1_GUIDE_PATH = ROOT / "static" / "yui-guide-day1-home-guide.js"
DAY2_GUIDE_PATH = ROOT / "static" / "yui-guide-day2-screen-voice-guide.js"
DAY3_GUIDE_PATH = ROOT / "static" / "yui-guide-day3-interaction-guide.js"
DIRECTOR_PATH = ROOT / "static" / "yui-guide-director.js"
INTERPAGE_PATH = ROOT / "static" / "app-interpage.js"
REACT_APP_PATH = ROOT / "frontend" / "react-neko-chat" / "src" / "App.tsx"
REACT_SCHEMA_PATH = ROOT / "frontend" / "react-neko-chat" / "src" / "message-schema.ts"
REACT_HOST_PATH = ROOT / "static" / "app-react-chat-window.js"
MANAGER_PATH = ROOT / "static" / "universal-tutorial-manager.js"
RESET_PATH = ROOT / "static" / "avatar-floating-guide-reset.js"


EXPECTED_DAY1_SCENES = [
    "day1_intro_activation",
    "day1_intro_greeting",
    "day1_capsule_drag_hint",
    "day1_history_handle",
    "day1_intro_basic_voice",
    "day1_screen_entry",
    "day1_screen_entry_invite",
    "day1_takeover_capture_cursor",
    "day1_takeover_return_control",
]

EXPECTED_DAY2_SCENES = [
    "day2_intro_context",
    "day2_personalization_space",
    "day2_personalization_detail",
    "day2_proactive_chat",
    "day2_wrap_intro",
    "day2_wrap_companion",
    "day2_wrap",
]

EXPECTED_DAY3_SCENES = [
    "day3_tool_toggle_intro",
    "day3_avatar_tools",
    "day3_avatar_tools_props",
    "day3_galgame_entry",
    "day3_galgame_choices",
    "day3_wrap",
    "day3_wrap_ready",
]


def assert_scene_order(source, expected):
    first_scene = source.index(f"id: '{expected[0]}'")
    for scene_id in expected[1:]:
        current = source.index(f"id: '{scene_id}'")
        assert first_scene < current
        first_scene = current


def test_day1_daily_guide_registers_round_scenes_in_day2_to_7_shape():
    source = DAY1_GUIDE_PATH.read_text(encoding="utf-8")

    assert "round: {" in source
    round_block = source.split("round: {", 1)[1].split("sceneOrder:", 1)[0]
    assert "scenes: [" in round_block
    for scene_id in EXPECTED_DAY1_SCENES:
        assert f"id: '{scene_id}'" in round_block
    for old_scene_id in [
        "day1_takeover_plugin_preview_home",
        "day1_takeover_plugin_dashboard",
        "day1_takeover_settings_peek_intro",
        "day1_takeover_settings_peek_detail",
        "day1_takeover_proactive_chat",
    ]:
        assert f"id: '{old_scene_id}'" not in round_block

    assert_scene_order(round_block, EXPECTED_DAY1_SCENES)


def test_day2_round_keeps_intro_text_and_moves_personalization_after_it():
    source = DAY2_GUIDE_PATH.read_text(encoding="utf-8")
    round_block = source.split("round: {", 1)[1]
    detail_block = round_block.split("id: 'day2_personalization_detail'", 1)[1].split(
        "id: 'day2_proactive_chat'",
        1,
    )[0]
    wrap_intro_block = round_block.split("id: 'day2_wrap_intro'", 1)[1].split(
        "id: 'day2_wrap_companion'",
        1,
    )[0]
    wrap_companion_block = round_block.split("id: 'day2_wrap_companion'", 1)[1].split(
        "id: 'day2_wrap'",
        1,
    )[0]
    wrap_block = round_block.split("id: 'day2_wrap'", 1)[1]

    for scene_id in EXPECTED_DAY2_SCENES:
        assert f"id: '{scene_id}'" in round_block
    assert_scene_order(round_block, EXPECTED_DAY2_SCENES)
    assert "昨天你一直在噼里啪啦打字，我还没听过你说话呢。" in round_block
    assert "voiceKey: 'avatar_floating_day2_intro'" in round_block
    assert "id: 'day2_screen_entry'" not in round_block
    assert "id: 'day2_screen_entry_invite'" not in round_block
    assert "cursorAction: 'wobble'" not in round_block
    assert "target: '#${p}-menu-character'" in detail_block
    assert "cursorAction: 'click'" in detail_block
    assert "target: '#${p}-popup-settings'" not in detail_block
    assert "target: 'chat-input'" in wrap_intro_block
    assert "target: 'chat-input'" in wrap_companion_block
    assert "target: 'chat-input'" in wrap_block


def test_day3_round_targets_new_compact_tool_flow():
    source = DAY3_GUIDE_PATH.read_text(encoding="utf-8")
    round_block = source.split("round: {", 1)[1]
    intro_block = round_block.split("id: 'day3_tool_toggle_intro'", 1)[1].split(
        "id: 'day3_avatar_tools'",
        1,
    )[0]
    avatar_tools_block = round_block.split("id: 'day3_avatar_tools'", 1)[1].split(
        "id: 'day3_avatar_tools_props'",
        1,
    )[0]
    avatar_tools_props_block = round_block.split("id: 'day3_avatar_tools_props'", 1)[1].split(
        "id: 'day3_galgame_entry'",
        1,
    )[0]

    for scene_id in EXPECTED_DAY3_SCENES:
        assert f"id: '{scene_id}'" in round_block
    assert_scene_order(round_block, EXPECTED_DAY3_SCENES)
    assert "day3_avatar_tools_more" not in round_block
    assert "avatarToolsMore" not in round_block
    assert "avatar_floating_day3_avatar_tools_more" not in round_block
    assert "show-galgame-in-compact-tool-fan" not in round_block
    assert "cursorAction: 'wobble'" not in round_block
    assert "target: 'chat-input'" in intro_block
    assert "cursorAction: 'move'" in intro_block
    assert "operation: 'open-compact-tool-fan'" not in intro_block
    assert "persistent: 'chat-tool-toggle'" in avatar_tools_block
    assert "target: 'chat-tool-toggle'" in avatar_tools_block
    assert "cursorAction: 'click'" in avatar_tools_block
    assert "cursorMoveDurationMs: 1480" in avatar_tools_block
    assert "operation: 'open-compact-tool-fan'" in avatar_tools_block
    assert "persistent: 'chat-tool-toggle'" in avatar_tools_props_block
    assert "target: 'chat-avatar-tools'" in avatar_tools_props_block
    assert "cursorAction: 'click'" in avatar_tools_props_block
    assert "operation: 'show-avatar-tools-then-hide-after-narration'" in avatar_tools_props_block
    assert "target: 'chat-tool-toggle'" in round_block
    assert "target: 'chat-avatar-tools'" in round_block
    assert "target: 'chat-galgame'" in round_block
    assert "day3_chat_tools" not in round_block
    assert "day3_galgame_games" not in round_block


def test_compact_chat_tutorial_bridge_exposes_new_targets_and_requests():
    director = DIRECTOR_PATH.read_text(encoding="utf-8")
    interpage = INTERPAGE_PATH.read_text(encoding="utf-8")
    react_app = REACT_APP_PATH.read_text(encoding="utf-8")
    react_schema = REACT_SCHEMA_PATH.read_text(encoding="utf-8")
    react_host = REACT_HOST_PATH.read_text(encoding="utf-8")

    for token in [
        "chat-history-handle",
        "chat-tool-toggle",
        ".compact-history-visibility-handle",
        ".send-button-circle.compact-input-tool-toggle",
        ".compact-input-tool-item-avatar",
        ".compact-input-tool-item-galgame",
        "setCompactToolFanOpen",
        "setExternalizedChatCompactHistoryOpen",
    ]:
        assert token in director

    assert "yui_guide_set_compact_history_open" in interpage
    assert "yui_guide_set_compact_tool_fan_open" in interpage
    assert "compactToolFanOpenRequest" in react_schema
    assert "compactToolFanOpenRequest" in react_app
    assert "setCompactToolFanOpen" in react_host


def test_external_chat_cursor_retry_cannot_replay_stale_wobble_after_clear():
    source = INTERPAGE_PATH.read_text(encoding="utf-8")

    assert "yuiGuideChatCursorRequestToken" in source
    assert "var cursorRequestToken = ++yuiGuideChatCursorRequestToken;" in source
    assert "if (cursorRequestToken !== yuiGuideChatCursorRequestToken) {" in source


def test_tutorial_exit_clears_externalized_guide_chat_messages():
    director = DIRECTOR_PATH.read_text(encoding="utf-8")
    takeover = (ROOT / "static" / "tutorial-interaction-takeover.js").read_text(encoding="utf-8")

    termination_block = director.split("beginTerminationVisualCleanup()", 1)[1].split(
        "async playAvatarFloatingScene",
        1,
    )[0]
    destroy_block = director.split("requestTermination(reason, tutorialReason)", 1)[1].split(
        "updatePlaybackState",
        1,
    )[0]
    fx_block = takeover.split("clearExternalizedChatFx()", 1)[1].split(
        "onExternalChatReady()",
        1,
    )[0]

    assert "clearGuideChatMessages()" in director
    assert "action: 'yui_guide_clear_chat_messages'" in director
    assert termination_block.index("this.clearGuideChatStreamTimers();") < termination_block.index(
        "this.clearGuideChatMessages();"
    )
    assert "this.clearGuideChatMessages();" in termination_block
    assert destroy_block.index("this.clearGuideChatStreamTimers();") < destroy_block.index(
        "this.clearGuideChatMessages();"
    )
    assert "this.clearGuideChatMessages();" in destroy_block
    assert "clearExternalizedChatGuideMessages()" in takeover
    assert "this.clearExternalizedChatGuideMessages();" in fx_block


def test_pc_external_chat_ghost_cursor_routes_to_global_overlay_only():
    source = INTERPAGE_PATH.read_text(encoding="utf-8")
    cursor_block = source.split("function applyYuiGuideChatCursor(kind, options)", 1)[1].split(
        "function clearYuiGuideChatSpotlightTracking()",
        1,
    )[0]

    assert "yui-guide-chat-cursor" not in source
    assert "getYuiGuideChatCursorElement" not in source
    assert "cancelYuiGuideChatCursorElementAnimations" not in source
    assert ".animate(" not in cursor_block
    assert "sendYuiGuidePcOverlayPatch({" in cursor_block
    assert "isYuiGuidePcCursorOnlyMode" in source
    assert "cursor: {" in cursor_block
    assert "visible: true" in cursor_block
    assert "effect: normalizedOptions.effect || ''" in cursor_block
    assert "cursor.hidden = false" not in cursor_block
    assert "if (isYuiGuidePcCursorOnlyMode())" in cursor_block


def test_day1_round_start_uses_avatar_floating_round_lifecycle():
    source = MANAGER_PATH.read_text(encoding="utf-8")
    start_block = source.split("async startAvatarFloatingGuideRound(day, options = {})", 1)[1].split(
        "clearModelManagerTutorialRecheckTimer()",
        1,
    )[0]

    assert "if (round === 1)" not in start_block
    assert "requestTutorialStart" not in start_block
    assert "director.playAvatarFloatingRound(round" in start_block


def test_day1_reset_uses_avatar_floating_day_launcher():
    source = RESET_PATH.read_text(encoding="utf-8")
    reset_block = source.split("async function resetHomeTutorialDay(day, options = {})", 1)[1].split(
        "function detectModelPrefix()",
        1,
    )[0]

    assert "if (round === 1)" not in reset_block
    assert "resetPageTutorial('home')" not in reset_block
    assert "startAvatarFloatingGuideDay(round" in reset_block


def test_day1_reset_fallback_keeps_the_same_scene_shape():
    source = RESET_PATH.read_text(encoding="utf-8")
    day1_block = source.split("1: {", 1)[1].split("2: {", 1)[0]

    for scene_id in EXPECTED_DAY1_SCENES:
        assert f"id: '{scene_id}'" in day1_block
    assert day1_block.count("cursorAction: 'wobble'") == 1
    assert "id: 'day1_capsule_drag_hint'" in day1_block.split("cursorAction: 'wobble'", 1)[0]


def test_day1_chat_input_round_rect_highlight_excludes_mid_flow_cursor_scenes():
    source = DAY1_GUIDE_PATH.read_text(encoding="utf-8")
    round_block = source.split("round: {", 1)[1].split("sceneOrder:", 1)[0]
    greeting_scene_block = round_block.split("id: 'day1_intro_greeting'", 1)[1].split("id: 'day1_capsule_drag_hint'", 1)[0]
    capsule_block = round_block.split("id: 'day1_capsule_drag_hint'", 1)[1].split("id: 'day1_history_handle'", 1)[0]
    history_block = round_block.split("id: 'day1_history_handle'", 1)[1].split("id: 'day1_intro_basic_voice'", 1)[0]
    screen_entry_block = round_block.split("id: 'day1_screen_entry'", 1)[1].split("id: 'day1_screen_entry_invite'", 1)[0]
    screen_invite_block = round_block.split("id: 'day1_screen_entry_invite'", 1)[1].split(
        "id: 'day1_takeover_capture_cursor'",
        1,
    )[0]

    assert "id: 'day1_intro_greeting'" in round_block
    assert "id: 'day1_takeover_return_control'" in round_block
    assert "cursorAction: 'wobble'" not in greeting_scene_block
    assert "target: 'chat-input'" in capsule_block
    assert "spotlight: false" in capsule_block
    assert "cursorWobbleDurationMs: 2000" in capsule_block
    assert "target: 'chat-input'" in history_block
    assert "cursorTarget: 'chat-history-handle'" in history_block
    assert "spotlight: false" in history_block
    assert "persistent: 'chat-input'" not in history_block
    assert "cursorAction: 'move'" in screen_entry_block
    assert "cursorAction: 'wobble'" not in screen_entry_block
    assert "cursorAction: 'move'" in screen_invite_block
    assert "cursorAction: 'wobble'" not in screen_invite_block

    return_control_performance = source.split("takeover_return_control: {", 1)[1].split(
        "interrupt_resist_light:",
        1,
    )[0]
    assert "cursorAction: 'move'" in return_control_performance
    assert "cursorAction: 'wobble'" not in return_control_performance

    director_source = DIRECTOR_PATH.read_text(encoding="utf-8")
    assert "scene.cursorTarget || scene.target || ''" in director_source
    assert "scene.cursorTarget || scene.target" in director_source

    director = DIRECTOR_PATH.read_text(encoding="utf-8")
    activation_block = director.split("async playDay1IntroActivationRoundScene", 1)[1].split(
        "async playDay1IntroGreetingRoundScene",
        1,
    )[0]
    greeting_block = director.split("async playDay1IntroGreetingRoundScene", 1)[1].split(
        "async playDay1IntroBasicVoiceRoundScene",
        1,
    )[0]
    assert "focusAndHighlightChatInput" not in activation_block
    assert "setExternalizedChatSpotlight('input')" not in activation_block
    assert "setExternalizedChatCursor('input'" not in activation_block
    assert "effect: 'wobble'" not in activation_block
    assert "setExternalizedChatCursor('');" in activation_block
    assert "this.cursor.hide();" in activation_block
    assert "setSpotlightGeometryHint(inputTarget" in greeting_block
    assert "overlay.setPersistentSpotlight(inputTarget)" in greeting_block


def test_day1_intro_basic_voice_moves_from_history_handle_anchor():
    director = DIRECTOR_PATH.read_text(encoding="utf-8")
    showcase_block = director.split("async runIntroVoiceControlButtonShowcase", 1)[1].split(
        "async runTakeoverKeyboardControlSequence",
        1,
    )[0]

    assert "getAvatarFloatingSceneCursorAnchor('day1_history_handle')" in showcase_block
    assert "this.cursor.showAt(historyHandleAnchor.x, historyHandleAnchor.y);" in showcase_block
    assert "await this.moveCursorToElement(voiceControlButton, moveDurationMs);" in showcase_block


def test_day1_intro_greeting_highlights_input_without_cursor_wobble():
    source = DIRECTOR_PATH.read_text(encoding="utf-8")
    greeting_block = source.split("async playDay1IntroGreetingRoundScene", 1)[1].split(
        "async playDay1IntroBasicVoiceRoundScene",
        1,
    )[0]

    assert "setExternalizedChatSpotlight('input')" in greeting_block
    assert "setExternalizedChatCursor('input'" not in greeting_block
    assert "setSpotlightGeometryHint(inputTarget" in greeting_block
    assert "overlay.setPersistentSpotlight(inputTarget)" in greeting_block
    assert "setExternalizedChatCursor('');" in greeting_block
    assert "this.cursor.hide();" in greeting_block
    assert "cursor.wobble" not in greeting_block


def test_day1_legacy_externalized_intro_greeting_does_not_send_cursor_wobble():
    source = DIRECTOR_PATH.read_text(encoding="utf-8")
    externalized_block = source.split("async runChatIntroPreludeExternalized", 1)[1].split(
        "const introText = this.resolvePerformanceBubbleText",
        1,
    )[0]

    assert "setExternalizedChatSpotlight('input')" in externalized_block
    assert "setExternalizedChatCursor('input'" not in externalized_block
    assert "setExternalizedChatCursor('');" in externalized_block
    assert "effect: 'wobble'" not in externalized_block
    assert "this.cursor.hide();" in externalized_block
    assert "hideHomeCursorForExternalizedChat" not in externalized_block


def test_day2_intro_externalized_cursor_uses_scene_action_not_wobble():
    source = DIRECTOR_PATH.read_text(encoding="utf-8")
    first_daily_externalized_block = source.split("if (introExternalizedChatSpotlightKind) {", 1)[1].split(
        "} else if (introChatSpotlightTarget)",
        1,
    )[0]

    assert "effect: this.getExternalizedChatCursorEffect(scene)" in first_daily_externalized_block
    assert "effect: 'wobble'" not in first_daily_externalized_block


def test_day2_and_day3_reset_fallbacks_match_new_scene_shape():
    source = RESET_PATH.read_text(encoding="utf-8")
    day2_block = source.split("2: {", 1)[1].split("3: {", 1)[0]
    day3_block = source.split("3: {", 1)[1].split("4: {", 1)[0]
    day2_detail_block = day2_block.split("id: 'day2_personalization_detail'", 1)[1].split(
        "id: 'day2_proactive_chat'",
        1,
    )[0]
    reset_open_settings_block = source.split("step.operation === 'day2-open-settings-personalization'", 1)[1].split(
        "step.operation === 'day2-settings-detail'",
        1,
    )[0]

    for scene_id in EXPECTED_DAY2_SCENES:
        assert f"id: '{scene_id}'" in day2_block
    assert "id: 'day2_screen_entry'" not in day2_block
    assert "id: 'day2_screen_entry_invite'" not in day2_block
    assert "cursorAction: 'wobble'" not in day2_block
    assert "selector: '#${prefix}-menu-character'" in day2_detail_block
    assert "cursorAction: 'click'" in day2_detail_block
    assert "selector: '#${prefix}-popup-settings'" not in day2_detail_block
    assert "prepareResetStepOperation(step, prefix)" in source
    assert "ensureResetCharacterSettingsSidePanelVisible(prefix)" in source
    assert "runGhostCursorEllipseOnTarget(sidePanel, token)" in source
    assert "collapseResetCharacterSettingsSidePanel()" in source
    assert "ensureResetCharacterSettingsSidePanelVisible(prefix)" not in reset_open_settings_block
    for scene_id in EXPECTED_DAY3_SCENES:
        assert f"id: '{scene_id}'" in day3_block
