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


def test_live2d_peek_restore_anchor_is_consumed_on_return():
    # The return handler must keep the peek anchor captured from the return-ball
    # container and restore it after the model is shown again, so the model
    # re-enters the edge-peek state instead of coming back flat at the edge.
    app_ui_source = read_js_parts(APP_UI_PATH)
    return_block = app_ui_source.split("const handleReturnClick", 1)[1]

    restore_call = "window.nekoLive2DPeek.restoreAnchor(live2DPeekRestoreAnchor)"
    settle_call = "settleReturnedModelBounds(returnModelWasMoved)"
    complete_dispatch = "new CustomEvent('neko:cat-return-complete'"

    assert "let live2DPeekRestoreAnchor = null;" in return_block
    assert "live2DPeekRestoreAnchor = returnContainer.__nekoLive2DPeekEdgeAnchor;" in return_block
    assert restore_call in return_block
    # The fire-and-forget call must keep its Promise rejection handler so a
    # failed restore never surfaces as an unhandled rejection.
    assert restore_call + ".catch(() => {});" in return_block
    assert settle_call in return_block
    assert complete_dispatch in return_block
    # Order constraint: settle the model bounds first, then restore the edge
    # peek, and only then dispatch return-complete.
    assert return_block.index(settle_call) < return_block.index(restore_call) < return_block.index(complete_dispatch)


def test_live2d_peek_return_ball_supports_exactly_four_corners_and_two_side_edges():
    source = read_js_parts(APP_UI_PATH)
    anchor_block = source.split("const NEKO_LIVE2D_PEEK_RETURN_EDGE_ANCHORS = [", 1)[1].split("];", 1)[0]

    for edge in ("left", "right", "top-left", "top-right", "bottom-left", "bottom-right"):
        assert f"'{edge}'" in anchor_block
    assert "'top'" not in anchor_block
    assert "'bottom'" not in anchor_block
    assert "container.setAttribute('data-neko-live2d-peek-anchor', edge);" in source
    assert "positionLive2DPeekReturnBallAtEdge(container, container.__nekoLive2DPeekEdgeAnchor);" in source
    assert "detail.reason === 'return-ball-drag-active'" in source
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


def test_live2d_peek_return_ball_keeps_edge_position_without_model_tilt():
    css = INDEX_CSS_PATH.read_text(encoding="utf-8")

    rule = css.split("[data-neko-live2d-peek-anchor] .neko-idle-return-art", 1)[1].split("}", 1)[0]
    assert "--neko-idle-return-edge-transform: rotate(0deg);" in rule

    transferred_anchor_styles = css.split(
        '[data-neko-live2d-peek-anchor$="left"] .neko-idle-return-art', 1
    )[1].split(".neko-idle-thought-bubble", 1)[0]
    for rotation in ("rotate(60deg)", "rotate(-60deg)", "rotate(45deg)", "rotate(-45deg)"):
        assert rotation not in transferred_anchor_styles
