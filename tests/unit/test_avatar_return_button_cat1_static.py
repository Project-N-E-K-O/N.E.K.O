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

    side_target_block = source.split("function _getNekoIdleCat1SideTarget", 1)[1].split(
        "function _getNekoIdleCat1CompactTopEdgeBounds",
        1,
    )[0]
    assert "const lookFacingRight = chatCenterX > catCenterX;" in side_target_block
    assert "sideTarget.moveFacingRight === lookFacingRight" in side_target_block
    assert "alternateTarget.moveFacingRight === null || alternateTarget.moveFacingRight === lookFacingRight" in side_target_block
    assert "facingRight: facingRight," in source
    assert "lookFacingRight: facingRight" in source
    assert "moveFacingRight: moveFacingRight" in source


def test_cat1_walk_uses_resolved_target_facing_instead_of_raw_chat_side():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "function _resolveNekoIdleCat1TargetFacing" in source
    assert "function _resolveNekoIdleCat1FinalTargetFacing" in source
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
    assert "_interruptNekoIdleCat1PairMovesForRetarget({ scheduleSync: !dragging });" in compact_move_block
