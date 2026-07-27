import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION = PROJECT_ROOT / "static" / "live2d" / "live2d-interaction.js"
VRM_INTERACTION = PROJECT_ROOT / "static" / "vrm" / "vrm-interaction.js"


def test_live2d_wheel_zoom_requires_model_hit_before_consuming_event():
    source = LIVE2D_INTERACTION.read_text(encoding="utf-8")
    start = source.index("Live2DManager.prototype.setupWheelZoom = function (model)")
    end = source.index("// 设置触摸缩放", start)
    block = source[start:end]

    assert re.search(r"const\s+isWheelPointOnCurrentModel\s*=\s*\(event\)\s*=>\s*{", block)
    assert re.search(r"getBoundingClientRect\s*\(\)", block)
    assert re.search(r"event\.clientX\s*-\s*canvasRect\.left", block)
    assert re.search(r"event\.clientY\s*-\s*canvasRect\.top", block)
    # 逐个消费点判定，而不是拿首个 preventDefault 当"那一个"：#2253 的挂边
    # 探身分支在缩放路径之前自带一个 preventDefault（它自己也在命中检查里面），
    # 首匹配写法会误判成守卫失效——测试比它声称的主张更弱。
    hit_checks = [m.start() for m in re.finditer(r"isWheelPointOnCurrentModel\(event\)", block)]
    prevent_sites = [m.start() for m in re.finditer(r"event\.preventDefault\(\);", block)]
    assert prevent_sites, "找不到任何 preventDefault，切片或实现已变"
    for site in prevent_sites:
        assert any(check < site for check in hit_checks), (
            f"offset {site} 处的 preventDefault 前面没有命中检查，滚轮会在模型外被吞掉"
        )
    guard_index = re.search(r"if\s*\(!isWheelPointOnCurrentModel\(event\)\)\s*return;", block).start()
    scale_index = re.search(r"this\.currentModel\.scale\.set\(newScale\);", block).start()
    assert guard_index < scale_index
    assert any(guard_index < site < scale_index for site in prevent_sites), (
        "缩放路径自己那一次 preventDefault 必须夹在早退守卫与 scale.set 之间"
    )


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
