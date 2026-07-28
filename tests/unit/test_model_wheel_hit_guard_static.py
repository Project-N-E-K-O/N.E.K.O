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


def _node_path() -> str:
    node_path = shutil.which("node")
    if not node_path:
        # 硬失败而不是 skip：这些用例取代的是无条件运行的静态检查，若 node 从
        # PATH 上消失就静默跳过，闸门会在没有检查过滚轮行为的情况下报绿。
        # CI runner 自带 node（见 unit-tests.yml 的说明）。
        raise AssertionError("node is required to run the wheel guard behaviour tests")
    return node_path


def _run_harness(harness: str) -> list[dict]:
    completed = run_node_stdin(
        _node_path(),
        harness,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, (
        f"wheel harness failed:\n{completed.stderr or completed.stdout}"
    )
    return json.loads(completed.stdout)


def _live2d_wheel_zoom_source() -> str:
    source = LIVE2D_INTERACTION.read_text(encoding="utf-8")
    start = source.index("Live2DManager.prototype.setupWheelZoom = function (model)")
    end = source.index("// 设置触摸缩放", start)
    return source[start:end]


def _vrm_wheel_handler_source() -> str:
    source = VRM_INTERACTION.read_text(encoding="utf-8")
    start = source.index("this.wheelHandler = (e) => {")
    end = source.index("this.auxClickHandler = (e) => {", start)
    return source[start:end]


def _run_live2d_wheel_scenarios(scenarios: list[dict]) -> dict[str, dict]:
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
    harness = textwrap.dedent("""
        const SCALE_LIMITS = { MIN: 0.1, MAX: 10 };
        function Live2DManager() {}
        __WHEEL_ZOOM_SOURCE__

        const scenarios = __SCENARIOS__;
        const results = [];

        for (const scenario of scenarios) {
            // 浏览器会挨个调用同一元素上注册的每个 wheel listener，所以这里
            // 累积全部而不是只留最后一个：只留最后一个的话，实现多注册一个
            // 无守卫的 listener，真实页面照样吞事件，而这里会被覆盖掉。
            const listeners = [];
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
                    addEventListener: (type, handler) => {
                        if (type === 'wheel') listeners.push(handler);
                    },
                    removeEventListener: (type, handler) => {
                        const index = listeners.indexOf(handler);
                        if (index >= 0) listeners.splice(index, 1);
                    },
                },
            };

            Live2DManager.prototype.setupWheelZoom.call(manager, model);
            if (listeners.length === 0) {
                throw new Error('setupWheelZoom did not register a wheel listener');
            }

            let prevented = false;
            for (const listener of listeners) {
                listener({
                    clientX: scenario.x,
                    clientY: scenario.y,
                    deltaY: scenario.deltaY,
                    preventDefault: () => { prevented = true; },
                });
            }

            results.push({
                name: scenario.name,
                prevented,
                zoomed: model.scale.x !== 1,
                listeners: listeners.length,
            });
        }

        process.stdout.write(JSON.stringify(results));
    """)
    harness = harness.replace("__WHEEL_ZOOM_SOURCE__", _live2d_wheel_zoom_source())
    harness = harness.replace("__SCENARIOS__", json.dumps(scenarios))
    return {r["name"]: r for r in _run_harness(harness)}


def _run_vrm_wheel_scenarios(scenarios: list[dict]) -> dict[str, dict]:
    """Same treatment for the VRM handler.

    It used to be checked by comparing offsets plus counting the literal string
    ``e.preventDefault()``, which misses ``e?.preventDefault()`` — an idiom the
    repo already uses elsewhere. Counting syntax forms is the same losing game
    as parsing them, so this drives the handler instead.
    """
    harness = textwrap.dedent("""
        const scenarios = __SCENARIOS__;
        const results = [];

        for (const scenario of scenarios) {
            const canvasEl = { contains: (node) => node === canvasEl };
            const scaleCalls = [];
            const context = {
                checkLocked: () => scenario.locked === true,
                _hitTestModel: () => scenario.hit === true,
                _debouncedSavePosition: () => {},
                _wheelListenerOptions: { passive: false },
                manager: {
                    currentModel: { scene: { scale: { x: 1 } } },
                    renderer: { domElement: canvasEl },
                    _lastInteractionBoostTs: 0,
                    _boostInteractiveFPS: () => {},
                    setModelScaleScalar: (next) => { scaleCalls.push(next); },
                },
                wheelHandler: null,
            };

            (function () { __VRM_WHEEL_SOURCE__ }).call(context);
            if (typeof context.wheelHandler !== 'function') {
                throw new Error('wheelHandler was not assigned');
            }

            let prevented = false;
            context.wheelHandler({
                clientX: scenario.x,
                clientY: scenario.y,
                deltaY: scenario.deltaY,
                target: scenario.offCanvas === true ? { other: true } : canvasEl,
                preventDefault: () => { prevented = true; },
                stopPropagation: () => {},
            });

            results.push({ name: scenario.name, prevented, zoomed: scaleCalls.length > 0 });
        }

        process.stdout.write(JSON.stringify(results));
    """)
    harness = harness.replace("__VRM_WHEEL_SOURCE__", _vrm_wheel_handler_source())
    harness = harness.replace("__SCENARIOS__", json.dumps(scenarios))
    return {r["name"]: r for r in _run_harness(harness)}


def _both_directions(base: dict) -> list[dict]:
    """Same scenario scrolled up and down.

    A guard that only covers one sign still reads as correct otherwise: e.g.
    ``if (event.deltaY > 0) event.preventDefault()`` ahead of the hit test
    swallows off-model wheel-down while every upward assertion stays green.
    """
    return [
        {**base, "name": f"{base['name']}_up", "deltaY": -100},
        {**base, "name": f"{base['name']}_down", "deltaY": 100},
    ]


@pytest.mark.unit
def test_live2d_wheel_zoom_requires_model_hit_before_consuming_event():
    """Off-model wheel events must reach the page; on-model ones must not."""
    # 模型盒是 canvas 上的 100..300；中心点必然命中，远角必然不命中。
    scenarios = (
        _both_directions({"name": "on_model", "x": 200, "y": 200})
        + _both_directions({"name": "off_model", "x": 20, "y": 20})
        + _both_directions({"name": "on_model_peek", "x": 200, "y": 200, "peek": True})
        # 探身分支必须同样以命中为前提：只测 on-model 的话，把该分支改成
        # 无条件 preventDefault 仍能满足全部断言（实测过）。
        + _both_directions({"name": "off_model_peek", "x": 20, "y": 20, "peek": True})
        + _both_directions({"name": "locked", "x": 200, "y": 200, "locked": True})
    )
    results = _run_live2d_wheel_scenarios(scenarios)

    for direction in ("up", "down"):
        assert results[f"off_model_{direction}"]["prevented"] is False, (
            f"指针不在模型上时吞掉滚轮（{direction}），页面就滚不动了"
        )
        assert results[f"off_model_{direction}"]["zoomed"] is False
        # 挂边探身（#2253）同样以命中为前提。
        assert results[f"off_model_peek_{direction}"]["prevented"] is False
        assert results[f"off_model_peek_{direction}"]["zoomed"] is False
        assert results[f"on_model_{direction}"]["prevented"] is True, "命中模型必须消费掉滚轮"
        assert results[f"on_model_{direction}"]["zoomed"] is True, "命中模型必须真的缩放"
        # 探身态：吞事件但不缩放。
        assert results[f"on_model_peek_{direction}"]["prevented"] is True
        assert results[f"on_model_peek_{direction}"]["zoomed"] is False
        assert results[f"locked_{direction}"]["prevented"] is False
        assert results[f"locked_{direction}"]["zoomed"] is False


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
    scenarios = (
        _both_directions({"name": "on_model", "x": 200, "y": 200, "hit": True})
        + _both_directions({"name": "off_model", "x": 20, "y": 20})
        + _both_directions({"name": "off_canvas", "x": 200, "y": 200, "hit": True,
                            "offCanvas": True})
        + _both_directions({"name": "locked", "x": 200, "y": 200, "hit": True,
                            "locked": True})
    )
    results = _run_vrm_wheel_scenarios(scenarios)

    for direction in ("up", "down"):
        assert results[f"off_model_{direction}"]["prevented"] is False, (
            f"未命中模型时吞掉滚轮（{direction}）"
        )
        assert results[f"off_model_{direction}"]["zoomed"] is False
        # 事件不在 canvas 上时必须完全放行，否则聊天区滚不动。
        assert results[f"off_canvas_{direction}"]["prevented"] is False
        assert results[f"off_canvas_{direction}"]["zoomed"] is False
        assert results[f"locked_{direction}"]["prevented"] is False
        assert results[f"on_model_{direction}"]["prevented"] is True
        assert results[f"on_model_{direction}"]["zoomed"] is True


@pytest.mark.unit
def test_vrm_wheel_hit_test_uses_client_coordinates():
    """Dual of the live2d mechanism check: the stubbed hit test hides this."""
    block = _vrm_wheel_handler_source()
    assert "this._hitTestModel(e.clientX, e.clientY)" in block
