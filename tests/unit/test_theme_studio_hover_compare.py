"""The library hover comparison card must not change the preview column's layout.

Hovering a library row shows ``#pv-compare``. When the card sat in normal flow
next to ``.preview`` it grew the column, pushed the hovered row out from under
the pointer and made the card flicker on and off. It now lives inside
``#preview`` and is absolutely positioned over its bottom edge (ported from the
original theme-studio author's devkit 1.0.6.1, section 176).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "theme_studio.html"
STYLE = ROOT / "static" / "theme-studio" / "style.css"
APP = ROOT / "static" / "theme-studio" / "app.js"


def _css_block(css: str, selector: str) -> str:
    match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"{selector} rule not found"
    return match.group(1)


@pytest.mark.unit
def test_compare_card_is_inside_the_preview():
    html = TEMPLATE.read_text(encoding="utf-8")
    preview_at = html.index('id="preview"')
    compare_at = html.index('id="pv-compare"')
    input_at = html.index('id="pv-input-area"')
    foot_at = html.index('data-i18n="themeStudio.page.preview.foot"')
    assert preview_at < input_at < compare_at < foot_at

    # #pv-compare must be nested in #preview: count the <div> depth between them.
    between = html[preview_at:compare_at]
    opened = len(re.findall(r"<div\b", between))
    closed = len(re.findall(r"</div>", between))
    assert opened - closed >= 1, "#pv-compare closed out of #preview"

    # The existing i18n keys are kept, so no locale change is needed.
    assert 'data-i18n="themeStudio.page.preview.compareCurrent"' in html
    assert 'data-i18n="themeStudio.page.preview.compareRehearsal"' in html


@pytest.mark.unit
def test_compare_card_floats_without_taking_layout_space():
    css = STYLE.read_text(encoding="utf-8")
    compare = _css_block(css, ".pv-compare")
    assert re.search(r"position:\s*absolute", compare)
    assert "margin" not in compare
    for side in ("left", "right", "bottom"):
        assert re.search(side + r":\s*\d", compare), side
    assert re.search(r"z-index:\s*\d", compare)

    preview = _css_block(css, ".preview")
    assert re.search(r"position:\s*relative", preview)
    assert re.search(r"overflow:\s*hidden", preview)
    assert re.search(r"height:\s*\d", preview)


@pytest.mark.unit
def test_compare_card_is_still_driven_by_render_compare():
    js = APP.read_text(encoding="utf-8")
    assert "function renderCompare" in js
    assert "$('pv-compare')" in js
