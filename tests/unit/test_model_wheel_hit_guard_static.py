import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION = PROJECT_ROOT / "static" / "live2d" / "live2d-interaction.js"
VRM_INTERACTION = PROJECT_ROOT / "static" / "vrm-interaction.js"


def test_live2d_wheel_zoom_requires_model_hit_before_consuming_event():
    source = LIVE2D_INTERACTION.read_text(encoding="utf-8")
    start = source.index("Live2DManager.prototype.setupWheelZoom = function (model)")
    end = source.index("// 设置触摸缩放", start)
    block = source[start:end]

    assert re.search(r"const\s+isWheelPointOnCurrentModel\s*=\s*\(event\)\s*=>\s*{", block)
    assert re.search(r"getBoundingClientRect\s*\(\)", block)
    assert re.search(r"event\.clientX\s*-\s*canvasRect\.left", block)
    assert re.search(r"event\.clientY\s*-\s*canvasRect\.top", block)
    guard_index = re.search(r"if\s*\(!isWheelPointOnCurrentModel\(event\)\)\s*return;", block).start()
    prevent_index = re.search(r"event\.preventDefault\(\);", block).start()
    scale_index = re.search(r"this\.currentModel\.scale\.set\(newScale\);", block).start()
    assert guard_index < prevent_index < scale_index


def test_vrm_wheel_zoom_requires_model_hit_before_consuming_event():
    source = VRM_INTERACTION.read_text(encoding="utf-8")
    start = source.index("this.wheelHandler = (e) => {")
    end = source.index("this.auxClickHandler = (e) => {", start)
    block = source[start:end]

    hit_guard = re.search(r"if\s*\(!this\._hitTestModel\(e\.clientX,\s*e\.clientY\)\)\s*{", block)
    assert hit_guard
    guard_index = hit_guard.start()
    prevent_index = re.search(r"e\.preventDefault\(\);", block).start()
    scale_index = re.search(r"const\s+scaleFactor\s*=\s*e\.deltaY\s*>\s*0\s*\?\s*0\.95\s*:\s*1\.05;", block).start()
    assert guard_index < prevent_index < scale_index
