import json
import re
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION = PROJECT_ROOT / "static" / "live2d" / "live2d-interaction.js"
VRM_INTERACTION = PROJECT_ROOT / "static" / "vrm" / "vrm-interaction.js"


def _live2d_wheel_zoom_source() -> str:
    source = LIVE2D_INTERACTION.read_text(encoding="utf-8")
    start = source.index("Live2DManager.prototype.setupWheelZoom = function (model)")
    end = source.index("// 设置触摸缩放", start)
    return source[start:end]


def _run_wheel_scenarios(scenarios: list[dict]) -> list[dict]:
    """Drive the real setupWheelZoom in node and report what each event did.

    Static text analysis was tried three times here and leaked every time —
    per-line brace depth missed ``} else {``, registering a line's guards
    before scanning it missed ``consume; if (!hit) return;``, and a regex-based
    scrubber still had to keep up with comments, strings, regex literals and
    automatic semicolon insertion.  That is a JavaScript parser, and a
    half-written one reads as a passing guard.  Running the handler answers the
    question the file actually claims to answer: does the wheel get consumed
    when the pointer is not on the model?
    """
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is required for the wheel guard behaviour test")

    harness = textwrap.dedent("""
        const SCALE_LIMITS = { MIN: 0.1, MAX: 10 };
        function Live2DManager() {}
        __WHEEL_ZOOM_SOURCE__

        const scenarios = __SCENARIOS__;
        const results = [];

        for (const scenario of scenarios) {
            let registered = null;
            const model = {
                getBounds: () => ({ x: 100, y: 100, width: 200, height: 200,
                                    left: 100, top: 100, right: 300, bottom: 300 }),
                scale: { x: 1, set(next) { this.x = next; } },
            };
            const manager = new Live2DManager();
            manager.isLocked = scenario.locked === true;
            manager.currentModel = model;
            manager.isLive2DPeekActive = () => scenario.peek === true;
            manager._debouncedSnapCheck = () => {};
            manager.pixi_app = {
                renderer: { screen: { width: 400, height: 400 } },
                view: {
                    getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 400 }),
                    addEventListener: (type, handler) => { if (type === 'wheel') registered = handler; },
                    removeEventListener: () => {},
                },
            };

            Live2DManager.prototype.setupWheelZoom.call(manager, model);
            if (typeof registered !== 'function') {
                throw new Error('setupWheelZoom did not register a wheel listener');
            }

            let prevented = false;
            registered({
                clientX: scenario.x,
                clientY: scenario.y,
                deltaY: -100,
                preventDefault: () => { prevented = true; },
            });

            results.push({
                name: scenario.name,
                prevented,
                zoomed: model.scale.x !== 1,
            });
        }

        process.stdout.write(JSON.stringify(results));
    """)
    harness = harness.replace("__WHEEL_ZOOM_SOURCE__", _live2d_wheel_zoom_source())
    harness = harness.replace("__SCENARIOS__", json.dumps(scenarios))

    completed = run_node_stdin(
        node_path,
        harness,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, (
        f"wheel harness failed:\n{completed.stderr or completed.stdout}"
    )
    return json.loads(completed.stdout)


@pytest.mark.unit
def test_live2d_wheel_zoom_requires_model_hit_before_consuming_event():
    """Off-model wheel events must reach the page; on-model ones must not."""
    results = {
        r["name"]: r
        for r in _run_wheel_scenarios([
            # 模型盒是 canvas 上的 100..300；中心点必然命中，远角必然不命中。
            {"name": "on_model", "x": 200, "y": 200},
            {"name": "off_model", "x": 20, "y": 20},
            {"name": "on_model_peek", "x": 200, "y": 200, "peek": True},
            {"name": "locked", "x": 200, "y": 200, "locked": True},
        ])
    }

    assert results["off_model"]["prevented"] is False, (
        "指针不在模型上时吞掉滚轮，页面就滚不动了"
    )
    assert results["off_model"]["zoomed"] is False

    assert results["on_model"]["prevented"] is True, "命中模型必须消费掉滚轮"
    assert results["on_model"]["zoomed"] is True, "命中模型必须真的缩放"

    # 挂边探身（#2253）：吞掉滚轮但不缩放，且这一步同样以命中检查为前提。
    assert results["on_model_peek"]["prevented"] is True
    assert results["on_model_peek"]["zoomed"] is False

    assert results["locked"]["prevented"] is False
    assert results["locked"]["zoomed"] is False


@pytest.mark.unit
def test_live2d_wheel_hit_test_uses_canvas_relative_coordinates():
    """Pin the mechanism the behaviour test cannot see from outside.

    Reading the hit point off the canvas rect (rather than raw client
    coordinates) is what keeps the check correct once the canvas is offset or
    scaled; a regression there still passes a centred-canvas simulation.
    """
    block = _live2d_wheel_zoom_source()
    assert re.search(r"const\s+isWheelPointOnCurrentModel\s*=\s*\(event\)\s*=>\s*{", block)
    assert re.search(r"getBoundingClientRect\s*\(\)", block)
    assert re.search(r"event\.clientX\s*-\s*canvasRect\.left", block)
    assert re.search(r"event\.clientY\s*-\s*canvasRect\.top", block)


@pytest.mark.unit
def test_vrm_wheel_zoom_requires_model_hit_before_consuming_event():
    # VRM 侧只有一个 preventDefault，且守卫是同一层的早退，顺序比较足够表达
    # 该不变量；live2d 那边换成行为验证是因为它有两条消费路径（缩放 + 挂边
    # 探身），静态判定要区分它们就得真的解析 JS。
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
    assert block.count("e.preventDefault()") == 1, (
        "VRM handler 出现了第二个消费点，顺序比较不再足够，改走行为验证"
    )
