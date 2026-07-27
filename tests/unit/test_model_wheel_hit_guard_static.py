import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION = PROJECT_ROOT / "static" / "live2d" / "live2d-interaction.js"
VRM_INTERACTION = PROJECT_ROOT / "static" / "vrm" / "vrm-interaction.js"

_TOKENS = re.compile(
    r"(?P<positive_guard>if\s*\(\s*isWheelPointOnCurrentModel\(event\)\s*\)\s*(?=\{))"
    r"|(?P<early_return>if\s*\(\s*!\s*isWheelPointOnCurrentModel\(event\)\s*\)\s*return;)"
    r"|(?P<consume>event\.preventDefault\(\);)"
    r"|(?P<open>\{)"
    r"|(?P<close>\})"
)
_COMMENTS_AND_STRINGS = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`",
    re.S,
)


def _blank_out_noise(block: str) -> str:
    """Blank comments and string literals, preserving offsets.

    Braces inside them are not block structure; counting them would desync the
    scope stack below.
    """
    return _COMMENTS_AND_STRINGS.sub(lambda m: " " * len(m.group(0)), block)


def _unguarded_prevent_defaults(block: str) -> tuple[int, list[str]]:
    """Find preventDefault calls that no hit check actually controls.

    Three weaker shapes were tried and each admitted a real regression:

    * "some hit check appears earlier in the text" — the peek branch's own
      check sits in a nested block and governs nothing once that block closes,
      so a consume added after it still read as guarded.
    * per-line brace depth — ``} else {`` nets to zero, leaving the positive
      guard's depth in scope, so ``if (hit) {...} else { consume }`` read as
      guarded even though the else branch runs exactly when the hit test fails.
    * registering a line's guards before scanning it for consumes — the order
      within a line was lost, so ``consume; if (!hit) return;`` read as guarded
      despite consuming before testing.

    So walk the tokens in source order over a real scope stack: a consume is
    guarded only if some enclosing scope was opened by a positive hit check, or
    an early-return guard has already fired in a scope still on the stack.
    """
    scrubbed = _blank_out_noise(block)
    # 栈里每层记 (是否由正向命中检查开出, 该层是否已经过早退守卫)
    scopes: list[dict[str, bool]] = [{"guarded": False, "early_returned": False}]
    pending_guarded_open = False
    total = 0
    unguarded: list[str] = []

    for match in _TOKENS.finditer(scrubbed):
        kind = match.lastgroup
        if kind == "positive_guard":
            pending_guarded_open = True
        elif kind == "early_return":
            scopes[-1]["early_returned"] = True
        elif kind == "open":
            scopes.append({"guarded": pending_guarded_open, "early_returned": False})
            pending_guarded_open = False
        elif kind == "close":
            if len(scopes) > 1:
                scopes.pop()
        elif kind == "consume":
            total += 1
            covered = any(s["guarded"] or s["early_returned"] for s in scopes)
            if not covered:
                lineno = scrubbed.count("\n", 0, match.start()) + 1
                unguarded.append(f"line {lineno}: {block.splitlines()[lineno - 1].strip()}")

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
