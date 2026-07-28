import json
import re
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOCCER_TEMPLATE = PROJECT_ROOT / "templates" / "soccer_demo.html"


def _extract_js_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    opening_brace = source.index("{", start)
    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def _run_node(script: str):
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")
    return run_node_stdin(
        node_executable,
        script,
        capture_output=True,
        cwd=PROJECT_ROOT,
        timeout=10,
        check=False,
    )


@pytest.mark.unit
def test_soccer_opening_movement_bias_is_scoped_to_the_opening_play():
    source = SOCCER_TEMPLATE.read_text(encoding="utf-8")

    assert "function estimateOpeningAttackRouteY(" in source
    assert "openingMovementActive = true;" in source
    assert "if (!isPlayer) openingMovementActive = false;" in source
    assert "b.x > W * 0.72" in source
    assert "ty + routeShift * OPENING_MOVEMENT.routeBlend" in source
    assert "if (ballThreatensOwnGoal)" not in source
    assert "function predictBallAtX(" not in source


@pytest.mark.unit
def test_soccer_gameplay_script_has_valid_javascript_syntax():
    source = SOCCER_TEMPLATE.read_text(encoding="utf-8")
    inline_scripts = re.findall(r"<script>([\s\S]*?)</script>", source)
    assert inline_scripts

    result = _run_node(f"new Function({json.dumps(inline_scripts[-1])});")

    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_soccer_opening_route_follows_player_stance_with_symmetric_wall_bounces():
    source = SOCCER_TEMPLATE.read_text(encoding="utf-8")
    route_function = _extract_js_function(source, "estimateOpeningAttackRouteY")
    script = f"""
const assert = require('node:assert/strict');
const estimateRouteY = (0, eval)('(' + {json.dumps(route_function)} + ')');
const cfg = {{
  charSize: 80,
  ballRadius: 15,
  wallRestitution: 0.7,
}};
const openingConfig = {{
  liveBallSpeedMin: 120,
  maxProjectionRatio: 1.5,
}};
const ball = {{ x: 640, y: 360, vx: 0, vy: 0 }};
const ai = {{ x: 960, y: 320 }};
const playerBelow = {{ x: 460, y: 460 }};
const playerAbove = {{ x: 460, y: 180 }};

const upperRoute = estimateRouteY(ball, playerBelow, ai, 720, cfg, openingConfig);
const lowerRoute = estimateRouteY(ball, playerAbove, ai, 720, cfg, openingConfig);

assert.ok(upperRoute < 360);
assert.ok(lowerRoute > 360);
assert.equal(upperRoute, 25.5);
assert.equal(lowerRoute, 694.5);
assert.ok(Math.abs((upperRoute + lowerRoute) - 720) < 0.001);
assert.equal(
  estimateRouteY(ball, {{ x: 680, y: 460 }}, ai, 720, cfg, openingConfig),
  null,
);

const liveDownwardRoute = estimateRouteY(
  {{ x: 640, y: 360, vx: 500, vy: 500 }},
  playerBelow,
  ai,
  720,
  cfg,
  openingConfig,
);
assert.equal(liveDownwardRoute, 694.5);

assert.equal(
  estimateRouteY(
    {{ x: 640, y: 360, vx: 0, vy: 500 }},
    playerBelow,
    ai,
    720,
    cfg,
    openingConfig,
  ),
  null,
);
assert.equal(
  estimateRouteY(
    {{ x: 640, y: 360, vx: -500, vy: 100 }},
    playerBelow,
    ai,
    720,
    cfg,
    openingConfig,
  ),
  null,
);

const multipleBounceRoute = estimateRouteY(
  {{ x: 640, y: 705, vx: 120, vy: 500 }},
  playerBelow,
  ai,
  720,
  cfg,
  openingConfig,
);
assert.ok(Math.abs(multipleBounceRoute - 61.2) < 0.001);
"""
    result = _run_node(script)

    assert result.returncode == 0, result.stderr
