from pathlib import Path

from main_routers import pages_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar-ui-buttons.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"
CAT1_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1.gif"


def test_cat1_return_button_visual_contract_is_present():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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
    assert AVATAR_UI_BUTTONS_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert INDEX_CSS_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT1_ASSET_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT1_ASSET_PATH.is_file()


def test_cat1_minimized_side_target_separates_look_and_move_direction():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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


def test_cat1_minimized_side_target_commits_approach_side_to_prevent_center_straddle():
    """Approach side must be committed with hysteresis, never re-judged each frame via catCenter vs chatCenter (which makes the cat straddle the ball center and jitter against it)."""
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    speed_block = source.split("function _updateNekoIdleCat1WalkSpeedRate", 1)[1].split(
        "function _stepNekoIdleCat1Walk",
        1,
    )[0]
    assert "currentDistance < previousDistance" in speed_block
    assert "(previousDistance - currentDistance)" in speed_block


def test_cat1_walk_uses_resolved_target_facing_instead_of_raw_chat_side():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

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
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "function _interruptNekoIdleCat1PairMoveForRetarget" in source
    minimized_state_block = source.split("window.addEventListener('neko:idle-chat-minimized-state'", 1)[1].split(
        "window.addEventListener('neko:idle-chat-compact-surface-state'",
        1,
    )[0]
    assert "const pairMoveFeedback = !!(detail && detail.reason === 'cat1-pair-move');" in minimized_state_block
    assert "if (pairMoveFeedback) return;" in minimized_state_block
    assert "_interruptNekoIdleCat1PairMoveForRetarget(button, currentState)" in minimized_state_block

    compact_move_block = source.split("function _handleNekoIdleCompactSurfaceMoveState", 1)[1].split(
        "function _shouldRecheckNekoIdleCat1AfterManualMove",
        1,
    )[0]
    assert "_interruptNekoIdleCat1PairMovesForRetarget({ scheduleSync: !activeSurfaceAdjustment });" in compact_move_block
