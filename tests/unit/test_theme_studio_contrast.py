"""Readability floors (WCAG contrast) for the built-in theme presets.

A badly tuned palette never raises an error or drops a frame; the only symptom
is a user quietly switching back to the default theme. ``theme_studio/contrast.py``
defines 17 text/surface pairs, each composited the way the chat window paints
it, and this module turns "every preset x light/dark x 17 pairs" into a
regression check that every change has to pass.

When a pair fails, the usual cause is a text colour that was not adjusted
together with its background: fix the text key in
``theme_studio/presets/<id>.json`` (and its ``<id>-material.json`` twin).
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from theme_studio import contrast

PRESETS = pathlib.Path(__file__).resolve().parents[2] / "theme_studio" / "presets"


def test_presets_dir_exists():
    assert PRESETS.is_dir(), f"preset directory not found: {PRESETS}"
    assert len(list(PRESETS.glob("*.json"))) >= 100


def test_all_presets_meet_contrast_floors():
    """Every preset, in both light and dark mode, passes all 17 pairs."""
    themes, fails, pair_checks = contrast.audit_dir(str(PRESETS))
    assert pair_checks > 3000, f"too few pair checks ({pair_checks}); presets may not have loaded"
    if fails:
        details = "\n".join(
            f"  {theme_id} [{mode}] {row['pair']}: {row['ratio']:.2f} < {row['floor']:.1f} ({row['fg']} / {row['bg']})"
            for theme_id, mode, row in sorted(fails, key=lambda it: it[2]["ratio"])[:20]
        )
        pytest.fail(f"{len(fails)} failing pairs out of {pair_checks}:\n{details}")


def test_audit_can_actually_fail():
    """The floor has teeth: a link colour close to its surface must be reported."""
    file_path = PRESETS / "arctic-glass-material.json"
    doc = json.loads(file_path.read_text(encoding="utf-8-sig"))
    broken = copy.deepcopy(doc)
    broken.setdefault("vars", {})["--nts-link"] = "#eef2f4"

    fails, pair_checks = contrast.failing([("broken", broken)])
    assert pair_checks > 0
    link_fails = [row for _, _, row in fails if row["pair"] == "link"]
    assert link_fails, "a link colour close to its surface was not reported"
    assert link_fails[0]["ratio"] < 1.5

    # The original file must still pass, so the check is not just "already broken".
    ok_fails, _ = contrast.failing([("original", doc)])
    assert not [row for _, _, row in ok_fails if row["pair"] == "link"]


def test_bubble_gradient_paints_above_bubble_colour():
    """An opaque bubble gradient decides the surface, not the bubble colour below it."""
    vars_map = {
        "--nts-chat-bg": "#ffffff",
        "--nts-user-bubble-bg": "#000000",
        "--nts-user-bubble-gradient": "linear-gradient(135deg, #ffffff 0%, #ffffff 100%)",
    }
    assert contrast.resolve_surface(vars_map, contrast.USER_BUBBLE, "--nts-user-bubble-bg")[:3] == (255, 255, 255)


def test_glass_tint_replaces_bubble_colour():
    """``--nts-bubble-glass-tint`` replaces the bubble colour instead of stacking on it."""
    vars_map = {
        "--nts-chat-bg": "#000000",
        "--nts-ai-bubble-bg": "#ffffff",
        "--nts-bubble-glass-tint": "rgba(255, 255, 255, 0.2)",
    }
    surface = contrast.resolve_surface(vars_map, contrast.AI_BUBBLE, "--nts-ai-bubble-bg")
    assert surface[:3] == (51, 51, 51)


def test_contrast_extremes():
    white, black = (255, 255, 255, 1.0), (0, 0, 0, 1.0)
    assert contrast.contrast(black, white) == pytest.approx(21.0, abs=0.02)
    assert contrast.contrast(white, white) == pytest.approx(1.0, abs=1e-6)


def test_parse_color_formats():
    assert contrast.parse_color("#4089B3") == (64, 137, 179, 1.0)
    assert contrast.parse_color("#abc") == (170, 187, 204, 1.0)
    assert contrast.parse_color("rgba(64, 137, 179, 0.5)") == (64, 137, 179, 0.5)
    assert contrast.parse_color("none") is None
    assert contrast.parse_color("not a colour") is None
