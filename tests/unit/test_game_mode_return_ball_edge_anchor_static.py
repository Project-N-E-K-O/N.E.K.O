from pathlib import Path

from tests.static_app_parts import read_js_parts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
APP_GAME_MODE_PATH = PROJECT_ROOT / "static" / "app" / "app-game-mode-beta.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def test_game_mode_auto_switch_transfers_the_live2d_edge_anchor_to_return_ball():
    game_mode_source = APP_GAME_MODE_PATH.read_text(encoding="utf-8")
    app_ui_source = read_js_parts(APP_UI_PATH)

    assert "edgeAnchor: clientState.restoreAnchor" in game_mode_source
    assert "const gameModeEdgeAnchor = goodbyeDetail.gameModeAuto === true ? goodbyeDetail.edgeAnchor : null;" in app_ui_source
    assert "edgeAnchor: gameModeEdgeAnchor" in app_ui_source
    assert "positionReturnBallContainer(container, anchorRect, options.edgeAnchor);" in app_ui_source


def test_game_mode_return_ball_supports_exactly_four_corners_and_two_side_edges():
    source = read_js_parts(APP_UI_PATH)
    anchor_block = source.split("const NEKO_GAME_MODE_RETURN_EDGE_ANCHORS = [", 1)[1].split("];", 1)[0]

    for edge in ("left", "right", "top-left", "top-right", "bottom-left", "bottom-right"):
        assert f"'{edge}'" in anchor_block
    assert "'top'" not in anchor_block
    assert "'bottom'" not in anchor_block
    assert "container.setAttribute('data-neko-game-mode-edge-anchor', edge);" in source
    assert "positionGameModeReturnBallAtEdge(container, container.__nekoGameModeEdgeAnchor);" in source
    assert "detail.reason === 'return-ball-drag-start'" in source
    assert "clearGameModeReturnBallEdgeAnchor(detail.container);" in source


def test_blocked_model_restore_keeps_the_game_mode_return_ball_anchor():
    source = read_js_parts(APP_UI_PATH)
    restore_block = source.split("function restoreReturnBallAfterBlockedModelViewport(event)", 1)[1].split(
        "const handleReturnClick", 1
    )[0]

    assert "if (container.__nekoGameModeEdgeAnchor)" in restore_block
    assert "showReturnBallContainer(container, returnRect, {" in restore_block
    assert "edgeAnchor: container.__nekoGameModeEdgeAnchor" in restore_block
    assert "showReturnBallContainer(container, returnRect);" in restore_block


def test_game_mode_return_ball_uses_60_degree_sides_and_45_degree_corners():
    css = INDEX_CSS_PATH.read_text(encoding="utf-8")

    expected_rules = {
        'data-neko-game-mode-edge-anchor="left"': "rotate(60deg)",
        'data-neko-game-mode-edge-anchor="right"': "rotate(-60deg)",
        'data-neko-game-mode-edge-anchor="top-left"': "rotate(45deg)",
        'data-neko-game-mode-edge-anchor="top-right"': "rotate(-45deg)",
        'data-neko-game-mode-edge-anchor="bottom-left"': "rotate(45deg)",
        'data-neko-game-mode-edge-anchor="bottom-right"': "rotate(-45deg)",
    }
    for selector, rotation in expected_rules.items():
        rule = css.split(f"[{selector}]", 1)[1].split("}", 1)[0]
        assert rotation in rule
