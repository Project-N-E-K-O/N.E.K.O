from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_CORE = PROJECT_ROOT / "static/live2d/live2d-core.js"
LIVE2D_MODEL = PROJECT_ROOT / "static/live2d/live2d-model.js"


def test_mobile_renderer_initializes_from_current_web_viewport():
    source = LIVE2D_CORE.read_text(encoding="utf-8")
    init_start = source.index("const useViewportSize = isMobileWidth();")
    app_start = source.index("this.pixi_app = new PIXI.Application", init_start)
    app_end = source.index("});", app_start) + len("});")
    init_block = source[init_start:app_end]

    assert "useViewportSize ? window.innerWidth : window.screen.width" in init_block
    assert "useViewportSize ? window.innerHeight : window.screen.height" in init_block
    assert "width: initW" in init_block
    assert "height: initH" in init_block


def test_mobile_default_and_reset_positions_use_viewport_not_renderer_screen():
    model_source = LIVE2D_MODEL.read_text(encoding="utf-8")
    core_source = LIVE2D_CORE.read_text(encoding="utf-8")

    apply_block = model_source[
        model_source.index("if (isMobile) {"):
        model_source.index("    } else {", model_source.index("if (isMobile) {"))
    ]
    reset_start = core_source.index("async resetModelPosition()")
    reset_method = core_source[reset_start:core_source.index("\n    /**", reset_start)]
    reset_position_block = reset_method[:reset_method.index("console.log('模型位置已复位到初始状态');")]

    for block in (apply_block, reset_position_block):
        assert "const viewportWidth = Math.max(window.innerWidth" in block
        assert "const viewportHeight = Math.max(window.innerHeight" in block
        assert "viewportWidth * 0.5" in block
        assert "viewportHeight * 0.28" in block

    assert "resetViewport = { width: viewportWidth, height: viewportHeight };" in reset_method
    assert "const viewport = resetViewport || {" in reset_method
