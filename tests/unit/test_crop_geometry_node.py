"""Shared crop geometry of the parent bridge matches avatar-portrait's upper-body math."""

from __future__ import annotations

from pathlib import Path

from tests.visit_js_harness import BRIDGE_FILES, run_visit_js

PORTRAIT = Path(__file__).resolve().parents[2] / "static" / "avatar" / "avatar-portrait.js"


def test_portrait_formula_is_the_one_mirrored():
    src = PORTRAIT.read_text(encoding="utf-8")
    assert "Math.max(subjectRect.width * 1.04, subjectRect.height * 0.58 * aspect)" in src
    assert "Math.max(subjectRect.height * 0.64, portraitWidth / aspect)" in src


def test_upper_full_and_pixel_rects():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const G = ctx.NekoVisitCropGeometry;
        const a = 320 / 448;
        const subj = { left: 100, top: 50, width: 400, height: 1000 };
        const up = G.upperBodyRect(subj, a, 0.32);
        const noHead = G.upperBodyRect(G.upperBodySubject(subj, null), a, 0.32);
        const withHead = G.upperBodySubject(subj, { left: 250, top: 200, width: 100, height: 120 });
        const full = G.fullBodyRect(subj, 256 / 560);
        const px = G.cssRectToPixelRect({ x: 110, y: 60, width: 100, height: 140 }, { originX: 10, originY: 10, pixelRatioX: 2, pixelRatioY: 2 });
        return { up, noHead, withHead, full, px };
        """,
        files=BRIDGE_FILES,
    )
    w, h, a = 400, 1000, 320 / 448
    exp_w = max(w * 1.04, h * 0.58 * a)
    exp_h = max(h * 0.64, exp_w / a)
    up = out["up"]
    assert abs(up["width"] - exp_w) < 1e-9 and abs(up["height"] - exp_h) < 1e-9
    assert abs(up["x"] + up["width"] / 2 - 300) < 1e-9
    assert abs(up["y"] + up["height"] / 2 - (50 + 1000 * 0.32)) < 1e-9
    # No head information: align the result's top with the bounding-box top (upper 64%).
    assert abs(out["noHead"]["y"] - 50) < 1e-6 and abs(out["noHead"]["height"] - 640) < 1e-6
    assert out["withHead"]["y"] == 200 - 120 * 0.2
    full = out["full"]
    assert abs(full["width"] / full["height"] - 256 / 560) < 1e-9
    assert full["height"] >= 1040 - 1e-9 and full["width"] >= 416 - 1e-9
    assert out["px"] == {"x": 200, "y": 100, "w": 200, "h": 280}


def test_hysteresis_thresholds():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const G = ctx.NekoVisitCropGeometry;
        const base = { x: 0, y: 0, width: 100, height: 200 };
        return {
          tiny: G.shouldMove(base, { x: 1, y: 2, width: 103, height: 210 }),
          center: G.shouldMove(base, { x: 4, y: 0, width: 100, height: 200 }),
          size: G.shouldMove(base, { x: 0, y: 0, width: 100, height: 216 }),
          first: G.shouldMove(null, base),
          mid: G.lerpRect(base, { x: 100, y: 0, width: 300, height: 200 }, 0.5),
          transition: G.HYSTERESIS.transitionMs,
        };
        """,
        files=BRIDGE_FILES,
    )
    assert out["tiny"] is False
    assert out["center"] is True
    assert out["size"] is True
    assert out["first"] is True
    assert out["mid"] == {"x": 50, "y": 0, "width": 200, "height": 200}
    assert out["transition"] == 300



def test_bounds_are_clipped_to_the_viewport_before_framing():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const G = ctx.NekoVisitCropGeometry;
        const vp = { x: 0, y: 0, width: 1920, height: 1080 };
        return {
          half: G.intersectRect({ left: 1500, top: 600, right: 2300, bottom: 1900, width: 800, height: 1300 }, vp),
          outside: G.intersectRect({ x: 2000, y: 0, width: 100, height: 100 }, vp),
        };
        """,
        files=BRIDGE_FILES,
    )
    assert out["half"] == {"x": 1500, "y": 600, "width": 420, "height": 480}
    assert out["outside"] is None
