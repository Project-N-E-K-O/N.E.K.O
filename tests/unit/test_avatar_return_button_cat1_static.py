from pathlib import Path
from tests.static_app_parts import read_js_parts

from main_routers import pages_router
from tests.unit.avatar_ui_buttons_source import read_avatar_ui_buttons_source


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_DIR = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"


def _read_avatar_ui_buttons_source() -> str:
    return read_avatar_ui_buttons_source()


APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
APP_REACT_CHAT_WINDOW_PATH = PROJECT_ROOT / "static" / "app" / "app-react-chat-window"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app" / "app-interpage"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"
CAT1_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1.gif"
CAT1_PLAY_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat-play-1.gif"
CAT1_EAT_SOUND_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat1-voice-eat.mp3"


def test_cat1_return_button_visual_contract_is_present():
    source = _read_avatar_ui_buttons_source()

    assert "neko:auto-goodbye:state-change" in source
    assert "data-neko-idle-tier" in source
    assert "/static/assets/neko-idle/cat-idle-cat1.gif" in source

    create_return_block = source.split("ManagerPrototype.createReturnButton = function()", 1)[1].split(
        "ManagerPrototype._setupReturnButtonDrag",
        1,
    )[0]
    assert "rest_off.png" not in create_return_block
    assert "rest_on.png" not in create_return_block
    assert "neko-idle-return-art" in create_return_block


def test_cat1_return_button_assets_are_version_tracked():
    assert set(AVATAR_UI_BUTTONS_DIR.glob("*.js")) <= set(pages_router._YUI_GUIDE_ASSET_VERSION_PATHS)
    assert INDEX_CSS_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT1_ASSET_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT1_PLAY_ASSET_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT1_EAT_SOUND_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT1_ASSET_PATH.is_file()
    assert CAT1_PLAY_ASSET_PATH.is_file()
    assert CAT1_EAT_SOUND_PATH.is_file()


def test_cat1_play_action_module_is_independent_from_eat_action():
    source = _read_avatar_ui_buttons_source()
    app_ui_source = read_js_parts(APP_UI_PATH)
    css = INDEX_CSS_PATH.read_text(encoding="utf-8")
    chat_source = read_js_parts(APP_REACT_CHAT_WINDOW_PATH)
    interpage_source = read_js_parts(APP_INTERPAGE_PATH)

    assert "_NEKO_IDLE_CAT1_PLAY_ASSET_URL = '/static/assets/neko-idle/cat-idle-cat-play-1.gif'" in source
    assert "_NEKO_IDLE_CAT1_PLAY_SOUND_URL = '/static/assets/neko-idle/cat1-voice3.mp3'" in source
    assert "function _playNekoIdleCat1PlayAction(button)" in source

    play_block = source.split("function _playNekoIdleCat1PlayAction(button)", 1)[1].split(
        "function _clearNekoIdleThoughtBubble",
        1,
    )[0]
    assert "_cancelNekoIdleCat1EatAction(button, { restoreArt: false });" in play_block
    assert "_NEKO_IDLE_CAT1_PLAY_ASSET_URL" in play_block
    assert "_NEKO_IDLE_CAT1_PLAY_SOUND_URL" in play_block
    assert "'cat1-play-action'" in play_block
    assert "let audioDone" not in play_block
    assert "markAudioDone" not in play_block
    assert "if (!gifDone) return;" in play_block
    assert "_playNekoIdleSound(state, _NEKO_IDLE_CAT1_PLAY_SOUND_URL, _NEKO_IDLE_CAT1_PLAY_SOUND_VOLUME);" in play_block
    finish_play_block = source.split("function _finishNekoIdleCat1PlayAction(button, token)", 1)[1].split(
        "function _playNekoIdleCat1PlayAction(button)",
        1,
    )[0]
    assert "animate: true" in finish_play_block
    assert "animate: false" not in finish_play_block
    assert "_NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR" in finish_play_block
    assert "art.setAttribute(_NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR, 'true');" in finish_play_block
    assert "art.removeAttribute(_NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR);" in source
    assert "_setNekoIdleCat1PlayYarnHidden(state, true);" in play_block
    assert "_getNekoIdleCat1PlayYarnReleasePayload(button, state" in source
    assert "_setNekoIdleCat1PlayYarnHidden(" in source
    assert "_NEKO_IDLE_CAT1_PLAY_YARN_RELEASE_SIZE_PX = 51" in source
    assert "data-neko-cat1-play-hidden" in source
    assert "idle_cat1_play_yarn_visibility" in source
    assert "targetScreenRect" in source
    release_block = source.split("function _getNekoIdleCat1PlayYarnReleasePayload", 1)[1].split(
        "function _postNekoIdleCat1PlayYarnVisibilityState",
        1,
    )[0]
    assert "_NEKO_IDLE_CAT1_NATIVE_YARN_VISIBLE_SIZE_PX = 58" in source
    assert "if (_isNekoIdleCat1NativeWaylandSelfBallRuntime()) return payload;" not in release_block
    assert "const ballSize = _isNekoIdleCat1NativeWaylandSelfBallRuntime()" in release_block
    assert "? _NEKO_IDLE_CAT1_NATIVE_YARN_VISIBLE_SIZE_PX" in release_block
    assert "payload.targetScreenRect" in release_block
    assert "idle_cat1_play_yarn_visibility" in interpage_source
    assert "dispatchIdleCat1PlayYarnVisibility(event.data)" in interpage_source
    assert "neko:idle-cat1-play-yarn-visibility" in chat_source
    assert "setCompactChatBallTemporarilyHidden(hidden, {" in chat_source
    assert "releaseDrag: !!(detail && detail.releaseDrag)" in chat_source
    assert "targetScreenRect: detail && detail.targetScreenRect" in chat_source
    assert "applyIdleCat1PlayYarnRelease(detail)" in chat_source
    assert "showCompactChatBall(" not in chat_source
    assert "hideCompactChatBall(" not in chat_source

    assert ".neko-idle-return-btn.is-cat1-playing > .neko-idle-return-art" in css
    cat1_play_style_block = css.split(
        ".neko-idle-return-btn.is-cat1-playing > .neko-idle-return-art",
        1,
    )[1].split("}", 1)[0]
    assert "width: 175% !important" in cat1_play_style_block
    assert "min-width: 175%" in cat1_play_style_block
    assert "height: 100% !important" in cat1_play_style_block
    assert "max-width: none" in cat1_play_style_block
    assert '.neko-idle-return-art[data-neko-cat1-play-finishing="true"]' in css
    assert '#react-chat-window-shell.is-minimized[data-neko-cat1-play-hidden="true"]' in css
    cat1_play_yarn_style_block = css.split(
        '#react-chat-window-shell.is-minimized[data-neko-cat1-play-hidden="true"]',
        1,
    )[1].split("}", 1)[0]
    assert "visibility: hidden !important" in cat1_play_yarn_style_block
    assert "pointer-events: none !important" in cat1_play_yarn_style_block
    thought_bubble_hidden_block = css.split(
        '.neko-idle-return-btn[data-neko-idle-tier="none"] .neko-idle-thought-bubble',
        1,
    )[1].split("}", 1)[0]
    assert ".neko-idle-return-btn.is-cat1-stretching .neko-idle-thought-bubble" in thought_bubble_hidden_block
    assert ".neko-idle-return-btn.is-cat1-playing .neko-idle-thought-bubble" in thought_bubble_hidden_block
    assert ".neko-idle-return-btn.is-cat1-eating:not(.is-thought-bubble-popping) .neko-idle-thought-bubble" in thought_bubble_hidden_block
    assert ".neko-idle-return-btn.is-cat1-eating .neko-idle-thought-bubble" not in thought_bubble_hidden_block

    assert 'data-neko-cat1-wide-art' in chat_source
    assert '/static/assets/neko-idle/cat-idle-cat-play-1.gif' in chat_source
    assert '.neko-idle-cat1-compact-mirror[data-neko-cat1-wide-art="true"] .neko-idle-cat1-compact-mirror-art' in css
    assert "body[data-neko-ball-drag] .neko-idle-return-btn.is-cat1-playing > .neko-idle-return-art" in app_ui_source
    assert "width:175%!important" in app_ui_source


def test_cat1_walk_finish_keeps_legacy_probability_branch_outside_cat_mind():
    source = _read_avatar_ui_buttons_source()

    finish_block = source.split("function _finishNekoIdleCat1Walk", 1)[1].split(
        "function _finishNekoIdleCat1CompactTopEdgeWalk",
        1,
    )[0]
    assert "targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE" in finish_block
    assert "CAT1_WALK_DONE_NEAR_CHAT" in finish_block
    assert "CAT1_PLAY_YARN_WAKEUP" not in finish_block
    assert "Math.random() < _NEKO_IDLE_CAT1_WALK_FINISH_PLAY_PROBABILITY" in finish_block
    assert "state.walkFinishResolution = walkFinishResolution" in finish_block
    assert "state.walkFinishResolution = 'stretch';" in finish_block
    assert "if (targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE && state.walkFinishResolution)" in finish_block
    assert "_playNekoIdleCat1PlayAction(button, {" in finish_block
    assert "source: 'cat1-journey-local'" in finish_block
    assert "_setNekoIdleCat1Substate(button, state.profile.finishingSubstate, { animate: true });" in finish_block

    walk_start_block = source.split("function _startNekoIdleCat1Walk", 1)[1].split(
        "function _scheduleNekoIdleCat1WalkStart",
        1,
    )[0]
    assert "_resetNekoIdleCat1WalkFinishResolution(state);" in walk_start_block


def test_cat1_pair_move_is_adapter_only_small_move_runner():
    source = _read_avatar_ui_buttons_source()

    play_block = source.split("function _playNekoIdleCat1PlayAction(button)", 1)[1].split(
        "function _clearNekoIdleThoughtBubble",
        1,
    )[0]
    assert "journey.substate === journey.profile.walkingSubstate" in play_block
    assert "journey.substate === journey.profile.finishingSubstate" in play_block

    pair_move_start_block = source.split("function _startNekoIdleCat1PairMove(button)", 1)[1].split(
        "function _refreshNekoIdleCat1Observer",
        1,
    )[0]
    assert "const isCatMindRun = catMindRunOptions.source === 'cat_mind';" in pair_move_start_block
    assert "if (!isCatMindRun) return false;" in pair_move_start_block
    assert "_NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE" in pair_move_start_block
    assert "_playNekoIdleCat1PlayAction(button)" not in pair_move_start_block
    finish_pair_move_block = source.split("function _finishNekoIdleCat1PairMove(button)", 1)[1].split(
        "function _stepNekoIdleCat1PairMove",
        1,
    )[0]
    assert "_playNekoIdleCat1PlayAction(button)" not in finish_pair_move_block


def test_cat1_minimized_side_target_separates_look_and_move_direction():
    source = _read_avatar_ui_buttons_source()

    # #1749 的“朝毛球前进、避免倒退”的取侧逻辑只用于本次走路首次决策，已抽到 forward picker。
    forward_pick_block = source.split("function _pickNekoIdleCat1ForwardSideTarget", 1)[1].split(
        "function _clearNekoIdleCat1WalkApproachSide",
        1,
    )[0]
    assert "const lookFacingRight = chatCenterX > catCenterX;" in forward_pick_block
    assert "sideTarget.moveFacingRight === lookFacingRight" in forward_pick_block
    assert "alternateTarget.moveFacingRight === null || alternateTarget.moveFacingRight === lookFacingRight" in forward_pick_block
    assert "facingRight: facingRight," in source
    assert "lookFacingRight: facingRight" in source
    assert "stretchFacingRight: stretchFacingRight" in source
    assert "moveFacingRight: moveFacingRight" in source


def test_cat1_electron_multi_window_uses_real_desktop_chat_geometry():
    source = _read_avatar_ui_buttons_source()

    for function_name in (
        "_getNekoIdleReactChatMinimizedRect",
        "_getNekoIdleReactChatMinimizedShell",
        "_getNekoIdleReactChatExpandedShell",
    ):
        block = source.split(f"function {function_name}", 1)[1].split("\n}\n", 1)[0]
        assert "if (window.__NEKO_MULTI_WINDOW__ === true) return null;" in block


def test_cat1_native_wayland_yarn_corrections_are_direction_and_state_specific():
    source = _read_avatar_ui_buttons_source()
    css = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert "_NEKO_IDLE_CAT1_NATIVE_YARN_LEFT_SIDE_CONTACT_CORRECTION_PX = 34" in source
    assert "function _isNekoIdleCat1NativeWaylandSelfBallRuntime()" in source
    native_runtime_block = source.split(
        "function _isNekoIdleCat1NativeWaylandRuntime",
        1,
    )[1].split("function _getNekoIdleReturnAssetUrl", 1)[0]
    assert "window.__NEKO_MULTI_WINDOW__ === true" in native_runtime_block
    assert "runtime.isWayland === true" in native_runtime_block
    assert "runtime.isNiriWayland !== true" in source
    assert "function _usesNekoIdleCat1NativeYarnVisualAnchor(chatRect)" in source
    assert "Number.isFinite(width) && width > 0 && width <= 60" in source
    assert "function _getNekoIdleCat1NativeYarnSide(container, chatRect)" in source

    side_block = source.split(
        "function _getNekoIdleCat1NativeYarnSide",
        1,
    )[1].split("function _getNekoIdleCat1NativeYarnVisualTargetLeft", 1)[0]
    assert "const catCenterX = catLeft + catWidth / 2;" in side_block
    assert "const yarnCenterX = yarnLeft + yarnWidth / 2;" in side_block
    assert "catCenterX <= yarnCenterX" in side_block
    assert "facingRight" not in side_block

    visual_target_block = source.split(
        "function _getNekoIdleCat1NativeYarnVisualTargetLeft",
        1,
    )[1].split("function _getNekoIdleCat1TargetMoveDirection", 1)[0]
    assert "const leftSideCorrection = facingRight" in visual_target_block
    assert "? _NEKO_IDLE_CAT1_NATIVE_YARN_LEFT_SIDE_CONTACT_CORRECTION_PX" in visual_target_block
    assert ": 0;" in visual_target_block

    # 右侧待机终点不动；待机素材与已正常的走路/伸懒腰素材使用同一 33px 视觉校准。
    assert 'data-neko-cat1-native-yarn-visual-anchor="true"' in css
    assert 'data-neko-cat1-native-yarn-side="right"' in css
    assert 'data-neko-cat1-substate="idle"' in css
    assert ":not(.is-cat1-playing):not(.is-cat1-eating):not(.is-drag-action)" in css
    assert "left: 33px;" in css
    assert "left: calc(12.3046875% + 33px);" in css
    assert "left: -17.96875%;" in css
    assert ".is-cat1-walking:not(.is-cat1-facing-right)" in css
    assert ".is-cat1-stretching:not(.is-cat1-facing-right)" in css
    assert 'data-neko-cat1-native-yarn-side="left"' not in css

    class_block = source.split("function _setNekoIdleCat1Classes", 1)[1].split(
        "function _cancelNekoIdleCat1Frame",
        1,
    )[0]
    assert "_getNekoIdleCat1NativeYarnSide(container, nativeYarnRect)" in class_block
    assert "button.setAttribute(_NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_ATTR, nativeYarnSide);" in class_block
    assert "button.removeAttribute(_NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_ATTR);" in class_block

    side_target_block = source.split("function _getNekoIdleCat1SideTarget", 1)[1].split(
        "function _getNekoIdleCat1CompactTopEdgeBounds",
        1,
    )[0]
    assert "!_usesNekoIdleCat1NativeYarnVisualAnchor(chatRect)" in side_target_block


def test_cat1_minimized_side_target_commits_approach_side_to_prevent_center_straddle():
    """Approach side must be committed with hysteresis, never re-judged each frame via catCenter vs chatCenter (which makes the cat straddle the ball center and jitter against it)."""
    source = _read_avatar_ui_buttons_source()

    side_target_block = source.split("function _getNekoIdleCat1SideTarget", 1)[1].split(
        "function _getNekoIdleCat1CompactTopEdgeBounds",
        1,
    )[0]
    assert "_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP" in side_target_block
    # 仍在毛球水平跨度内 -> 保持提交侧，不在球心附近翻面
    assert "catCenterX >= chatRect.left && catCenterX <= chatRect.right" in side_target_block
    # 只在猫已整体越到毛球另一侧时才重选接近侧
    assert "committed === true && catCenterX > chatRect.right" in side_target_block
    assert "committed === false && catCenterX < chatRect.left" in side_target_block
    # 提交侧持有期间，禁止再出现旧的“每帧重判 lookFacingRight”
    assert "const lookFacingRight = chatCenterX > catCenterX;" not in side_target_block

    # 走完/取消时必须清掉提交侧，便于下次重新决策
    assert "function _clearNekoIdleCat1WalkApproachSide" in source
    finish_block = source.split("function _finishNekoIdleCat1Walk", 1)[1].split(
        "function _finishNekoIdleCat1CompactTopEdgeWalk",
        1,
    )[0]
    assert "_clearNekoIdleCat1WalkApproachSide(" in finish_block




def test_cat1_walk_speed_rate_relaxes_when_converging():
    """Catch-up speed rate must relax while converging, so one momentary distance spike does not pin the speed at maxRate forever."""
    source = _read_avatar_ui_buttons_source()

    speed_block = source.split("function _updateNekoIdleCat1WalkSpeedRate", 1)[1].split(
        "function _stepNekoIdleCat1Walk",
        1,
    )[0]
    assert "currentDistance < previousDistance" in speed_block
    assert "(previousDistance - currentDistance)" in speed_block


def test_cat1_walk_uses_resolved_target_facing_instead_of_raw_chat_side():
    source = _read_avatar_ui_buttons_source()

    assert "function _resolveNekoIdleCat1TargetFacing" in source
    assert "function _resolveNekoIdleCat1StretchFacing" in source
    assert "function _resolveNekoIdleCat1FinalTargetFacing" in source

    final_facing_block = source.split("function _resolveNekoIdleCat1FinalTargetFacing", 1)[1].split(
        "function _makeNekoIdleCat1SideTarget",
        1,
    )[0]
    assert "stretchFacingRight" in final_facing_block
    assert final_facing_block.index("stretchFacingRight") < final_facing_block.index("lookFacingRight")

    walk_step_block = source.split("function _stepNekoIdleCat1Walk", 1)[1].split(
        "function _startNekoIdleCat1Walk",
        1,
    )[0]
    assert "state.facingRight = _resolveNekoIdleCat1TargetFacing(rect, target);" in walk_step_block
    assert "state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);" in walk_step_block
    assert "state.facingRight = target.facingRight;" not in walk_step_block

    walk_start_block = source.split("function _startNekoIdleCat1Walk", 1)[1].split(
        "function _scheduleNekoIdleCat1WalkStart",
        1,
    )[0]
    assert "state.facingRight = _resolveNekoIdleCat1TargetFacing(currentRect, target);" in walk_start_block
    assert "state.facingRight = !!(target && target.facingRight);" not in walk_start_block

    journey_sync_block = source.split("function _syncNekoIdleCat1Journey", 1)[1].split(
        "function _scheduleNekoIdleCat1JourneySync",
        1,
    )[0]
    assert "_resolveNekoIdleCat1FinalTargetFacing(target)" in journey_sync_block
    assert "state.facingRight = target.facingRight;" not in journey_sync_block


def test_cat1_finishing_animation_rechecks_chat_target_after_settle():
    source = _read_avatar_ui_buttons_source()

    settle_block = source.split("function _settleNekoIdleReturnSubactionToIdle", 1)[1].split(
        "function _scheduleNekoIdleReturnSubactionSettle",
        1,
    )[0]
    assert "const shouldRecheckTargetAfterSettle = !!(state.target ||" in settle_block
    assert "state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE" in settle_block
    assert "state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE" in settle_block
    assert "_getNekoIdleChatMinimizedRect() || _getNekoIdleChatCompactSurfaceRect()" in settle_block
    assert "_scheduleNekoIdleCat1JourneySync(button);" in settle_block
    assert settle_block.index("const shouldRecheckTargetAfterSettle") < settle_block.index("state.target = null;")
    assert settle_block.index("_scheduleNekoIdleCat1JourneySync(button);") < settle_block.index("setTimeout(() => {")


def test_cat1_hover_blocked_walk_starts_immediately_after_hover_playback():
    source = _read_avatar_ui_buttons_source()

    walk_start_block = source.split("function _scheduleNekoIdleCat1WalkStart", 1)[1].split(
        "function _canScheduleNekoIdleCat1PairMove",
        1,
    )[0]
    hover_block = walk_start_block.split("if (art && art.__nekoIdleHoverSrc) {", 1)[1].split(
        "if (state.pendingWalkReady)",
        1,
    )[0]
    assert "state.pendingWalkReady = true;" in hover_block
    assert "state.pendingWalkDelayMs = 0;" in hover_block
    assert "_finishNekoIdleHoverArtAfterPlayback(art, profile.tier);" in hover_block
    assert hover_block.index("state.pendingWalkReady = true;") < hover_block.index("return;")


def test_cat1_compact_top_edge_to_minimized_side_transition_forces_walk():
    source = _read_avatar_ui_buttons_source()

    journey_sync_block = source.split("function _syncNekoIdleCat1Journey", 1)[1].split(
        "function _scheduleNekoIdleCat1JourneySync",
        1,
    )[0]
    assert "const previousTargetKind = state.targetKind || '';" in journey_sync_block
    assert "const switchingFromCompactTopEdgeToMinimizedSide =" in journey_sync_block
    assert "previousTargetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE" in journey_sync_block
    assert "target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE" in journey_sync_block
    assert "switchingFromCompactTopEdgeToMinimizedSide && target.distance > profile.target.exitDistancePx" in journey_sync_block
    assert journey_sync_block.index("const previousTargetKind = state.targetKind || '';") < journey_sync_block.index("state.targetKind = target.kind || '';")
    assert journey_sync_block.index("const switchingFromCompactTopEdgeToMinimizedSide =") < journey_sync_block.index("_scheduleNekoIdleCat1WalkStart(button, target);")


def test_cat1_settled_minimized_side_uses_regular_walk_delay_when_ball_moves():
    source = _read_avatar_ui_buttons_source()

    journey_sync_block = source.split("function _syncNekoIdleCat1Journey", 1)[1].split(
        "function _scheduleNekoIdleCat1JourneySync",
        1,
    )[0]
    assert "const followingMovedMinimizedSideTarget =" not in journey_sync_block
    assert "target.distance >= profile.target.enterDistancePx" in journey_sync_block
    assert "if (switchingFromCompactTopEdgeToMinimizedSide) {" in journey_sync_block
    assert "if (switchingFromCompactTopEdgeToMinimizedSide ||" not in journey_sync_block
    assert "state.pendingWalkReady = true;" in journey_sync_block
    assert "state.pendingWalkDelayMs = 0;" in journey_sync_block
    assert journey_sync_block.index("target.distance >= profile.target.enterDistancePx") < journey_sync_block.index("_scheduleNekoIdleCat1WalkStart(button, target);")


def test_cat1_settled_minimized_side_bypasses_small_desktop_move_filter():
    source = _read_avatar_ui_buttons_source()

    assert "function _isNekoIdleCat1SettledOnMinimizedSide(state, profile)" in source
    minimized_state_block = source.split("window.addEventListener('neko:idle-chat-minimized-state'", 1)[1].split(
        "window.addEventListener('neko:idle-chat-compact-surface-state'",
        1,
    )[0]
    assert "const settledMinimizedSide = _isNekoIdleCat1SettledOnMinimizedSide(" in minimized_state_block
    assert "currentState && currentState.profile" in minimized_state_block
    assert "if (isSmallDesktopChatMove && !_isNekoIdleCat1Walking(button) && !settledMinimizedSide) return;" in minimized_state_block
    assert minimized_state_block.index("const settledMinimizedSide = _isNekoIdleCat1SettledOnMinimizedSide(") < minimized_state_block.index("if (isSmallDesktopChatMove && !_isNekoIdleCat1Walking(button) && !settledMinimizedSide) return;")


def test_cat1_external_chat_position_updates_interrupt_pair_move_for_retarget():
    source = _read_avatar_ui_buttons_source()

    assert "function _interruptNekoIdleCat1PairMoveForRetarget" in source
    minimized_state_block = source.split("window.addEventListener('neko:idle-chat-minimized-state'", 1)[1].split(
        "window.addEventListener('neko:idle-chat-compact-surface-state'",
        1,
    )[0]
    assert "const pairMoveFeedback = _isNekoIdleCat1PlaygroundPairMoveFeedback(detail);" in minimized_state_block
    assert "if (pairMoveFeedback) return;" in minimized_state_block
    assert "_interruptNekoIdleCat1PairMoveForRetarget(button, currentState)" in minimized_state_block

    compact_move_block = source.split("function _handleNekoIdleCompactSurfaceMoveState", 1)[1].split(
        "function _shouldRecheckNekoIdleCat1AfterManualMove",
        1,
    )[0]
    assert "_interruptNekoIdleCat1PairMovesForRetarget({ scheduleSync: !activeSurfaceAdjustment });" in compact_move_block
