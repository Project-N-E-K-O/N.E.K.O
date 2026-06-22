from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION = PROJECT_ROOT / "static" / "live2d-interaction.js"
VRM_INTERACTION = PROJECT_ROOT / "static" / "vrm-interaction.js"


def test_live2d_wheel_zoom_requires_model_hit_before_consuming_event():
    source = LIVE2D_INTERACTION.read_text(encoding="utf-8")
    start = source.index("Live2DManager.prototype.setupWheelZoom = function (model)")
    end = source.index("// 设置触摸缩放", start)
    block = source[start:end]

    assert "const isWheelPointOnCurrentModel = (event) => {" in block
    guard_index = block.index("if (!isWheelPointOnCurrentModel(event)) return;")
    prevent_index = block.index("event.preventDefault();")
    scale_index = block.index("this.currentModel.scale.set(newScale);")
    assert guard_index < prevent_index < scale_index


def test_vrm_wheel_zoom_requires_model_hit_before_consuming_event():
    source = VRM_INTERACTION.read_text(encoding="utf-8")
    start = source.index("this.wheelHandler = (e) => {")
    end = source.index("this.auxClickHandler = (e) => {", start)
    block = source[start:end]

    assert "if (!this._hitTestModel(e.clientX, e.clientY)) {" in block
    guard_index = block.index("if (!this._hitTestModel(e.clientX, e.clientY)) {")
    prevent_index = block.index("e.preventDefault();")
    scale_index = block.index("const scaleFactor = e.deltaY > 0 ? 0.95 : 1.05;")
    assert guard_index < prevent_index < scale_index
