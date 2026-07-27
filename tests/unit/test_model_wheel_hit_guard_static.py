import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION = PROJECT_ROOT / "static" / "live2d" / "live2d-interaction.js"
VRM_INTERACTION = PROJECT_ROOT / "static" / "vrm" / "vrm-interaction.js"

_POSITIVE_GUARD_OPEN = re.compile(r"if\s*\(\s*isWheelPointOnCurrentModel\(event\)\s*\)\s*\{")
_EARLY_RETURN_GUARD = re.compile(r"if\s*\(\s*!\s*isWheelPointOnCurrentModel\(event\)\s*\)\s*return;")


def _unguarded_prevent_defaults(block: str) -> tuple[int, list[str]]:
    """Find preventDefault calls that no hit check actually controls.

    Comparing raw offsets ("some hit check appears earlier in the text") is
    weaker than the invariant this file claims: the peek branch's own check
    sits inside a nested block and governs nothing after that block closes, so
    a new unguarded consume placed after it would still read as guarded.  Track
    brace depth instead and only count a call as guarded when it sits inside a
    block opened by a positive check, or after an early-return guard in an
    enclosing scope.
    """
    depth = 0
    guarded_depths: set[int] = set()
    early_return_depths: set[int] = set()
    total = 0
    unguarded: list[str] = []

    for lineno, raw in enumerate(block.split("\n"), start=1):
        line = raw.strip()
        opens_guard = bool(_POSITIVE_GUARD_OPEN.search(line))
        if opens_guard:
            guarded_depths.add(depth + 1)
        if _EARLY_RETURN_GUARD.search(line):
            early_return_depths.add(depth)

        if "event.preventDefault();" in line:
            total += 1
            effective_depth = depth + 1 if opens_guard else depth
            covered = any(d <= effective_depth for d in early_return_depths) or any(
                d <= effective_depth for d in guarded_depths
            )
            if not covered:
                unguarded.append(f"line {lineno}: {line}")

        depth += line.count("{") - line.count("}")
        # 退出某层后，该层及更深处立下的守卫不再覆盖后续代码。
        guarded_depths = {d for d in guarded_depths if d <= depth}
        early_return_depths = {d for d in early_return_depths if d <= depth}

    return total, unguarded


def test_live2d_wheel_zoom_requires_model_hit_before_consuming_event():
    source = LIVE2D_INTERACTION.read_text(encoding="utf-8")
    start = source.index("Live2DManager.prototype.setupWheelZoom = function (model)")
    end = source.index("// 设置触摸缩放", start)
    block = source[start:end]

    assert re.search(r"const\s+isWheelPointOnCurrentModel\s*=\s*\(event\)\s*=>\s*{", block)
    assert re.search(r"getBoundingClientRect\s*\(\)", block)
    assert re.search(r"event\.clientX\s*-\s*canvasRect\.left", block)
    assert re.search(r"event\.clientY\s*-\s*canvasRect\.top", block)
    # 逐个消费点按「归它管的那个分支」判定，而不是拿首个 preventDefault 当
    # "那一个"，也不是只比全文偏移：#2253 的挂边探身分支在缩放路径之前自带
    # 一个 preventDefault（它自己在命中检查里面），首匹配写法会误判成守卫
    # 失效；而只看"前面出现过命中检查"又会放过一个新加在探身块之后、早退
    # 守卫之前的裸消费点——两种写法都比这条用例声称的主张弱。
    total_prevents, unguarded = _unguarded_prevent_defaults(block)
    assert total_prevents, "找不到任何 preventDefault，切片或实现已变"
    assert not unguarded, (
        f"这些 preventDefault 不在任何命中检查管辖的分支里，滚轮会在模型外被吞掉: {unguarded}"
    )
    prevent_sites = [m.start() for m in re.finditer(r"event\.preventDefault\(\);", block)]
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
