"""Fractional frame accumulator of the parent bridge (design §3.3.5, D.5)."""

from __future__ import annotations

import pytest

from tests.visit_js_harness import BRIDGE_FILES, run_visit_js

_SAMPLER_JS = """
const ctx = makeContext();
load(ctx, FILES);
const S = ctx.NekoVisitParentBridge;
// 源以 period 毫秒出帧，带 ±jitter 抖动；返回每帧是否抓取
function drive(sampler, frames) { return frames.map((t) => sampler.onRender(t) ? t : null).filter((x) => x !== null); }
function series(startMs, periodMs, durationMs, jitter) {
  const out = []; let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 - 0.5; };
  for (let t = startMs; t < startMs + durationMs; t += periodMs) out.push(t + (jitter ? rnd() * jitter : 0));
  return out;
}
function gate33(frames) { let last = -Infinity; return frames.filter((t) => { if (t - last >= 33.3) { last = t; return true; } return false; }); }
"""


@pytest.mark.parametrize("hz", [60, 75, 144, 165])
def test_any_refresh_rate_samples_exactly_thirty(hz):
    out = run_visit_js(
        _SAMPLER_JS + f"""
        const s = S.createSampler({{ targetFps: 30, initialHz: {hz} }});
        const caps = drive(s, series(0, 1000 / {hz}, 10000, 0.6));
        const gate = gate33(series(0, 1000 / {hz}, 10000, 0.6));
        return {{ fps: caps.length / 10, gate: gate.length / 10, firstSecond: caps.filter((t) => t < 1000).length }};
        """,
        files=BRIDGE_FILES,
    )
    assert abs(out["fps"] - 30) <= 0.5
    # No extra capture in the first cold-start second (initialize from refresh rate).
    assert abs(out["firstSecond"] - 30) <= 1
    if hz == 144:
        assert out["gate"] < 29.5  # Comparison: a >=33 ms since-last-capture gate yields only about 28.8 fps at 144 Hz.


def test_timer_driven_seventeen_ms_and_compat_fifteen():
    out = run_visit_js(
        _SAMPLER_JS + """
        const timer = drive(S.createSampler({ targetFps: 30, initialHz: 59 }), series(0, 17, 10000, 2));
        const compat = drive(S.createSampler({ targetFps: 15, initialHz: 60 }), series(0, 1000 / 60, 10000, 0.5));
        const slow = drive(S.createSampler({ targetFps: 30, initialHz: 60 }), series(0, 40, 10000, 0));
        return { timer: timer.length / 10, compat: compat.length / 10, slow: slow.length / 10 };
        """,
        files=BRIDGE_FILES,
    )
    assert abs(out["timer"] - 30) <= 0.5
    assert abs(out["compat"] - 15) <= 0.5
    assert abs(out["slow"] - 25) <= 0.15  # Source below target rate: capture every frame.


def test_high_rates_keep_the_fractional_remainder():
    out = run_visit_js(
        _SAMPLER_JS + """
        const r75 = drive(S.createSampler({ targetFps: 30, initialHz: 75 }), series(0, 1000 / 75, 1000, 0)).length;
        const r144 = drive(S.createSampler({ targetFps: 30, initialHz: 144 }), series(0, 1000 / 144, 1000, 0)).length;
        return { r75, r144 };
        """,
        files=BRIDGE_FILES,
    )
    assert abs(out["r75"] - 30) <= 0.5
    assert abs(out["r144"] - 30) <= 0.5


def test_recovery_from_a_slow_source_does_not_burst():
    out = run_visit_js(
        _SAMPLER_JS + """
        const s = S.createSampler({ targetFps: 30, initialHz: 60 });
        drive(s, series(0, 100, 5000, 0));                 // 10 fps 持续 5 s
        const caps = drive(s, series(5000, 1000 / 60, 1000, 0.3));   // 回到 60 fps
        let worst = 0;
        for (const t of caps) worst = Math.max(worst, caps.filter((x) => x >= t && x < t + 100).length);
        // 帧率突变（60 → 144）1 s 内收敛
        const s2 = S.createSampler({ targetFps: 30, initialHz: 60 });
        drive(s2, series(0, 1000 / 60, 2000, 0));
        const after = drive(s2, series(2000, 1000 / 144, 1000, 0)).length;
        return { second: caps.length, worst, after };
        """,
        files=BRIDGE_FILES,
    )
    assert abs(out["second"] - 30) <= 0.5
    assert out["worst"] <= 4
    assert abs(out["after"] - 30) <= 1
