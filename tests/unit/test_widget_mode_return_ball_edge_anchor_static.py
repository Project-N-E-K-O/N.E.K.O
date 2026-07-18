from pathlib import Path

from tests.static_app_parts import read_js_parts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
LIVE2D_INTERACTION_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-interaction.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def test_live2d_peek_goodbye_transfers_the_edge_anchor_to_return_ball():
    interaction_source = LIVE2D_INTERACTION_PATH.read_text(encoding="utf-8")
    app_ui_source = read_js_parts(APP_UI_PATH)

    assert "const restoreAnchor = captureLive2DPeekRestoreAnchor();" in interaction_source
    assert "event.detail.edgeAnchor = restoreAnchor;" in interaction_source
    assert "event.__nekoLive2DPeekEdgeAnchor = restoreAnchor;" in interaction_source
    assert "|| (event && event.__nekoLive2DPeekEdgeAnchor)" in app_ui_source
    assert "edgeAnchor: live2DPeekEdgeAnchor" in app_ui_source
    assert "positionReturnBallContainer(container, anchorRect, options.edgeAnchor);" in app_ui_source


def test_live2d_peek_return_ball_supports_exactly_four_corners_and_two_side_edges():
    source = read_js_parts(APP_UI_PATH)
    anchor_block = source.split("const NEKO_LIVE2D_PEEK_RETURN_EDGE_ANCHORS = [", 1)[1].split("];", 1)[0]

    for edge in ("left", "right", "top-left", "top-right", "bottom-left", "bottom-right"):
        assert f"'{edge}'" in anchor_block
    assert "'top'" not in anchor_block
    assert "'bottom'" not in anchor_block
    assert "container.setAttribute('data-neko-live2d-peek-anchor', edge);" in source
    assert "positionLive2DPeekReturnBallAtEdge(container, container.__nekoLive2DPeekEdgeAnchor);" in source
    assert "detail.reason === 'return-ball-drag-start'" in source
    assert "clearLive2DPeekReturnBallEdgeAnchor(detail.container);" in source


def test_blocked_model_restore_keeps_the_live2d_peek_return_ball_anchor():
    source = read_js_parts(APP_UI_PATH)
    restore_block = source.split("function restoreReturnBallAfterBlockedModelViewport(event)", 1)[1].split(
        "const handleReturnClick", 1
    )[0]

    assert "if (container.__nekoLive2DPeekEdgeAnchor)" in restore_block
    assert "showReturnBallContainer(container, returnRect, {" in restore_block
    assert "edgeAnchor: container.__nekoLive2DPeekEdgeAnchor" in restore_block
    assert "showReturnBallContainer(container, returnRect);" in restore_block


def test_live2d_peek_return_ball_uses_60_degree_sides_and_45_degree_corners():
    css = INDEX_CSS_PATH.read_text(encoding="utf-8")

    expected_rules = {
        'data-neko-live2d-peek-anchor="left"': "rotate(60deg)",
        'data-neko-live2d-peek-anchor="right"': "rotate(-60deg)",
        'data-neko-live2d-peek-anchor="top-left"': "rotate(45deg)",
        'data-neko-live2d-peek-anchor="top-right"': "rotate(-45deg)",
        'data-neko-live2d-peek-anchor="bottom-left"': "rotate(45deg)",
        'data-neko-live2d-peek-anchor="bottom-right"': "rotate(-45deg)",
    }
    for selector, rotation in expected_rules.items():
        rule = css.split(f"[{selector}]", 1)[1].split("}", 1)[0]
        assert rotation in rule
