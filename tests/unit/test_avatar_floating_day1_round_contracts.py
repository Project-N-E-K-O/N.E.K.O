from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DAY1_GUIDE_PATH = ROOT / "static" / "tutorial/yui-guide/days/day1-home-guide.js"
DAY2_GUIDE_PATH = ROOT / "static" / "tutorial/yui-guide/days/day2-screen-voice-guide.js"
DAY3_GUIDE_PATH = ROOT / "static" / "tutorial/yui-guide/days/day3-interaction-guide.js"
DAY4_GUIDE_PATH = ROOT / "static" / "tutorial/yui-guide/days/day4-companion-guide.js"
DAY5_GUIDE_PATH = ROOT / "static" / "tutorial/yui-guide/days/day5-personalization-guide.js"
DAY6_GUIDE_PATH = ROOT / "static" / "tutorial/yui-guide/days/day6-agent-guide.js"
DAY7_GUIDE_PATH = ROOT / "static" / "tutorial/yui-guide/days/day7-graduation-guide.js"
DIRECTOR_PATH = ROOT / "static" / "tutorial/yui-guide/director.js"
INTERPAGE_PATH = ROOT / "static" / "app-interpage.js"
REACT_APP_PATH = ROOT / "frontend" / "react-neko-chat" / "src" / "App.tsx"
REACT_SCHEMA_PATH = ROOT / "frontend" / "react-neko-chat" / "src" / "message-schema.ts"
REACT_HOST_PATH = ROOT / "static" / "app-react-chat-window.js"
MANAGER_PATH = ROOT / "static" / "tutorial/core/universal-manager.js"


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


EXPECTED_DAY4_SCENES = [
    "day4_intro_companion",
    "day4_chat_settings",
    "day4_model_behavior",
    "day4_gaze_follow",
    "day4_privacy_mode",
    "day4_model_lock",
    "day4_return_home",
    "day4_wrap",
]


EXPECTED_DAY5_SCENES = [
    "day5_character_settings",
    "day5_character_panic",
    "day5_memory_entry",
    "day5_wrap",
]


EXPECTED_DAY6_SCENES = [
    "day6_intro_agent",
    "day6_agent_status_master",
    "day6_plugin_side_panel",
    "day6_plugin_dashboard",
    "day6_agent_task_hud",
    "day6_agent_task_hud_control",
    "day6_wrap_cleanup",
    "day6_wrap",
]


EXPECTED_DAY7_SCENES = [
    "day7_memory_review",
    "day7_memory_control",
    "day7_graduation_wrap",
]


def assert_scene_order(source, expected):
    first_scene = source.index(f"id: '{expected[0]}'")
    for scene_id in expected[1:]:
        current = source.index(f"id: '{scene_id}'")
        assert first_scene < current
        first_scene = current


def extract_day1_round_block(source):
    return source.split("round: {", 1)[1].split("audioFileNames:", 1)[0]


def test_day1_daily_guide_registers_round_scenes_in_day2_to_7_shape():
    source = DAY1_GUIDE_PATH.read_text(encoding="utf-8")

    assert "round: {" in source
    round_block = extract_day1_round_block(source)
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
    if not DAY3_GUIDE_PATH.exists():
        pytest.skip("Day 3 guide is not shipped in this PR")
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


def test_day4_round_wrap_returns_to_capsule_input_like_day2_wrap():
    if not DAY4_GUIDE_PATH.exists():
        pytest.skip("Day 4 guide is not shipped in this PR")
    source = DAY4_GUIDE_PATH.read_text(encoding="utf-8")
    round_block = source.split("round: {", 1)[1]
    wrap_block = round_block.split("id: 'day4_wrap'", 1)[1]

    for scene_id in EXPECTED_DAY4_SCENES:
        assert f"id: '{scene_id}'" in round_block
    assert_scene_order(round_block, EXPECTED_DAY4_SCENES)
    assert "target: 'chat-input'" in wrap_block
    assert "cursorAction: 'move'" in wrap_block
    assert "operation: 'cleanup'" in wrap_block
    assert "petalTransition: true" in wrap_block


def test_day5_round_wrap_returns_to_capsule_input_like_day2_wrap():
    if not DAY5_GUIDE_PATH.exists():
        pytest.skip("Day 5 guide is not shipped in this PR")
    source = DAY5_GUIDE_PATH.read_text(encoding="utf-8")
    round_block = source.split("round: {", 1)[1]
    wrap_block = round_block.split("id: 'day5_wrap'", 1)[1]

    for scene_id in EXPECTED_DAY5_SCENES:
        assert f"id: '{scene_id}'" in round_block
    assert_scene_order(round_block, EXPECTED_DAY5_SCENES)
    assert "target: 'chat-input'" in wrap_block
    assert "cursorAction: 'move'" in wrap_block
    assert "operation: 'cleanup'" in wrap_block
    assert "petalTransition: true" in wrap_block


def test_day5_wrap_voice_key_has_audio_file():
    if not DAY5_GUIDE_PATH.exists():
        pytest.skip("Day 5 guide is not shipped in this PR")
    source = DAY5_GUIDE_PATH.read_text(encoding="utf-8")
    audio_file = "好啦好啦，快去试试这.mp3"

    assert f"avatar_floating_day5_wrap: zhAudio('{audio_file}')" in source
    assert (ROOT / "static" / "assets" / "tutorial" / "guide-audio" / "zh" / audio_file).is_file()


def test_day6_round_wrap_returns_to_capsule_input_like_day2_wrap():
    if not DAY6_GUIDE_PATH.exists():
        pytest.skip("Day 6 guide is not shipped in this PR")
    source = DAY6_GUIDE_PATH.read_text(encoding="utf-8")
    director_source = DIRECTOR_PATH.read_text(encoding="utf-8")
    round_block = source.split("round: {", 1)[1]
    plugin_side_panel_block = round_block.split("id: 'day6_plugin_side_panel'", 1)[1].split(
        "id: 'day6_plugin_dashboard'",
        1,
    )[0]
    task_hud_block = round_block.split("id: 'day6_agent_task_hud'", 1)[1].split(
        "id: 'day6_agent_task_hud_control'",
        1,
    )[0]
    task_hud_control_block = round_block.split("id: 'day6_agent_task_hud_control'", 1)[1].split(
        "id: 'day6_wrap_cleanup'",
        1,
    )[0]
    wrap_cleanup_block = round_block.split("id: 'day6_wrap_cleanup'", 1)[1].split(
        "id: 'day6_wrap'",
        1,
    )[0]
    wrap_block = round_block.split("id: 'day6_wrap'", 1)[1]

    for scene_id in EXPECTED_DAY6_SCENES:
        assert f"id: '{scene_id}'" in round_block
    assert_scene_order(round_block, EXPECTED_DAY6_SCENES)
    assert "除了之前介绍的功能，这里还有超多好玩的插件呢。" in plugin_side_panel_block
    assert "除了之前介绍的功能，这里还有超多好玩的插件呢'," not in plugin_side_panel_block
    assert "target: '#agent-task-hud'" in task_hud_block
    assert "cursorAction: 'move'" in task_hud_block
    assert "cursorAction: 'tour'" not in task_hud_block
    assert "target: '#agent-task-hud'" in task_hud_control_block
    assert "cursorAction: 'move'" in task_hud_control_block
    assert "cursorAction: 'ellipse'" not in task_hud_control_block
    assert "cursorAction: 'tour'" not in task_hud_control_block
    assert "target: 'chat-input'" in wrap_cleanup_block
    assert "target: 'chat-input'" in wrap_block
    assert "preserveExternalizedChatGuideTarget: true" in wrap_cleanup_block
    assert "cursorAction: 'hold'" in wrap_block
    assert "cursorAction: 'move'" not in wrap_block
    assert "petalTransition: true" in wrap_block
    assert "avatar_floating_day6_wrap: Object.freeze({" in director_source
    assert "zh: 11340" in director_source


def test_day7_round_wrap_returns_to_capsule_input_like_day2_wrap():
    if not DAY7_GUIDE_PATH.exists():
        pytest.skip("Day 7 guide is not shipped in this PR")
    source = DAY7_GUIDE_PATH.read_text(encoding="utf-8")
    round_block = source.split("round: {", 1)[1]
    wrap_block = round_block.split("id: 'day7_graduation_wrap'", 1)[1]

    for scene_id in EXPECTED_DAY7_SCENES:
        assert f"id: '{scene_id}'" in round_block
    assert_scene_order(round_block, EXPECTED_DAY7_SCENES)
    assert "target: 'chat-input'" in wrap_block
    assert "cursorAction: 'move'" in wrap_block
    assert "operation: 'cleanup'" in wrap_block
    assert "petalTransition: true" in wrap_block


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
    takeover = (ROOT / "static" / "tutorial/core/interaction-takeover.js").read_text(encoding="utf-8")

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
    assert "this.setExternalizedChatInputLocked(false, 'clear-externalized-chat-fx');" in fx_block


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


def test_pc_external_chat_spotlight_uses_overlay_without_dom_fallback():
    source = INTERPAGE_PATH.read_text(encoding="utf-8")
    spotlight_block = source.split("function getYuiGuideChatSpotlightElement(createIfMissing)", 1)[1].split(
        "function getYuiGuidePcOverlayHost",
        1,
    )[0]
    update_block = source.split("function updateYuiGuideChatSpotlight(kind)", 1)[1].split(
        "function applyYuiGuideChatSpotlight",
        1,
    )[0]

    assert "isYuiGuidePcOverlayAvailable()" in spotlight_block
    assert "var pcOverlayAvailable = isYuiGuidePcOverlayAvailable();" in update_block
    assert "getYuiGuideChatSpotlightElement(!pcOverlayAvailable)" in update_block
    assert "sendYuiGuidePcOverlayPatch({ spotlights: pcRects });" in update_block


def test_pc_overlay_cursor_effect_is_one_shot_not_persisted_on_home_bridge():
    source = (ROOT / "static" / "tutorial/yui-guide/overlay.js").read_text(encoding="utf-8")
    bridge_block = source.split("function createPcOverlayBridge(doc)", 1)[1].split(
        "function createExtraSpotlightElement",
        1,
    )[0]
    send_block = source.split("const send = (patch, force) => {", 1)[1].split(
        "const key = JSON.stringify(payload || {});",
        1,
    )[0]

    assert "function withoutTransientCursorEffect(cursor)" in bridge_block
    assert "currentCursor = withoutTransientCursorEffect(patch.cursor);" in send_block
    assert "payload.cursor = patch.cursor || null;" in send_block
    assert "payload.cursor = currentCursor;" in send_block


def test_pc_overlay_cursor_effect_is_one_shot_not_persisted_on_external_chat_bridge():
    source = INTERPAGE_PATH.read_text(encoding="utf-8")
    bridge_block = source.split("function sendYuiGuidePcOverlayPatch(patch)", 1)[1].split(
        "function isYuiGuidePcCursorOnlyMode()",
        1,
    )[0]

    assert "function withoutTransientYuiGuideCursorEffect(cursor)" in source
    assert "yuiGuidePcOverlayCursor = withoutTransientYuiGuideCursorEffect(patch.cursor);" in bridge_block
    assert "payload.cursor = patch.cursor || null;" in bridge_block
    assert "payload.cursor = yuiGuidePcOverlayCursor;" in bridge_block


def test_day1_round_start_uses_avatar_floating_round_lifecycle():
    source = MANAGER_PATH.read_text(encoding="utf-8")
    start_block = source.split("async startAvatarFloatingGuideRound(day, options = {})", 1)[1].split(
        "clearModelManagerTutorialRecheckTimer()",
        1,
    )[0]

    assert "if (round === 1)" not in start_block
    assert "requestTutorialStart" not in start_block
    assert "director.playAvatarFloatingRound(round" in start_block


def test_avatar_floating_round_start_keeps_tutorial_model_reload_before_first_scene():
    source = MANAGER_PATH.read_text(encoding="utf-8")
    prelude_source = (ROOT / "static" / "tutorial/core/round-prelude-controller.js").read_text(encoding="utf-8")
    start_block = source.split("async startAvatarFloatingGuideRound(day, options = {})", 1)[1].split(
        "clearModelManagerTutorialRecheckTimer()",
        1,
    )[0]

    assert "this._tutorialModelPrefix = 'live2d';" in start_block
    assert "await this.playAvatarFloatingRoundPrelude(round, source, director);" in start_block
    assert "this.beginAvatarOverride()" in prelude_source
    assert "this.ensureVisible(sceneId)" in prelude_source
    assert "director.playAvatarFloatingRound(round" in start_block
    assert start_block.index("this.playAvatarFloatingRoundPrelude(round, source, director)") < start_block.index(
        "director.playAvatarFloatingRound(round"
    )


def test_avatar_floating_round_waits_after_tutorial_model_is_visible():
    source = MANAGER_PATH.read_text(encoding="utf-8")
    prelude_source = (ROOT / "static" / "tutorial/core/round-prelude-controller.js").read_text(encoding="utf-8")
    start_block = source.split("async startAvatarFloatingGuideRound(day, options = {})", 1)[1].split(
        "clearModelManagerTutorialRecheckTimer()",
        1,
    )[0]

    assert "await toPromise(() => this.sleep(delayMs));" in prelude_source
    assert "this.defaultDelayMs" in prelude_source
    assert "1500" in prelude_source
    assert prelude_source.index("this.ensureVisible(sceneId)") < prelude_source.index(
        "await toPromise(() => this.sleep(delayMs));"
    )
    assert start_block.index("this.playAvatarFloatingRoundPrelude(round, source, director)") < start_block.index(
        "director.playAvatarFloatingRound(round"
    )


def test_avatar_floating_round_does_not_preheat_surface_before_playback():
    source = MANAGER_PATH.read_text(encoding="utf-8")
    start_block = source.split("async startAvatarFloatingGuideRound(day, options = {})", 1)[1].split(
        "clearModelManagerTutorialRecheckTimer()",
        1,
    )[0]

    assert "surfaceReadyPromise" not in start_block
    assert "ensureAvatarFloatingGuideSurfaceReady(round)" not in start_block
    assert "surfaceReady: true" in start_block


def test_tutorial_avatar_override_does_not_capture_avatar_preview():
    source = (ROOT / "static" / "tutorial/avatar/reload-controller.js").read_text(encoding="utf-8")
    begin_block = source.split("beginOverride()", 1)[1].split("restoreOverride()", 1)[0]

    assert "this.sleep(350)" not in begin_block
    assert "captureAvatarPreview" not in source
    assert "startIdentityOverrideCapture" not in source
    assert "this.applyIdentityOverride({" in begin_block
    assert begin_block.index("this.applyIdentityOverride({") > begin_block.index(
        "await this.reloadModel(currentName, tutorialModelPayload, { temporary: true });"
    )


def test_avatar_floating_round_does_not_start_idle_sway_before_first_scene():
    source = (ROOT / "static" / "tutorial/core/scene-orchestrator.js").read_text(encoding="utf-8")
    round_block = source.split("async playRound(round, options)", 1)[1].split(
        "return {",
        1,
    )[0]
    before_scene_loop = round_block.split("for (let index = 0; index < config.scenes.length; index += 1)", 1)[0]

    assert "ensureGuideIdleSwayPerformance()" not in before_scene_loop


def test_avatar_floating_round_does_not_await_look_at_before_first_scene():
    source = (ROOT / "static" / "tutorial/core/scene-orchestrator.js").read_text(encoding="utf-8")
    round_block = source.split("async playRound(round, options)", 1)[1].split(
        "return {",
        1,
    )[0]
    before_scene_loop = round_block.split("for (let index = 0; index < config.scenes.length; index += 1)", 1)[0]

    assert "director.withLookAt({" in before_scene_loop
    assert "director.ensurePersistentGhostCursorLookAtPerformance(" not in before_scene_loop


def test_day1_chat_input_round_rect_highlight_excludes_mid_flow_cursor_scenes():
    source = DAY1_GUIDE_PATH.read_text(encoding="utf-8")
    round_block = extract_day1_round_block(source)
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

    return_control_scene = round_block.split("id: 'day1_takeover_return_control'", 1)[1]
    assert "cursorAction: 'move'" in return_control_scene
    assert "cursorAction: 'wobble'" not in return_control_scene

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
    assert "setExternalizedChatCursor('');" not in activation_block
    assert "this.hideHomeCursorForExternalizedChat();" in activation_block
    assert "setSpotlightGeometryHint(inputTarget" in greeting_block
    assert "overlay.setPersistentSpotlight(inputTarget)" in greeting_block


def test_day1_capsule_drag_hint_copy_uses_single_click_language():
    source = DAY1_GUIDE_PATH.read_text(encoding="utf-8")
    capsule_block = source.split("id: 'day1_capsule_drag_hint'", 1)[1].split(
        "id: 'day1_history_handle'",
        1,
    )[0]

    assert "点击一下就能随时发消息给我哦！" in capsule_block
    assert "双击两下" not in capsule_block


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
    assert "setExternalizedChatCursor('');" not in greeting_block
    assert "this.hideHomeCursorForExternalizedChat();" in greeting_block
    assert "this.cursor.hide();" not in greeting_block
    assert "cursor.wobble" not in greeting_block


def test_day1_legacy_externalized_intro_greeting_does_not_send_cursor_wobble():
    source = DIRECTOR_PATH.read_text(encoding="utf-8")
    externalized_block = source.split("async runChatIntroPreludeExternalized", 1)[1].split(
        "const introText = this.resolvePerformanceBubbleText",
        1,
    )[0]

    assert "setExternalizedChatSpotlight('input')" in externalized_block
    assert "setExternalizedChatCursor('input'" not in externalized_block
    assert "setExternalizedChatCursor('');" not in externalized_block
    assert "effect: 'wobble'" not in externalized_block
    assert "this.cursor.hide();" not in externalized_block
    assert "hideHomeCursorForExternalizedChat" in externalized_block


def test_day2_intro_externalized_cursor_uses_scene_action_not_wobble():
    source = DIRECTOR_PATH.read_text(encoding="utf-8")
    first_daily_externalized_block = source.split("if (introExternalizedChatSpotlightKind) {", 1)[1].split(
        "} else if (introChatSpotlightTarget)",
        1,
    )[0]

    assert "effect: this.getExternalizedChatCursorEffect(scene)" in first_daily_externalized_block
    assert "effect: 'wobble'" not in first_daily_externalized_block


def test_only_day1_tutorial_configs_use_cursor_wobble():
    guide_files = sorted(Path("static").glob("tutorial/yui-guide/days/day*-*.js"))
    for guide_file in guide_files:
        if guide_file.name.startswith("tutorial/yui-guide/days/day1-"):
            continue
        source = guide_file.read_text(encoding="utf-8")
        assert "cursorAction: 'wobble'" not in source
