"""Guard measured frontend performance regressions.

The playback loop previously wrote textContent and setAttribute 122 times each
per second. Thirty range-handle moves rebuilt state rows through fourteen
replaceChildren and 108 querySelectorAll calls. Updates should occur only when
the displayed values change.
"""

from __future__ import annotations

from playwright.sync_api import Page
from tests.emote_fixtures import spine_zip
from tests.frontend.test_emote_state_stage import BOOT

INSTR = """() => {
    window.__C = { rc: 0, qs: 0, text: 0, attr: 0, ls: 0 };
    const bump = (k) => { window.__C[k] += 1; };
    const rc = Element.prototype.replaceChildren;
    Element.prototype.replaceChildren = function (...a) { bump('rc'); return rc.apply(this, a); };
    const qsa = Element.prototype.querySelectorAll;
    Element.prototype.querySelectorAll = function (s) { bump('qs'); return qsa.call(this, s); };
    const sa = Element.prototype.setAttribute;
    window.__ATTR = [];
    Element.prototype.setAttribute = function (n, v) { bump('attr'); window.__ATTR.push({id:this.id,name:n,playback:Boolean(this.closest('.playback-controls'))}); return sa.call(this, n, v); };
    const d = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');
    Object.defineProperty(Node.prototype, 'textContent', {
        get: d.get, set(v) { bump('text'); return d.set.call(this, v); },
    });
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (k, v) { bump('ls'); return setItem.call(this, k, v); };
    return true;
}"""
BASE = "() => JSON.parse(JSON.stringify(window.__C))"
DELTA = """(base) => {
    const out = {};
    for (const k of Object.keys(window.__C)) out[k] = window.__C[k] - base[k];
    return out;
}"""


def test_playing_does_not_rewrite_unchanged_dom(page: Page, running_server: str) -> None:
    """Avoid DOM writes when the per-frame playback readings have not changed."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.set_input_files('#template-archive-file', {
        'name': 'playback.zip', 'mimeType': 'application/zip', 'buffer': spine_zip(),
    })
    page.wait_for_function("!document.querySelector('#play-animation-btn').disabled")
    page.evaluate(INSTR)
    page.evaluate("() => document.querySelector('#play-animation-btn')?.click()")
    page.wait_for_function("Number(document.querySelector('#animation-progress').value) > 1")
    page.wait_for_timeout(600)  # Exclude import completion and its initial accessibility updates.
    page.evaluate('window.__ATTR = []')
    base = page.evaluate(BASE)
    page.wait_for_timeout(1000)
    writes = page.evaluate(DELTA, base)
    # 之前这里是 text=122 / attr=122（每秒）
    assert writes["text"] <= 3, f"播放中每秒仍在重写 {writes['text']} 次文本: {writes}"
    playback_attributes = page.evaluate('__ATTR.filter(entry => entry.playback)')
    assert len(playback_attributes) <= 3, f"播放控件仍在重复写属性: {playback_attributes}"
    assert writes["rc"] == 0, writes


def test_dragging_the_range_handles_rebuilds_nothing(page: Page, running_server: str) -> None:
    """Redraw the canvas during range dragging without rebuilding its DOM."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.evaluate("""async () => { window.__STAGE_ZH = (await (await fetch('/static/locales/zh-CN.json')).json()).emoteMaker; return 1; }""")
    page.evaluate(INSTR)
    page.evaluate(BOOT, 1.0 / 12)
    page.wait_for_timeout(500)
    base = page.evaluate(BASE)
    before = page.evaluate("JSON.stringify(__STAGE.getGraph())")
    strip = page.locator(".st-strip-canvas").bounding_box()
    page.mouse.move(strip["x"] + strip["width"] * 0.98, strip["y"] + strip["height"] / 2)
    page.mouse.down()
    for i in range(30):
        page.mouse.move(strip["x"] + strip["width"] * (0.98 - i * 0.018),
                        strip["y"] + strip["height"] / 2)
    page.mouse.up()
    page.wait_for_timeout(150)
    cost = page.evaluate(DELTA, base)
    assert page.evaluate("JSON.stringify(__STAGE.getGraph())") != before
    assert cost["rc"] == 0, f"30 次拖动重建了 {cost['rc']} 块 DOM"
    # 标签表被缓存后，拖动路径上不该再有整表查询
    assert cost["qs"] <= 12, f"拖动时仍在整表查询: {cost['qs']} 次"
